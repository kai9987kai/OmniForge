from __future__ import annotations

import copy
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .history_store import HistoryStore
    from .service import OmniForgeService


class MissionControl:
    def __init__(self, service: "OmniForgeService", out_dir: Path, history: "HistoryStore"):
        self.service = service
        self.out_dir = Path(out_dir).resolve()
        self.history = history
        self.out_dir.mkdir(parents=True, exist_ok=True)

    def catalog(self) -> dict[str, Any]:
        return {
            "ok": True,
            "missions": [
                self._mission_meta("quick_intel"),
                self._mission_meta("performance_watch"),
                self._mission_meta("workflow_stress_probe"),
                self._mission_meta("repo_hunt"),
            ],
        }

    def plan(self, mission_name: str, inputs: dict[str, Any] | None = None) -> dict[str, Any]:
        name = str(mission_name or "").strip()
        inputs = dict(inputs or {})
        mission = self._mission_def(name)
        rendered_steps = [self._render(step, {"inputs": inputs}) for step in mission["steps"]]
        return {
            "ok": True,
            "mission": name,
            "description": mission["description"],
            "inputs": inputs,
            "steps": rendered_steps,
        }

    def run(self, mission_name: str, inputs: dict[str, Any] | None = None) -> dict[str, Any]:
        name = str(mission_name or "").strip()
        inputs = dict(inputs or {})
        mission = self._mission_def(name)
        plan = self.plan(name, inputs)
        now = datetime.now(timezone.utc)
        run_id = f"mission_{now.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
        outputs: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        runtime_ctx: dict[str, Any] = {"inputs": inputs}
        for idx, raw_step in enumerate(mission["steps"], start=1):
            step = self._render(raw_step, runtime_ctx)
            kind = str(step.get("kind") or "")
            params = dict(step.get("params") or {})
            if kind == "noop":
                outputs.append({"step_index": idx, "kind": kind, "params": params, "ok": True})
                runtime_ctx[f"step{idx}"] = {"skipped": True}
                continue
            try:
                result = self.service.run_operation(kind, params)
                summary = self.service.summarize_operation_result(kind, result)
                outputs.append(
                    {
                        "step_index": idx,
                        "kind": kind,
                        "params": params,
                        "ok": True,
                        "summary": summary,
                        "result": result,
                    }
                )
                if isinstance(result, dict):
                    runtime_ctx[f"step{idx}"] = dict(result)
                else:
                    runtime_ctx[f"step{idx}"] = {"value": result}
                runtime_ctx[f"step{idx}_summary"] = summary
            except Exception as exc:
                errors.append({"step_index": idx, "kind": kind, "error": str(exc)})
                outputs.append({"step_index": idx, "kind": kind, "params": params, "ok": False, "error": str(exc)})
                runtime_ctx[f"step{idx}"] = {"error": str(exc)}
        artifact = self.out_dir / f"{run_id}.json"
        payload = {
            "ok": len(errors) == 0,
            "run_id": run_id,
            "created_at": now.isoformat(),
            "mission": plan["mission"],
            "description": plan["description"],
            "inputs": plan["inputs"],
            "steps": outputs,
            "errors": errors,
        }
        artifact.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        self.history.record(
            "mission.run",
            {"run_id": run_id, "mission": plan["mission"], "artifact": str(artifact), "ok": payload["ok"], "errors": errors},
            source="missions",
            tags=["mission"],
            summary={
                "run_id": run_id,
                "mission": plan["mission"],
                "ok": payload["ok"],
                "step_count": len(outputs),
                "error_count": len(errors),
                "artifact": str(artifact),
            },
        )
        return {**payload, "artifact_file": str(artifact)}

    def _mission_meta(self, name: str) -> dict[str, Any]:
        m = self._mission_def(name)
        return {
            "name": name,
            "description": m["description"],
            "step_count": len(m["steps"]),
            "input_schema": m.get("input_schema", {}),
        }

    def _mission_def(self, name: str) -> dict[str, Any]:
        missions = {
            "quick_intel": {
                "description": "Fast local capability + benchmark + benchmark trend snapshot.",
                "input_schema": {},
                "steps": [
                    {"kind": "status", "params": {}},
                    {"kind": "benchmark.run", "params": {"profile": "fast_local"}},
                    {"kind": "benchmark.analytics.trends", "params": {"suite": "vision_random", "metric": "infer_ms_stats.avg", "limit": 10}},
                ],
            },
            "performance_watch": {
                "description": "Run performance benchmark and compare with a baseline if provided.",
                "input_schema": {"baseline_run": "optional benchmark run_id/file path"},
                "steps": [
                    {"kind": "benchmark.run", "params": {"profile": "fast_local"}},
                    {"kind": "benchmark.analytics.list", "params": {"limit": 5}},
                    {
                        "kind": "benchmark.analytics.compare",
                        "params": {
                            "baseline": "${inputs.baseline_run}",
                            "candidate": "${step1.run_id}",
                        },
                        "when": {"inputs.baseline_run": "nonempty"},
                    },
                ],
            },
            "workflow_stress_probe": {
                "description": "Probe workflow execution path and summarize with local chat.",
                "input_schema": {"task": "optional synthesis task"},
                "steps": [
                    {"kind": "benchmark.run", "params": {"profile": "workflow_probe"}},
                    {
                        "kind": "chat.answer",
                        "params": {
                            "task": "Summarize the workflow benchmark health and likely bottlenecks in one paragraph. ${inputs.task}",
                            "session_id": "mission_workflow_probe",
                        },
                    },
                ],
            },
            "repo_hunt": {
                "description": "Search local cloned repos for a feature keyword and summarize hits.",
                "input_schema": {"query": "required file-name query", "text": "optional text pattern"},
                "steps": [
                    {"kind": "search.files", "params": {"query": "${inputs.query}", "limit": 40}},
                    {"kind": "search.text", "params": {"pattern": "${inputs.text}", "limit_matches": 30}, "when": {"inputs.text": "nonempty"}},
                ],
            },
        }
        if name not in missions:
            raise ValueError(f"unknown mission: {name}")
        return copy.deepcopy(missions[name])

    def _render(self, step: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
        out = copy.deepcopy(step)
        when = out.pop("when", None)
        if when and not self._when_matches(when, ctx):
            return {"kind": "noop", "params": {"reason": "condition not met"}}
        return self._render_obj(out, ctx)

    def _render_obj(self, value: Any, ctx: dict[str, Any]) -> Any:
        if isinstance(value, str):
            return _substitute(value, ctx)
        if isinstance(value, list):
            return [self._render_obj(v, ctx) for v in value]
        if isinstance(value, dict):
            return {k: self._render_obj(v, ctx) for k, v in value.items()}
        return value

    def _when_matches(self, when: dict[str, Any], ctx: dict[str, Any]) -> bool:
        for key, rule in when.items():
            val = _resolve_path(ctx, str(key))
            if rule == "nonempty":
                if val is None or str(val).strip() == "":
                    return False
            elif rule == "exists":
                if val is None:
                    return False
            else:
                if val != rule:
                    return False
        return True


_TOKEN_RE = re.compile(r"\$\{([A-Za-z0-9_.-]+)\}")


def _substitute(text: str, ctx: dict[str, Any]) -> str:
    def repl(match: re.Match[str]) -> str:
        key = match.group(1)
        val = _resolve_path(ctx, key)
        if val is None:
            return ""
        if isinstance(val, (dict, list)):
            return json.dumps(val, ensure_ascii=False)
        return str(val)

    return _TOKEN_RE.sub(repl, text).strip()


def _resolve_path(data: Any, path: str) -> Any:
    cur = data
    for part in str(path).split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    return cur

from __future__ import annotations

import json
import statistics
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .history_store import HistoryStore
    from .service import OmniForgeService


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


class BenchmarkArena:
    def __init__(self, service: "OmniForgeService", out_dir: Path, history: "HistoryStore"):
        self.service = service
        self.out_dir = Path(out_dir).resolve()
        self.history = history
        self.out_dir.mkdir(parents=True, exist_ok=True)

    def profiles(self) -> dict[str, Any]:
        return {
            "ok": True,
            "profiles": {
                "fast_local": {
                    "description": "Quick local sanity benchmark (status + vision random inference).",
                    "suites": ["system_status", "vision_random"],
                    "vision_random": {"runs": 3},
                },
                "chat_vision": {
                    "description": "Vision inference + chat latency benchmark.",
                    "suites": ["vision_random", "chat_latency"],
                    "vision_random": {"runs": 5},
                    "chat_latency": {"prompts": ["hello", "Summarize local AI workbench status in one sentence."], "runs_per_prompt": 1},
                },
                "workflow_probe": {
                    "description": "Runs a NexusFlow example to verify workflow execution path.",
                    "suites": ["system_status", "nexusflow_examples"],
                    "nexusflow_examples": {"examples": [{"name": "npu_workflow_demo.nxf", "pipeline": "build"}]},
                },
            },
        }

    def run(
        self,
        *,
        config: dict[str, Any] | None = None,
        profile: str | None = None,
    ) -> dict[str, Any]:
        cfg = self._resolved_config(config=config, profile=profile)
        run_id = self._new_run_id()
        t0 = time.perf_counter()
        started = _now_utc().isoformat()
        suites: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        for suite_name in cfg.get("suites", []):
            try:
                suites.append(self._run_suite(str(suite_name), cfg))
            except Exception as exc:
                errors.append({"suite": str(suite_name), "error": str(exc)})
        finished = _now_utc().isoformat()
        total_ms = round((time.perf_counter() - t0) * 1000, 1)
        result = {
            "ok": len(errors) == 0,
            "run_id": run_id,
            "profile": profile,
            "config": cfg,
            "started_at": started,
            "finished_at": finished,
            "total_ms": total_ms,
            "suites": suites,
            "errors": errors,
            "summary": {
                "suite_count": len(suites),
                "error_count": len(errors),
                "total_ms": total_ms,
            },
        }
        artifact = self.out_dir / f"{run_id}.json"
        artifact.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
        self.history.record(
            "benchmark.run",
            {"run_id": run_id, "profile": profile, "artifact": str(artifact), "result": result},
            source="benchmark_arena",
            tags=["benchmark"],
            summary={
                "run_id": run_id,
                "profile": profile,
                "total_ms": total_ms,
                "suite_count": len(suites),
                "error_count": len(errors),
                "artifact": str(artifact),
            },
        )
        return {**result, "artifact_file": str(artifact)}

    def _resolved_config(self, *, config: dict[str, Any] | None, profile: str | None) -> dict[str, Any]:
        if profile:
            profiles = self.profiles()["profiles"]
            if profile not in profiles:
                raise ValueError(f"unknown benchmark profile: {profile}")
            base = dict(profiles[profile])
        else:
            base = {}
        cfg = dict(base)
        if config:
            for k, v in dict(config).items():
                cfg[k] = v
        suites = cfg.get("suites") or ["system_status", "vision_random"]
        if isinstance(suites, str):
            suites = [s.strip() for s in suites.split(",") if s.strip()]
        cfg["suites"] = list(suites)
        return cfg

    def _run_suite(self, suite_name: str, cfg: dict[str, Any]) -> dict[str, Any]:
        fn = getattr(self, f"_suite_{suite_name}", None)
        if fn is None:
            raise ValueError(f"unsupported suite: {suite_name}")
        t0 = time.perf_counter()
        payload = fn(dict(cfg.get(suite_name) or {}))
        duration_ms = round((time.perf_counter() - t0) * 1000, 1)
        return {
            "suite": suite_name,
            "duration_ms": duration_ms,
            **(payload if isinstance(payload, dict) else {"result": payload}),
        }

    def _suite_system_status(self, _cfg: dict[str, Any]) -> dict[str, Any]:
        status = self.service.status()
        return {
            "ok": True,
            "summary": {
                "chat_loaded": bool(((status.get("chat") or {}).get("loaded"))),
                "vision_model_exists": bool(((status.get("vision") or {}).get("model_exists"))),
                "nexusflow_examples": len(((status.get("nexusflow") or {}).get("examples") or [])),
            },
        }

    def _suite_vision_random(self, cfg: dict[str, Any]) -> dict[str, Any]:
        runs = max(1, int(cfg.get("runs", 3)))
        samples = []
        for _ in range(runs):
            samples.append(self.service.vision.benchmark_random())
        ms = [float(s.get("infer_ms")) for s in samples if isinstance(s, dict) and s.get("infer_ms") is not None]
        provider = next((s.get("provider") for s in samples if isinstance(s, dict) and s.get("provider")), None)
        return {
            "ok": True,
            "provider": provider,
            "runs": runs,
            "infer_ms_stats": self._stats(ms),
            "samples": samples,
        }

    def _suite_chat_latency(self, cfg: dict[str, Any]) -> dict[str, Any]:
        prompts = cfg.get("prompts") or ["hello"]
        if isinstance(prompts, str):
            prompts = [prompts]
        runs_per_prompt = max(1, int(cfg.get("runs_per_prompt", 1)))
        session_prefix = str(cfg.get("session_prefix") or "benchmark")
        results = []
        wall_ms = []
        model_ms = []
        for i, prompt in enumerate(prompts):
            for j in range(runs_per_prompt):
                t0 = time.perf_counter()
                resp = self.service.chat.chat(str(prompt), session_id=f"{session_prefix}_{i}_{j}")
                elapsed = round((time.perf_counter() - t0) * 1000, 1)
                wall_ms.append(elapsed)
                timing = resp.get("timing_ms")
                if isinstance(timing, (int, float)):
                    model_ms.append(float(timing))
                results.append(
                    {
                        "prompt": str(prompt),
                        "run_index": j,
                        "wall_ms": elapsed,
                        "timing_ms": timing,
                        "response_preview": str(resp.get("response") or "")[:240],
                        "style_mode": resp.get("style_mode"),
                    }
                )
        return {
            "ok": True,
            "runs": len(results),
            "wall_ms_stats": self._stats(wall_ms),
            "model_timing_ms_stats": self._stats(model_ms) if model_ms else None,
            "samples": results,
        }

    def _suite_nexusflow_examples(self, cfg: dict[str, Any]) -> dict[str, Any]:
        examples = cfg.get("examples") or [{"name": "npu_workflow_demo.nxf", "pipeline": "build"}]
        if isinstance(examples, str):
            examples = [{"name": examples}]
        out_dir = cfg.get("out_dir") or str(self.service.paths.workspace_root / "omniforge_ai" / "out" / "benchmarks" / "nexusflow")
        runs = []
        durations = []
        for item in examples:
            if isinstance(item, str):
                item = {"name": item}
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            t0 = time.perf_counter()
            res = self.service.nexusflow.run_example(name, pipeline=item.get("pipeline"), out_dir=out_dir)
            elapsed = round((time.perf_counter() - t0) * 1000, 1)
            durations.append(elapsed)
            runs.append(
                {
                    "example": name,
                    "pipeline": item.get("pipeline"),
                    "wall_ms": elapsed,
                    "ok": res.get("ok"),
                    "returncode": res.get("returncode"),
                    "summary": res.get("summary"),
                }
            )
        return {
            "ok": all(bool(r.get("ok")) for r in runs) if runs else False,
            "runs": runs,
            "wall_ms_stats": self._stats(durations) if durations else None,
        }

    @staticmethod
    def _stats(values: list[float]) -> dict[str, Any] | None:
        if not values:
            return None
        vals = [float(v) for v in values]
        ordered = sorted(vals)
        return {
            "count": len(vals),
            "min": round(min(vals), 3),
            "max": round(max(vals), 3),
            "avg": round(statistics.fmean(vals), 3),
            "p50": round(_percentile(ordered, 50), 3),
            "p95": round(_percentile(ordered, 95), 3),
        }

    @staticmethod
    def _new_run_id() -> str:
        now = _now_utc()
        return f"bench_{now.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"


def _percentile(sorted_values: list[float], pct: float) -> float:
    if not sorted_values:
        raise ValueError("no values")
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    rank = max(0.0, min(100.0, float(pct))) / 100.0 * (len(sorted_values) - 1)
    lo = int(rank)
    hi = min(lo + 1, len(sorted_values) - 1)
    frac = rank - lo
    return float(sorted_values[lo] * (1.0 - frac) + sorted_values[hi] * frac)

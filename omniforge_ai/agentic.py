from __future__ import annotations

import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .service import OmniForgeService


class AgenticWorkflowEngine:
    def __init__(self, service: "OmniForgeService"):
        self.service = service

    def plan(
        self,
        task: str,
        *,
        image_path: str | None = None,
        session_id: str = "agentic",
    ) -> dict[str, Any]:
        task = (task or "").strip()
        image_path = (image_path or "").strip() or None
        if not task and not image_path:
            raise ValueError("task or image_path is required")

        task_l = task.lower()
        now = datetime.now(timezone.utc)
        plan_id = f"plan_{now.strftime('%Y%m%d_%H%M%S')}_{hashlib.sha1((task + '|' + str(image_path)).encode('utf-8')).hexdigest()[:8]}"
        steps: list[dict[str, Any]] = []
        rationale: list[str] = []

        if image_path:
            steps.append(
                {
                    "id": "vision_classify_1",
                    "kind": "vision.classify_path",
                    "title": "Classify local image with npu_easy + ONNX",
                    "image_path": image_path,
                    "top_k": 3,
                }
            )
            rationale.append("Image path provided, so include local vision inference.")
        elif any(k in task_l for k in ("image", "photo", "picture", "classify")):
            rationale.append("Task mentions image classification, but no image path was provided.")

        if any(k in task_l for k in ("neurodsl", "dsl", "architecture", "model design", "agent api")):
            steps.append(
                {
                    "id": "neurodsl_devices_1",
                    "kind": "neurodsl.devices",
                    "title": "Inspect NeuroDSL compute backends",
                }
            )
            rationale.append("Task suggests model/DSL design, so include NeuroDSL environment inspection.")
            if "agent api" in task_l or "serve-agent-api" in task_l:
                steps.append(
                    {
                        "id": "neurodsl_manifest_1",
                        "kind": "neurodsl.agent_manifest",
                        "title": "Inspect NeuroDSL local agent API manifest",
                    }
                )

        if any(k in task_l for k in ("nexusflow", "workflow", "automation", "npu", "benchmark", "windows automation", "windhawk")):
            example = "npu_workflow_demo.nxf"
            pipeline = "build"
            if any(k in task_l for k in ("window", "explorer", "windhawk", "desktop", "mouse", "keyboard")):
                example = "windows_automation_dryrun.nxf"
                pipeline = "check"
            elif any(k in task_l for k in ("web", "dashboard", "ui")):
                example = "eco_ai_web.nxf"
                pipeline = None
            steps.append(
                {
                    "id": "nexusflow_example_1",
                    "kind": "nexusflow.run_example",
                    "title": f"Run NexusFlow example {example}",
                    "example": example,
                    "pipeline": pipeline,
                    "out_dir": "out/agentic_nexusflow",
                }
            )
            rationale.append("Task suggests workflow/automation/NPU operations, so include a NexusFlow run.")

        # Always include a chat step to synthesize or answer.
        steps.append(
            {
                "id": "chat_answer_1",
                "kind": "chat.answer",
                "title": "Answer/synthesize with local Supermix chat model",
                "session_id": session_id,
                "task": task or "Summarize the provided results.",
                "use_previous_results": True if len(steps) > 0 else False,
            }
        )
        rationale.append("Always include a local chat synthesis/answer step.")

        return {
            "ok": True,
            "plan_id": plan_id,
            "created_at": now.isoformat(),
            "task": task,
            "image_path": image_path,
            "session_id": session_id,
            "rationale": rationale,
            "steps": steps,
        }

    def execute(self, plan_or_task: dict[str, Any] | str, *, image_path: str | None = None) -> dict[str, Any]:
        if isinstance(plan_or_task, str):
            plan = self.plan(plan_or_task, image_path=image_path)
        else:
            plan = dict(plan_or_task)
            if "steps" not in plan:
                raise ValueError("plan is missing 'steps'")

        outputs: list[dict[str, Any]] = []
        condensed: list[dict[str, Any]] = []

        for step in plan.get("steps", []):
            kind = str(step.get("kind") or "")
            sid = str(step.get("id") or f"step_{len(outputs)+1}")
            try:
                result = self._execute_step(step, previous=outputs)
                outputs.append({"id": sid, "kind": kind, "ok": True, "result": result})
                condensed.append({"id": sid, "kind": kind, "summary": self._condense_result(kind, result)})
            except Exception as exc:
                outputs.append({"id": sid, "kind": kind, "ok": False, "error": str(exc)})
                condensed.append({"id": sid, "kind": kind, "error": str(exc)})

        return {
            "ok": True,
            "plan": plan,
            "results": outputs,
            "condensed_results": condensed,
        }

    def export_nexusflow_pipeline(self, plan_or_task: dict[str, Any] | str, *, image_path: str | None = None) -> dict[str, Any]:
        if isinstance(plan_or_task, str):
            plan = self.plan(plan_or_task, image_path=image_path)
        else:
            plan = dict(plan_or_task)
        generated_dir = self.service.paths.workflow_file.parent / "generated"
        generated_dir.mkdir(parents=True, exist_ok=True)
        slug = self._slug(str(plan.get("plan_id") or "agentic"))
        file_path = generated_dir / f"{slug}.nxf"
        text = self._build_nxf_from_plan(plan)
        file_path.write_text(text, encoding="utf-8")
        return {
            "ok": True,
            "plan_id": plan.get("plan_id"),
            "file_path": str(file_path),
            "pipeline": "main",
            "text": text,
        }

    def run_nexusflow_pipeline(
        self, plan_or_task: dict[str, Any] | str, *, image_path: str | None = None
    ) -> dict[str, Any]:
        export = self.export_nexusflow_pipeline(plan_or_task, image_path=image_path)
        out_root = self.service.paths.workspace_root / "omniforge_ai" / "out" / "agentic_nxf_runs"
        out_root.mkdir(parents=True, exist_ok=True)
        run_res = self.service.nexusflow.run_file(export["file_path"], pipeline="main", out_dir=str(out_root))
        return {
            "ok": True,
            "export": {k: v for k, v in export.items() if k != "text"},
            "nexusflow_run": run_res,
        }

    def _execute_step(self, step: dict[str, Any], *, previous: list[dict[str, Any]]) -> Any:
        kind = str(step.get("kind") or "")
        if kind == "vision.classify_path":
            return self.service.vision.classify_path(step["image_path"], top_k=int(step.get("top_k", 3)))
        if kind == "neurodsl.devices":
            return self.service.neurodsl.devices()
        if kind == "neurodsl.agent_manifest":
            return self.service.neurodsl.agent_manifest()
        if kind == "nexusflow.run_example":
            return self.service.nexusflow.run_example(
                step["example"],
                pipeline=step.get("pipeline"),
                out_dir=step.get("out_dir"),
            )
        if kind == "chat.answer":
            msg = str(step.get("task") or "")
            if step.get("use_previous_results") and previous:
                msg = self._build_synthesis_prompt(str(step.get("task") or "Summarize results"), previous)
            return self.service.chat.chat(msg, session_id=str(step.get("session_id") or "agentic"))
        raise ValueError(f"Unsupported step kind: {kind}")

    def _build_synthesis_prompt(self, task: str, previous: list[dict[str, Any]]) -> str:
        summary = []
        for item in previous:
            if not item.get("ok"):
                summary.append({"id": item.get("id"), "kind": item.get("kind"), "error": item.get("error")})
                continue
            summary.append(
                {
                    "id": item.get("id"),
                    "kind": item.get("kind"),
                    "summary": self._condense_result(str(item.get("kind")), item.get("result")),
                }
            )
        return (
            "User task:\n"
            + task
            + "\n\nTool results (summarized JSON):\n"
            + json.dumps(summary, ensure_ascii=False)
            + "\n\nRespond with a concise, actionable answer using the tool outputs."
        )

    def _condense_result(self, kind: str, result: Any) -> Any:
        if kind == "chat.answer" and isinstance(result, dict):
            return {
                "response": result.get("response"),
                "timing_ms": result.get("timing_ms"),
                "style_mode": result.get("style_mode"),
            }
        if kind == "vision.classify_path" and isinstance(result, dict):
            return {
                "provider": result.get("provider"),
                "infer_ms": result.get("infer_ms"),
                "predictions": result.get("predictions"),
            }
        if kind.startswith("nexusflow.") and isinstance(result, dict):
            return result.get("summary") or {
                "ok": result.get("ok"),
                "returncode": result.get("returncode"),
            }
        if kind.startswith("neurodsl.") and isinstance(result, dict):
            return result.get("json") or result.get("parsed") or {
                "ok": result.get("ok"),
                "returncode": result.get("returncode"),
            }
        return result

    def _build_nxf_from_plan(self, plan: dict[str, Any]) -> str:
        omniforge_root = (self.service.paths.workspace_root / "omniforge_ai").resolve()
        omniforge_cwd = str(omniforge_root).replace("\\", "/")
        py = str(Path(sys.executable).resolve()).replace("\\", "/")
        out_dir = "out/agentic_proc"

        lines: list[str] = []
        lines.append(f'project "OmniForgeAgenticPlan" {{')
        lines.append("  pipeline main {")
        lines.append(f'    step write_text("{out_dir}/plan.json", {self._nxf_str(json.dumps(plan, ensure_ascii=False))});')
        for step in plan.get("steps", []):
            cmd, args, timeout_sec = self._nxf_command_for_step(step, py)
            if cmd is None:
                continue
            cfg = {"cwd": omniforge_cwd, "timeout_sec": timeout_sec}
            lines.append(
                "    step proc_exec("
                + self._nxf_str(cmd)
                + ", "
                + self._nxf_lit(args)
                + ", "
                + self._nxf_lit(cfg)
                + ");"
            )
        lines.append(f'    step proc_history_json("{out_dir}/proc_history.json");')
        lines.append(f'    step export_json("{out_dir}/snapshot.json");')
        lines.append("    step summary();")
        lines.append("  }")
        lines.append("}")
        lines.append("")
        return "\n".join(lines)

    def _nxf_command_for_step(self, step: dict[str, Any], py: str) -> tuple[str | None, list[str], float]:
        kind = str(step.get("kind") or "")
        if kind == "chat.answer":
            msg = str(step.get("task") or "hello")
            session_id = str(step.get("session_id") or "agentic_nxf")
            return py, ["-m", "omniforge_ai", "chat", msg, "--session-id", session_id], 240.0
        if kind == "vision.classify_path":
            return py, ["-m", "omniforge_ai", "vision", "--image", str(step["image_path"]), "--top-k", str(step.get("top_k", 3))], 180.0
        if kind == "neurodsl.devices":
            return py, ["-m", "omniforge_ai", "neurodsl-devices"], 180.0
        if kind == "neurodsl.agent_manifest":
            return py, ["-m", "omniforge_ai", "neurodsl-manifest"], 180.0
        if kind == "nexusflow.run_example":
            args = ["-m", "omniforge_ai", "nexusflow-example", str(step.get("example"))]
            if step.get("pipeline"):
                args += ["--pipeline", str(step.get("pipeline"))]
            if step.get("out_dir"):
                args += ["--out-dir", str(step.get("out_dir"))]
            return py, args, 600.0
        return None, [], 60.0

    @staticmethod
    def _slug(value: str) -> str:
        s = re.sub(r"[^a-zA-Z0-9._-]+", "_", value.strip())
        return s[:120] or "agentic"

    @staticmethod
    def _nxf_str(value: str) -> str:
        return json.dumps(str(value), ensure_ascii=False)

    @classmethod
    def _nxf_lit(cls, value: Any) -> str:
        if value is None:
            return "null"
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return repr(value)
        if isinstance(value, str):
            return cls._nxf_str(value)
        if isinstance(value, list):
            return "[" + ", ".join(cls._nxf_lit(v) for v in value) + "]"
        if isinstance(value, dict):
            items = []
            for k, v in value.items():
                items.append(cls._nxf_str(str(k)) + ": " + cls._nxf_lit(v))
            return "{" + ", ".join(items) + "}"
        return cls._nxf_str(str(value))


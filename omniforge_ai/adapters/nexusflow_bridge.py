from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any


class NexusFlowBridge:
    def __init__(self, repo_dir: Path):
        self.repo_dir = Path(repo_dir).resolve()
        self.examples_dir = self.repo_dir / "examples"

    def _run_cli(self, *args: str, timeout_sec: float = 120.0) -> dict[str, Any]:
        cmd = [sys.executable, "-m", "nexusflow", *args]
        proc = subprocess.run(
            cmd,
            cwd=self.repo_dir,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            check=False,
        )
        stdout = (proc.stdout or "").strip()
        stderr = (proc.stderr or "").strip()
        parsed: Any = None
        if stdout:
            try:
                parsed = json.loads(stdout)
            except Exception:
                parsed = stdout
        result = {
            "ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "command": cmd,
            "stdout": stdout,
            "stderr": stderr,
            "json": parsed if isinstance(parsed, (dict, list)) else None,
            "parsed": parsed,
        }
        result["summary"] = self._summarize(parsed)
        return result

    @staticmethod
    def _summarize(parsed: Any) -> dict[str, Any] | None:
        if not isinstance(parsed, dict):
            return None
        summary: dict[str, Any] = {}
        if "project" in parsed:
            summary["project"] = parsed.get("project")
        pipeline = parsed.get("pipeline")
        if isinstance(pipeline, dict):
            summary["pipeline"] = pipeline.get("pipeline")
            executed = pipeline.get("executed")
            if isinstance(executed, list):
                summary["steps_executed"] = len(executed)
                names = []
                for item in executed[:8]:
                    if isinstance(item, dict) and "step" in item:
                        names.append(item["step"])
                if names:
                    summary["step_names_preview"] = names
        snapshot = parsed.get("snapshot")
        if isinstance(snapshot, dict):
            metrics = snapshot.get("metrics")
            if isinstance(metrics, dict):
                summary["metric_keys"] = sorted(metrics.keys())
            events = snapshot.get("events")
            if isinstance(events, list):
                summary["event_count"] = len(events)
            npu = snapshot.get("npu")
            if isinstance(npu, dict):
                last_run = npu.get("last_run")
                if isinstance(last_run, dict):
                    summary["npu_last_run_ok"] = last_run.get("ok")
                    summary["npu_last_run_type"] = last_run.get("type")
        return summary or None

    def status(self) -> dict[str, Any]:
        examples = []
        if self.examples_dir.exists():
            examples = sorted(p.name for p in self.examples_dir.glob("*.nxf"))
        return {
            "repo_exists": self.repo_dir.exists(),
            "examples_dir": str(self.examples_dir),
            "examples": examples,
        }

    def run_file(self, file_path: str | Path, pipeline: str | None = None, out_dir: str | Path | None = None) -> dict[str, Any]:
        args = ["run", str(file_path)]
        if pipeline:
            args.extend(["--pipeline", str(pipeline)])
        if out_dir:
            args.extend(["--out-dir", str(out_dir)])
        return self._run_cli(*args)

    def run_example(self, example_name: str, pipeline: str | None = None, out_dir: str | Path | None = None) -> dict[str, Any]:
        example_path = self.examples_dir / example_name
        return self.run_file(example_path, pipeline=pipeline, out_dir=out_dir)

from __future__ import annotations

import concurrent.futures as futures
import json
import threading
import time
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .history_store import HistoryStore
    from .service import OmniForgeService


class AsyncJobManager:
    def __init__(
        self,
        service: "OmniForgeService",
        jobs_dir: Path,
        history: "HistoryStore",
        *,
        max_workers: int = 1,
    ):
        self.service = service
        self.jobs_dir = Path(jobs_dir).resolve()
        self.history = history
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._executor = futures.ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="omniforge-job")
        self._jobs: dict[str, dict[str, Any]] = {}
        self._futures: dict[str, futures.Future[Any]] = {}

    def submit(self, kind: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = dict(params or {})
        job_id = self._new_job_id()
        job = {
            "id": job_id,
            "kind": str(kind),
            "params": self._jsonable(params),
            "status": "queued",
            "created_at": self._now_iso(),
            "started_at": None,
            "finished_at": None,
            "duration_ms": None,
            "result": None,
            "error": None,
            "traceback": None,
        }
        with self._lock:
            self._jobs[job_id] = job
            self._persist_locked(job_id)
            fut = self._executor.submit(self._run_job, job_id, str(kind), params)
            self._futures[job_id] = fut
        self.history.record(
            "job.submitted",
            {"job_id": job_id, "kind": kind, "params": params},
            source="jobs",
            tags=["job", "submit"],
            summary={"job_id": job_id, "kind": kind},
        )
        return self._public_job(job)

    def list(self, *, limit: int = 50, status: str | None = None) -> dict[str, Any]:
        combined: dict[str, dict[str, Any]] = {}
        for file in self.jobs_dir.glob("*.json"):
            try:
                data = json.loads(file.read_text(encoding="utf-8", errors="replace"))
            except Exception:
                continue
            if isinstance(data, dict) and data.get("id"):
                combined[str(data["id"])] = data
        with self._lock:
            for jid, job in self._jobs.items():
                combined[jid] = dict(job)
        items = list(combined.values())
        if status:
            items = [j for j in items if str(j.get("status")) == str(status)]
        items.sort(key=lambda x: str(x.get("created_at") or ""), reverse=True)
        items = items[: max(1, int(limit))]
        return {"ok": True, "count": len(items), "items": [self._public_job(i) for i in items]}

    def get(self, job_id: str) -> dict[str, Any]:
        jid = str(job_id).strip()
        if not jid:
            raise ValueError("job_id is required")
        with self._lock:
            if jid in self._jobs:
                return {"ok": True, "job": self._public_job(self._jobs[jid])}
        path = self.jobs_dir / f"{jid}.json"
        if not path.exists():
            raise FileNotFoundError(f"job not found: {jid}")
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        return {"ok": True, "job": self._public_job(data)}

    def wait(self, job_id: str, *, timeout_sec: float | None = None) -> dict[str, Any]:
        jid = str(job_id).strip()
        with self._lock:
            fut = self._futures.get(jid)
        if fut is not None:
            try:
                fut.result(timeout=None if timeout_sec is None else float(timeout_sec))
            except futures.TimeoutError:
                return {"ok": False, "timeout": True, "job": self.get(jid)["job"]}
        return {"ok": True, "job": self.get(jid)["job"]}

    def cancel(self, job_id: str) -> dict[str, Any]:
        jid = str(job_id).strip()
        with self._lock:
            fut = self._futures.get(jid)
            job = self._jobs.get(jid)
            if job is None:
                raise FileNotFoundError(f"job not found: {jid}")
            cancelled = bool(fut.cancel()) if fut is not None else False
            if cancelled:
                job["status"] = "cancelled"
                job["finished_at"] = self._now_iso()
                job["error"] = "cancelled before start"
                self._persist_locked(jid)
        if cancelled:
            self.history.record(
                "job.cancelled",
                {"job_id": jid},
                source="jobs",
                tags=["job", "cancel"],
                summary={"job_id": jid},
            )
        return {"ok": True, "cancelled": cancelled, "job": self.get(jid)["job"]}

    def _run_job(self, job_id: str, kind: str, params: dict[str, Any]) -> None:
        started_perf = time.perf_counter()
        with self._lock:
            job = self._jobs[job_id]
            job["status"] = "running"
            job["started_at"] = self._now_iso()
            self._persist_locked(job_id)
        try:
            result = self.service.run_operation(kind, params)
            with self._lock:
                job = self._jobs[job_id]
                job["status"] = "completed"
                job["result"] = self._jsonable(result)
                job["error"] = None
                job["traceback"] = None
                job["finished_at"] = self._now_iso()
                job["duration_ms"] = round((time.perf_counter() - started_perf) * 1000, 1)
                self._persist_locked(job_id)
            self.history.record(
                "job.completed",
                {"job_id": job_id, "kind": kind, "result_summary": self.service.summarize_operation_result(kind, result)},
                source="jobs",
                tags=["job", "complete"],
                summary={
                    "job_id": job_id,
                    "kind": kind,
                    "duration_ms": self._jobs[job_id].get("duration_ms"),
                    "status": "completed",
                },
            )
        except Exception as exc:
            tb = traceback.format_exc(limit=20)
            with self._lock:
                job = self._jobs[job_id]
                job["status"] = "failed"
                job["error"] = str(exc)
                job["traceback"] = tb
                job["finished_at"] = self._now_iso()
                job["duration_ms"] = round((time.perf_counter() - started_perf) * 1000, 1)
                self._persist_locked(job_id)
            self.history.record(
                "job.failed",
                {"job_id": job_id, "kind": kind, "error": str(exc)},
                source="jobs",
                tags=["job", "error"],
                summary={"job_id": job_id, "kind": kind, "error": str(exc)},
            )

    def _persist_locked(self, job_id: str) -> None:
        job = self._jobs[job_id]
        path = self.jobs_dir / f"{job_id}.json"
        path.write_text(json.dumps(self._jsonable(job), indent=2, ensure_ascii=False), encoding="utf-8")

    @staticmethod
    def _public_job(job: dict[str, Any]) -> dict[str, Any]:
        out = dict(job)
        result = out.get("result")
        if isinstance(result, dict):
            out["result_preview"] = _preview_result(result)
        elif result is not None:
            out["result_preview"] = str(result)[:400]
        return out

    @classmethod
    def _jsonable(cls, value: Any) -> Any:
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, Path):
            return str(value)
        if isinstance(value, bytes):
            return {"type": "bytes", "len": len(value)}
        if isinstance(value, list):
            return [cls._jsonable(v) for v in value]
        if isinstance(value, tuple):
            return [cls._jsonable(v) for v in value]
        if isinstance(value, dict):
            return {str(k): cls._jsonable(v) for k, v in value.items()}
        try:
            json.dumps(value)
            return value
        except Exception:
            return str(value)

    @staticmethod
    def _new_job_id() -> str:
        now = datetime.now(timezone.utc)
        return f"job_{now.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()


def _preview_result(data: dict[str, Any]) -> dict[str, Any]:
    keys = list(data.keys())
    out: dict[str, Any] = {"keys": keys[:30]}
    for key in ("ok", "run_id", "plan_id", "provider", "infer_ms", "returncode", "response"):
        if key in data:
            val = data[key]
            out[key] = (str(val)[:240] if key == "response" else val)
    if "predictions" in data and isinstance(data["predictions"], list):
        out["predictions"] = data["predictions"][:3]
    return out

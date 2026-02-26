from __future__ import annotations

import json
import threading
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


class AutonomousScheduler:
    def __init__(self, service: "OmniForgeService", schedules_dir: Path, history: "HistoryStore"):
        self.service = service
        self.schedules_dir = Path(schedules_dir).resolve()
        self.history = history
        self.schedules_dir.mkdir(parents=True, exist_ok=True)
        self._db_file = self.schedules_dir / "schedules.json"
        self._lock = threading.RLock()
        self._schedules: dict[str, dict[str, Any]] = {}
        self._running = False
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_tick_at: str | None = None
        self._last_tick_result: dict[str, Any] | None = None
        self._load_locked()

    def status(self) -> dict[str, Any]:
        with self._lock:
            thread_alive = self._thread is not None and self._thread.is_alive()
            enabled = sum(1 for s in self._schedules.values() if s.get("enabled"))
            count = len(self._schedules)
            return {
                "ok": True,
                "running": bool(self._running and thread_alive),
                "thread_alive": bool(thread_alive),
                "schedule_count": count,
                "enabled_count": enabled,
                "db_file": str(self._db_file),
                "last_tick_at": self._last_tick_at,
                "last_tick_result": self._last_tick_result,
            }

    def list(self) -> dict[str, Any]:
        with self._lock:
            items = sorted((dict(v) for v in self._schedules.values()), key=lambda x: str(x.get("name") or x.get("id")))
        return {"ok": True, "count": len(items), "items": items}

    def get(self, schedule_id: str) -> dict[str, Any]:
        sid = str(schedule_id or "").strip()
        if not sid:
            raise ValueError("schedule_id is required")
        with self._lock:
            row = self._schedules.get(sid)
            if row is None:
                raise FileNotFoundError(f"schedule not found: {sid}")
            return {"ok": True, "schedule": dict(row)}

    def create(
        self,
        *,
        name: str | None = None,
        target_kind: str,
        params: dict[str, Any] | None = None,
        interval_sec: float,
        enabled: bool = True,
        start_immediately: bool = False,
    ) -> dict[str, Any]:
        target_kind = str(target_kind or "").strip()
        if not target_kind:
            raise ValueError("target_kind is required")
        interval = float(interval_sec)
        if interval <= 0:
            raise ValueError("interval_sec must be > 0")
        now = _now_utc()
        sid = f"sched_{now.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
        next_run = now.timestamp() if start_immediately else now.timestamp() + interval
        row = {
            "id": sid,
            "name": (str(name).strip() if name else target_kind),
            "target_kind": target_kind,
            "params": self._jsonable(params or {}),
            "interval_sec": interval,
            "enabled": bool(enabled),
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
            "last_run_at": None,
            "last_submit_job_id": None,
            "last_error": None,
            "run_count": 0,
            "next_run_at": _ts_to_iso(next_run) if enabled else None,
        }
        with self._lock:
            self._schedules[sid] = row
            self._persist_locked()
        self.history.record(
            "scheduler.created",
            {"schedule": row},
            source="scheduler",
            tags=["scheduler"],
            summary={"id": sid, "name": row["name"], "target_kind": target_kind, "interval_sec": interval},
        )
        return {"ok": True, "schedule": dict(row)}

    def update(self, schedule_id: str, **changes: Any) -> dict[str, Any]:
        sid = str(schedule_id or "").strip()
        if not sid:
            raise ValueError("schedule_id is required")
        with self._lock:
            row = self._schedules.get(sid)
            if row is None:
                raise FileNotFoundError(f"schedule not found: {sid}")
            if "name" in changes and changes["name"] is not None:
                row["name"] = str(changes["name"]).strip() or row["name"]
            if "target_kind" in changes and changes["target_kind"] is not None:
                t = str(changes["target_kind"]).strip()
                if not t:
                    raise ValueError("target_kind cannot be empty")
                row["target_kind"] = t
            if "params" in changes and isinstance(changes["params"], dict):
                row["params"] = self._jsonable(changes["params"])
            if "interval_sec" in changes and changes["interval_sec"] is not None:
                iv = float(changes["interval_sec"])
                if iv <= 0:
                    raise ValueError("interval_sec must be > 0")
                row["interval_sec"] = iv
            if "enabled" in changes and changes["enabled"] is not None:
                row["enabled"] = bool(changes["enabled"])
                if row["enabled"] and not row.get("next_run_at"):
                    row["next_run_at"] = _ts_to_iso(_now_utc().timestamp() + float(row["interval_sec"]))
                if not row["enabled"]:
                    row["next_run_at"] = None
            row["updated_at"] = _now_utc().isoformat()
            self._persist_locked()
            out = dict(row)
        self.history.record(
            "scheduler.updated",
            {"schedule_id": sid, "changes": self._jsonable(changes)},
            source="scheduler",
            tags=["scheduler"],
            summary={"id": sid, "enabled": out.get("enabled"), "interval_sec": out.get("interval_sec")},
        )
        return {"ok": True, "schedule": out}

    def delete(self, schedule_id: str) -> dict[str, Any]:
        sid = str(schedule_id or "").strip()
        if not sid:
            raise ValueError("schedule_id is required")
        with self._lock:
            row = self._schedules.pop(sid, None)
            if row is None:
                raise FileNotFoundError(f"schedule not found: {sid}")
            self._persist_locked()
        self.history.record(
            "scheduler.deleted",
            {"schedule_id": sid, "target_kind": row.get("target_kind")},
            source="scheduler",
            tags=["scheduler"],
            summary={"id": sid, "name": row.get("name")},
        )
        return {"ok": True, "deleted": sid}

    def run_now(self, schedule_id: str) -> dict[str, Any]:
        sid = str(schedule_id or "").strip()
        with self._lock:
            row = self._schedules.get(sid)
            if row is None:
                raise FileNotFoundError(f"schedule not found: {sid}")
            row = dict(row)
        return self._submit_schedule(row, manual=True)

    def tick_once(self) -> dict[str, Any]:
        now = _now_utc()
        due: list[dict[str, Any]] = []
        with self._lock:
            for row in self._schedules.values():
                if not row.get("enabled"):
                    continue
                next_run_at = _iso_to_ts(row.get("next_run_at"))
                if next_run_at is None:
                    next_run_at = now.timestamp() + float(row.get("interval_sec") or 1.0)
                    row["next_run_at"] = _ts_to_iso(next_run_at)
                if next_run_at <= now.timestamp():
                    due.append(dict(row))
            self._last_tick_at = now.isoformat()
        triggered = []
        errors = []
        for row in due:
            try:
                triggered.append(self._submit_schedule(row, manual=False))
            except Exception as exc:
                errors.append({"schedule_id": row.get("id"), "error": str(exc)})
                self._mark_error(str(row.get("id")), str(exc))
        result = {
            "ok": len(errors) == 0,
            "tick_at": now.isoformat(),
            "due_count": len(due),
            "triggered": triggered,
            "errors": errors,
        }
        with self._lock:
            self._last_tick_result = result
        return result

    def start(self) -> dict[str, Any]:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                self._running = True
                self._stop_event.clear()
                return {"ok": True, "already_running": True, **self.status()}
            self._stop_event.clear()
            self._running = True
            self._thread = threading.Thread(target=self._loop, name="omniforge-scheduler", daemon=True)
            self._thread.start()
        self.history.record(
            "scheduler.started",
            {"status": self.status()},
            source="scheduler",
            tags=["scheduler"],
            summary={"running": True},
        )
        return self.status()

    def stop(self, *, timeout_sec: float = 5.0) -> dict[str, Any]:
        with self._lock:
            self._running = False
            self._stop_event.set()
            thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=max(0.1, float(timeout_sec)))
        out = self.status()
        self.history.record(
            "scheduler.stopped",
            {"status": out},
            source="scheduler",
            tags=["scheduler"],
            summary={"running": False},
        )
        return out

    def _loop(self) -> None:
        while not self._stop_event.wait(1.0):
            try:
                self.tick_once()
            except Exception as exc:
                self.history.record(
                    "scheduler.loop_error",
                    {"error": str(exc)},
                    source="scheduler",
                    tags=["scheduler", "error"],
                    summary={"error": str(exc)},
                )

    def _submit_schedule(self, row: dict[str, Any], *, manual: bool) -> dict[str, Any]:
        sid = str(row["id"])
        job = self.service.jobs.submit(str(row["target_kind"]), dict(row.get("params") or {}))
        now = _now_utc()
        with self._lock:
            cur = self._schedules.get(sid)
            if cur is not None:
                cur["last_run_at"] = now.isoformat()
                cur["last_submit_job_id"] = job["id"]
                cur["last_error"] = None
                cur["run_count"] = int(cur.get("run_count") or 0) + 1
                if cur.get("enabled"):
                    interval = float(cur.get("interval_sec") or row.get("interval_sec") or 1.0)
                    cur["next_run_at"] = _ts_to_iso(now.timestamp() + interval)
                cur["updated_at"] = now.isoformat()
                self._persist_locked()
        payload = {
            "schedule_id": sid,
            "job_id": job["id"],
            "target_kind": row.get("target_kind"),
            "manual": bool(manual),
            "params": self._jsonable(row.get("params") or {}),
        }
        self.history.record(
            "scheduler.triggered",
            payload,
            source="scheduler",
            tags=["scheduler", "job"],
            summary={"schedule_id": sid, "job_id": job["id"], "manual": bool(manual)},
        )
        return {"ok": True, **payload}

    def _mark_error(self, schedule_id: str, message: str) -> None:
        with self._lock:
            cur = self._schedules.get(schedule_id)
            if cur is None:
                return
            cur["last_error"] = str(message)
            cur["updated_at"] = _now_utc().isoformat()
            if cur.get("enabled"):
                interval = float(cur.get("interval_sec") or 1.0)
                cur["next_run_at"] = _ts_to_iso(_now_utc().timestamp() + interval)
            self._persist_locked()
        self.history.record(
            "scheduler.trigger_error",
            {"schedule_id": schedule_id, "error": str(message)},
            source="scheduler",
            tags=["scheduler", "error"],
            summary={"schedule_id": schedule_id, "error": str(message)},
        )

    def _load_locked(self) -> None:
        with self._lock:
            self._schedules = {}
            if not self._db_file.exists():
                return
            try:
                data = json.loads(self._db_file.read_text(encoding="utf-8", errors="replace"))
            except Exception:
                return
            items = data.get("schedules") if isinstance(data, dict) else None
            if not isinstance(items, list):
                return
            for item in items:
                if not isinstance(item, dict):
                    continue
                sid = str(item.get("id") or "").strip()
                if not sid:
                    continue
                self._schedules[sid] = item

    def _persist_locked(self) -> None:
        items = sorted(self._schedules.values(), key=lambda x: str(x.get("created_at") or ""))
        payload = {"schedules": items, "updated_at": _now_utc().isoformat()}
        self._db_file.write_text(json.dumps(self._jsonable(payload), indent=2, ensure_ascii=False), encoding="utf-8")

    @classmethod
    def _jsonable(cls, value: Any) -> Any:
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, Path):
            return str(value)
        if isinstance(value, list):
            return [cls._jsonable(v) for v in value]
        if isinstance(value, tuple):
            return [cls._jsonable(v) for v in value]
        if isinstance(value, dict):
            return {str(k): cls._jsonable(v) for k, v in value.items()}
        return str(value)


def _ts_to_iso(ts: float | None) -> str | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()


def _iso_to_ts(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return datetime.fromisoformat(str(value)).timestamp()
    except Exception:
        return None

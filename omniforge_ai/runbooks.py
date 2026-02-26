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


class RunbookCenter:
    def __init__(self, service: "OmniForgeService", root_dir: Path, history: "HistoryStore"):
        self.service = service
        self.root_dir = Path(root_dir).resolve()
        self.history = history
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self.templates_file = self.root_dir / "templates.json"
        self.runs_dir = self.root_dir / "runs"
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self.runs_index_file = self.root_dir / "runs_index.jsonl"
        self._lock = threading.RLock()
        self._templates: dict[str, dict[str, Any]] = {}
        self._load_templates_locked()

    def status(self) -> dict[str, Any]:
        templates = self.list_templates()
        runs = self.list_runs(limit=200)
        rows = runs.get("items") or []
        return {
            "ok": True,
            "root_dir": str(self.root_dir),
            "templates_file": str(self.templates_file),
            "runs_dir": str(self.runs_dir),
            "runs_index_file": str(self.runs_index_file),
            "template_count": templates.get("count"),
            "builtin_template_count": sum(1 for t in (templates.get("items") or []) if t.get("builtin")),
            "custom_template_count": sum(1 for t in (templates.get("items") or []) if not t.get("builtin")),
            "run_count": runs.get("count"),
            "recent_success_count": sum(1 for r in rows if str(r.get("status")) == "completed"),
            "recent_failure_count": sum(1 for r in rows if str(r.get("status")) == "failed"),
        }

    def templates_catalog(self) -> dict[str, Any]:
        return self.list_templates(include_steps=True)

    def list_templates(self, *, include_steps: bool = False) -> dict[str, Any]:
        builtins = self._builtin_templates()
        with self._lock:
            custom_rows = {name: dict(row) for name, row in self._templates.items()}
        items: list[dict[str, Any]] = []
        for name, row in {**builtins, **custom_rows}.items():
            items.append(_template_summary(dict(row), include_steps=include_steps))
        items.sort(key=lambda x: (not bool(x.get("builtin")), str(x.get("name") or "")))
        return {"ok": True, "count": len(items), "items": items}

    def get_template(self, name: str) -> dict[str, Any]:
        key = str(name or "").strip()
        if not key:
            raise ValueError("template name is required")
        builtins = self._builtin_templates()
        if key in builtins:
            return {"ok": True, "template": dict(builtins[key])}
        with self._lock:
            row = self._templates.get(key)
            if row is None:
                raise FileNotFoundError(f"runbook template not found: {key}")
            return {"ok": True, "template": dict(row)}

    def create_template(
        self,
        *,
        name: str,
        title: str | None = None,
        description: str | None = None,
        steps: list[dict[str, Any]] | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        key = _normalize_name(name)
        if not key:
            raise ValueError("name is required")
        if key in self._builtin_templates():
            raise ValueError(f"cannot override builtin runbook template: {key}")
        if not isinstance(steps, list) or not steps:
            raise ValueError("steps (non-empty array) is required")
        validated_steps = [_validate_step(s, idx=i + 1) for i, s in enumerate(steps)]
        now = _now_utc().isoformat()
        row = {
            "name": key,
            "title": str(title or key),
            "description": str(description or ""),
            "steps": validated_steps,
            "tags": [str(t) for t in (tags or []) if str(t).strip()],
            "metadata": _jsonable(metadata or {}),
            "builtin": False,
            "created_at": now,
            "updated_at": now,
        }
        with self._lock:
            if key in self._templates:
                raise ValueError(f"runbook template already exists: {key}")
            self._templates[key] = row
            self._persist_templates_locked()
        self.history.record(
            "runbook.template_created",
            {"template": _template_summary(row, include_steps=False)},
            source="runbooks",
            tags=["runbook", "template"],
            summary={"template": key},
        )
        return {"ok": True, "template": dict(row)}

    def update_template(self, name: str, **changes: Any) -> dict[str, Any]:
        key = _normalize_name(name)
        if not key:
            raise ValueError("name is required")
        if key in self._builtin_templates():
            raise ValueError("builtin runbook templates cannot be modified")
        with self._lock:
            row = self._templates.get(key)
            if row is None:
                raise FileNotFoundError(f"runbook template not found: {key}")
            if "title" in changes and changes["title"] is not None:
                row["title"] = str(changes["title"]).strip() or row["title"]
            if "description" in changes and changes["description"] is not None:
                row["description"] = str(changes["description"])
            if "steps" in changes and changes["steps"] is not None:
                steps = changes["steps"]
                if not isinstance(steps, list) or not steps:
                    raise ValueError("steps must be a non-empty array")
                row["steps"] = [_validate_step(s, idx=i + 1) for i, s in enumerate(steps)]
            if "tags" in changes and isinstance(changes["tags"], list):
                row["tags"] = [str(t) for t in changes["tags"] if str(t).strip()]
            if "metadata" in changes and isinstance(changes["metadata"], dict):
                row["metadata"] = _jsonable(changes["metadata"])
            row["updated_at"] = _now_utc().isoformat()
            self._persist_templates_locked()
            out = dict(row)
        self.history.record(
            "runbook.template_updated",
            {"template": key, "changes": _jsonable(changes)},
            source="runbooks",
            tags=["runbook", "template"],
            summary={"template": key},
        )
        return {"ok": True, "template": out}

    def delete_template(self, name: str) -> dict[str, Any]:
        key = _normalize_name(name)
        if not key:
            raise ValueError("name is required")
        if key in self._builtin_templates():
            raise ValueError("builtin runbook templates cannot be deleted")
        with self._lock:
            row = self._templates.pop(key, None)
            if row is None:
                raise FileNotFoundError(f"runbook template not found: {key}")
            self._persist_templates_locked()
        self.history.record(
            "runbook.template_deleted",
            {"template": key},
            source="runbooks",
            tags=["runbook", "template"],
            summary={"template": key},
        )
        return {"ok": True, "deleted": key}

    def list_runs(
        self,
        *,
        limit: int = 50,
        status: str | None = None,
        template: str | None = None,
        incident_id: str | None = None,
    ) -> dict[str, Any]:
        rows = []
        if self.runs_index_file.exists():
            with self._lock:
                lines = self.runs_index_file.read_text(encoding="utf-8", errors="replace").splitlines()
            for line in reversed(lines):
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                if status and str(row.get("status")) != str(status):
                    continue
                if template and str(row.get("template")) != str(template):
                    continue
                if incident_id and str(row.get("incident_id")) != str(incident_id):
                    continue
                rows.append(row)
                if len(rows) >= max(1, int(limit)):
                    break
        return {"ok": True, "count": len(rows), "items": rows}

    def get_run(self, run_id: str) -> dict[str, Any]:
        rid = str(run_id or "").strip()
        if not rid:
            raise ValueError("run_id is required")
        path = self.runs_dir / f"{rid}.json"
        if not path.exists():
            raise FileNotFoundError(f"runbook run not found: {rid}")
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        return {"ok": True, "run": data, "file": str(path)}

    def run(
        self,
        template: str,
        *,
        context: dict[str, Any] | None = None,
        incident_id: str | None = None,
        alert_id: str | None = None,
        dry_run: bool = False,
        stop_on_error: bool | None = None,
    ) -> dict[str, Any]:
        tpl = (self.get_template(template) or {}).get("template") or {}
        name = str(tpl.get("name") or "")
        if not name:
            raise ValueError("invalid runbook template")
        rid = self._new_run_id()
        started_at = _now_utc().isoformat()
        incident_ctx = None
        alert_ctx = None
        incident_id = (str(incident_id).strip() if incident_id else None) or None
        alert_id = (str(alert_id).strip() if alert_id else None) or None
        if incident_id:
            incident_ctx = (self.service.incidents.get(incident_id) or {}).get("incident")
        if alert_id:
            alert_ctx = (self.service.alerts.get_alert(alert_id) or {}).get("alert")
        raw_ctx = {
            "input": _jsonable(context or {}),
            "incident": _jsonable(incident_ctx or {}),
            "alert": _jsonable(alert_ctx or {}),
            "template": {"name": name, "title": tpl.get("title")},
            "now": {"iso": started_at},
        }
        template_stop = ((tpl.get("metadata") or {}).get("stop_on_error") if isinstance(tpl.get("metadata"), dict) else None)
        stop_on_error_val = bool(template_stop) if stop_on_error is None and template_stop is not None else bool(stop_on_error if stop_on_error is not None else True)

        steps_out = []
        overall_ok = True
        failed_step_id = None
        if incident_id and not dry_run:
            try:
                self.service.incidents.add_note(incident_id, note=f"Runbook `{name}` started (`{rid}`)", author="runbook", kind="runbook")
            except Exception:
                pass
        self.history.record(
            "runbook.run_started",
            {"run_id": rid, "template": name, "incident_id": incident_id, "alert_id": alert_id, "dry_run": bool(dry_run)},
            source="runbooks",
            tags=["runbook", "run"],
            summary={"run_id": rid, "template": name, "dry_run": bool(dry_run)},
        )
        t0 = time.perf_counter()
        for idx, step in enumerate(list(tpl.get("steps") or []), start=1):
            rendered = _render_obj(step, raw_ctx)
            step_id = str(rendered.get("id") or f"step_{idx}")
            st_t0 = time.perf_counter()
            step_record = {
                "index": idx,
                "id": step_id,
                "type": str(rendered.get("type") or ""),
                "title": str(rendered.get("title") or rendered.get("type") or f"Step {idx}"),
                "rendered": _jsonable(rendered),
                "ok": True,
                "skipped": False,
                "duration_ms": None,
                "result": None,
                "error": None,
            }
            try:
                if dry_run:
                    step_record["result"] = {"dry_run": True}
                else:
                    res = self._execute_step(rendered, raw_ctx, incident_id=incident_id, alert_id=alert_id)
                    step_record["result"] = _jsonable(res)
                    # Feed step results back into context for downstream templating.
                    raw_ctx.setdefault("steps", {})
                    if isinstance(raw_ctx.get("steps"), dict):
                        raw_ctx["steps"][step_id] = {"result": _jsonable(res), "ok": True}
            except Exception as exc:
                overall_ok = False
                failed_step_id = step_id
                step_record["ok"] = False
                step_record["error"] = str(exc)
                raw_ctx.setdefault("steps", {})
                if isinstance(raw_ctx.get("steps"), dict):
                    raw_ctx["steps"][step_id] = {"error": str(exc), "ok": False}
                if stop_on_error_val:
                    step_record["stop_on_error"] = True
            finally:
                step_record["duration_ms"] = round((time.perf_counter() - st_t0) * 1000, 1)
                steps_out.append(step_record)
            if (not step_record["ok"]) and stop_on_error_val:
                break
        finished_at = _now_utc().isoformat()
        run_status = "completed" if overall_ok else "failed"
        run = {
            "ok": True,
            "run_id": rid,
            "template": name,
            "template_title": tpl.get("title"),
            "status": run_status,
            "dry_run": bool(dry_run),
            "started_at": started_at,
            "finished_at": finished_at,
            "duration_ms": round((time.perf_counter() - t0) * 1000, 1),
            "stop_on_error": stop_on_error_val,
            "failed_step_id": failed_step_id,
            "incident_id": incident_id,
            "alert_id": alert_id,
            "input_context": _jsonable(context or {}),
            "steps": steps_out,
            "summary": {
                "step_count": len(steps_out),
                "ok_steps": sum(1 for s in steps_out if s.get("ok")),
                "failed_steps": sum(1 for s in steps_out if not s.get("ok")),
            },
        }
        path = self._persist_run(run)
        run["file"] = str(path)
        if incident_id and not dry_run:
            try:
                note = f"Runbook `{name}` {run_status} (`{rid}`), steps={len(steps_out)}"
                self.service.incidents.add_note(incident_id, note=note, author="runbook", kind="runbook")
            except Exception:
                pass
        self.history.record(
            "runbook.run_completed",
            {
                "run_id": rid,
                "template": name,
                "status": run_status,
                "failed_step_id": failed_step_id,
                "incident_id": incident_id,
                "alert_id": alert_id,
            },
            source="runbooks",
            tags=["runbook", "run", run_status],
            summary={"run_id": rid, "template": name, "status": run_status, "step_count": len(steps_out)},
        )
        return {"ok": True, "run": run, "file": str(path)}

    def _execute_step(
        self,
        step: dict[str, Any],
        ctx: dict[str, Any],
        *,
        incident_id: str | None,
        alert_id: str | None,
    ) -> Any:
        stype = str(step.get("type") or "").strip().lower()
        if not stype:
            raise ValueError("runbook step type is required")
        if stype == "operation":
            kind = str(step.get("kind") or "").strip()
            params = step.get("params") if isinstance(step.get("params"), dict) else {}
            if not kind:
                raise ValueError("operation step requires kind")
            return self.service.run_operation(kind, params)
        if stype == "incident_note":
            iid = str(step.get("incident_id") or incident_id or "").strip()
            if not iid:
                raise ValueError("incident_note step requires incident_id or run incident_id")
            note = str(step.get("note") or "").strip()
            if not note:
                raise ValueError("incident_note step requires note")
            return self.service.incidents.add_note(
                iid,
                note=note,
                author=(str(step.get("author") or "runbook")),
                kind=str(step.get("kind") or "runbook"),
            )
        if stype == "incident_capture_bundle":
            iid = str(step.get("incident_id") or incident_id or "").strip()
            if not iid:
                raise ValueError("incident_capture_bundle step requires incident_id or run incident_id")
            return self.service.incidents.capture_bundle(
                iid,
                health_profile=str(step.get("health_profile") or "full"),
                report_profile=str(step.get("report_profile") or "incident_watch"),
                include_health=bool(step.get("include_health", True)),
                include_report=bool(step.get("include_report", True)),
            )
        if stype == "advisor_analyze":
            return self.service.advisor.analyze(
                profile=str(step.get("profile") or "quick"),
                incident_id=(str(step["incident_id"]) if step.get("incident_id") is not None else incident_id),
                options=(step.get("options") if isinstance(step.get("options"), dict) else {}),
                persist=bool(step.get("persist", True)),
            )
        if stype == "notification_send":
            return self.service.notifications.dispatch(
                topic=str(step.get("topic") or "runbook.message"),
                message=str(step.get("message") or ""),
                payload=(step.get("payload") if isinstance(step.get("payload"), dict) else {}),
                severity=str(step.get("severity") or "info"),
                tags=(step.get("tags") if isinstance(step.get("tags"), list) else ["runbook"]),
                target_channel_ids=(step.get("target_channel_ids") if isinstance(step.get("target_channel_ids"), list) else None),
            )
        if stype == "sleep":
            sec = max(0.0, float(step.get("seconds") or 0.0))
            time.sleep(sec)
            return {"slept_sec": sec}
        raise ValueError(f"unsupported runbook step type: {stype}")

    def _builtin_templates(self) -> dict[str, dict[str, Any]]:
        return {
            "critical_alert_triage": {
                "name": "critical_alert_triage",
                "title": "Critical Alert Triage",
                "description": "Capture incident bundle, run advisor triage, and append incident notes.",
                "builtin": True,
                "created_at": None,
                "updated_at": None,
                "tags": ["incident", "alert", "triage"],
                "metadata": {"requires_incident": True, "stop_on_error": True},
                "steps": [
                    {
                        "id": "incident_note_start",
                        "type": "incident_note",
                        "title": "Mark triage start",
                        "note": "Runbook triage started for incident ${incident.id} (alert ${alert.id})",
                        "author": "runbook",
                        "kind": "triage",
                    },
                    {
                        "id": "capture_bundle",
                        "type": "incident_capture_bundle",
                        "title": "Capture diagnostic bundle",
                        "health_profile": "full",
                        "report_profile": "incident_watch",
                    },
                    {
                        "id": "advisor_triage",
                        "type": "advisor_analyze",
                        "title": "Run incident triage advisor",
                        "profile": "incident_triage",
                        "incident_id": "${incident.id}",
                        "options": {"doctor": True},
                    },
                    {
                        "id": "incident_note_end",
                        "type": "incident_note",
                        "title": "Record triage completion",
                        "note": "Runbook triage completed. Review latest advisor and incident_watch report.",
                        "author": "runbook",
                        "kind": "triage",
                    },
                ],
            },
            "ops_baseline_refresh": {
                "name": "ops_baseline_refresh",
                "title": "Ops Baseline Refresh",
                "description": "Refresh operational baselines (health, reports, alerts) and run doctor.",
                "builtin": True,
                "created_at": None,
                "updated_at": None,
                "tags": ["ops", "baseline"],
                "metadata": {"stop_on_error": False},
                "steps": [
                    {
                        "id": "health_full",
                        "type": "operation",
                        "kind": "health.snapshot",
                        "params": {"profile": "full", "options": {"notify": False}},
                    },
                    {
                        "id": "report_ops",
                        "type": "operation",
                        "kind": "report.generate",
                        "params": {"profile": "ops_digest", "options": {}},
                    },
                    {
                        "id": "report_health",
                        "type": "operation",
                        "kind": "report.generate",
                        "params": {"profile": "health_watch", "options": {}},
                    },
                    {
                        "id": "alerts_eval",
                        "type": "operation",
                        "kind": "alerts.evaluate",
                        "params": {"only_enabled": True, "force": False},
                    },
                    {
                        "id": "doctor",
                        "type": "operation",
                        "kind": "advisor.doctor",
                        "params": {},
                    },
                ],
            },
            "incident_bundle_refresh": {
                "name": "incident_bundle_refresh",
                "title": "Incident Bundle Refresh",
                "description": "Refresh an incident's linked health snapshot/report and add a timeline note.",
                "builtin": True,
                "created_at": None,
                "updated_at": None,
                "tags": ["incident", "bundle"],
                "metadata": {"requires_incident": True, "stop_on_error": True},
                "steps": [
                    {
                        "id": "capture_bundle",
                        "type": "incident_capture_bundle",
                        "health_profile": "quick",
                        "report_profile": "incident_watch",
                    },
                    {
                        "id": "note_bundle",
                        "type": "incident_note",
                        "note": "Refreshed incident bundle (health snapshot + incident_watch report).",
                        "author": "runbook",
                        "kind": "bundle",
                    },
                ],
            },
        }

    def _load_templates_locked(self) -> None:
        with self._lock:
            self._templates = {}
            if not self.templates_file.exists():
                return
            try:
                data = json.loads(self.templates_file.read_text(encoding="utf-8", errors="replace"))
            except Exception:
                return
            rows = data.get("templates") if isinstance(data, dict) else None
            if not isinstance(rows, list):
                return
            for row in rows:
                if not isinstance(row, dict):
                    continue
                name = _normalize_name(row.get("name"))
                if not name:
                    continue
                if name in self._builtin_templates():
                    continue
                self._templates[name] = dict(row)

    def _persist_templates_locked(self) -> None:
        rows = sorted(self._templates.values(), key=lambda x: str(x.get("name") or ""))
        payload = {"templates": rows, "updated_at": _now_utc().isoformat()}
        self.templates_file.write_text(json.dumps(_jsonable(payload), indent=2, ensure_ascii=False), encoding="utf-8")

    def _persist_run(self, run: dict[str, Any]) -> Path:
        rid = str(run.get("run_id") or "").strip()
        if not rid:
            raise ValueError("run missing run_id")
        path = self.runs_dir / f"{rid}.json"
        with self._lock:
            path.write_text(json.dumps(_jsonable(run), indent=2, ensure_ascii=False), encoding="utf-8")
            idx = {
                "run_id": rid,
                "template": run.get("template"),
                "template_title": run.get("template_title"),
                "status": run.get("status"),
                "dry_run": run.get("dry_run"),
                "incident_id": run.get("incident_id"),
                "alert_id": run.get("alert_id"),
                "started_at": run.get("started_at"),
                "finished_at": run.get("finished_at"),
                "duration_ms": run.get("duration_ms"),
                "step_count": ((run.get("summary") or {}).get("step_count")),
                "failed_step_id": run.get("failed_step_id"),
                "file": str(path),
            }
            with self.runs_index_file.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(_jsonable(idx), ensure_ascii=False))
                fh.write("\n")
        return path

    @staticmethod
    def _new_run_id() -> str:
        now = _now_utc()
        return f"runbook_{now.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"


def _normalize_name(value: Any) -> str:
    s = str(value or "").strip().lower()
    if not s:
        return ""
    out = []
    for ch in s:
        if ch.isalnum() or ch in {"_", "-"}:
            out.append(ch)
        elif ch in {" ", ".", "/"}:
            out.append("_")
    return "".join(out).strip("_-")


def _template_summary(row: dict[str, Any], *, include_steps: bool) -> dict[str, Any]:
    out = {
        "name": row.get("name"),
        "title": row.get("title"),
        "description": row.get("description"),
        "builtin": bool(row.get("builtin")),
        "tags": list(row.get("tags") or []),
        "metadata": _jsonable(row.get("metadata") or {}),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        "step_count": len(row.get("steps") or []),
    }
    if include_steps:
        out["steps"] = _jsonable(row.get("steps") or [])
    return out


def _validate_step(step: dict[str, Any], *, idx: int) -> dict[str, Any]:
    if not isinstance(step, dict):
        raise ValueError(f"runbook step {idx} must be an object")
    stype = str(step.get("type") or "").strip().lower()
    if not stype:
        raise ValueError(f"runbook step {idx} missing type")
    row = dict(step)
    row["type"] = stype
    row["id"] = str(row.get("id") or f"step_{idx}")
    if stype == "operation" and not str(row.get("kind") or "").strip():
        raise ValueError(f"operation step {idx} requires kind")
    if stype == "incident_note" and not str(row.get("note") or "").strip():
        raise ValueError(f"incident_note step {idx} requires note")
    if stype == "notification_send" and not str(row.get("message") or "").strip():
        raise ValueError(f"notification_send step {idx} requires message")
    if stype not in {"operation", "incident_note", "incident_capture_bundle", "advisor_analyze", "notification_send", "sleep"}:
        raise ValueError(f"unsupported runbook step type in step {idx}: {stype}")
    return _jsonable(row)


def _render_obj(value: Any, ctx: dict[str, Any]) -> Any:
    if isinstance(value, str):
        return _render_string(value, ctx)
    if isinstance(value, list):
        return [_render_obj(v, ctx) for v in value]
    if isinstance(value, dict):
        return {str(k): _render_obj(v, ctx) for k, v in value.items()}
    return value


def _render_string(text: str, ctx: dict[str, Any]) -> str:
    s = str(text)
    out = s
    for token in _extract_tokens(s):
        val = _resolve_ctx(ctx, token)
        out = out.replace("${" + token + "}", "" if val is None else str(val))
    return out


def _extract_tokens(text: str) -> list[str]:
    tokens = []
    i = 0
    while True:
        start = text.find("${", i)
        if start < 0:
            break
        end = text.find("}", start + 2)
        if end < 0:
            break
        tokens.append(text[start + 2 : end])
        i = end + 1
    return tokens


def _resolve_ctx(ctx: dict[str, Any], path: str) -> Any:
    cur: Any = ctx
    for part in str(path).split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    return cur


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    if isinstance(value, tuple):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    return str(value)

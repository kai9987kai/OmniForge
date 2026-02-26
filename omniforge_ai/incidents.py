from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .history_store import HistoryStore
    from .service import OmniForgeService


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


class IncidentCenter:
    def __init__(self, service: "OmniForgeService", root_dir: Path, history: "HistoryStore"):
        self.service = service
        self.root_dir = Path(root_dir).resolve()
        self.history = history
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self.incidents_dir = self.root_dir / "records"
        self.incidents_dir.mkdir(parents=True, exist_ok=True)
        self.timeline_file = self.root_dir / "timeline.jsonl"
        self._lock = threading.RLock()

    def status(self) -> dict[str, Any]:
        items = self.list(limit=500).get("items") or []
        open_count = sum(1 for i in items if str(i.get("status")) != "closed")
        closed_count = sum(1 for i in items if str(i.get("status")) == "closed")
        sev_counts: dict[str, int] = {}
        for row in items:
            sev = str(row.get("severity") or "warning")
            sev_counts[sev] = sev_counts.get(sev, 0) + 1
        recent = self.timeline(limit=100)
        return {
            "ok": True,
            "root_dir": str(self.root_dir),
            "incidents_dir": str(self.incidents_dir),
            "timeline_file": str(self.timeline_file),
            "incident_count": len(items),
            "open_count": open_count,
            "closed_count": closed_count,
            "severity_counts": sev_counts,
            "recent_timeline_count": recent.get("count"),
        }

    def templates(self) -> dict[str, Any]:
        return {
            "ok": True,
            "statuses": ["open", "investigating", "mitigated", "closed"],
            "severities": ["info", "warning", "critical"],
            "auto_alert_policy": {
                "default_open_severities": ["critical"],
                "correlates_by": ["rule_id", "rule_kind", "severity"],
            },
            "bundle_templates": [
                {"name": "incident_snapshot", "health_profile": "full", "report_profile": "health_watch"},
                {"name": "alert_response", "health_profile": "quick", "report_profile": "alert_watch"},
            ],
        }

    def list(
        self,
        *,
        limit: int = 50,
        status: str | None = None,
        severity: str | None = None,
        source: str | None = None,
    ) -> dict[str, Any]:
        rows = []
        for p in sorted(self.incidents_dir.glob("inc_*.json"), reverse=True):
            try:
                row = json.loads(p.read_text(encoding="utf-8", errors="replace"))
            except Exception:
                continue
            if not isinstance(row, dict):
                continue
            if status and str(row.get("status")) != str(status):
                continue
            if severity and str(row.get("severity")) != str(severity):
                continue
            if source and str(row.get("source")) != str(source):
                continue
            rows.append(_incident_summary(row))
            if len(rows) >= max(1, int(limit)):
                break
        return {"ok": True, "count": len(rows), "items": rows}

    def get(self, incident_id: str, *, timeline_limit: int = 50) -> dict[str, Any]:
        row = self._read_incident(incident_id)
        return {"ok": True, "incident": row, "timeline": self.timeline(incident_id=incident_id, limit=timeline_limit)}

    def create(
        self,
        *,
        title: str,
        severity: str = "warning",
        description: str | None = None,
        source: str = "manual",
        status: str = "open",
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        alert: dict[str, Any] | None = None,
        auto_created: bool = False,
    ) -> dict[str, Any]:
        title_text = str(title or "").strip()
        if not title_text:
            raise ValueError("title is required")
        now = _now_utc()
        iid = f"inc_{now.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
        sev = str(severity or "warning").strip() or "warning"
        st = str(status or "open").strip() or "open"
        if st not in {"open", "investigating", "mitigated", "closed"}:
            raise ValueError(f"unsupported incident status: {st}")
        row = {
            "id": iid,
            "title": title_text,
            "severity": sev,
            "status": st,
            "description": str(description or ""),
            "source": str(source or "manual"),
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
            "closed_at": None,
            "resolution": "",
            "tags": [str(t) for t in (tags or []) if str(t).strip()],
            "metadata": _jsonable(metadata or {}),
            "auto_created": bool(auto_created),
            "linked_alert_ids": [],
            "linked_report_ids": [],
            "linked_health_snapshot_ids": [],
            "timeline_count": 0,
            "last_event_at": None,
            "last_event_kind": None,
        }
        if isinstance(alert, dict):
            alert_id = str(alert.get("id") or "").strip()
            if alert_id:
                row["linked_alert_ids"] = [alert_id]
                rule_id = str(alert.get("rule_id") or "").strip()
                if rule_id:
                    md = dict(row.get("metadata") or {})
                    md.setdefault("correlation", {})
                    if isinstance(md.get("correlation"), dict):
                        md["correlation"]["rule_id"] = rule_id
                        md["correlation"]["rule_kind"] = str(alert.get("rule_kind") or "")
                        md["correlation"]["severity"] = str(alert.get("severity") or sev)
                    row["metadata"] = _jsonable(md)
        self._write_incident(row)
        self._timeline_event(iid, "created", {"incident": _incident_summary(row), "alert": _jsonable(alert) if isinstance(alert, dict) else None})
        self.history.record(
            "incident.created",
            {"incident": _incident_summary(row)},
            source="incidents",
            tags=["incident", sev, st],
            summary={"incident_id": iid, "severity": sev, "status": st, "source": row.get("source")},
        )
        return {"ok": True, "incident": row}

    def update(self, incident_id: str, **changes: Any) -> dict[str, Any]:
        row = self._read_incident(incident_id)
        before = _incident_summary(row)
        if "title" in changes and changes["title"] is not None:
            title_text = str(changes["title"]).strip()
            if title_text:
                row["title"] = title_text
        if "severity" in changes and changes["severity"] is not None:
            row["severity"] = str(changes["severity"]).strip() or row.get("severity") or "warning"
        if "status" in changes and changes["status"] is not None:
            st = str(changes["status"]).strip()
            if st not in {"open", "investigating", "mitigated", "closed"}:
                raise ValueError(f"unsupported incident status: {st}")
            row["status"] = st
            if st == "closed" and not row.get("closed_at"):
                row["closed_at"] = _now_utc().isoformat()
            if st != "closed":
                row["closed_at"] = None
        if "description" in changes and changes["description"] is not None:
            row["description"] = str(changes["description"])
        if "resolution" in changes and changes["resolution"] is not None:
            row["resolution"] = str(changes["resolution"])
        if "tags" in changes and isinstance(changes["tags"], list):
            row["tags"] = [str(t) for t in changes["tags"] if str(t).strip()]
        if "metadata_patch" in changes and isinstance(changes["metadata_patch"], dict):
            md = dict(row.get("metadata") or {})
            md.update(_jsonable(changes["metadata_patch"]))
            row["metadata"] = md
        row["updated_at"] = _now_utc().isoformat()
        self._write_incident(row)
        after = _incident_summary(row)
        self._timeline_event(incident_id, "updated", {"before": before, "after": after, "changes": _jsonable(changes)})
        self.history.record(
            "incident.updated",
            {"incident_id": incident_id, "changes": _jsonable(changes)},
            source="incidents",
            tags=["incident", "update"],
            summary={"incident_id": incident_id, "status": row.get("status"), "severity": row.get("severity")},
        )
        return {"ok": True, "incident": row}

    def close(self, incident_id: str, *, resolution: str | None = None, note: str | None = None) -> dict[str, Any]:
        row = self._read_incident(incident_id)
        row["status"] = "closed"
        row["closed_at"] = _now_utc().isoformat()
        if resolution is not None:
            row["resolution"] = str(resolution)
        row["updated_at"] = _now_utc().isoformat()
        self._write_incident(row)
        self._timeline_event(incident_id, "closed", {"resolution": row.get("resolution"), "note": str(note or "")})
        if note:
            self._timeline_event(incident_id, "note", {"note": str(note), "author": "system", "context": "close"})
        self.history.record(
            "incident.closed",
            {"incident_id": incident_id, "resolution": row.get("resolution")},
            source="incidents",
            tags=["incident", "closed"],
            summary={"incident_id": incident_id},
        )
        return {"ok": True, "incident": row}

    def reopen(self, incident_id: str, *, note: str | None = None) -> dict[str, Any]:
        row = self._read_incident(incident_id)
        row["status"] = "open"
        row["closed_at"] = None
        row["updated_at"] = _now_utc().isoformat()
        self._write_incident(row)
        self._timeline_event(incident_id, "reopened", {"note": str(note or "")})
        if note:
            self._timeline_event(incident_id, "note", {"note": str(note), "author": "system", "context": "reopen"})
        self.history.record(
            "incident.reopened",
            {"incident_id": incident_id},
            source="incidents",
            tags=["incident", "reopen"],
            summary={"incident_id": incident_id},
        )
        return {"ok": True, "incident": row}

    def add_note(self, incident_id: str, *, note: str, author: str | None = None, kind: str = "note") -> dict[str, Any]:
        text = str(note or "").strip()
        if not text:
            raise ValueError("note is required")
        row = self._read_incident(incident_id)
        ev = self._timeline_event(
            incident_id,
            kind,
            {"note": text, "author": (str(author).strip() if author else "user")},
        )
        row = self._read_incident(incident_id)
        self.history.record(
            "incident.note",
            {"incident_id": incident_id, "timeline_event_id": ev.get("id"), "kind": kind},
            source="incidents",
            tags=["incident", "note"],
            summary={"incident_id": incident_id, "kind": kind},
        )
        return {"ok": True, "incident": row, "timeline_event": ev}

    def link_alert(self, incident_id: str, alert_or_id: dict[str, Any] | str) -> dict[str, Any]:
        row = self._read_incident(incident_id)
        alert = self._resolve_alert(alert_or_id)
        alert_id = str(alert.get("id") or "").strip()
        if not alert_id:
            raise ValueError("alert.id is required")
        linked = list(row.get("linked_alert_ids") or [])
        if alert_id not in linked:
            linked.append(alert_id)
            row["linked_alert_ids"] = linked
            row["updated_at"] = _now_utc().isoformat()
            self._write_incident(row)
        ev = self._timeline_event(
            incident_id,
            "alert_linked",
            {
                "alert_id": alert_id,
                "rule_id": alert.get("rule_id"),
                "rule_kind": alert.get("rule_kind"),
                "severity": alert.get("severity"),
                "message": str(alert.get("message") or "")[:300],
            },
        )
        self.history.record(
            "incident.alert_linked",
            {"incident_id": incident_id, "alert_id": alert_id},
            source="incidents",
            tags=["incident", "alert"],
            summary={"incident_id": incident_id, "alert_id": alert_id},
        )
        return {"ok": True, "incident": self._read_incident(incident_id), "alert": alert, "timeline_event": ev}

    def timeline(self, *, incident_id: str | None = None, limit: int = 100) -> dict[str, Any]:
        rows = []
        if self.timeline_file.exists():
            with self._lock:
                lines = self.timeline_file.read_text(encoding="utf-8", errors="replace").splitlines()
            for line in reversed(lines):
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                if incident_id and str(row.get("incident_id")) != str(incident_id):
                    continue
                rows.append(row)
                if len(rows) >= max(1, int(limit)):
                    break
        return {"ok": True, "count": len(rows), "items": rows}

    def capture_bundle(
        self,
        incident_id: str,
        *,
        health_profile: str = "full",
        report_profile: str = "health_watch",
        include_health: bool = True,
        include_report: bool = True,
    ) -> dict[str, Any]:
        row = self._read_incident(incident_id)
        bundle: dict[str, Any] = {"incident_id": incident_id}
        if include_health:
            hs = self.service.health.snapshot(
                profile=health_profile,
                options={"notify": False, "incident_id": incident_id},
            )
            bundle["health"] = hs
            snap = (hs or {}).get("snapshot") or {}
            sid = str(snap.get("snapshot_id") or "").strip()
            if sid:
                linked = list(row.get("linked_health_snapshot_ids") or [])
                if sid not in linked:
                    linked.append(sid)
                    row["linked_health_snapshot_ids"] = linked
        if include_report:
            rep = self.service.reports.generate(
                profile=report_profile,
                options={"incident_id": incident_id},
            )
            bundle["report"] = rep
            rid = str((rep or {}).get("report_id") or "").strip()
            if rid:
                linked_reports = list(row.get("linked_report_ids") or [])
                if rid not in linked_reports:
                    linked_reports.append(rid)
                    row["linked_report_ids"] = linked_reports
        row["updated_at"] = _now_utc().isoformat()
        self._write_incident(row)
        ev = self._timeline_event(
            incident_id,
            "bundle_captured",
            {
                "health_profile": health_profile if include_health else None,
                "report_profile": report_profile if include_report else None,
                "linked_report_ids": row.get("linked_report_ids"),
                "linked_health_snapshot_ids": row.get("linked_health_snapshot_ids"),
            },
        )
        self.history.record(
            "incident.bundle_captured",
            {"incident_id": incident_id, "bundle": _bundle_summary(bundle)},
            source="incidents",
            tags=["incident", "bundle"],
            summary={"incident_id": incident_id, "has_report": include_report, "has_health": include_health},
        )
        return {"ok": True, "incident": self._read_incident(incident_id), "bundle": bundle, "timeline_event": ev}

    def handle_alert(self, alert_or_id: dict[str, Any] | str) -> dict[str, Any]:
        alert = self._resolve_alert(alert_or_id)
        severity = str(alert.get("severity") or "warning")
        if severity not in {"critical"}:
            return {"ok": True, "handled": False, "reason": "severity_not_auto_opened", "severity": severity}

        corr = self._alert_correlation_key(alert)
        open_items = self.list(limit=500).get("items") or []
        target_id = None
        for item in open_items:
            if str(item.get("status")) == "closed":
                continue
            if str(item.get("source")) != "alert":
                continue
            md = item.get("metadata") or {}
            if not isinstance(md, dict):
                continue
            if str(md.get("correlation_key") or "") == corr and corr:
                target_id = str(item.get("id") or "")
                break

        if target_id:
            link_res = self.link_alert(target_id, alert)
            self._timeline_event(
                target_id,
                "alert_correlated",
                {"alert_id": alert.get("id"), "correlation_key": corr},
            )
            return {"ok": True, "handled": True, "action": "linked_existing", "incident_id": target_id, "link": link_res}

        title = f"[{severity.upper()}] {str(alert.get('rule_name') or alert.get('rule_kind') or 'Alert Incident')}"
        metadata = {
            "alert_seed": {
                "rule_id": alert.get("rule_id"),
                "rule_kind": alert.get("rule_kind"),
                "message": str(alert.get("message") or "")[:500],
            },
            "correlation_key": corr,
            "correlation": {
                "rule_id": alert.get("rule_id"),
                "rule_kind": alert.get("rule_kind"),
                "severity": alert.get("severity"),
            },
        }
        created = self.create(
            title=title,
            severity=severity,
            description=str(alert.get("message") or ""),
            source="alert",
            status="open",
            tags=["auto", "alert", str(alert.get("rule_kind") or "unknown")],
            metadata=metadata,
            alert=alert,
            auto_created=True,
        )
        inc = created.get("incident") or {}
        self._timeline_event(
            str(inc.get("id") or ""),
            "auto_opened_from_alert",
            {"alert_id": alert.get("id"), "rule_id": alert.get("rule_id"), "correlation_key": corr},
        )
        return {"ok": True, "handled": True, "action": "created", "incident_id": inc.get("id"), "incident": inc}

    def _alert_correlation_key(self, alert: dict[str, Any]) -> str:
        parts = [
            str(alert.get("rule_id") or "").strip(),
            str(alert.get("rule_kind") or "").strip(),
            str(alert.get("severity") or "").strip(),
        ]
        if not any(parts):
            return ""
        return "|".join(parts)

    def _resolve_alert(self, alert_or_id: dict[str, Any] | str) -> dict[str, Any]:
        if isinstance(alert_or_id, dict):
            return dict(alert_or_id)
        aid = str(alert_or_id or "").strip()
        if not aid:
            raise ValueError("alert_id is required")
        return self.service.alerts.get_alert(aid)["alert"]

    def _timeline_event(self, incident_id: str, kind: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
        iid = str(incident_id or "").strip()
        if not iid:
            raise ValueError("incident_id is required")
        ev = {
            "id": f"inc_ev_{_now_utc().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}",
            "incident_id": iid,
            "created_at": _now_utc().isoformat(),
            "kind": str(kind or "event"),
            "data": _jsonable(data or {}),
        }
        with self._lock:
            with self.timeline_file.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(_jsonable(ev), ensure_ascii=False))
                fh.write("\n")
        try:
            row = self._read_incident(iid)
            row["timeline_count"] = int(row.get("timeline_count") or 0) + 1
            row["last_event_at"] = ev["created_at"]
            row["last_event_kind"] = ev["kind"]
            row["updated_at"] = _now_utc().isoformat()
            self._write_incident(row)
        except Exception:
            pass
        return ev

    def _incident_path(self, incident_id: str) -> Path:
        iid = str(incident_id or "").strip()
        if not iid:
            raise ValueError("incident_id is required")
        return self.incidents_dir / f"{iid}.json"

    def _read_incident(self, incident_id: str) -> dict[str, Any]:
        path = self._incident_path(incident_id)
        if not path.exists():
            raise FileNotFoundError(f"incident not found: {incident_id}")
        with self._lock:
            data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        if not isinstance(data, dict):
            raise ValueError(f"incident file invalid: {path}")
        return data

    def _write_incident(self, row: dict[str, Any]) -> None:
        iid = str(row.get("id") or "").strip()
        if not iid:
            raise ValueError("incident row missing id")
        path = self._incident_path(iid)
        with self._lock:
            path.write_text(json.dumps(_jsonable(row), indent=2, ensure_ascii=False), encoding="utf-8")


def _incident_summary(row: dict[str, Any]) -> dict[str, Any]:
    out = {
        "id": row.get("id"),
        "title": row.get("title"),
        "severity": row.get("severity"),
        "status": row.get("status"),
        "source": row.get("source"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        "closed_at": row.get("closed_at"),
        "auto_created": row.get("auto_created"),
        "tags": row.get("tags") or [],
        "linked_alert_ids": list(row.get("linked_alert_ids") or []),
        "linked_report_ids": list(row.get("linked_report_ids") or []),
        "linked_health_snapshot_ids": list(row.get("linked_health_snapshot_ids") or []),
        "timeline_count": row.get("timeline_count"),
        "last_event_at": row.get("last_event_at"),
        "last_event_kind": row.get("last_event_kind"),
        "metadata": _jsonable(row.get("metadata") or {}),
    }
    if row.get("description"):
        out["description"] = str(row.get("description"))
    if row.get("resolution"):
        out["resolution"] = str(row.get("resolution"))
    return out


def _bundle_summary(bundle: dict[str, Any]) -> dict[str, Any]:
    out = {"incident_id": bundle.get("incident_id")}
    if isinstance(bundle.get("health"), dict):
        snap = (bundle["health"].get("snapshot") or {}) if isinstance(bundle["health"], dict) else {}
        out["health_snapshot_id"] = snap.get("snapshot_id")
    if isinstance(bundle.get("report"), dict):
        out["report_id"] = bundle["report"].get("report_id")
    return out


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

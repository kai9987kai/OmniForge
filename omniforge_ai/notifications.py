from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .history_store import HistoryStore
    from .service import OmniForgeService


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


class NotificationCenter:
    def __init__(self, service: "OmniForgeService", root_dir: Path, history: "HistoryStore"):
        self.service = service
        self.root_dir = Path(root_dir).resolve()
        self.history = history
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self.sinks_dir = self.root_dir / "sinks"
        self.sinks_dir.mkdir(parents=True, exist_ok=True)
        self.channels_file = self.root_dir / "channels.json"
        self.deliveries_file = self.root_dir / "deliveries.jsonl"
        self._lock = threading.RLock()
        self._channels: dict[str, dict[str, Any]] = {}
        self._load_channels_locked()

    def status(self) -> dict[str, Any]:
        with self._lock:
            enabled_count = sum(1 for c in self._channels.values() if c.get("enabled"))
        deliveries = self.list_deliveries(limit=100)
        recent = deliveries.get("items") or []
        return {
            "ok": True,
            "root_dir": str(self.root_dir),
            "channels_file": str(self.channels_file),
            "deliveries_file": str(self.deliveries_file),
            "channel_count": self.list_channels()["count"],
            "enabled_channel_count": enabled_count,
            "recent_delivery_count": deliveries.get("count"),
            "recent_success_count": sum(1 for d in recent if d.get("ok")),
            "recent_failure_count": sum(1 for d in recent if not d.get("ok")),
        }

    def templates(self) -> dict[str, Any]:
        return {
            "ok": True,
            "channel_types": [
                {
                    "type": "console",
                    "description": "Prints notification summaries to stdout.",
                    "config": {"prefix": "[omniforge]"},
                },
                {
                    "type": "history_event",
                    "description": "Writes notifications back into OmniForge history as events.",
                    "config": {"event_type": "notification.message", "source": "notifications"},
                },
                {
                    "type": "file_append",
                    "description": "Appends notifications to a local file (jsonl or text).",
                    "config": {"path": "alerts.log", "format": "jsonl"},
                },
                {
                    "type": "webhook",
                    "description": "POSTs notification JSON to an HTTP endpoint.",
                    "config": {"url": "http://127.0.0.1:9999/hook", "timeout_sec": 3.0},
                },
            ],
            "match_fields": [
                "topic",
                "topic_prefix",
                "severity",
                "rule_kind",
                "rule_id",
                "message_contains",
                "tags_any",
            ],
            "examples": [
                {
                    "name": "Critical alert logger",
                    "type": "file_append",
                    "match": {"topic": "alert.triggered", "severity": "critical"},
                    "config": {"path": "critical_alerts.jsonl", "format": "jsonl"},
                },
                {
                    "name": "All health snapshots to history",
                    "type": "history_event",
                    "match": {"topic_prefix": "health."},
                    "config": {"event_type": "notification.health"},
                },
            ],
        }

    def list_channels(self) -> dict[str, Any]:
        with self._lock:
            items = sorted((dict(v) for v in self._channels.values()), key=lambda x: str(x.get("name") or x.get("id")))
        return {"ok": True, "count": len(items), "items": items}

    def get_channel(self, channel_id: str) -> dict[str, Any]:
        cid = str(channel_id or "").strip()
        if not cid:
            raise ValueError("channel_id is required")
        with self._lock:
            row = self._channels.get(cid)
            if row is None:
                raise FileNotFoundError(f"notification channel not found: {cid}")
            return {"ok": True, "channel": dict(row)}

    def create_channel(
        self,
        *,
        channel_type: str,
        name: str | None = None,
        config: dict[str, Any] | None = None,
        match: dict[str, Any] | None = None,
        enabled: bool = True,
    ) -> dict[str, Any]:
        ctype = str(channel_type or "").strip()
        if ctype not in {"console", "history_event", "file_append", "webhook"}:
            raise ValueError(f"unsupported notification channel type: {ctype}")
        now = _now_utc()
        cid = f"ntf_{now.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
        row = {
            "id": cid,
            "name": (str(name).strip() if name else cid),
            "type": ctype,
            "config": _jsonable(config or {}),
            "match": _jsonable(match or {}),
            "enabled": bool(enabled),
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
            "last_delivery_at": None,
            "last_delivery_id": None,
            "last_result": None,
        }
        with self._lock:
            self._channels[cid] = row
            self._persist_channels_locked()
        self.history.record(
            "notification.channel_created",
            {"channel": row},
            source="notifications",
            tags=["notification", "channel"],
            summary={"channel_id": cid, "type": ctype, "enabled": bool(enabled)},
        )
        return {"ok": True, "channel": dict(row)}

    def update_channel(self, channel_id: str, **changes: Any) -> dict[str, Any]:
        cid = str(channel_id or "").strip()
        if not cid:
            raise ValueError("channel_id is required")
        with self._lock:
            row = self._channels.get(cid)
            if row is None:
                raise FileNotFoundError(f"notification channel not found: {cid}")
            if "name" in changes and changes["name"] is not None:
                row["name"] = str(changes["name"]).strip() or row["name"]
            if "enabled" in changes and changes["enabled"] is not None:
                row["enabled"] = bool(changes["enabled"])
            if "config" in changes and isinstance(changes["config"], dict):
                row["config"] = _jsonable(changes["config"])
            if "match" in changes and isinstance(changes["match"], dict):
                row["match"] = _jsonable(changes["match"])
            row["updated_at"] = _now_utc().isoformat()
            self._persist_channels_locked()
            out = dict(row)
        self.history.record(
            "notification.channel_updated",
            {"channel_id": cid, "changes": _jsonable(changes)},
            source="notifications",
            tags=["notification", "channel"],
            summary={"channel_id": cid, "enabled": out.get("enabled")},
        )
        return {"ok": True, "channel": out}

    def delete_channel(self, channel_id: str) -> dict[str, Any]:
        cid = str(channel_id or "").strip()
        if not cid:
            raise ValueError("channel_id is required")
        with self._lock:
            row = self._channels.pop(cid, None)
            if row is None:
                raise FileNotFoundError(f"notification channel not found: {cid}")
            self._persist_channels_locked()
        self.history.record(
            "notification.channel_deleted",
            {"channel_id": cid, "type": row.get("type")},
            source="notifications",
            tags=["notification", "channel"],
            summary={"channel_id": cid},
        )
        return {"ok": True, "deleted": cid}

    def list_deliveries(
        self,
        *,
        limit: int = 50,
        channel_id: str | None = None,
        ok: bool | None = None,
        topic: str | None = None,
        severity: str | None = None,
    ) -> dict[str, Any]:
        rows = []
        if self.deliveries_file.exists():
            with self._lock:
                lines = self.deliveries_file.read_text(encoding="utf-8", errors="replace").splitlines()
            for line in reversed(lines):
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                if channel_id and str(row.get("channel_id")) != str(channel_id):
                    continue
                if ok is not None and bool(row.get("ok")) != bool(ok):
                    continue
                if topic and str(row.get("topic")) != str(topic):
                    continue
                if severity and str(row.get("severity")) != str(severity):
                    continue
                rows.append(row)
                if len(rows) >= max(1, int(limit)):
                    break
        return {"ok": True, "count": len(rows), "items": rows}

    def dispatch(
        self,
        *,
        topic: str,
        message: str,
        payload: dict[str, Any] | None = None,
        severity: str = "info",
        tags: list[str] | None = None,
        target_channel_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        topic = str(topic or "").strip()
        msg = str(message or "").strip()
        if not topic:
            raise ValueError("topic is required")
        if not msg:
            raise ValueError("message is required")
        envelope = {
            "id": f"msg_{_now_utc().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}",
            "created_at": _now_utc().isoformat(),
            "topic": topic,
            "message": msg,
            "severity": str(severity or "info"),
            "tags": [str(t) for t in (tags or []) if str(t).strip()],
            "payload": _jsonable(payload or {}),
        }

        with self._lock:
            channels = [dict(c) for c in self._channels.values() if c.get("enabled")]
        if target_channel_ids:
            wanted = {str(x).strip() for x in target_channel_ids if str(x).strip()}
            channels = [c for c in channels if str(c.get("id")) in wanted]
        else:
            channels = [c for c in channels if self._channel_matches(c, envelope)]

        deliveries = []
        matched_count = len(channels)
        for channel in sorted(channels, key=lambda x: str(x.get("name") or x.get("id"))):
            deliveries.append(self._deliver(channel, envelope))

        ok_count = sum(1 for d in deliveries if d.get("ok"))
        fail_count = sum(1 for d in deliveries if not d.get("ok"))
        out = {
            "ok": True,
            "message": envelope,
            "matched_count": matched_count,
            "delivered_count": ok_count,
            "failed_count": fail_count,
            "deliveries": deliveries,
        }
        self.history.record(
            "notification.dispatched",
            {
                "topic": topic,
                "severity": envelope.get("severity"),
                "matched_count": matched_count,
                "delivered_count": ok_count,
                "failed_count": fail_count,
            },
            source="notifications",
            tags=["notification", "dispatch", str(envelope.get("severity") or "info")],
            summary={"topic": topic, "matched": matched_count, "delivered": ok_count, "failed": fail_count},
        )
        return out

    def send(
        self,
        *,
        topic: str,
        message: str,
        payload: dict[str, Any] | None = None,
        severity: str = "info",
        tags: list[str] | None = None,
        target_channel_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        return self.dispatch(
            topic=topic,
            message=message,
            payload=payload,
            severity=severity,
            tags=tags,
            target_channel_ids=target_channel_ids,
        )

    def notify_alert(self, alert: dict[str, Any]) -> dict[str, Any]:
        a = dict(alert or {})
        sev = str(a.get("severity") or "warning")
        msg = str(a.get("message") or "alert triggered")
        tags = ["alert", sev]
        rule_kind = str(a.get("rule_kind") or "").strip()
        if rule_kind:
            tags.append(rule_kind)
        return self.dispatch(
            topic="alert.triggered",
            message=msg,
            payload={"alert": _jsonable(a)},
            severity=sev,
            tags=tags,
        )

    def notify_health_snapshot(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        snap = dict(snapshot or {})
        score = ((snap.get("summary") or {}).get("score"))
        level = str(((snap.get("summary") or {}).get("level") or "info"))
        msg = f"Health snapshot score={score} level={level}"
        sev = "warning" if level in {"warning", "degraded"} else ("critical" if level in {"critical", "error"} else "info")
        return self.dispatch(
            topic="health.snapshot",
            message=msg,
            payload={"snapshot": _jsonable(snap)},
            severity=sev,
            tags=["health", level],
        )

    def _channel_matches(self, channel: dict[str, Any], envelope: dict[str, Any]) -> bool:
        match = channel.get("match")
        if not isinstance(match, dict) or not match:
            return True
        topic = str(envelope.get("topic") or "")
        severity = str(envelope.get("severity") or "")
        message = str(envelope.get("message") or "")
        tags = {str(t) for t in (envelope.get("tags") or [])}
        alert_obj = ((envelope.get("payload") or {}).get("alert") or {}) if isinstance(envelope.get("payload"), dict) else {}

        if "topic" in match and str(match.get("topic")) != topic:
            return False
        if "topic_prefix" in match and not topic.startswith(str(match.get("topic_prefix") or "")):
            return False
        if "severity" in match and str(match.get("severity")) != severity:
            return False
        if "message_contains" in match and str(match.get("message_contains") or "").lower() not in message.lower():
            return False
        if "rule_kind" in match and str(alert_obj.get("rule_kind")) != str(match.get("rule_kind")):
            return False
        if "rule_id" in match and str(alert_obj.get("rule_id")) != str(match.get("rule_id")):
            return False
        if "tags_any" in match:
            expected = match.get("tags_any")
            if isinstance(expected, list):
                if not any(str(x) in tags for x in expected):
                    return False
        return True

    def _deliver(self, channel: dict[str, Any], envelope: dict[str, Any]) -> dict[str, Any]:
        did = f"ntfd_{_now_utc().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
        ctype = str(channel.get("type") or "")
        cfg = dict(channel.get("config") or {})
        ok = True
        result: Any = None
        error = None
        try:
            if ctype == "console":
                prefix = str(cfg.get("prefix") or "[omniforge]")
                print(f"{prefix} {envelope.get('topic')} {envelope.get('severity')}: {envelope.get('message')}")
                result = {"printed": True}
            elif ctype == "history_event":
                event_type = str(cfg.get("event_type") or "notification.message")
                source = str(cfg.get("source") or "notifications")
                hist = self.history.record(
                    event_type,
                    {"notification": envelope, "channel": {"id": channel.get("id"), "name": channel.get("name"), "type": ctype}},
                    source=source,
                    tags=["notification", str(envelope.get("severity") or "info")],
                    summary={"topic": envelope.get("topic"), "channel_id": channel.get("id")},
                )
                result = {"history_event": hist}
            elif ctype == "file_append":
                file_info = self._deliver_file_append(cfg, envelope)
                result = file_info
            elif ctype == "webhook":
                result = self._deliver_webhook(cfg, envelope)
            else:
                raise ValueError(f"unsupported channel type: {ctype}")
        except Exception as exc:
            ok = False
            error = str(exc)

        record = {
            "id": did,
            "created_at": _now_utc().isoformat(),
            "channel_id": channel.get("id"),
            "channel_name": channel.get("name"),
            "channel_type": ctype,
            "topic": envelope.get("topic"),
            "severity": envelope.get("severity"),
            "message": envelope.get("message"),
            "ok": ok,
            "result": _jsonable(result),
            "error": error,
        }
        with self._lock:
            with self.deliveries_file.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(_jsonable(record), ensure_ascii=False))
                fh.write("\n")
            cur = self._channels.get(str(channel.get("id")))
            if cur is not None:
                cur["last_delivery_at"] = record["created_at"]
                cur["last_delivery_id"] = did
                cur["last_result"] = {"ok": ok, "topic": envelope.get("topic"), "error": error}
                cur["updated_at"] = _now_utc().isoformat()
                self._persist_channels_locked()
        self.history.record(
            "notification.delivery",
            {"delivery": record},
            source="notifications",
            tags=["notification", "delivery", ("ok" if ok else "error")],
            summary={"delivery_id": did, "channel_id": channel.get("id"), "ok": ok, "topic": envelope.get("topic")},
        )
        return record

    def _deliver_file_append(self, cfg: dict[str, Any], envelope: dict[str, Any]) -> dict[str, Any]:
        path_text = str(cfg.get("path") or "").strip()
        if not path_text:
            raise ValueError("file_append channel requires config.path")
        p = Path(path_text)
        if not p.is_absolute():
            p = (self.sinks_dir / p).resolve()
        p.parent.mkdir(parents=True, exist_ok=True)
        fmt = str(cfg.get("format") or "jsonl").strip().lower()
        if fmt == "jsonl":
            with p.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(_jsonable(envelope), ensure_ascii=False))
                fh.write("\n")
        elif fmt == "text":
            line = f"[{envelope.get('created_at')}] {envelope.get('topic')} {envelope.get('severity')}: {envelope.get('message')}\n"
            with p.open("a", encoding="utf-8") as fh:
                fh.write(line)
        else:
            raise ValueError(f"unsupported file_append format: {fmt}")
        return {"path": str(p), "format": fmt}

    def _deliver_webhook(self, cfg: dict[str, Any], envelope: dict[str, Any]) -> dict[str, Any]:
        url = str(cfg.get("url") or "").strip()
        if not url:
            raise ValueError("webhook channel requires config.url")
        timeout_sec = float(cfg.get("timeout_sec", 3.0))
        body = json.dumps(_jsonable(envelope), ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url=url,
            data=body,
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout_sec) as resp:  # nosec B310 (local tooling)
                text = resp.read().decode("utf-8", errors="replace")
                return {"status_code": int(getattr(resp, "status", 200)), "body_preview": text[:500]}
        except urllib.error.HTTPError as exc:
            body_text = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"webhook http {exc.code}: {body_text[:400]}") from exc

    def _load_channels_locked(self) -> None:
        with self._lock:
            self._channels = {}
            if not self.channels_file.exists():
                return
            try:
                data = json.loads(self.channels_file.read_text(encoding="utf-8", errors="replace"))
            except Exception:
                return
            items = data.get("channels") if isinstance(data, dict) else None
            if not isinstance(items, list):
                return
            for item in items:
                if not isinstance(item, dict):
                    continue
                cid = str(item.get("id") or "").strip()
                if cid:
                    self._channels[cid] = item

    def _persist_channels_locked(self) -> None:
        rows = sorted(self._channels.values(), key=lambda x: str(x.get("created_at") or ""))
        payload = {"channels": rows, "updated_at": _now_utc().isoformat()}
        self.channels_file.write_text(json.dumps(_jsonable(payload), indent=2, ensure_ascii=False), encoding="utf-8")


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

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


class AutoRemediationEngine:
    def __init__(self, service: "OmniForgeService", root_dir: Path, history: "HistoryStore"):
        self.service = service
        self.root_dir = Path(root_dir).resolve()
        self.history = history
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self.policies_file = self.root_dir / "policies.json"
        self.actions_file = self.root_dir / "actions.jsonl"
        self._lock = threading.RLock()
        self._local = threading.local()
        self._policies: dict[str, dict[str, Any]] = {}
        self._load_policies_locked()

    def status(self) -> dict[str, Any]:
        with self._lock:
            enabled = sum(1 for p in self._policies.values() if p.get("enabled"))
        actions = self.list_actions(limit=100)
        recent = actions.get("items") or []
        return {
            "ok": True,
            "policies_file": str(self.policies_file),
            "actions_file": str(self.actions_file),
            "policy_count": self.list_policies()["count"],
            "enabled_policy_count": enabled,
            "recent_action_count": actions.get("count"),
            "recent_success_count": sum(1 for a in recent if a.get("ok")),
            "recent_failure_count": sum(1 for a in recent if not a.get("ok")),
        }

    def templates(self) -> dict[str, Any]:
        return {
            "ok": True,
            "action_templates": [
                {"type": "job_submit", "config": {"kind": "search.roots", "params": {}}},
                {"type": "report_generate", "config": {"profile": "alert_watch", "options": {}}},
                {"type": "scheduler_start", "config": {}},
                {"type": "scheduler_tick", "config": {}},
                {"type": "operation_run", "config": {"kind": "status", "params": {}}},
                {"type": "notification_send", "config": {"topic": "remediation.note", "message": "Policy ${policy.name} matched alert ${alert.id}"}},
                {"type": "health_snapshot", "config": {"profile": "quick", "options": {"notify": False}}},
                {"type": "runbook_run", "config": {"template": "critical_alert_triage", "incident_id": "${alert.incident.incident_id}"}},
            ],
            "policy_templates": [
                {
                    "name": "Create alert watch report on critical alerts",
                    "match": {"severity": "critical"},
                    "cooldown_sec": 300,
                    "actions": [{"type": "report_generate", "config": {"profile": "alert_watch"}}],
                },
                {
                    "name": "Warm scheduler when scheduler-not-running alert fires",
                    "match": {"rule_kind": "scheduler_not_running"},
                    "cooldown_sec": 60,
                    "actions": [{"type": "scheduler_start", "config": {}}],
                },
                {
                    "name": "Run quick intel mission on benchmark regression",
                    "match": {"rule_kind": "benchmark_regression"},
                    "cooldown_sec": 600,
                    "actions": [{"type": "job_submit", "config": {"kind": "missions.run", "params": {"mission": "quick_intel", "inputs": {}}}}],
                },
                {
                    "name": "Notify on critical alert",
                    "match": {"severity": "critical"},
                    "cooldown_sec": 120,
                    "actions": [{"type": "notification_send", "config": {"topic": "remediation.critical", "severity": "critical", "message": "Critical alert handled: ${alert.message}"}}],
                },
                {
                    "name": "Run critical alert triage playbook when incident was auto-opened",
                    "match": {"severity": "critical"},
                    "cooldown_sec": 300,
                    "actions": [{"type": "runbook_run", "config": {"template": "critical_alert_triage", "incident_id": "${alert.incident.incident_id}", "alert_id": "${alert.id}"}}],
                },
            ],
        }

    def list_policies(self) -> dict[str, Any]:
        with self._lock:
            items = sorted((dict(v) for v in self._policies.values()), key=lambda x: str(x.get("name") or x.get("id")))
        return {"ok": True, "count": len(items), "items": items}

    def get_policy(self, policy_id: str) -> dict[str, Any]:
        pid = str(policy_id or "").strip()
        if not pid:
            raise ValueError("policy_id is required")
        with self._lock:
            row = self._policies.get(pid)
            if row is None:
                raise FileNotFoundError(f"remediation policy not found: {pid}")
            return {"ok": True, "policy": dict(row)}

    def create_policy(
        self,
        *,
        name: str | None = None,
        match: dict[str, Any] | None = None,
        actions: list[dict[str, Any]] | None = None,
        enabled: bool = True,
        cooldown_sec: float = 300.0,
    ) -> dict[str, Any]:
        if not isinstance(actions, list) or not actions:
            raise ValueError("actions (non-empty list) is required")
        now = _now_utc()
        pid = f"rmp_{now.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
        row = {
            "id": pid,
            "name": (str(name).strip() if name else pid),
            "match": _jsonable(match or {}),
            "actions": _jsonable(actions),
            "enabled": bool(enabled),
            "cooldown_sec": float(cooldown_sec),
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
            "last_triggered_at": None,
            "last_action_run_id": None,
            "last_match": None,
            "last_result": None,
        }
        with self._lock:
            self._policies[pid] = row
            self._persist_policies_locked()
        self.history.record(
            "remediation.policy_created",
            {"policy": row},
            source="remediations",
            tags=["remediation", "policy"],
            summary={"policy_id": pid, "enabled": bool(enabled)},
        )
        return {"ok": True, "policy": dict(row)}

    def update_policy(self, policy_id: str, **changes: Any) -> dict[str, Any]:
        pid = str(policy_id or "").strip()
        if not pid:
            raise ValueError("policy_id is required")
        with self._lock:
            row = self._policies.get(pid)
            if row is None:
                raise FileNotFoundError(f"remediation policy not found: {pid}")
            if "name" in changes and changes["name"] is not None:
                row["name"] = str(changes["name"]).strip() or row["name"]
            if "enabled" in changes and changes["enabled"] is not None:
                row["enabled"] = bool(changes["enabled"])
            if "cooldown_sec" in changes and changes["cooldown_sec"] is not None:
                row["cooldown_sec"] = float(changes["cooldown_sec"])
            if "match" in changes and isinstance(changes["match"], dict):
                row["match"] = _jsonable(changes["match"])
            if "actions" in changes and isinstance(changes["actions"], list):
                if not changes["actions"]:
                    raise ValueError("actions cannot be empty")
                row["actions"] = _jsonable(changes["actions"])
            row["updated_at"] = _now_utc().isoformat()
            self._persist_policies_locked()
            out = dict(row)
        self.history.record(
            "remediation.policy_updated",
            {"policy_id": pid, "changes": _jsonable(changes)},
            source="remediations",
            tags=["remediation", "policy"],
            summary={"policy_id": pid, "enabled": out.get("enabled")},
        )
        return {"ok": True, "policy": out}

    def delete_policy(self, policy_id: str) -> dict[str, Any]:
        pid = str(policy_id or "").strip()
        if not pid:
            raise ValueError("policy_id is required")
        with self._lock:
            row = self._policies.pop(pid, None)
            if row is None:
                raise FileNotFoundError(f"remediation policy not found: {pid}")
            self._persist_policies_locked()
        self.history.record(
            "remediation.policy_deleted",
            {"policy_id": pid},
            source="remediations",
            tags=["remediation", "policy"],
            summary={"policy_id": pid},
        )
        return {"ok": True, "deleted": pid}

    def list_actions(self, *, limit: int = 50, policy_id: str | None = None, ok: bool | None = None) -> dict[str, Any]:
        rows = []
        if self.actions_file.exists():
            with self._lock:
                lines = self.actions_file.read_text(encoding="utf-8", errors="replace").splitlines()
            for line in reversed(lines):
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                if policy_id and str(row.get("policy_id")) != str(policy_id):
                    continue
                if ok is not None and bool(row.get("ok")) != bool(ok):
                    continue
                rows.append(row)
                if len(rows) >= max(1, int(limit)):
                    break
        return {"ok": True, "count": len(rows), "items": rows}

    def handle_alert(self, alert_or_id: dict[str, Any] | str, *, force: bool = False) -> dict[str, Any]:
        if getattr(self._local, "in_handle", False):
            return {"ok": True, "skipped": True, "reason": "nested_remediation_guard"}
        alert = self._resolve_alert(alert_or_id)
        setattr(self._local, "in_handle", True)
        try:
            with self._lock:
                policies = [dict(p) for p in self._policies.values() if p.get("enabled")]
            matched = []
            executed = []
            for policy in sorted(policies, key=lambda x: str(x.get("name") or x.get("id"))):
                match_res = self._policy_matches(policy, alert)
                if not match_res["matches"]:
                    continue
                matched.append({"policy_id": policy["id"], "name": policy.get("name"), "match": match_res})
                cooldown = self._policy_cooldown(policy)
                if cooldown["suppressed"] and not force:
                    executed.append(
                        {
                            "policy_id": policy["id"],
                            "policy_name": policy.get("name"),
                            "ok": True,
                            "suppressed": True,
                            "cooldown_remaining_sec": cooldown["cooldown_remaining_sec"],
                            "actions": [],
                        }
                    )
                    continue
                executed.append(self._execute_policy(policy, alert, match_res))
            result = {
                "ok": True,
                "alert_id": alert.get("id"),
                "matched_count": len(matched),
                "executed_count": len(executed),
                "matched": matched,
                "executions": executed,
            }
            self.history.record(
                "remediation.handle_alert",
                {"alert_id": alert.get("id"), "matched_count": len(matched), "executed_count": len(executed)},
                source="remediations",
                tags=["remediation", "alert"],
                summary={"alert_id": alert.get("id"), "matched": len(matched), "executed": len(executed)},
            )
            return result
        finally:
            setattr(self._local, "in_handle", False)

    def _resolve_alert(self, alert_or_id: dict[str, Any] | str) -> dict[str, Any]:
        if isinstance(alert_or_id, dict):
            return dict(alert_or_id)
        aid = str(alert_or_id or "").strip()
        if not aid:
            raise ValueError("alert_id is required")
        return self.service.alerts.get_alert(aid)["alert"]

    def _policy_matches(self, policy: dict[str, Any], alert: dict[str, Any]) -> dict[str, Any]:
        match = dict(policy.get("match") or {})
        reasons = []
        ok = True

        def cmp_field(key: str, actual: Any):
            nonlocal ok
            if key not in match:
                return
            expected = match.get(key)
            if str(actual) != str(expected):
                ok = False
                reasons.append(f"{key} mismatch: expected {expected}, got {actual}")

        cmp_field("severity", alert.get("severity"))
        cmp_field("rule_id", alert.get("rule_id"))
        cmp_field("rule_kind", alert.get("rule_kind"))
        cmp_field("rule_name", alert.get("rule_name"))

        if "message_contains" in match:
            needle = str(match.get("message_contains") or "")
            hay = str(alert.get("message") or "")
            if needle and needle.lower() not in hay.lower():
                ok = False
                reasons.append("message_contains mismatch")

        if "acknowledged" in match:
            if bool(alert.get("acknowledged")) != bool(match.get("acknowledged")):
                ok = False
                reasons.append("acknowledged mismatch")

        return {"matches": ok, "reasons": reasons, "match": _jsonable(match)}

    def _policy_cooldown(self, policy: dict[str, Any]) -> dict[str, Any]:
        cooldown_sec = float(policy.get("cooldown_sec") or 0.0)
        last = _iso_to_ts(policy.get("last_triggered_at"))
        if cooldown_sec <= 0 or last is None:
            return {"suppressed": False, "cooldown_remaining_sec": None}
        elapsed = _now_utc().timestamp() - last
        if elapsed >= cooldown_sec:
            return {"suppressed": False, "cooldown_remaining_sec": None}
        return {"suppressed": True, "cooldown_remaining_sec": round(cooldown_sec - elapsed, 3)}

    def _execute_policy(self, policy: dict[str, Any], alert: dict[str, Any], match_res: dict[str, Any]) -> dict[str, Any]:
        run_id = f"rma_{_now_utc().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
        action_results = []
        all_ok = True
        for idx, action in enumerate(policy.get("actions") or [], start=1):
            try:
                res = self._execute_action(policy, alert, action)
                action_results.append({"index": idx, "ok": True, "action": _jsonable(action), "result": _jsonable(res)})
            except Exception as exc:
                all_ok = False
                action_results.append({"index": idx, "ok": False, "action": _jsonable(action), "error": str(exc)})

        record = {
            "id": run_id,
            "created_at": _now_utc().isoformat(),
            "policy_id": policy.get("id"),
            "policy_name": policy.get("name"),
            "alert_id": alert.get("id"),
            "rule_id": alert.get("rule_id"),
            "severity": alert.get("severity"),
            "ok": all_ok,
            "suppressed": False,
            "match": _jsonable(match_res),
            "actions": action_results,
        }
        with self._lock:
            with self.actions_file.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(_jsonable(record), ensure_ascii=False))
                fh.write("\n")
            cur = self._policies.get(str(policy.get("id")))
            if cur is not None:
                cur["last_triggered_at"] = record["created_at"]
                cur["last_action_run_id"] = run_id
                cur["last_match"] = _jsonable(match_res)
                cur["last_result"] = {"ok": all_ok, "action_count": len(action_results)}
                cur["updated_at"] = _now_utc().isoformat()
                self._persist_policies_locked()
        self.history.record(
            "remediation.executed",
            {"record": record},
            source="remediations",
            tags=["remediation", ("ok" if all_ok else "error")],
            summary={"run_id": run_id, "policy_id": policy.get("id"), "ok": all_ok, "action_count": len(action_results)},
        )
        return {"policy_id": policy.get("id"), "policy_name": policy.get("name"), "ok": all_ok, "suppressed": False, "run_id": run_id, "actions": action_results}

    def _execute_action(self, policy: dict[str, Any], alert: dict[str, Any], action: dict[str, Any]) -> Any:
        if not isinstance(action, dict):
            raise ValueError("action must be an object")
        action_type = str(action.get("type") or "").strip()
        cfg = dict(action.get("config") or {})
        ctx = {"alert": alert, "policy": policy}
        cfg = _render_obj(cfg, ctx)
        if action_type == "job_submit":
            kind = str(cfg.get("kind") or "").strip()
            if not kind:
                raise ValueError("job_submit action requires config.kind")
            params = cfg.get("params") if isinstance(cfg.get("params"), dict) else {}
            return self.service.jobs.submit(kind, params)
        if action_type == "report_generate":
            profile = str(cfg.get("profile") or "alert_watch")
            options = cfg.get("options") if isinstance(cfg.get("options"), dict) else {}
            return self.service.reports.generate(profile=profile, options=options)
        if action_type == "scheduler_start":
            return self.service.scheduler.start()
        if action_type == "scheduler_tick":
            return self.service.scheduler.tick_once()
        if action_type == "operation_run":
            kind = str(cfg.get("kind") or "").strip()
            params = cfg.get("params") if isinstance(cfg.get("params"), dict) else {}
            if not kind:
                raise ValueError("operation_run action requires config.kind")
            return self.service.run_operation(kind, params)
        if action_type == "notification_send":
            topic = str(cfg.get("topic") or "remediation.notification").strip()
            message = str(cfg.get("message") or "").strip()
            if not message:
                raise ValueError("notification_send action requires config.message")
            payload = cfg.get("payload") if isinstance(cfg.get("payload"), dict) else {}
            tags = cfg.get("tags") if isinstance(cfg.get("tags"), list) else ["remediation"]
            target_channel_ids = cfg.get("target_channel_ids") if isinstance(cfg.get("target_channel_ids"), list) else None
            return self.service.notifications.dispatch(
                topic=topic,
                message=message,
                payload=payload,
                severity=str(cfg.get("severity") or "info"),
                tags=[str(t) for t in tags],
                target_channel_ids=target_channel_ids,
            )
        if action_type == "health_snapshot":
            profile = str(cfg.get("profile") or "quick")
            options = cfg.get("options") if isinstance(cfg.get("options"), dict) else {}
            return self.service.health.snapshot(profile=profile, options=options)
        if action_type == "runbook_run":
            template = str(cfg.get("template") or cfg.get("name") or "").strip()
            if not template:
                raise ValueError("runbook_run action requires config.template")
            context = cfg.get("context") if isinstance(cfg.get("context"), dict) else {}
            incident_id = str(cfg.get("incident_id") or "").strip() or None
            alert_id = str(cfg.get("alert_id") or "").strip() or None
            if incident_id is None and isinstance(alert, dict):
                try:
                    incident_res = self.service.incidents.handle_alert(alert)
                    incident_id = str((incident_res or {}).get("incident_id") or "").strip() or None
                except Exception:
                    incident_id = None
            dry_run = bool(cfg.get("dry_run", False))
            stop_on_error = cfg.get("stop_on_error")
            if stop_on_error is not None:
                stop_on_error = bool(stop_on_error)
            return self.service.runbooks.run(
                template,
                context=context,
                incident_id=incident_id,
                alert_id=alert_id,
                dry_run=dry_run,
                stop_on_error=stop_on_error,
            )
        raise ValueError(f"unsupported remediation action type: {action_type}")

    def _load_policies_locked(self) -> None:
        with self._lock:
            self._policies = {}
            if not self.policies_file.exists():
                return
            try:
                data = json.loads(self.policies_file.read_text(encoding="utf-8", errors="replace"))
            except Exception:
                return
            items = data.get("policies") if isinstance(data, dict) else None
            if not isinstance(items, list):
                return
            for item in items:
                if not isinstance(item, dict):
                    continue
                pid = str(item.get("id") or "").strip()
                if pid:
                    self._policies[pid] = item

    def _persist_policies_locked(self) -> None:
        rows = sorted(self._policies.values(), key=lambda x: str(x.get("created_at") or ""))
        payload = {"policies": rows, "updated_at": _now_utc().isoformat()}
        self.policies_file.write_text(json.dumps(_jsonable(payload), indent=2, ensure_ascii=False), encoding="utf-8")


def _iso_to_ts(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return datetime.fromisoformat(str(value)).timestamp()
    except Exception:
        return None


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

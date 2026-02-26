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


class AlertEngine:
    def __init__(self, service: "OmniForgeService", root_dir: Path, history: "HistoryStore"):
        self.service = service
        self.root_dir = Path(root_dir).resolve()
        self.history = history
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self.rules_file = self.root_dir / "rules.json"
        self.alerts_file = self.root_dir / "alerts.jsonl"
        self._lock = threading.RLock()
        self._rules: dict[str, dict[str, Any]] = {}
        self._load_rules_locked()

    def status(self) -> dict[str, Any]:
        with self._lock:
            enabled_count = sum(1 for r in self._rules.values() if r.get("enabled"))
        alerts = self.list_alerts(limit=50)
        unacked = sum(1 for a in (alerts.get("items") or []) if not a.get("acknowledged"))
        return {
            "ok": True,
            "rules_file": str(self.rules_file),
            "alerts_file": str(self.alerts_file),
            "rule_count": self.list_rules()["count"],
            "enabled_rule_count": enabled_count,
            "recent_alert_count": alerts.get("count"),
            "recent_unacked_count": unacked,
        }

    def templates(self) -> dict[str, Any]:
        return {
            "ok": True,
            "templates": [
                {
                    "kind": "benchmark_regression",
                    "name": "Vision Benchmark Regression",
                    "severity": "warning",
                    "cooldown_sec": 900,
                    "config": {"suite": "vision_random", "threshold_pct": 15.0, "direction": "increase"},
                },
                {
                    "kind": "job_failure_recent",
                    "name": "Recent Failed Jobs",
                    "severity": "critical",
                    "cooldown_sec": 300,
                    "config": {"lookback_jobs": 20, "min_failures": 1},
                },
                {
                    "kind": "history_event_recent",
                    "name": "Repeated Error Events",
                    "severity": "warning",
                    "cooldown_sec": 300,
                    "config": {"event_type": "job.failed", "lookback_events": 50, "min_count": 1},
                },
                {
                    "kind": "scheduler_not_running",
                    "name": "Scheduler Disabled While Schedules Exist",
                    "severity": "warning",
                    "cooldown_sec": 300,
                    "config": {"require_enabled_schedules": True},
                },
            ],
        }

    def list_rules(self) -> dict[str, Any]:
        with self._lock:
            items = sorted((dict(v) for v in self._rules.values()), key=lambda x: str(x.get("name") or x.get("id")))
        return {"ok": True, "count": len(items), "items": items}

    def get_rule(self, rule_id: str) -> dict[str, Any]:
        rid = str(rule_id or "").strip()
        if not rid:
            raise ValueError("rule_id is required")
        with self._lock:
            row = self._rules.get(rid)
            if row is None:
                raise FileNotFoundError(f"alert rule not found: {rid}")
            return {"ok": True, "rule": dict(row)}

    def create_rule(
        self,
        *,
        kind: str,
        name: str | None = None,
        config: dict[str, Any] | None = None,
        severity: str = "warning",
        enabled: bool = True,
        cooldown_sec: float = 300.0,
    ) -> dict[str, Any]:
        k = str(kind or "").strip()
        if not k:
            raise ValueError("kind is required")
        if k not in {"benchmark_regression", "job_failure_recent", "history_event_recent", "scheduler_not_running"}:
            raise ValueError(f"unsupported alert kind: {k}")
        now = _now_utc()
        rid = f"rule_{now.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
        row = {
            "id": rid,
            "kind": k,
            "name": (str(name).strip() if name else k),
            "config": _jsonable(config or {}),
            "severity": str(severity or "warning"),
            "enabled": bool(enabled),
            "cooldown_sec": float(cooldown_sec),
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
            "last_evaluated_at": None,
            "last_evaluation": None,
            "last_triggered_at": None,
            "last_alert_id": None,
        }
        with self._lock:
            self._rules[rid] = row
            self._persist_rules_locked()
        self.history.record(
            "alert.rule_created",
            {"rule": row},
            source="alerts",
            tags=["alert", "rule"],
            summary={"rule_id": rid, "kind": k, "enabled": bool(enabled)},
        )
        return {"ok": True, "rule": dict(row)}

    def update_rule(self, rule_id: str, **changes: Any) -> dict[str, Any]:
        rid = str(rule_id or "").strip()
        if not rid:
            raise ValueError("rule_id is required")
        with self._lock:
            row = self._rules.get(rid)
            if row is None:
                raise FileNotFoundError(f"alert rule not found: {rid}")
            if "name" in changes and changes["name"] is not None:
                row["name"] = str(changes["name"]).strip() or row["name"]
            if "enabled" in changes and changes["enabled"] is not None:
                row["enabled"] = bool(changes["enabled"])
            if "severity" in changes and changes["severity"] is not None:
                row["severity"] = str(changes["severity"]).strip() or row["severity"]
            if "cooldown_sec" in changes and changes["cooldown_sec"] is not None:
                row["cooldown_sec"] = float(changes["cooldown_sec"])
            if "config" in changes and isinstance(changes["config"], dict):
                row["config"] = _jsonable(changes["config"])
            row["updated_at"] = _now_utc().isoformat()
            self._persist_rules_locked()
            out = dict(row)
        self.history.record(
            "alert.rule_updated",
            {"rule_id": rid, "changes": _jsonable(changes)},
            source="alerts",
            tags=["alert", "rule"],
            summary={"rule_id": rid, "enabled": out.get("enabled")},
        )
        return {"ok": True, "rule": out}

    def delete_rule(self, rule_id: str) -> dict[str, Any]:
        rid = str(rule_id or "").strip()
        if not rid:
            raise ValueError("rule_id is required")
        with self._lock:
            row = self._rules.pop(rid, None)
            if row is None:
                raise FileNotFoundError(f"alert rule not found: {rid}")
            self._persist_rules_locked()
        self.history.record(
            "alert.rule_deleted",
            {"rule_id": rid, "kind": row.get("kind")},
            source="alerts",
            tags=["alert", "rule"],
            summary={"rule_id": rid},
        )
        return {"ok": True, "deleted": rid}

    def evaluate_all(self, *, only_enabled: bool = True, force: bool = False) -> dict[str, Any]:
        with self._lock:
            rules = [dict(r) for r in self._rules.values()]
        if only_enabled:
            rules = [r for r in rules if r.get("enabled")]
        results = []
        triggered = []
        for row in sorted(rules, key=lambda x: str(x.get("name") or x.get("id"))):
            res = self._evaluate_rule(row["id"], force=force)
            results.append(res)
            if (res.get("trigger") or {}).get("triggered"):
                triggered.append(res["trigger"])
        out = {
            "ok": True,
            "evaluated_count": len(results),
            "triggered_count": len(triggered),
            "results": results,
            "triggered": triggered,
        }
        self.history.record(
            "alert.evaluate_run",
            {"evaluated_count": len(results), "triggered_count": len(triggered), "force": bool(force), "only_enabled": bool(only_enabled)},
            source="alerts",
            tags=["alert", "evaluate"],
            summary={"evaluated": len(results), "triggered": len(triggered)},
        )
        return out

    def evaluate_rule(self, rule_id: str, *, force: bool = False) -> dict[str, Any]:
        return self._evaluate_rule(rule_id, force=force)

    def list_alerts(
        self,
        *,
        limit: int = 50,
        rule_id: str | None = None,
        severity: str | None = None,
        acknowledged: bool | None = None,
    ) -> dict[str, Any]:
        items = []
        if self.alerts_file.exists():
            with self._lock:
                lines = self.alerts_file.read_text(encoding="utf-8", errors="replace").splitlines()
            for line in reversed(lines):
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                if rule_id and str(row.get("rule_id")) != str(rule_id):
                    continue
                if severity and str(row.get("severity")) != str(severity):
                    continue
                if acknowledged is not None and bool(row.get("acknowledged")) != bool(acknowledged):
                    continue
                items.append(row)
                if len(items) >= max(1, int(limit)):
                    break
        return {"ok": True, "count": len(items), "items": items}

    def get_alert(self, alert_id: str) -> dict[str, Any]:
        aid = str(alert_id or "").strip()
        if not aid:
            raise ValueError("alert_id is required")
        res = self.list_alerts(limit=10_000)
        for row in res.get("items") or []:
            if str(row.get("id")) == aid:
                return {"ok": True, "alert": row}
        raise FileNotFoundError(f"alert not found: {aid}")

    def ack_alert(self, alert_id: str, *, note: str | None = None) -> dict[str, Any]:
        aid = str(alert_id or "").strip()
        if not aid:
            raise ValueError("alert_id is required")
        with self._lock:
            records = []
            found = None
            if self.alerts_file.exists():
                for line in self.alerts_file.read_text(encoding="utf-8", errors="replace").splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except Exception:
                        continue
                    if str(row.get("id")) == aid:
                        row["acknowledged"] = True
                        row["acknowledged_at"] = _now_utc().isoformat()
                        row["ack_note"] = str(note or "")
                        found = row
                    records.append(row)
            if found is None:
                raise FileNotFoundError(f"alert not found: {aid}")
            self.alerts_file.write_text(
                "\n".join(json.dumps(_jsonable(r), ensure_ascii=False) for r in records) + ("\n" if records else ""),
                encoding="utf-8",
            )
        self.history.record(
            "alert.acknowledged",
            {"alert_id": aid, "rule_id": found.get("rule_id"), "note": str(note or "")},
            source="alerts",
            tags=["alert", "ack"],
            summary={"alert_id": aid, "rule_id": found.get("rule_id")},
        )
        return {"ok": True, "alert": found}

    def _evaluate_rule(self, rule_id: str, *, force: bool = False) -> dict[str, Any]:
        rid = str(rule_id or "").strip()
        if not rid:
            raise ValueError("rule_id is required")
        with self._lock:
            row = self._rules.get(rid)
            if row is None:
                raise FileNotFoundError(f"alert rule not found: {rid}")
            rule = dict(row)
        eval_result = self._compute_rule(rule)
        now = _now_utc()
        trigger = {
            "triggered": False,
            "suppressed": False,
            "cooldown_remaining_sec": None,
            "alert": None,
        }
        if eval_result.get("matches"):
            cooldown_sec = float(rule.get("cooldown_sec") or 0.0)
            last_triggered_ts = _iso_to_ts(rule.get("last_triggered_at"))
            if (
                not force
                and cooldown_sec > 0
                and last_triggered_ts is not None
                and (now.timestamp() - last_triggered_ts) < cooldown_sec
            ):
                trigger["suppressed"] = True
                trigger["cooldown_remaining_sec"] = round(cooldown_sec - (now.timestamp() - last_triggered_ts), 3)
            else:
                alert = self._emit_alert(rule, eval_result)
                trigger["triggered"] = True
                trigger["alert"] = alert
        with self._lock:
            cur = self._rules.get(rid)
            if cur is not None:
                cur["last_evaluated_at"] = now.isoformat()
                cur["last_evaluation"] = _jsonable(eval_result)
                if trigger.get("triggered") and isinstance(trigger.get("alert"), dict):
                    cur["last_triggered_at"] = now.isoformat()
                    cur["last_alert_id"] = trigger["alert"].get("id")
                cur["updated_at"] = now.isoformat()
                self._persist_rules_locked()
                rule_out = dict(cur)
            else:
                rule_out = rule
        return {"ok": True, "rule": rule_out, "evaluation": eval_result, "trigger": trigger}

    def _compute_rule(self, rule: dict[str, Any]) -> dict[str, Any]:
        kind = str(rule.get("kind") or "")
        cfg = dict(rule.get("config") or {})
        if kind == "benchmark_regression":
            return self._eval_benchmark_regression(cfg)
        if kind == "job_failure_recent":
            return self._eval_job_failure_recent(cfg)
        if kind == "history_event_recent":
            return self._eval_history_event_recent(cfg)
        if kind == "scheduler_not_running":
            return self._eval_scheduler_not_running(cfg)
        raise ValueError(f"unsupported alert kind: {kind}")

    def _eval_benchmark_regression(self, cfg: dict[str, Any]) -> dict[str, Any]:
        suite = str(cfg.get("suite") or "vision_random")
        threshold_pct = float(cfg.get("threshold_pct") or 10.0)
        direction = str(cfg.get("direction") or "increase").strip().lower()
        baseline = str(cfg.get("baseline") or "").strip()
        candidate = str(cfg.get("candidate") or "").strip()
        if not baseline or not candidate:
            runs = self.service.benchmark_analytics.list_runs(limit=2).get("items") or []
            if len(runs) < 2:
                return {"ok": True, "matches": False, "message": "not enough benchmark runs", "details": {"suite": suite}}
            baseline = str(runs[1].get("run_id") or "")
            candidate = str(runs[0].get("run_id") or "")
        cmp_res = self.service.benchmark_analytics.compare(baseline, candidate)
        row = None
        for item in cmp_res.get("suite_diffs") or []:
            if str(item.get("suite")) == suite:
                row = item
                break
        if row is None:
            return {
                "ok": True,
                "matches": False,
                "message": f"suite not found in compare: {suite}",
                "details": {"suite": suite, "baseline": baseline, "candidate": candidate},
            }
        delta_pct = row.get("delta_pct")
        if delta_pct is None:
            return {
                "ok": True,
                "matches": False,
                "message": "delta_pct unavailable",
                "details": {"suite": suite, "compare": row},
            }
        val = float(delta_pct)
        if direction == "increase":
            matches = val >= threshold_pct
        elif direction == "decrease":
            matches = val <= -threshold_pct
        elif direction == "abs":
            matches = abs(val) >= threshold_pct
        else:
            raise ValueError(f"unsupported benchmark_regression direction: {direction}")
        return {
            "ok": True,
            "matches": bool(matches),
            "message": f"Benchmark delta_pct for {suite}: {val} (threshold {threshold_pct}, direction {direction})",
            "details": {
                "suite": suite,
                "threshold_pct": threshold_pct,
                "direction": direction,
                "baseline": baseline,
                "candidate": candidate,
                "compare_row": row,
            },
        }

    def _eval_job_failure_recent(self, cfg: dict[str, Any]) -> dict[str, Any]:
        lookback_jobs = max(1, int(cfg.get("lookback_jobs", 20)))
        min_failures = max(1, int(cfg.get("min_failures", 1)))
        kinds = cfg.get("kinds")
        kinds_set = {str(k) for k in kinds} if isinstance(kinds, list) else None
        statuses = cfg.get("statuses")
        status_set = {str(s) for s in statuses} if isinstance(statuses, list) else {"failed"}
        rows = self.service.jobs.list(limit=lookback_jobs).get("items") or []
        failures = []
        for job in rows:
            if str(job.get("status")) not in status_set:
                continue
            if kinds_set and str(job.get("kind")) not in kinds_set:
                continue
            failures.append(job)
        count = len(failures)
        return {
            "ok": True,
            "matches": count >= min_failures,
            "message": f"{count} matching failed jobs in last {lookback_jobs}",
            "details": {"lookback_jobs": lookback_jobs, "min_failures": min_failures, "count": count, "sample": failures[:5]},
        }

    def _eval_history_event_recent(self, cfg: dict[str, Any]) -> dict[str, Any]:
        event_type = str(cfg.get("event_type") or "").strip()
        if not event_type:
            raise ValueError("history_event_recent requires config.event_type")
        lookback_events = max(1, int(cfg.get("lookback_events", 50)))
        min_count = max(1, int(cfg.get("min_count", 1)))
        source = str(cfg.get("source") or "").strip() or None
        rows = self.service.history.list(limit=lookback_events, event_type=event_type).get("items") or []
        if source:
            rows = [r for r in rows if str(r.get("source")) == source]
        count = len(rows)
        return {
            "ok": True,
            "matches": count >= min_count,
            "message": f"{count} {event_type} events in recent {lookback_events}",
            "details": {"event_type": event_type, "lookback_events": lookback_events, "min_count": min_count, "count": count, "sample": rows[:5]},
        }

    def _eval_scheduler_not_running(self, cfg: dict[str, Any]) -> dict[str, Any]:
        require_enabled = bool(cfg.get("require_enabled_schedules", True))
        sched_status = self.service.scheduler.status()
        enabled_count = int(sched_status.get("enabled_count") or 0)
        running = bool(sched_status.get("running"))
        if require_enabled:
            matches = (enabled_count > 0) and not running
        else:
            matches = not running
        return {
            "ok": True,
            "matches": bool(matches),
            "message": f"Scheduler running={running}, enabled_count={enabled_count}",
            "details": {"scheduler_status": sched_status, "require_enabled_schedules": require_enabled},
        }

    def _emit_alert(self, rule: dict[str, Any], eval_result: dict[str, Any]) -> dict[str, Any]:
        now = _now_utc()
        alert = {
            "id": f"alert_{now.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}",
            "created_at": now.isoformat(),
            "rule_id": rule.get("id"),
            "rule_name": rule.get("name"),
            "rule_kind": rule.get("kind"),
            "severity": rule.get("severity") or "warning",
            "message": str(eval_result.get("message") or "alert triggered"),
            "details": _jsonable(eval_result.get("details") or {}),
            "evaluation": _jsonable(eval_result),
            "acknowledged": False,
            "acknowledged_at": None,
            "ack_note": "",
        }
        with self._lock:
            with self.alerts_file.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(_jsonable(alert), ensure_ascii=False))
                fh.write("\n")
        self.history.record(
            "alert.triggered",
            {"alert": alert},
            source="alerts",
            tags=["alert", str(alert.get("severity") or "warning")],
            summary={
                "alert_id": alert["id"],
                "rule_id": alert.get("rule_id"),
                "severity": alert.get("severity"),
                "message": str(alert.get("message") or "")[:240],
            },
        )
        try:
            remediations = getattr(self.service, "remediations", None)
            if remediations is not None:
                alert["remediation"] = remediations.handle_alert(alert)
        except Exception as exc:
            # Keep alert emission resilient even if remediations fail.
            alert["remediation_error"] = str(exc)
            try:
                self.history.record(
                    "remediation.handle_alert_error",
                    {"alert_id": alert.get("id"), "error": str(exc)},
                    source="remediations",
                    tags=["remediation", "error"],
                    summary={"alert_id": alert.get("id"), "error": str(exc)},
                )
            except Exception:
                pass
        try:
            notifications = getattr(self.service, "notifications", None)
            if notifications is not None:
                alert["notification"] = notifications.notify_alert(alert)
        except Exception as exc:
            # Notifications are best-effort; do not fail alerting if delivery is broken.
            alert["notification_error"] = str(exc)
            try:
                self.history.record(
                    "notification.alert_dispatch_error",
                    {"alert_id": alert.get("id"), "error": str(exc)},
                    source="notifications",
                    tags=["notification", "error"],
                    summary={"alert_id": alert.get("id"), "error": str(exc)},
                )
            except Exception:
                pass
        try:
            incidents = getattr(self.service, "incidents", None)
            if incidents is not None:
                alert["incident"] = incidents.handle_alert(alert)
        except Exception as exc:
            # Incident automation is best-effort.
            alert["incident_error"] = str(exc)
            try:
                self.history.record(
                    "incident.handle_alert_error",
                    {"alert_id": alert.get("id"), "error": str(exc)},
                    source="incidents",
                    tags=["incident", "error"],
                    summary={"alert_id": alert.get("id"), "error": str(exc)},
                )
            except Exception:
                pass
        return alert

    def _load_rules_locked(self) -> None:
        with self._lock:
            self._rules = {}
            if not self.rules_file.exists():
                return
            try:
                data = json.loads(self.rules_file.read_text(encoding="utf-8", errors="replace"))
            except Exception:
                return
            items = data.get("rules") if isinstance(data, dict) else None
            if not isinstance(items, list):
                return
            for item in items:
                if not isinstance(item, dict):
                    continue
                rid = str(item.get("id") or "").strip()
                if rid:
                    self._rules[rid] = item

    def _persist_rules_locked(self) -> None:
        rows = sorted(self._rules.values(), key=lambda x: str(x.get("created_at") or ""))
        payload = {"rules": rows, "updated_at": _now_utc().isoformat()}
        self.rules_file.write_text(json.dumps(_jsonable(payload), indent=2, ensure_ascii=False), encoding="utf-8")


def _iso_to_ts(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return datetime.fromisoformat(str(value)).timestamp()
    except Exception:
        return None


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

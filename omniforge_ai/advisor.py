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


class SmartOpsAdvisor:
    def __init__(self, service: "OmniForgeService", root_dir: Path, history: "HistoryStore"):
        self.service = service
        self.root_dir = Path(root_dir).resolve()
        self.history = history
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self.analyses_dir = self.root_dir / "analyses"
        self.analyses_dir.mkdir(parents=True, exist_ok=True)
        self.index_file = self.root_dir / "index.jsonl"
        self._lock = threading.RLock()

    def status(self) -> dict[str, Any]:
        items = self.list(limit=200).get("items") or []
        return {
            "ok": True,
            "root_dir": str(self.root_dir),
            "analyses_dir": str(self.analyses_dir),
            "index_file": str(self.index_file),
            "analysis_count": len(items),
            "latest_analysis_id": (items[0].get("analysis_id") if items else None),
            "latest_risk_score": (items[0].get("risk_score") if items else None),
        }

    def profiles(self) -> dict[str, Any]:
        return {
            "ok": True,
            "profiles": {
                "quick": {
                    "description": "Fast operational analysis with actionable recommendations.",
                    "options": {"include_incidents": True, "include_trends": False, "max_items": 20},
                },
                "incident_triage": {
                    "description": "Incident-focused analysis; pass incident_id for tailored triage recommendations.",
                    "options": {"include_incidents": True, "incident_bundle_hint": True, "max_items": 50},
                },
                "hardening": {
                    "description": "Configuration/automation hardening suggestions (alerts, notifications, scheduler, remediations).",
                    "options": {"include_incidents": True, "max_items": 30, "hardening_bias": True},
                },
            },
        }

    def list(self, *, limit: int = 50) -> dict[str, Any]:
        rows = []
        if self.index_file.exists():
            with self._lock:
                for line in self.index_file.read_text(encoding="utf-8", errors="replace").splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except Exception:
                        continue
                    rows.append(row)
        rows = list(reversed(rows))[: max(1, int(limit))]
        return {"ok": True, "count": len(rows), "items": rows}

    def get(self, analysis_id: str) -> dict[str, Any]:
        aid = str(analysis_id or "").strip()
        if not aid:
            raise ValueError("analysis_id is required")
        path = self.analyses_dir / f"{aid}.json"
        if not path.exists():
            raise FileNotFoundError(f"advisor analysis not found: {aid}")
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        return {"ok": True, "analysis": data, "file": str(path)}

    def analyze(
        self,
        *,
        profile: str = "quick",
        incident_id: str | None = None,
        options: dict[str, Any] | None = None,
        persist: bool = True,
    ) -> dict[str, Any]:
        profile = str(profile or "quick").strip() or "quick"
        profiles = self.profiles()["profiles"]
        if profile not in profiles:
            raise ValueError(f"unknown advisor profile: {profile}")
        merged_options = dict(profiles[profile].get("options") or {})
        if isinstance(options, dict):
            merged_options.update(options)

        max_items = max(1, int(merged_options.get("max_items", 20)))
        health_latest = self.service.health.latest(optional=True)
        health_snapshot = (health_latest.get("snapshot") or {}) if isinstance(health_latest, dict) else {}
        health_summary = dict(health_snapshot.get("summary") or {})
        scheduler = self.service.scheduler.status()
        alerts_status = self.service.alerts.status()
        alerts = self.service.alerts.list_alerts(limit=max_items)
        remediations_status = self.service.remediations.status()
        rem_actions = self.service.remediations.list_actions(limit=max_items)
        notifications_status = self.service.notifications.status()
        jobs = self.service.jobs.list(limit=max_items)
        incidents_status = self.service.incidents.status()
        incidents = self.service.incidents.list(limit=max_items)

        incident = None
        incident_timeline = None
        incident_id = (str(incident_id).strip() if incident_id else None) or None
        if incident_id:
            inc_get = self.service.incidents.get(incident_id, timeline_limit=max_items)
            incident = inc_get.get("incident")
            incident_timeline = inc_get.get("timeline")

        findings: list[dict[str, Any]] = []
        recommendations: list[dict[str, Any]] = []

        self._analyze_scheduler(scheduler, findings, recommendations)
        self._analyze_alerts(alerts_status, alerts, findings, recommendations)
        self._analyze_jobs(jobs, findings, recommendations)
        self._analyze_remediations(remediations_status, rem_actions, findings, recommendations)
        self._analyze_notifications(notifications_status, findings, recommendations, profile=profile)
        self._analyze_health(health_summary, findings, recommendations)
        self._analyze_incidents(incidents_status, incidents, findings, recommendations)
        if incident:
            self._analyze_target_incident(incident, incident_timeline or {}, findings, recommendations)

        if profile == "hardening":
            self._hardening_recommendations(findings, recommendations)

        findings = self._dedupe_findings(findings)
        recommendations = self._dedupe_recommendations(recommendations)
        recommendations.sort(key=lambda x: (int(x.get("priority_score") or 0) * -1, str(x.get("kind") or "")))

        risk_score = self._compute_risk_score(findings, health_summary)
        verdict = self._risk_level(risk_score)
        analysis = {
            "ok": True,
            "analysis_id": self._new_analysis_id(),
            "created_at": _now_utc().isoformat(),
            "profile": profile,
            "incident_id": incident_id,
            "options": _jsonable(merged_options),
            "summary": {
                "risk_score": risk_score,
                "risk_level": verdict,
                "finding_count": len(findings),
                "recommendation_count": len(recommendations),
                "health_score": health_summary.get("score"),
                "open_incidents": incidents_status.get("open_count"),
                "recent_unacked_alerts": alerts_status.get("recent_unacked_count"),
            },
            "context": {
                "health_latest": _shallow_health(health_snapshot),
                "scheduler": _shallow_scheduler(scheduler),
                "alerts_status": alerts_status,
                "remediations_status": remediations_status,
                "notifications_status": notifications_status,
                "incidents_status": incidents_status,
                "jobs_summary": _jobs_summary(jobs),
                "target_incident": _incident_brief(incident) if isinstance(incident, dict) else None,
            },
            "findings": findings,
            "recommendations": recommendations,
        }
        if incident_timeline is not None:
            analysis["context"]["target_incident_timeline"] = incident_timeline

        path = None
        if persist:
            path = self._persist(analysis)
            self.history.record(
                "advisor.analysis",
                {
                    "analysis_id": analysis["analysis_id"],
                    "profile": profile,
                    "incident_id": incident_id,
                    "summary": analysis["summary"],
                },
                source="advisor",
                tags=["advisor", verdict],
                summary={"analysis_id": analysis["analysis_id"], "risk_score": risk_score, "risk_level": verdict},
            )
        result = {"ok": True, "analysis": analysis}
        if path is not None:
            result["file"] = str(path)
        return result

    def doctor(self, *, incident_id: str | None = None) -> dict[str, Any]:
        return self.analyze(profile="quick", incident_id=incident_id, options={"doctor": True}, persist=True)

    def _analyze_scheduler(self, scheduler: dict[str, Any], findings: list[dict[str, Any]], recs: list[dict[str, Any]]) -> None:
        running = bool(scheduler.get("running"))
        enabled = int(scheduler.get("enabled_count") or 0)
        if enabled > 0 and not running:
            findings.append(
                _finding(
                    "scheduler_stopped_with_work",
                    "warning",
                    "Scheduler is not running while enabled schedules exist",
                    {"scheduler": _shallow_scheduler(scheduler)},
                )
            )
            recs.append(_rec(90, "scheduler.start", {}, "Start scheduler to process enabled schedules"))

    def _analyze_alerts(
        self,
        alerts_status: dict[str, Any],
        alerts: dict[str, Any],
        findings: list[dict[str, Any]],
        recs: list[dict[str, Any]],
    ) -> None:
        rows = (alerts.get("items") or [])
        critical_unacked = [a for a in rows if (not a.get("acknowledged")) and str(a.get("severity")) == "critical"]
        unacked = [a for a in rows if not a.get("acknowledged")]
        if critical_unacked:
            findings.append(
                _finding(
                    "critical_unacked_alerts",
                    "critical",
                    f"{len(critical_unacked)} unacknowledged critical alert(s)",
                    {"sample_alerts": [_alert_brief(a) for a in critical_unacked[:5]]},
                )
            )
            recs.append(_rec(100, "report.generate", {"profile": "alert_watch"}, "Generate alert watch report"))
            recs.append(_rec(95, "incidents.list", {"severity": "critical", "status": "open", "limit": 20}, "Review open critical incidents"))
            recs.append(_rec(94, "runbooks.run", {"template": "ops_baseline_refresh", "dry_run": True}, "Preview the ops baseline refresh playbook"))
        elif unacked:
            findings.append(
                _finding(
                    "unacked_alerts",
                    "warning",
                    f"{len(unacked)} unacknowledged alert(s)",
                    {"sample_alerts": [_alert_brief(a) for a in unacked[:5]]},
                )
            )
            recs.append(_rec(70, "alerts.list", {"acknowledged": False, "limit": 20}, "Review unacknowledged alerts"))

        if int(alerts_status.get("rule_count") or 0) == 0:
            findings.append(
                _finding(
                    "no_alert_rules",
                    "warning",
                    "No alert rules are configured",
                    {"alerts_status": alerts_status},
                )
            )
            recs.append(_rec(65, "alerts.templates", {}, "Review alert templates and create baseline rules"))

    def _analyze_jobs(self, jobs: dict[str, Any], findings: list[dict[str, Any]], recs: list[dict[str, Any]]) -> None:
        rows = (jobs.get("items") or [])
        failed = [j for j in rows if str(j.get("status")) == "failed"]
        if failed:
            findings.append(
                _finding(
                    "recent_job_failures",
                    "warning",
                    f"{len(failed)} recent failed job(s)",
                    {"sample_jobs": [_job_brief(j) for j in failed[:5]]},
                )
            )
            recs.append(_rec(75, "jobs.list", {"status": "failed", "limit": 20}, "Inspect failed jobs"))

    def _analyze_remediations(
        self,
        rem_status: dict[str, Any],
        rem_actions: dict[str, Any],
        findings: list[dict[str, Any]],
        recs: list[dict[str, Any]],
    ) -> None:
        rows = (rem_actions.get("items") or [])
        fails = [a for a in rows if not a.get("ok")]
        if fails:
            findings.append(
                _finding(
                    "remediation_failures",
                    "warning",
                    f"{len(fails)} recent remediation action failure(s)",
                    {"sample_actions": [self._action_brief(a) for a in fails[:5]], "remediations_status": rem_status},
                )
            )
            recs.append(_rec(80, "remediations.actions.list", {"ok": False, "limit": 20}, "Inspect remediation action failures"))

    def _analyze_notifications(
        self,
        notif_status: dict[str, Any],
        findings: list[dict[str, Any]],
        recs: list[dict[str, Any]],
        *,
        profile: str,
    ) -> None:
        count = int(notif_status.get("channel_count") or 0)
        if count == 0:
            sev = "warning" if profile in {"hardening", "incident_triage"} else "info"
            findings.append(
                _finding(
                    "no_notification_channels",
                    sev,
                    "No notification channels configured (alerts are local-only)",
                    {"notifications_status": notif_status},
                )
            )
            recs.append(
                _rec(
                    60,
                    "notifications.channels.create",
                    {
                        "type": "file_append",
                        "name": "Critical Alert Log",
                        "match": {"topic": "alert.triggered", "severity": "critical"},
                        "config": {"path": "critical_alerts.jsonl", "format": "jsonl"},
                    },
                    "Create a local file notification channel for critical alerts",
                )
            )
        elif int(notif_status.get("recent_failure_count") or 0) > 0:
            findings.append(
                _finding(
                    "notification_delivery_failures",
                    "warning",
                    "Recent notification delivery failures detected",
                    {"notifications_status": notif_status},
                )
            )
            recs.append(_rec(70, "notifications.deliveries.list", {"ok": False, "limit": 20}, "Inspect notification delivery failures"))

    def _analyze_health(self, health_summary: dict[str, Any], findings: list[dict[str, Any]], recs: list[dict[str, Any]]) -> None:
        if not health_summary:
            findings.append(_finding("no_health_snapshot", "warning", "No health snapshot available", {}))
            recs.append(_rec(75, "health.snapshot", {"profile": "full", "options": {"notify": False}}, "Capture a health snapshot"))
            return
        score = health_summary.get("score")
        level = str(health_summary.get("level") or "unknown")
        try:
            score_f = float(score)
        except Exception:
            score_f = None
        if score_f is not None and score_f < 85:
            sev = "critical" if score_f < 65 else "warning"
            findings.append(
                _finding(
                    "health_degraded",
                    sev,
                    f"Health score is degraded ({score_f}, {level})",
                    {"health_summary": _jsonable(health_summary)},
                )
            )
            recs.append(_rec(88, "report.generate", {"profile": "health_watch"}, "Generate health watch report"))
            recs.append(_rec(82, "alerts.evaluate", {"only_enabled": True, "force": True}, "Re-evaluate alerts after health degradation"))

    def _analyze_incidents(
        self,
        incidents_status: dict[str, Any],
        incidents: dict[str, Any],
        findings: list[dict[str, Any]],
        recs: list[dict[str, Any]],
    ) -> None:
        rows = (incidents.get("items") or [])
        open_rows = [i for i in rows if str(i.get("status")) != "closed"]
        critical_open = [i for i in open_rows if str(i.get("severity")) == "critical"]
        if critical_open:
            findings.append(
                _finding(
                    "critical_open_incidents",
                    "critical",
                    f"{len(critical_open)} open critical incident(s)",
                    {"sample_incidents": [_incident_brief(i) for i in critical_open[:5]], "incidents_status": incidents_status},
                )
            )
            recs.append(_rec(98, "report.generate", {"profile": "incident_watch"}, "Generate incident watch report"))
            recs.append(_rec(92, "incidents.list", {"severity": "critical", "status": "open", "limit": 20}, "Review open critical incidents"))
            first_id = str((critical_open[0] or {}).get("id") or "")
            if first_id:
                recs.append(
                    _rec(
                        91,
                        "runbooks.run",
                        {"template": "critical_alert_triage", "incident_id": first_id, "dry_run": True},
                        "Preview the critical incident triage runbook for an open critical incident",
                    )
                )
        elif open_rows:
            findings.append(
                _finding(
                    "open_incidents_present",
                    "warning",
                    f"{len(open_rows)} open incident(s) require attention",
                    {"sample_incidents": [_incident_brief(i) for i in open_rows[:5]]},
                )
            )

    def _analyze_target_incident(
        self,
        incident: dict[str, Any],
        timeline: dict[str, Any],
        findings: list[dict[str, Any]],
        recs: list[dict[str, Any]],
    ) -> None:
        iid = str(incident.get("id") or "")
        status = str(incident.get("status") or "")
        linked_reports = list(incident.get("linked_report_ids") or [])
        linked_health = list(incident.get("linked_health_snapshot_ids") or [])
        timeline_count = int(incident.get("timeline_count") or 0)
        if status != "closed" and (not linked_reports or not linked_health):
            findings.append(
                _finding(
                    "incident_missing_bundle",
                    "warning",
                    "Target incident is open but missing linked report and/or health snapshot",
                    {"incident": _incident_brief(incident)},
                )
            )
            recs.append(
                _rec(
                    93,
                    "incidents.capture_bundle",
                    {"incident_id": iid, "health_profile": "full", "report_profile": "incident_watch"},
                    "Capture a diagnostic bundle for the target incident",
                )
            )
            recs.append(
                _rec(
                    92,
                    "runbooks.run",
                    {"template": "incident_bundle_refresh", "incident_id": iid, "dry_run": True},
                    "Preview the incident bundle refresh runbook for the target incident",
                )
            )
            recs.append(
                _rec(
                    91,
                    "opsgraph.explain_incident",
                    {"incident_id": iid, "depth": 2},
                    "Generate a graph-based RCA summary for the target incident",
                )
            )
        if status in {"open", "investigating"} and timeline_count < 2:
            findings.append(
                _finding(
                    "incident_low_timeline_activity",
                    "info",
                    "Target incident has little timeline activity",
                    {"incident": _incident_brief(incident), "timeline": timeline},
                )
            )
            recs.append(
                _rec(
                    55,
                    "incidents.note",
                    {"incident_id": iid, "note": "Advisor: add triage note with current hypothesis and next step"},
                    "Add a triage note to improve incident traceability",
                )
            )
        if status in {"open", "investigating", "mitigated"}:
            recs.append(
                _rec(
                    58,
                    "opsgraph.trace",
                    {"node_id": f"incident:{iid}", "depth": 2},
                    "Inspect the incident neighborhood (alerts, jobs, remediations, runbooks)",
                )
            )

    def _hardening_recommendations(self, findings: list[dict[str, Any]], recs: list[dict[str, Any]]) -> None:
        recs.append(_rec(50, "report.generate", {"profile": "ops_digest"}, "Generate an ops digest baseline"))
        recs.append(_rec(52, "health.snapshot", {"profile": "full", "options": {"notify": False}}, "Capture a fresh health baseline snapshot"))
        recs.append(_rec(54, "runbooks.run", {"template": "ops_baseline_refresh", "dry_run": True}, "Preview the baseline refresh runbook"))

    @staticmethod
    def _action_brief(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": row.get("id"),
            "created_at": row.get("created_at"),
            "policy_id": row.get("policy_id"),
            "policy_name": row.get("policy_name"),
            "alert_id": row.get("alert_id"),
            "ok": row.get("ok"),
        }

    def _compute_risk_score(self, findings: list[dict[str, Any]], health_summary: dict[str, Any]) -> float:
        severity_penalty = {"critical": 25.0, "warning": 10.0, "info": 2.0}
        penalty = 0.0
        for f in findings:
            sev = str(f.get("severity") or "info")
            penalty += severity_penalty.get(sev, 2.0)
        try:
            health_score = float(health_summary.get("score"))
        except Exception:
            health_score = None
        base = (health_score if health_score is not None else 90.0)
        risk_score = max(0.0, min(100.0, round(100.0 - (base * 0.4) + penalty, 2)))
        return risk_score

    @staticmethod
    def _risk_level(score: float) -> str:
        if score >= 70:
            return "critical"
        if score >= 40:
            return "warning"
        return "healthy"

    @staticmethod
    def _dedupe_findings(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        seen = set()
        out = []
        for row in items:
            key = (str(row.get("id")), str(row.get("severity")), str(row.get("title")))
            if key in seen:
                continue
            seen.add(key)
            out.append(row)
        return out

    @staticmethod
    def _dedupe_recommendations(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        seen = set()
        out = []
        for row in items:
            kind = str(row.get("kind") or "")
            params = json.dumps(row.get("params") or {}, sort_keys=True, ensure_ascii=False)
            key = (kind, params)
            if key in seen:
                continue
            seen.add(key)
            out.append(row)
        return out

    def _persist(self, analysis: dict[str, Any]) -> Path:
        aid = str(analysis.get("analysis_id") or "").strip()
        if not aid:
            raise ValueError("analysis missing analysis_id")
        path = self.analyses_dir / f"{aid}.json"
        with self._lock:
            path.write_text(json.dumps(_jsonable(analysis), indent=2, ensure_ascii=False), encoding="utf-8")
            idx = {
                "analysis_id": aid,
                "created_at": analysis.get("created_at"),
                "profile": analysis.get("profile"),
                "incident_id": analysis.get("incident_id"),
                "risk_score": ((analysis.get("summary") or {}).get("risk_score")),
                "risk_level": ((analysis.get("summary") or {}).get("risk_level")),
                "finding_count": ((analysis.get("summary") or {}).get("finding_count")),
                "recommendation_count": ((analysis.get("summary") or {}).get("recommendation_count")),
                "file": str(path),
            }
            with self.index_file.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(idx, ensure_ascii=False))
                fh.write("\n")
        return path

    @staticmethod
    def _new_analysis_id() -> str:
        now = _now_utc()
        return f"adv_{now.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"


def _finding(fid: str, severity: str, title: str, details: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(fid),
        "severity": str(severity),
        "title": str(title),
        "details": _jsonable(details),
    }


def _rec(priority_score: int, kind: str, params: dict[str, Any], reason: str) -> dict[str, Any]:
    params = _jsonable(params) if isinstance(params, dict) else {}
    return {
        "priority_score": int(priority_score),
        "kind": str(kind),
        "params": params,
        "reason": str(reason),
        "cli_command": _op_to_cli(str(kind), params if isinstance(params, dict) else {}),
    }


def _op_to_cli(kind: str, params: dict[str, Any]) -> str:
    return f"python -m omniforge_ai op-run {kind} --params-json {json.dumps(params, ensure_ascii=False)}"


def _shallow_health(snapshot: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(snapshot, dict):
        return {}
    return {
        "snapshot_id": snapshot.get("snapshot_id"),
        "profile": snapshot.get("profile"),
        "created_at": snapshot.get("created_at"),
        "summary": _jsonable(snapshot.get("summary") or {}),
    }


def _shallow_scheduler(scheduler: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(scheduler, dict):
        return {}
    return {
        "running": scheduler.get("running"),
        "enabled_count": scheduler.get("enabled_count"),
        "schedule_count": scheduler.get("schedule_count"),
        "last_tick_at": scheduler.get("last_tick_at"),
    }


def _jobs_summary(jobs: dict[str, Any]) -> dict[str, Any]:
    rows = (jobs.get("items") or []) if isinstance(jobs, dict) else []
    status_counts: dict[str, int] = {}
    for row in rows:
        st = str((row or {}).get("status") or "unknown")
        status_counts[st] = status_counts.get(st, 0) + 1
    return {"count": len(rows), "status_counts": status_counts}


def _alert_brief(alert: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": alert.get("id"),
        "created_at": alert.get("created_at"),
        "severity": alert.get("severity"),
        "rule_id": alert.get("rule_id"),
        "rule_kind": alert.get("rule_kind"),
        "rule_name": alert.get("rule_name"),
        "acknowledged": alert.get("acknowledged"),
        "message": str(alert.get("message") or "")[:200],
    }


def _job_brief(job: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": job.get("id"),
        "kind": job.get("kind"),
        "status": job.get("status"),
        "created_at": job.get("created_at"),
        "duration_ms": job.get("duration_ms"),
        "error": str(job.get("error") or "")[:200],
    }


def _incident_brief(incident: Any) -> dict[str, Any]:
    if not isinstance(incident, dict):
        return {}
    return {
        "id": incident.get("id"),
        "title": incident.get("title"),
        "severity": incident.get("severity"),
        "status": incident.get("status"),
        "source": incident.get("source"),
        "created_at": incident.get("created_at"),
        "updated_at": incident.get("updated_at"),
        "linked_alert_count": len(incident.get("linked_alert_ids") or []),
        "linked_report_count": len(incident.get("linked_report_ids") or []),
        "linked_health_snapshot_count": len(incident.get("linked_health_snapshot_ids") or []),
        "timeline_count": incident.get("timeline_count"),
    }


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

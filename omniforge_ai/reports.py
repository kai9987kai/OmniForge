from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .history_store import HistoryStore
    from .service import OmniForgeService


class IntelligenceReportBuilder:
    def __init__(self, service: "OmniForgeService", reports_dir: Path, history: "HistoryStore"):
        self.service = service
        self.reports_dir = Path(reports_dir).resolve()
        self.history = history
        self.reports_dir.mkdir(parents=True, exist_ok=True)

    def profiles(self) -> dict[str, Any]:
        return {
            "ok": True,
            "profiles": {
                "ops_digest": {
                    "description": "Operational digest: status, jobs, recent events, latest benchmarks and trends.",
                },
                "benchmark_watch": {
                    "description": "Benchmark-focused snapshot with trends and optional latest run comparison.",
                },
                "mission_brief": {
                    "description": "Mission-centric brief including mission catalog and recent mission runs.",
                },
                "alert_watch": {
                    "description": "Alert-centric watch report with rules, recent alerts, and scheduler status.",
                },
                "health_watch": {
                    "description": "Health-centric runtime snapshot with scored components, notifications, and alert/remediation context.",
                },
                "incident_watch": {
                    "description": "Incident-centric report with open incidents, recent timelines, alerts, and supporting health/report context.",
                },
                "runbook_watch": {
                    "description": "Runbook-centric report with template inventory, recent runs, incidents, and advisor context.",
                },
            },
        }

    def list(self, *, limit: int = 50) -> dict[str, Any]:
        items = []
        for p in sorted(self.reports_dir.glob("report_*.json"), reverse=True):
            try:
                data = json.loads(p.read_text(encoding="utf-8", errors="replace"))
            except Exception:
                continue
            items.append(
                {
                    "report_id": data.get("report_id") or p.stem,
                    "profile": data.get("profile"),
                    "created_at": data.get("created_at"),
                    "title": data.get("title"),
                    "json_file": str(p),
                    "markdown_file": str((p.with_suffix(".md"))) if p.with_suffix(".md").exists() else None,
                }
            )
            if len(items) >= max(1, int(limit)):
                break
        return {"ok": True, "count": len(items), "items": items}

    def generate(self, *, profile: str = "ops_digest", options: dict[str, Any] | None = None) -> dict[str, Any]:
        profile = str(profile or "ops_digest").strip() or "ops_digest"
        if profile not in self.profiles()["profiles"]:
            raise ValueError(f"unknown report profile: {profile}")
        options = dict(options or {})
        report_id = self._new_report_id()
        created_at = datetime.now(timezone.utc).isoformat()

        payload = self._build_payload(profile, options)
        title = payload.get("title") or f"OmniForge Report ({profile})"
        report = {
            "ok": True,
            "report_id": report_id,
            "profile": profile,
            "created_at": created_at,
            "title": title,
            "options": options,
            "payload": payload,
        }

        json_path = self.reports_dir / f"{report_id}.json"
        md_path = self.reports_dir / f"{report_id}.md"
        json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        md_text = self._render_markdown(report)
        md_path.write_text(md_text, encoding="utf-8")

        self.history.record(
            "report.generated",
            {
                "report_id": report_id,
                "profile": profile,
                "json_file": str(json_path),
                "markdown_file": str(md_path),
            },
            source="reports",
            tags=["report"],
            summary={"report_id": report_id, "profile": profile},
        )
        return {
            "ok": True,
            "report_id": report_id,
            "profile": profile,
            "created_at": created_at,
            "title": title,
            "json_file": str(json_path),
            "markdown_file": str(md_path),
            "markdown_preview": md_text[:1500],
            "payload_summary": payload.get("summary"),
        }

    def _build_payload(self, profile: str, options: dict[str, Any]) -> dict[str, Any]:
        hist_limit = int(options.get("history_limit", 20))
        job_limit = int(options.get("job_limit", 20))
        bench_limit = int(options.get("benchmark_limit", 10))
        alert_limit = int(options.get("alert_limit", 20))
        status = self.service.status()
        jobs = self.service.jobs.list(limit=job_limit)
        history_items = self.service.history.list(limit=hist_limit)
        benchmarks = self.service.benchmark_analytics.list_runs(limit=bench_limit)
        trends = self.service.benchmark_analytics.trends(
            suite=str(options.get("trend_suite") or "vision_random"),
            metric=str(options.get("trend_metric") or "infer_ms_stats.avg"),
            limit=int(options.get("trend_limit", 20)),
        )
        missions = self.service.missions.catalog()
        alerts_status = self.service.alerts.status()
        alert_rules = self.service.alerts.list_rules()
        alert_events = self.service.alerts.list_alerts(limit=alert_limit)
        remediations_status = self.service.remediations.status()
        remediation_actions = self.service.remediations.list_actions(limit=alert_limit)
        notifications_status = self.service.notifications.status()
        notification_deliveries = self.service.notifications.list_deliveries(limit=alert_limit)
        health_status = self.service.health.status()
        health_latest = self.service.health.latest(optional=True)
        incidents_status = self.service.incidents.status()
        incidents = self.service.incidents.list(limit=alert_limit)
        incident_timeline = self.service.incidents.timeline(limit=alert_limit)
        runbooks_status = self.service.runbooks.status()
        runbook_templates = self.service.runbooks.list_templates(include_steps=False)
        runbook_runs = self.service.runbooks.list_runs(limit=alert_limit)

        compare_latest = None
        bench_items = benchmarks.get("items") or []
        if len(bench_items) >= 2 and profile in {"ops_digest", "benchmark_watch"}:
            try:
                compare_latest = self.service.benchmark_analytics.compare(
                    str(bench_items[1].get("run_id") or ""),
                    str(bench_items[0].get("run_id") or ""),
                )
            except Exception as exc:
                compare_latest = {"ok": False, "error": str(exc)}

        if profile == "mission_brief":
            history_items = self.service.history.list(limit=hist_limit, event_type="mission.run")
        if profile == "alert_watch":
            history_items = self.service.history.list(limit=hist_limit, event_type="alert.triggered")
        if profile == "health_watch":
            history_items = self.service.history.list(limit=hist_limit, event_type="health.snapshot")
        if profile == "incident_watch":
            history_items = self.service.history.list(limit=hist_limit, event_type="incident.created")
        if profile == "runbook_watch":
            history_items = self.service.history.list(limit=hist_limit, event_type="runbook.run_completed")

        title = f"OmniForge {profile.replace('_', ' ').title()} Report"
        summary = {
            "history_events": history_items.get("count"),
            "jobs": jobs.get("count"),
            "benchmark_runs": benchmarks.get("count"),
            "mission_count": len(missions.get("missions") or []),
            "scheduler_running": self.service.scheduler.status().get("running"),
            "alert_rules": alert_rules.get("count"),
            "recent_alerts": alert_events.get("count"),
            "recent_unacked_alerts": sum(1 for a in (alert_events.get("items") or []) if not a.get("acknowledged")),
            "remediation_policies": remediations_status.get("policy_count"),
            "recent_remediation_actions": remediation_actions.get("count"),
            "notification_channels": notifications_status.get("channel_count"),
            "recent_notification_deliveries": notification_deliveries.get("count"),
            "latest_health_score": ((health_latest.get("snapshot") or {}).get("summary") or {}).get("score") if isinstance(health_latest, dict) else None,
            "latest_health_level": ((health_latest.get("snapshot") or {}).get("summary") or {}).get("level") if isinstance(health_latest, dict) else None,
            "open_incidents": incidents_status.get("open_count"),
            "recent_incidents": incidents.get("count"),
            "runbook_templates": runbooks_status.get("template_count"),
            "recent_runbook_runs": runbook_runs.get("count"),
        }
        return {
            "title": title,
            "summary": summary,
            "status": status,
            "scheduler": self.service.scheduler.status(),
            "jobs": jobs,
            "history": history_items,
            "benchmarks": benchmarks,
            "benchmark_trends": trends,
            "benchmark_compare_latest": compare_latest,
            "missions": missions,
            "alerts_status": alerts_status,
            "alert_rules": alert_rules,
            "alert_events": alert_events,
            "remediations_status": remediations_status,
            "remediation_actions": remediation_actions,
            "notifications_status": notifications_status,
            "notification_deliveries": notification_deliveries,
            "health_status": health_status,
            "health_latest": health_latest,
            "incidents_status": incidents_status,
            "incidents": incidents,
            "incident_timeline": incident_timeline,
            "runbooks_status": runbooks_status,
            "runbook_templates": runbook_templates,
            "runbook_runs": runbook_runs,
        }

    @staticmethod
    def _render_markdown(report: dict[str, Any]) -> str:
        payload = report.get("payload") or {}
        status = payload.get("status") or {}
        runtime = status.get("runtime") or {}
        summary = payload.get("summary") or {}
        lines: list[str] = []
        lines.append(f"# {report.get('title')}")
        lines.append("")
        lines.append(f"- Report ID: `{report.get('report_id')}`")
        lines.append(f"- Profile: `{report.get('profile')}`")
        lines.append(f"- Created: `{report.get('created_at')}`")
        lines.append("")
        lines.append("## Summary")
        lines.append("")
        for k, v in summary.items():
            lines.append(f"- {k}: `{v}`")
        lines.append("")
        lines.append("## Runtime")
        lines.append("")
        lines.append(f"- History entries: `{((runtime.get('history') or {}).get('entry_count'))}`")
        lines.append(f"- Benchmark runs tracked: `{runtime.get('benchmark_runs')}`")
        lines.append(f"- Search roots: `{', '.join(runtime.get('search_roots') or [])}`")
        lines.append(f"- Mission catalog: `{', '.join(runtime.get('mission_catalog') or [])}`")
        lines.append("")

        alerts_status = payload.get("alerts_status") or {}
        lines.append("## Alerts")
        lines.append("")
        lines.append(f"- Rule count: `{alerts_status.get('rule_count')}`")
        lines.append(f"- Enabled rules: `{alerts_status.get('enabled_rule_count')}`")
        lines.append(f"- Recent alerts: `{alerts_status.get('recent_alert_count')}`")
        lines.append(f"- Recent unacked alerts: `{alerts_status.get('recent_unacked_count')}`")
        lines.append("")

        rem_status = payload.get("remediations_status") or {}
        lines.append("## Remediations")
        lines.append("")
        lines.append(f"- Policy count: `{rem_status.get('policy_count')}`")
        lines.append(f"- Enabled policies: `{rem_status.get('enabled_policy_count')}`")
        lines.append(f"- Recent actions: `{rem_status.get('recent_action_count')}`")
        lines.append(f"- Recent action failures: `{rem_status.get('recent_failure_count')}`")
        lines.append("")

        notif_status = payload.get("notifications_status") or {}
        lines.append("## Notifications")
        lines.append("")
        lines.append(f"- Channel count: `{notif_status.get('channel_count')}`")
        lines.append(f"- Enabled channels: `{notif_status.get('enabled_channel_count')}`")
        lines.append(f"- Recent deliveries: `{notif_status.get('recent_delivery_count')}`")
        lines.append(f"- Recent delivery failures: `{notif_status.get('recent_failure_count')}`")
        lines.append("")

        health_status = payload.get("health_status") or {}
        health_latest = (payload.get("health_latest") or {}).get("snapshot") or {}
        health_summary = health_latest.get("summary") or {}
        lines.append("## Health")
        lines.append("")
        lines.append(f"- Snapshot count: `{health_status.get('snapshot_count')}`")
        lines.append(f"- Latest snapshot: `{health_status.get('latest_snapshot_id')}`")
        lines.append(f"- Latest health score: `{health_summary.get('score')}`")
        lines.append(f"- Latest health level: `{health_summary.get('level')}`")
        highlights = health_summary.get("highlights") or []
        if highlights:
            lines.append(f"- Highlights: `{'; '.join(str(h) for h in highlights[:6])}`")
        lines.append("")

        inc_status = payload.get("incidents_status") or {}
        lines.append("## Incidents")
        lines.append("")
        lines.append(f"- Total incidents: `{inc_status.get('incident_count')}`")
        lines.append(f"- Open incidents: `{inc_status.get('open_count')}`")
        lines.append(f"- Closed incidents: `{inc_status.get('closed_count')}`")
        lines.append(f"- Severity counts: `{json.dumps(inc_status.get('severity_counts') or {}, ensure_ascii=False)}`")
        lines.append("")

        rb_status = payload.get("runbooks_status") or {}
        lines.append("## Runbooks")
        lines.append("")
        lines.append(f"- Templates: `{rb_status.get('template_count')}`")
        lines.append(f"- Builtin templates: `{rb_status.get('builtin_template_count')}`")
        lines.append(f"- Custom templates: `{rb_status.get('custom_template_count')}`")
        lines.append(f"- Recent runs: `{rb_status.get('run_count')}`")
        lines.append(f"- Recent run failures: `{rb_status.get('recent_failure_count')}`")
        lines.append("")

        jobs = (payload.get("jobs") or {}).get("items") or []
        if jobs:
            lines.append("## Recent Jobs")
            lines.append("")
            for job in jobs[:10]:
                lines.append(
                    f"- `{job.get('id')}` | `{job.get('kind')}` | `{job.get('status')}` | duration_ms=`{job.get('duration_ms')}`"
                )
            lines.append("")

        events = (payload.get("history") or {}).get("items") or []
        if events:
            lines.append("## Recent Events")
            lines.append("")
            for ev in events[:12]:
                lines.append(
                    f"- `{ev.get('created_at')}` `{ev.get('event_type')}` `{(ev.get('summary') or {}).get('path') or (ev.get('summary') or {}).get('id') or ''}`"
                )
            lines.append("")

        benches = (payload.get("benchmarks") or {}).get("items") or []
        if benches:
            lines.append("## Benchmarks")
            lines.append("")
            for b in benches[:10]:
                lines.append(
                    f"- `{b.get('run_id')}` profile=`{b.get('profile')}` total_ms=`{b.get('total_ms')}` ok=`{b.get('ok')}`"
                )
            lines.append("")

        trends = payload.get("benchmark_trends") or {}
        if trends.get("points"):
            lines.append("## Benchmark Trend")
            lines.append("")
            lines.append(f"- Suite: `{trends.get('suite')}`")
            lines.append(f"- Metric: `{trends.get('metric')}`")
            lines.append(f"- Latest: `{(trends.get('summary') or {}).get('latest')}`")
            lines.append(f"- Delta From First: `{(trends.get('summary') or {}).get('delta_from_first')}`")
            lines.append("")

        cmp_latest = payload.get("benchmark_compare_latest")
        if isinstance(cmp_latest, dict) and cmp_latest.get("ok"):
            lines.append("## Latest Benchmark Comparison")
            lines.append("")
            for row in (cmp_latest.get("suite_diffs") or [])[:10]:
                lines.append(
                    f"- `{row.get('suite')}` delta_ms=`{row.get('delta_ms')}` delta_pct=`{row.get('delta_pct')}`"
                )
            lines.append("")

        alert_events = (payload.get("alert_events") or {}).get("items") or []
        if alert_events:
            lines.append("## Recent Alert Events")
            lines.append("")
            for a in alert_events[:12]:
                lines.append(
                    f"- `{a.get('created_at')}` [{a.get('severity')}] `{a.get('rule_name')}` ack=`{a.get('acknowledged')}`: {str(a.get('message') or '')[:180]}"
                )
            lines.append("")

        rem_actions = (payload.get("remediation_actions") or {}).get("items") or []
        if rem_actions:
            lines.append("## Recent Remediation Actions")
            lines.append("")
            for a in rem_actions[:12]:
                lines.append(
                    f"- `{a.get('created_at')}` policy=`{a.get('policy_name')}` alert=`{a.get('alert_id')}` ok=`{a.get('ok')}`"
                )
            lines.append("")

        deliveries = (payload.get("notification_deliveries") or {}).get("items") or []
        if deliveries:
            lines.append("## Recent Notification Deliveries")
            lines.append("")
            for d in deliveries[:12]:
                lines.append(
                    f"- `{d.get('created_at')}` `{d.get('channel_name')}` topic=`{d.get('topic')}` ok=`{d.get('ok')}`"
                )
            lines.append("")

        incident_items = (payload.get("incidents") or {}).get("items") or []
        if incident_items:
            lines.append("## Recent Incidents")
            lines.append("")
            for inc in incident_items[:12]:
                lines.append(
                    f"- `{inc.get('created_at')}` [{inc.get('severity')}] `{inc.get('status')}` `{inc.get('title')}` alerts=`{len(inc.get('linked_alert_ids') or [])}`"
                )
            lines.append("")

        incident_events = (payload.get("incident_timeline") or {}).get("items") or []
        if incident_events:
            lines.append("## Incident Timeline")
            lines.append("")
            for ev in incident_events[:12]:
                lines.append(
                    f"- `{ev.get('created_at')}` incident=`{ev.get('incident_id')}` kind=`{ev.get('kind')}`"
                )
            lines.append("")

        runbook_templates = (payload.get("runbook_templates") or {}).get("items") or []
        if runbook_templates:
            lines.append("## Runbook Templates")
            lines.append("")
            for rb in runbook_templates[:12]:
                lines.append(
                    f"- `{rb.get('name')}` builtin=`{rb.get('builtin')}` steps=`{rb.get('step_count')}` tags=`{','.join(rb.get('tags') or [])}`"
                )
            lines.append("")

        runbook_runs = (payload.get("runbook_runs") or {}).get("items") or []
        if runbook_runs:
            lines.append("## Recent Runbook Runs")
            lines.append("")
            for rr in runbook_runs[:12]:
                lines.append(
                    f"- `{rr.get('started_at')}` template=`{rr.get('template')}` status=`{rr.get('status')}` incident=`{rr.get('incident_id')}`"
                )
            lines.append("")

        health_components = (health_summary.get("components") or {}) if isinstance(health_summary, dict) else {}
        if health_components:
            lines.append("## Health Components")
            lines.append("")
            for key, row in list(health_components.items())[:12]:
                lines.append(
                    f"- `{key}` score=`{row.get('score')}` status=`{row.get('status')}`"
                )
            lines.append("")

        return "\n".join(lines).strip() + "\n"

    @staticmethod
    def _new_report_id() -> str:
        now = datetime.now(timezone.utc)
        return f"report_{now.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"

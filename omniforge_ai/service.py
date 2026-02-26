from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .config import RepoPaths, discover_paths
from .adapters import (
    NeuroDSLBridge,
    NexusFlowBridge,
    SupermixChatAdapter,
    VisionNPUAdapter,
    WindhawkBridge,
)

if TYPE_CHECKING:
    from .advisor import SmartOpsAdvisor
    from .agentic import AgenticWorkflowEngine
    from .artifacts import ArtifactStore
    from .alerts import AlertEngine
    from .benchmark_analytics import BenchmarkAnalytics
    from .benchmarks import BenchmarkArena
    from .health import HealthMonitor
    from .history_store import HistoryStore
    from .incidents import IncidentCenter
    from .jobs import AsyncJobManager
    from .missions import MissionControl
    from .notifications import NotificationCenter
    from .opsgraph import OpsGraphCenter
    from .remediations import AutoRemediationEngine
    from .reports import IntelligenceReportBuilder
    from .runbooks import RunbookCenter
    from .scheduler import AutonomousScheduler
    from .workspace_search import LocalWorkspaceSearch


@dataclass
class OmniForgeService:
    paths: RepoPaths
    chat: SupermixChatAdapter
    vision: VisionNPUAdapter
    neurodsl: NeuroDSLBridge
    nexusflow: NexusFlowBridge
    windhawk: WindhawkBridge
    history: "HistoryStore" = field(init=False, repr=False)
    artifacts: "ArtifactStore" = field(init=False, repr=False)
    benchmarks: "BenchmarkArena" = field(init=False, repr=False)
    benchmark_analytics: "BenchmarkAnalytics" = field(init=False, repr=False)
    search: "LocalWorkspaceSearch" = field(init=False, repr=False)
    missions: "MissionControl" = field(init=False, repr=False)
    jobs: "AsyncJobManager" = field(init=False, repr=False)
    scheduler: "AutonomousScheduler" = field(init=False, repr=False)
    notifications: "NotificationCenter" = field(init=False, repr=False)
    health: "HealthMonitor" = field(init=False, repr=False)
    incidents: "IncidentCenter" = field(init=False, repr=False)
    opsgraph: "OpsGraphCenter" = field(init=False, repr=False)
    advisor: "SmartOpsAdvisor" = field(init=False, repr=False)
    runbooks: "RunbookCenter" = field(init=False, repr=False)
    reports: "IntelligenceReportBuilder" = field(init=False, repr=False)
    alerts: "AlertEngine" = field(init=False, repr=False)
    remediations: "AutoRemediationEngine" = field(init=False, repr=False)
    _agentic_engine: "AgenticWorkflowEngine | None" = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        from .advisor import SmartOpsAdvisor
        from .artifacts import ArtifactStore
        from .alerts import AlertEngine
        from .benchmark_analytics import BenchmarkAnalytics
        from .benchmarks import BenchmarkArena
        from .health import HealthMonitor
        from .history_store import HistoryStore
        from .incidents import IncidentCenter
        from .jobs import AsyncJobManager
        from .missions import MissionControl
        from .notifications import NotificationCenter
        from .opsgraph import OpsGraphCenter
        from .remediations import AutoRemediationEngine
        from .reports import IntelligenceReportBuilder
        from .runbooks import RunbookCenter
        from .scheduler import AutonomousScheduler
        from .workspace_search import LocalWorkspaceSearch

        project_root = self.paths.dashboard_file.parent.parent
        out_root = project_root / "out"
        out_root.mkdir(parents=True, exist_ok=True)

        self.history = HistoryStore(out_root / "history")
        self.artifacts = ArtifactStore(
            {
                "out": out_root,
                "history": out_root / "history",
                "jobs": out_root / "jobs",
                "benchmarks": out_root / "benchmarks",
                "generated_workflows": project_root / "workflows" / "generated",
                "agentic_runs": out_root / "agentic_nxf_runs",
                "reports": out_root / "reports",
                "schedules": out_root / "schedules",
                "missions": out_root / "missions",
                "alerts": out_root / "alerts",
                "remediations": out_root / "remediations",
                "notifications": out_root / "notifications",
                "health": out_root / "health",
                "incidents": out_root / "incidents",
                "opsgraph": out_root / "opsgraph",
                "advisor": out_root / "advisor",
                "runbooks": out_root / "runbooks",
                "neurodsl_logs": self.paths.neurodsl_repo,
                "windhawk_local": project_root / "windhawk",
            }
        )
        self.benchmarks = BenchmarkArena(self, out_root / "benchmarks", self.history)
        self.benchmark_analytics = BenchmarkAnalytics(out_root / "benchmarks")
        self.search = LocalWorkspaceSearch(
            {
                "omniforge": project_root,
                "workspace": self.paths.workspace_root,
                "nexusflow": self.paths.nexusflow_repo,
                "supermix": self.paths.supermix_repo,
                "neurodsl": self.paths.neurodsl_repo,
                "npu_easy": self.paths.npu_easy_repo,
                "cifar": self.paths.cifar_repo,
                "single_file_lab": self.paths.single_file_lab_repo,
            }
        )
        self.missions = MissionControl(self, out_root / "missions", self.history)
        # Single worker avoids concurrent access issues in local adapters with global runtime state.
        self.jobs = AsyncJobManager(self, out_root / "jobs", self.history, max_workers=1)
        self.scheduler = AutonomousScheduler(self, out_root / "schedules", self.history)
        self.notifications = NotificationCenter(self, out_root / "notifications", self.history)
        self.health = HealthMonitor(self, out_root / "health", self.history)
        self.incidents = IncidentCenter(self, out_root / "incidents", self.history)
        self.opsgraph = OpsGraphCenter(self, out_root / "opsgraph", self.history)
        self.advisor = SmartOpsAdvisor(self, out_root / "advisor", self.history)
        self.runbooks = RunbookCenter(self, out_root / "runbooks", self.history)
        self.reports = IntelligenceReportBuilder(self, out_root / "reports", self.history)
        self.alerts = AlertEngine(self, out_root / "alerts", self.history)
        self.remediations = AutoRemediationEngine(self, out_root / "remediations", self.history)

    def status(self) -> dict[str, Any]:
        return {
            "workspace_root": str(self.paths.workspace_root),
            "repos": {
                "nexusflow": str(self.paths.nexusflow_repo),
                "supermix": str(self.paths.supermix_repo),
                "neurodsl": str(self.paths.neurodsl_repo),
                "npu_easy": str(self.paths.npu_easy_repo),
                "cifar_repo": str(self.paths.cifar_repo),
                "single_file_lab": str(self.paths.single_file_lab_repo),
            },
            "chat": self.chat.status(),
            "vision": self.vision.status(),
            "neurodsl": self.neurodsl.status(),
            "nexusflow": self.nexusflow.status(),
            "windhawk": self.windhawk.status(),
            "runtime": {
                "history": self.history.stats(),
                "artifact_roots": [r["name"] for r in self.artifacts.roots()["roots"]],
                "search_roots": [r["name"] for r in self.search.roots()["roots"]],
                "benchmark_runs": self.benchmark_analytics.list_runs(limit=3)["count"],
                "mission_catalog": [m["name"] for m in self.missions.catalog()["missions"]],
                "reports": self.reports.list(limit=3)["count"],
                "scheduler": self.scheduler.status(),
                "alerts": self.alerts.status(),
                "remediations": self.remediations.status(),
                "notifications": self.notifications.status(),
                "health": self.health.status(),
                "incidents": self.incidents.status(),
                "opsgraph": self.opsgraph.status(),
                "advisor": self.advisor.status(),
                "runbooks": self.runbooks.status(),
            },
        }

    def agentic_engine(self) -> "AgenticWorkflowEngine":
        if self._agentic_engine is None:
            from .agentic import AgenticWorkflowEngine

            self._agentic_engine = AgenticWorkflowEngine(self)
        return self._agentic_engine

    def run_operation(self, kind: str, params: dict[str, Any] | None = None) -> dict[str, Any] | Any:
        params = dict(params or {})
        k = str(kind or "").strip().lower()
        if not k:
            raise ValueError("operation kind is required")
        if k in {"ops.catalog", "operation.catalog"}:
            return self.operation_catalog()
        if k == "noop":
            return {"ok": True, "noop": True, "params": params}

        if k in {"chat", "chat.answer"}:
            message = str(params.get("message") or params.get("task") or "").strip()
            if not message:
                raise ValueError("chat operation requires 'message' or 'task'")
            session_id = str(params.get("session_id") or "omniforge-op")
            return self.chat.chat(message, session_id=session_id)
        if k == "chat.load":
            return self.chat.load()

        if k == "vision.classify_path":
            image_path = str(params.get("image_path") or "").strip()
            if not image_path:
                raise ValueError("vision.classify_path requires image_path")
            top_k = int(params.get("top_k", 3))
            return self.vision.classify_path(image_path, top_k=top_k)
        if k == "vision.benchmark":
            return self.vision.benchmark_random()
        if k == "vision.status":
            return self.vision.status()

        if k == "neurodsl.devices":
            return self.neurodsl.devices()
        if k == "neurodsl.agent_manifest":
            return self.neurodsl.agent_manifest()
        if k == "neurodsl.platform_health":
            return self.neurodsl.platform_health()
        if k == "neurodsl.agent_api.start":
            return self.neurodsl.start_agent_api(
                host=str(params.get("host") or "127.0.0.1"),
                port=int(params.get("port") or 8860),
                device=str(params.get("device") or "auto"),
                wait_sec=float(params.get("wait_sec") or 15.0),
            )
        if k == "neurodsl.agent_api.stop":
            return self.neurodsl.stop_agent_api(
                timeout_sec=float(params.get("timeout_sec") or 8.0),
                force=bool(params.get("force", True)),
            )

        if k == "nexusflow.run_example":
            example = str(params.get("example") or "").strip()
            if not example:
                raise ValueError("nexusflow.run_example requires example")
            return self.nexusflow.run_example(
                example,
                pipeline=(str(params["pipeline"]) if params.get("pipeline") is not None else None),
                out_dir=params.get("out_dir"),
            )
        if k == "nexusflow.run_file":
            file_path = str(params.get("file_path") or "").strip()
            if not file_path:
                raise ValueError("nexusflow.run_file requires file_path")
            return self.nexusflow.run_file(
                file_path,
                pipeline=(str(params["pipeline"]) if params.get("pipeline") is not None else None),
                out_dir=params.get("out_dir"),
            )

        if k == "windhawk.generate_mod":
            return self.windhawk.generate_hotkey_mod(
                host=str(params.get("host") or "127.0.0.1"),
                port=int(params.get("port") or 8787),
                path=str(params.get("path") or "/"),
                copy_to_windhawk=bool(params.get("copy_to_windhawk", True)),
            )
        if k == "windhawk.restart":
            return self.windhawk.restart(tray_only=bool(params.get("tray_only", True)))

        if k == "agentic.plan":
            return self.agentic_engine().plan(
                str(params.get("task") or ""),
                image_path=(str(params.get("image_path") or "").strip() or None),
                session_id=str(params.get("session_id") or "agentic"),
            )
        if k == "agentic.execute":
            return self.agentic_engine().execute(
                str(params.get("task") or ""),
                image_path=(str(params.get("image_path") or "").strip() or None),
            )
        if k == "agentic.nexusflow.export":
            return self.agentic_engine().export_nexusflow_pipeline(
                str(params.get("task") or ""),
                image_path=(str(params.get("image_path") or "").strip() or None),
            )
        if k == "agentic.nexusflow.run":
            return self.agentic_engine().run_nexusflow_pipeline(
                str(params.get("task") or ""),
                image_path=(str(params.get("image_path") or "").strip() or None),
            )

        if k in {"benchmark.arena", "benchmark.run"}:
            profile = params.get("profile")
            config = params.get("config")
            return self.benchmarks.run(
                profile=(str(profile) if profile is not None else None),
                config=(dict(config) if isinstance(config, dict) else None),
            )
        if k == "benchmark.profiles":
            return self.benchmarks.profiles()
        if k in {"benchmark.analytics.list", "benchmark.list"}:
            return self.benchmark_analytics.list_runs(limit=int(params.get("limit", 50)))
        if k == "benchmark.analytics.compare":
            return self.benchmark_analytics.compare(
                str(params.get("baseline") or ""),
                str(params.get("candidate") or ""),
            )
        if k == "benchmark.analytics.trends":
            return self.benchmark_analytics.trends(
                suite=str(params.get("suite") or "vision_random"),
                metric=str(params.get("metric") or "duration_ms"),
                limit=int(params.get("limit", 20)),
            )

        if k in {"search.files", "workspace.search.files"}:
            extensions = params.get("extensions")
            return self.search.find_files(
                str(params.get("query") or ""),
                root=(str(params["root"]) if params.get("root") is not None else None),
                glob=(str(params["glob"]) if params.get("glob") is not None else None),
                extensions=(list(extensions) if isinstance(extensions, list) else None),
                limit=int(params.get("limit", 100)),
            )
        if k in {"search.text", "workspace.search.text"}:
            extensions = params.get("extensions")
            return self.search.search_text(
                str(params.get("pattern") or ""),
                root=(str(params["root"]) if params.get("root") is not None else None),
                case_sensitive=bool(params.get("case_sensitive", False)),
                regex=bool(params.get("regex", False)),
                glob=(str(params["glob"]) if params.get("glob") is not None else None),
                extensions=(list(extensions) if isinstance(extensions, list) else None),
                limit_matches=int(params.get("limit_matches", 100)),
                max_file_bytes=int(params.get("max_file_bytes", 1_500_000)),
                context_lines=int(params.get("context_lines", 1)),
            )
        if k == "search.roots":
            return self.search.roots()

        if k == "missions.catalog":
            return self.missions.catalog()
        if k == "missions.plan":
            return self.missions.plan(str(params.get("mission") or ""), inputs=params.get("inputs") if isinstance(params.get("inputs"), dict) else {})
        if k == "missions.run":
            return self.missions.run(str(params.get("mission") or ""), inputs=params.get("inputs") if isinstance(params.get("inputs"), dict) else {})

        if k in {"alerts.status", "alerts.health"}:
            return self.alerts.status()
        if k in {"alerts.templates", "alert.templates"}:
            return self.alerts.templates()
        if k in {"alerts.rules.list", "alert.rules.list"}:
            return self.alerts.list_rules()
        if k in {"alerts.rules.get", "alert.rules.get"}:
            return self.alerts.get_rule(str(params.get("rule_id") or params.get("id") or ""))
        if k in {"alerts.rules.create", "alert.rules.create"}:
            return self.alerts.create_rule(
                kind=str(params.get("kind") or ""),
                name=(str(params["name"]) if params.get("name") is not None else None),
                config=(params.get("config") if isinstance(params.get("config"), dict) else {}),
                severity=str(params.get("severity") or "warning"),
                enabled=bool(params.get("enabled", True)),
                cooldown_sec=float(params.get("cooldown_sec", 300.0)),
            )
        if k in {"alerts.rules.update", "alert.rules.update"}:
            return self.alerts.update_rule(
                str(params.get("rule_id") or params.get("id") or ""),
                name=params.get("name"),
                config=(params.get("config") if isinstance(params.get("config"), dict) else None),
                severity=params.get("severity"),
                enabled=params.get("enabled"),
                cooldown_sec=params.get("cooldown_sec"),
            )
        if k in {"alerts.rules.delete", "alert.rules.delete"}:
            return self.alerts.delete_rule(str(params.get("rule_id") or params.get("id") or ""))
        if k in {"alerts.evaluate", "alerts.evaluate_all"}:
            return self.alerts.evaluate_all(
                only_enabled=bool(params.get("only_enabled", True)),
                force=bool(params.get("force", False)),
            )
        if k in {"alerts.evaluate_rule", "alert.evaluate_rule"}:
            return self.alerts.evaluate_rule(
                str(params.get("rule_id") or params.get("id") or ""),
                force=bool(params.get("force", False)),
            )
        if k in {"alerts.list", "alerts.events.list", "alerts.events"}:
            ack_val = params.get("acknowledged")
            return self.alerts.list_alerts(
                limit=int(params.get("limit", 50)),
                rule_id=(str(params["rule_id"]) if params.get("rule_id") is not None else None),
                severity=(str(params["severity"]) if params.get("severity") is not None else None),
                acknowledged=(bool(ack_val) if ack_val is not None else None),
            )
        if k in {"alerts.ack", "alerts.events.ack", "alerts.acknowledge"}:
            return self.alerts.ack_alert(
                str(params.get("alert_id") or params.get("id") or ""),
                note=(str(params["note"]) if params.get("note") is not None else None),
            )

        if k in {"remediations.status", "remediation.status"}:
            return self.remediations.status()
        if k in {"remediations.templates", "remediation.templates"}:
            return self.remediations.templates()
        if k in {"remediations.policies.list", "remediation.policies.list"}:
            return self.remediations.list_policies()
        if k in {"remediations.policies.get", "remediation.policies.get"}:
            return self.remediations.get_policy(str(params.get("policy_id") or params.get("id") or ""))
        if k in {"remediations.policies.create", "remediation.policies.create"}:
            return self.remediations.create_policy(
                name=(str(params["name"]) if params.get("name") is not None else None),
                match=(params.get("match") if isinstance(params.get("match"), dict) else {}),
                actions=(params.get("actions") if isinstance(params.get("actions"), list) else []),
                enabled=bool(params.get("enabled", True)),
                cooldown_sec=float(params.get("cooldown_sec", 300.0)),
            )
        if k in {"remediations.policies.update", "remediation.policies.update"}:
            return self.remediations.update_policy(
                str(params.get("policy_id") or params.get("id") or ""),
                name=params.get("name"),
                match=(params.get("match") if isinstance(params.get("match"), dict) else None),
                actions=(params.get("actions") if isinstance(params.get("actions"), list) else None),
                enabled=params.get("enabled"),
                cooldown_sec=params.get("cooldown_sec"),
            )
        if k in {"remediations.policies.delete", "remediation.policies.delete"}:
            return self.remediations.delete_policy(str(params.get("policy_id") or params.get("id") or ""))
        if k in {"remediations.actions.list", "remediation.actions.list"}:
            ok_filter = params.get("ok")
            return self.remediations.list_actions(
                limit=int(params.get("limit", 50)),
                policy_id=(str(params["policy_id"]) if params.get("policy_id") is not None else None),
                ok=(bool(ok_filter) if ok_filter is not None else None),
            )
        if k in {"remediations.handle_alert", "remediation.handle_alert"}:
            if params.get("alert") is not None:
                alert_val = params.get("alert")
            else:
                alert_val = str(params.get("alert_id") or params.get("id") or "")
            return self.remediations.handle_alert(alert_val, force=bool(params.get("force", False)))

        if k in {"notifications.status", "notification.status"}:
            return self.notifications.status()
        if k in {"notifications.templates", "notification.templates"}:
            return self.notifications.templates()
        if k in {"notifications.channels.list", "notification.channels.list"}:
            return self.notifications.list_channels()
        if k in {"notifications.channels.get", "notification.channels.get"}:
            return self.notifications.get_channel(str(params.get("channel_id") or params.get("id") or ""))
        if k in {"notifications.channels.create", "notification.channels.create"}:
            return self.notifications.create_channel(
                channel_type=str(params.get("type") or params.get("channel_type") or ""),
                name=(str(params["name"]) if params.get("name") is not None else None),
                config=(params.get("config") if isinstance(params.get("config"), dict) else {}),
                match=(params.get("match") if isinstance(params.get("match"), dict) else {}),
                enabled=bool(params.get("enabled", True)),
            )
        if k in {"notifications.channels.update", "notification.channels.update"}:
            return self.notifications.update_channel(
                str(params.get("channel_id") or params.get("id") or ""),
                name=params.get("name"),
                config=(params.get("config") if isinstance(params.get("config"), dict) else None),
                match=(params.get("match") if isinstance(params.get("match"), dict) else None),
                enabled=params.get("enabled"),
            )
        if k in {"notifications.channels.delete", "notification.channels.delete"}:
            return self.notifications.delete_channel(str(params.get("channel_id") or params.get("id") or ""))
        if k in {"notifications.deliveries.list", "notification.deliveries.list"}:
            ok_filter = params.get("ok")
            return self.notifications.list_deliveries(
                limit=int(params.get("limit", 50)),
                channel_id=(str(params["channel_id"]) if params.get("channel_id") is not None else None),
                ok=(bool(ok_filter) if ok_filter is not None else None),
                topic=(str(params["topic"]) if params.get("topic") is not None else None),
                severity=(str(params["severity"]) if params.get("severity") is not None else None),
            )
        if k in {"notifications.send", "notification.send"}:
            target_channel_ids = params.get("target_channel_ids")
            return self.notifications.dispatch(
                topic=str(params.get("topic") or ""),
                message=str(params.get("message") or ""),
                payload=(params.get("payload") if isinstance(params.get("payload"), dict) else {}),
                severity=str(params.get("severity") or "info"),
                tags=(list(params["tags"]) if isinstance(params.get("tags"), list) else None),
                target_channel_ids=(list(target_channel_ids) if isinstance(target_channel_ids, list) else None),
            )

        if k in {"health.status", "runtime.health.status"}:
            return self.health.status()
        if k in {"health.profiles", "runtime.health.profiles"}:
            return self.health.profiles()
        if k in {"health.snapshot", "runtime.health.snapshot"}:
            return self.health.snapshot(
                profile=str(params.get("profile") or "full"),
                options=(params.get("options") if isinstance(params.get("options"), dict) else {}),
            )
        if k in {"health.list", "runtime.health.list"}:
            return self.health.list(limit=int(params.get("limit", 50)))
        if k in {"health.latest", "runtime.health.latest"}:
            return self.health.latest(optional=bool(params.get("optional", False)))
        if k in {"health.get", "runtime.health.get"}:
            return self.health.get(str(params.get("snapshot_id") or params.get("id") or ""))
        if k in {"health.compare", "runtime.health.compare"}:
            return self.health.compare(
                str(params.get("baseline") or ""),
                str(params.get("candidate") or ""),
            )

        if k in {"incidents.status", "incident.status"}:
            return self.incidents.status()
        if k in {"incidents.templates", "incident.templates"}:
            return self.incidents.templates()
        if k in {"incidents.list", "incident.list"}:
            return self.incidents.list(
                limit=int(params.get("limit", 50)),
                status=(str(params["status"]) if params.get("status") is not None else None),
                severity=(str(params["severity"]) if params.get("severity") is not None else None),
                source=(str(params["source"]) if params.get("source") is not None else None),
            )
        if k in {"incidents.get", "incident.get"}:
            return self.incidents.get(
                str(params.get("incident_id") or params.get("id") or ""),
                timeline_limit=int(params.get("timeline_limit", 50)),
            )
        if k in {"incidents.create", "incident.create"}:
            return self.incidents.create(
                title=str(params.get("title") or ""),
                severity=str(params.get("severity") or "warning"),
                description=(str(params["description"]) if params.get("description") is not None else None),
                source=str(params.get("source") or "manual"),
                status=str(params.get("status") or "open"),
                tags=(list(params["tags"]) if isinstance(params.get("tags"), list) else None),
                metadata=(params.get("metadata") if isinstance(params.get("metadata"), dict) else None),
                alert=(params.get("alert") if isinstance(params.get("alert"), dict) else None),
                auto_created=bool(params.get("auto_created", False)),
            )
        if k in {"incidents.update", "incident.update"}:
            return self.incidents.update(
                str(params.get("incident_id") or params.get("id") or ""),
                title=params.get("title"),
                severity=params.get("severity"),
                status=params.get("status"),
                description=params.get("description"),
                resolution=params.get("resolution"),
                tags=(params.get("tags") if isinstance(params.get("tags"), list) else None),
                metadata_patch=(params.get("metadata_patch") if isinstance(params.get("metadata_patch"), dict) else None),
            )
        if k in {"incidents.close", "incident.close"}:
            return self.incidents.close(
                str(params.get("incident_id") or params.get("id") or ""),
                resolution=(str(params["resolution"]) if params.get("resolution") is not None else None),
                note=(str(params["note"]) if params.get("note") is not None else None),
            )
        if k in {"incidents.reopen", "incident.reopen"}:
            return self.incidents.reopen(
                str(params.get("incident_id") or params.get("id") or ""),
                note=(str(params["note"]) if params.get("note") is not None else None),
            )
        if k in {"incidents.note", "incident.note"}:
            return self.incidents.add_note(
                str(params.get("incident_id") or params.get("id") or ""),
                note=str(params.get("note") or ""),
                author=(str(params["author"]) if params.get("author") is not None else None),
                kind=str(params.get("kind") or "note"),
            )
        if k in {"incidents.link_alert", "incident.link_alert"}:
            if params.get("alert") is not None:
                alert_val = params.get("alert")
            else:
                alert_val = str(params.get("alert_id") or "")
            return self.incidents.link_alert(
                str(params.get("incident_id") or params.get("id") or ""),
                alert_val,
            )
        if k in {"incidents.timeline", "incident.timeline"}:
            return self.incidents.timeline(
                incident_id=(str(params["incident_id"]) if params.get("incident_id") is not None else None),
                limit=int(params.get("limit", 100)),
            )
        if k in {"incidents.capture_bundle", "incident.capture_bundle"}:
            return self.incidents.capture_bundle(
                str(params.get("incident_id") or params.get("id") or ""),
                health_profile=str(params.get("health_profile") or "full"),
                report_profile=str(params.get("report_profile") or "health_watch"),
                include_health=bool(params.get("include_health", True)),
                include_report=bool(params.get("include_report", True)),
            )

        if k in {"opsgraph.status", "ops_graph.status"}:
            return self.opsgraph.status()
        if k in {"opsgraph.profiles", "ops_graph.profiles"}:
            return self.opsgraph.profiles()
        if k in {"opsgraph.list", "opsgraph.snapshots.list", "ops_graph.list"}:
            return self.opsgraph.list_snapshots(
                limit=int(params.get("limit", 50)),
                profile=(str(params["profile"]) if params.get("profile") is not None else None),
            )
        if k in {"opsgraph.watch.list", "opsgraph.watches.list", "ops_graph.watch.list"}:
            return self.opsgraph.list_watch(
                limit=int(params.get("limit", 50)),
                profile=(str(params["profile"]) if params.get("profile") is not None else None),
            )
        if k in {"opsgraph.get", "opsgraph.snapshots.get", "ops_graph.get"}:
            return self.opsgraph.get_snapshot(str(params.get("snapshot_id") or params.get("id") or ""))
        if k in {"opsgraph.watch.get", "ops_graph.watch.get"}:
            return self.opsgraph.get_watch(str(params.get("watch_id") or params.get("id") or ""))
        if k in {"opsgraph.snapshot", "opsgraph.snapshots.create", "ops_graph.snapshot"}:
            return self.opsgraph.snapshot(
                profile=str(params.get("profile") or "runtime"),
                options=(params.get("options") if isinstance(params.get("options"), dict) else {}),
                persist=bool(params.get("persist", True)),
            )
        if k in {"opsgraph.watch", "opsgraph.watch.run", "ops_graph.watch"}:
            return self.opsgraph.watch(
                profile=str(params.get("profile") or "runtime"),
                options=(params.get("options") if isinstance(params.get("options"), dict) else {}),
                compare_with=(str(params["compare_with"]) if params.get("compare_with") is not None else "latest"),
                max_hotspots=int(params.get("max_hotspots", 10)),
                max_components=int(params.get("max_components", 6)),
                auto_incident=bool(params.get("auto_incident", False)),
                incident_threshold=float(params.get("incident_threshold", 80.0)),
                incident_cooldown_sec=float(params.get("incident_cooldown_sec", 3600.0)),
                persist=bool(params.get("persist", True)),
            )
        if k in {"opsgraph.watch.trends", "ops_graph.watch.trends"}:
            return self.opsgraph.watch_trends(
                limit=int(params.get("limit", 50)),
                profile=(str(params["profile"]) if params.get("profile") is not None else None),
            )
        if k in {"opsgraph.watch.anomalies", "ops_graph.watch.anomalies"}:
            return self.opsgraph.watch_anomalies(
                watch_id=(str(params["watch_id"]) if params.get("watch_id") is not None else None),
                profile=(str(params["profile"]) if params.get("profile") is not None else None),
                baseline_limit=int(params.get("baseline_limit", 20)),
                z_threshold=float(params.get("z_threshold", 1.8)),
                delta_threshold=float(params.get("delta_threshold", 10.0)),
            )
        if k in {"opsgraph.watch.forecast", "ops_graph.watch.forecast"}:
            return self.opsgraph.watch_forecast(
                limit=int(params.get("limit", 30)),
                horizon=int(params.get("horizon", 5)),
                profile=(str(params["profile"]) if params.get("profile") is not None else None),
            )
        if k in {"opsgraph.trace", "ops_graph.trace"}:
            return self.opsgraph.trace(
                str(params.get("node_id") or params.get("id") or ""),
                snapshot_id=(str(params["snapshot_id"]) if params.get("snapshot_id") is not None else None),
                depth=int(params.get("depth", 2)),
                direction=str(params.get("direction") or "both"),
                max_nodes=int(params.get("max_nodes", 200)),
            )
        if k in {"opsgraph.compare", "ops_graph.compare"}:
            return self.opsgraph.compare(
                str(params.get("baseline") or ""),
                str(params.get("candidate") or ""),
                max_samples=int(params.get("max_samples", 25)),
            )
        if k in {"opsgraph.path", "ops_graph.path"}:
            return self.opsgraph.path_between(
                str(params.get("source") or params.get("src") or params.get("src_node_id") or ""),
                str(params.get("target") or params.get("dst") or params.get("dst_node_id") or ""),
                snapshot_id=(str(params["snapshot_id"]) if params.get("snapshot_id") is not None else None),
                direction=str(params.get("direction") or "both"),
                max_depth=int(params.get("max_depth", 8)),
            )
        if k in {"opsgraph.blast_radius", "ops_graph.blast_radius"}:
            return self.opsgraph.blast_radius(
                str(params.get("node_id") or params.get("id") or ""),
                snapshot_id=(str(params["snapshot_id"]) if params.get("snapshot_id") is not None else None),
                depth=int(params.get("depth", 2)),
                direction=str(params.get("direction") or "out"),
                max_nodes=int(params.get("max_nodes", 300)),
            )
        if k in {"opsgraph.hotspots", "ops_graph.hotspots"}:
            return self.opsgraph.hotspots(
                snapshot_id=(str(params["snapshot_id"]) if params.get("snapshot_id") is not None else None),
                limit=int(params.get("limit", 20)),
                include_zero=bool(params.get("include_zero", False)),
            )
        if k in {"opsgraph.components", "ops_graph.components"}:
            return self.opsgraph.components(
                snapshot_id=(str(params["snapshot_id"]) if params.get("snapshot_id") is not None else None),
                limit=int(params.get("limit", 20)),
                order_by=str(params.get("order_by") or "risk"),
            )
        if k in {"opsgraph.explain_incident", "ops_graph.explain_incident"}:
            return self.opsgraph.explain_incident(
                str(params.get("incident_id") or params.get("id") or ""),
                snapshot_id=(str(params["snapshot_id"]) if params.get("snapshot_id") is not None else None),
                depth=int(params.get("depth", 2)),
                max_nodes=int(params.get("max_nodes", 200)),
            )

        if k in {"advisor.status", "smartops.status"}:
            return self.advisor.status()
        if k in {"advisor.profiles", "smartops.profiles"}:
            return self.advisor.profiles()
        if k in {"advisor.list", "smartops.list"}:
            return self.advisor.list(limit=int(params.get("limit", 50)))
        if k in {"advisor.get", "smartops.get"}:
            return self.advisor.get(str(params.get("analysis_id") or params.get("id") or ""))
        if k in {"advisor.analyze", "smartops.analyze"}:
            return self.advisor.analyze(
                profile=str(params.get("profile") or "quick"),
                incident_id=(str(params["incident_id"]) if params.get("incident_id") is not None else None),
                options=(params.get("options") if isinstance(params.get("options"), dict) else {}),
                persist=bool(params.get("persist", True)),
            )
        if k in {"advisor.doctor", "smartops.doctor"}:
            return self.advisor.doctor(
                incident_id=(str(params["incident_id"]) if params.get("incident_id") is not None else None),
            )

        if k in {"runbooks.status", "runbook.status"}:
            return self.runbooks.status()
        if k in {"runbooks.templates", "runbook.templates", "runbooks.templates.list"}:
            return self.runbooks.list_templates(include_steps=bool(params.get("include_steps", False)))
        if k in {"runbooks.templates.get", "runbook.templates.get"}:
            return self.runbooks.get_template(str(params.get("name") or params.get("template") or ""))
        if k in {"runbooks.templates.create", "runbook.templates.create"}:
            return self.runbooks.create_template(
                name=str(params.get("name") or ""),
                title=(str(params["title"]) if params.get("title") is not None else None),
                description=(str(params["description"]) if params.get("description") is not None else None),
                steps=(params.get("steps") if isinstance(params.get("steps"), list) else None),
                tags=(list(params["tags"]) if isinstance(params.get("tags"), list) else None),
                metadata=(params.get("metadata") if isinstance(params.get("metadata"), dict) else None),
            )
        if k in {"runbooks.templates.update", "runbook.templates.update"}:
            return self.runbooks.update_template(
                str(params.get("name") or params.get("template") or ""),
                title=params.get("title"),
                description=params.get("description"),
                steps=(params.get("steps") if isinstance(params.get("steps"), list) else None),
                tags=(params.get("tags") if isinstance(params.get("tags"), list) else None),
                metadata=(params.get("metadata") if isinstance(params.get("metadata"), dict) else None),
            )
        if k in {"runbooks.templates.delete", "runbook.templates.delete"}:
            return self.runbooks.delete_template(str(params.get("name") or params.get("template") or ""))
        if k in {"runbooks.runs.list", "runbook.runs.list", "runbooks.list_runs"}:
            return self.runbooks.list_runs(
                limit=int(params.get("limit", 50)),
                status=(str(params["status"]) if params.get("status") is not None else None),
                template=(str(params["template"]) if params.get("template") is not None else None),
                incident_id=(str(params["incident_id"]) if params.get("incident_id") is not None else None),
            )
        if k in {"runbooks.runs.get", "runbook.runs.get"}:
            return self.runbooks.get_run(str(params.get("run_id") or params.get("id") or ""))
        if k in {"runbooks.run", "runbook.run"}:
            return self.runbooks.run(
                str(params.get("template") or params.get("name") or ""),
                context=(params.get("context") if isinstance(params.get("context"), dict) else None),
                incident_id=(str(params["incident_id"]) if params.get("incident_id") is not None else None),
                alert_id=(str(params["alert_id"]) if params.get("alert_id") is not None else None),
                dry_run=bool(params.get("dry_run", False)),
                stop_on_error=(bool(params["stop_on_error"]) if params.get("stop_on_error") is not None else None),
            )

        if k in {"scheduler.status", "schedules.status"}:
            return self.scheduler.status()
        if k in {"scheduler.start", "schedules.start"}:
            return self.scheduler.start()
        if k in {"scheduler.stop", "schedules.stop"}:
            return self.scheduler.stop(timeout_sec=float(params.get("timeout_sec", 5.0)))
        if k in {"scheduler.tick", "schedules.tick"}:
            return self.scheduler.tick_once()
        if k in {"scheduler.list", "schedules.list"}:
            return self.scheduler.list()
        if k in {"scheduler.get", "schedules.get"}:
            return self.scheduler.get(str(params.get("schedule_id") or params.get("id") or ""))
        if k in {"scheduler.create", "schedules.create"}:
            return self.scheduler.create(
                name=(str(params["name"]) if params.get("name") is not None else None),
                target_kind=str(params.get("target_kind") or params.get("kind") or ""),
                params=(params.get("params") if isinstance(params.get("params"), dict) else {}),
                interval_sec=float(params.get("interval_sec") or params.get("interval") or 60.0),
                enabled=bool(params.get("enabled", True)),
                start_immediately=bool(params.get("start_immediately", False)),
            )
        if k in {"scheduler.update", "schedules.update"}:
            return self.scheduler.update(
                str(params.get("schedule_id") or params.get("id") or ""),
                name=params.get("name"),
                target_kind=params.get("target_kind"),
                params=(params.get("params") if isinstance(params.get("params"), dict) else None),
                interval_sec=params.get("interval_sec"),
                enabled=params.get("enabled"),
            )
        if k in {"scheduler.delete", "schedules.delete"}:
            return self.scheduler.delete(str(params.get("schedule_id") or params.get("id") or ""))
        if k in {"scheduler.run_now", "schedules.run_now"}:
            return self.scheduler.run_now(str(params.get("schedule_id") or params.get("id") or ""))

        if k in {"report.profiles", "reports.profiles"}:
            return self.reports.profiles()
        if k in {"report.list", "reports.list"}:
            return self.reports.list(limit=int(params.get("limit", 50)))
        if k in {"report.generate", "reports.generate"}:
            return self.reports.generate(
                profile=str(params.get("profile") or "ops_digest"),
                options=(params.get("options") if isinstance(params.get("options"), dict) else {}),
            )

        if k == "status":
            return self.status()

        raise ValueError(f"Unsupported operation kind: {kind}")

    def summarize_operation_result(self, kind: str, result: Any) -> dict[str, Any]:
        if isinstance(result, dict):
            out: dict[str, Any] = {"keys": list(result.keys())[:30]}
            for key in ("ok", "plan_id", "run_id", "provider", "infer_ms", "returncode", "artifact_file"):
                if key in result:
                    out[key] = result.get(key)
            if "report_id" in result:
                out["report_id"] = result.get("report_id")
            if "json_file" in result:
                out["json_file"] = result.get("json_file")
            if "response" in result:
                out["response"] = str(result.get("response") or "")[:240]
            if "predictions" in result and isinstance(result.get("predictions"), list):
                out["predictions"] = result["predictions"][:3]
            if "summary" in result and isinstance(result.get("summary"), dict):
                out["summary"] = result.get("summary")
            return out
        return {"type": type(result).__name__, "preview": str(result)[:400]}

    def operation_catalog(self) -> dict[str, Any]:
        return {
            "ok": True,
            "operations": [
                {"kind": "status", "params": {}},
                {"kind": "ops.catalog", "params": {}},
                {"kind": "chat.answer", "params": {"task": "text", "session_id": "optional"}},
                {"kind": "chat.load", "params": {}},
                {"kind": "vision.classify_path", "params": {"image_path": "path", "top_k": 3}},
                {"kind": "vision.benchmark", "params": {}},
                {"kind": "vision.status", "params": {}},
                {"kind": "neurodsl.devices", "params": {}},
                {"kind": "neurodsl.agent_manifest", "params": {}},
                {"kind": "neurodsl.platform_health", "params": {}},
                {"kind": "neurodsl.agent_api.start", "params": {"host": "127.0.0.1", "port": 8860, "device": "auto"}},
                {"kind": "neurodsl.agent_api.stop", "params": {"timeout_sec": 8.0, "force": True}},
                {"kind": "nexusflow.run_example", "params": {"example": "npu_workflow_demo.nxf", "pipeline": "build"}},
                {"kind": "nexusflow.run_file", "params": {"file_path": "path", "pipeline": "optional"}},
                {"kind": "windhawk.generate_mod", "params": {"host": "127.0.0.1", "port": 8787, "path": "/"}},
                {"kind": "windhawk.restart", "params": {"tray_only": True}},
                {"kind": "agentic.plan", "params": {"task": "text", "image_path": "optional"}},
                {"kind": "agentic.execute", "params": {"task": "text", "image_path": "optional"}},
                {"kind": "agentic.nexusflow.export", "params": {"task": "text", "image_path": "optional"}},
                {"kind": "agentic.nexusflow.run", "params": {"task": "text", "image_path": "optional"}},
                {"kind": "benchmark.run", "params": {"profile": "fast_local"}},
                {"kind": "benchmark.profiles", "params": {}},
                {"kind": "benchmark.analytics.list", "params": {"limit": 20}},
                {"kind": "benchmark.analytics.compare", "params": {"baseline": "run_id", "candidate": "run_id"}},
                {"kind": "benchmark.analytics.trends", "params": {"suite": "vision_random", "metric": "infer_ms_stats.avg", "limit": 20}},
                {"kind": "search.roots", "params": {}},
                {"kind": "search.files", "params": {"query": "keyword", "root": "optional", "limit": 50}},
                {"kind": "search.text", "params": {"pattern": "keyword/regex", "root": "optional", "regex": False, "limit_matches": 50}},
                {"kind": "missions.catalog", "params": {}},
                {"kind": "missions.plan", "params": {"mission": "quick_intel", "inputs": {}}},
                {"kind": "missions.run", "params": {"mission": "quick_intel", "inputs": {}}},
                {"kind": "alerts.status", "params": {}},
                {"kind": "alerts.templates", "params": {}},
                {"kind": "alerts.rules.list", "params": {}},
                {"kind": "alerts.rules.create", "params": {"kind": "job_failure_recent", "name": "Recent Failed Jobs", "config": {"lookback_jobs": 20, "min_failures": 1}}},
                {"kind": "alerts.rules.update", "params": {"rule_id": "rule_*", "enabled": False}},
                {"kind": "alerts.rules.delete", "params": {"rule_id": "rule_*"}},
                {"kind": "alerts.evaluate", "params": {"only_enabled": True}},
                {"kind": "alerts.evaluate_rule", "params": {"rule_id": "rule_*"}},
                {"kind": "alerts.list", "params": {"limit": 20}},
                {"kind": "alerts.ack", "params": {"alert_id": "alert_*", "note": "acknowledged"}},
                {"kind": "remediations.status", "params": {}},
                {"kind": "remediations.templates", "params": {}},
                {"kind": "remediations.policies.list", "params": {}},
                {"kind": "remediations.policies.create", "params": {"match": {"severity": "critical"}, "actions": [{"type": "report_generate", "config": {"profile": "alert_watch"}}]}},
                {"kind": "remediations.policies.update", "params": {"policy_id": "rmp_*", "enabled": False}},
                {"kind": "remediations.policies.delete", "params": {"policy_id": "rmp_*"}},
                {"kind": "remediations.actions.list", "params": {"limit": 20}},
                {"kind": "remediations.handle_alert", "params": {"alert_id": "alert_*"}},
                {"kind": "notifications.status", "params": {}},
                {"kind": "notifications.templates", "params": {}},
                {"kind": "notifications.channels.list", "params": {}},
                {"kind": "notifications.channels.create", "params": {"type": "file_append", "name": "Critical Alert Log", "match": {"topic": "alert.triggered", "severity": "critical"}, "config": {"path": "critical_alerts.jsonl"}}},
                {"kind": "notifications.channels.update", "params": {"channel_id": "ntf_*", "enabled": False}},
                {"kind": "notifications.channels.delete", "params": {"channel_id": "ntf_*"}},
                {"kind": "notifications.deliveries.list", "params": {"limit": 20}},
                {"kind": "notifications.send", "params": {"topic": "ops.note", "message": "test", "severity": "info"}},
                {"kind": "health.status", "params": {}},
                {"kind": "health.profiles", "params": {}},
                {"kind": "health.snapshot", "params": {"profile": "quick", "options": {"notify": False}}},
                {"kind": "health.list", "params": {"limit": 20}},
                {"kind": "health.latest", "params": {"optional": True}},
                {"kind": "health.compare", "params": {"baseline": "health_*", "candidate": "health_*"}},
                {"kind": "incidents.status", "params": {}},
                {"kind": "incidents.templates", "params": {}},
                {"kind": "incidents.list", "params": {"limit": 20}},
                {"kind": "incidents.create", "params": {"title": "GPU fallback detected", "severity": "warning", "tags": ["manual"]}},
                {"kind": "incidents.update", "params": {"incident_id": "inc_*", "status": "investigating"}},
                {"kind": "incidents.note", "params": {"incident_id": "inc_*", "note": "Investigating root cause"}},
                {"kind": "incidents.timeline", "params": {"incident_id": "inc_*", "limit": 50}},
                {"kind": "incidents.capture_bundle", "params": {"incident_id": "inc_*", "health_profile": "full", "report_profile": "health_watch"}},
                {"kind": "incidents.close", "params": {"incident_id": "inc_*", "resolution": "resolved"}},
                {"kind": "opsgraph.status", "params": {}},
                {"kind": "opsgraph.profiles", "params": {}},
                {"kind": "opsgraph.list", "params": {"limit": 20}},
                {"kind": "opsgraph.watch.list", "params": {"limit": 20}},
                {"kind": "opsgraph.watch.get", "params": {"watch_id": "watch_*"}},
                {"kind": "opsgraph.snapshot", "params": {"profile": "runtime", "options": {}}},
                {"kind": "opsgraph.watch", "params": {"profile": "runtime", "compare_with": "latest", "max_hotspots": 10, "max_components": 6, "auto_incident": False}},
                {"kind": "opsgraph.watch.trends", "params": {"limit": 30}},
                {"kind": "opsgraph.watch.anomalies", "params": {"baseline_limit": 20, "z_threshold": 1.8, "delta_threshold": 10.0}},
                {"kind": "opsgraph.watch.forecast", "params": {"limit": 30, "horizon": 5}},
                {"kind": "opsgraph.trace", "params": {"node_id": "incident:inc_*", "depth": 2}},
                {"kind": "opsgraph.compare", "params": {"baseline": "graph_*", "candidate": "graph_*"}},
                {"kind": "opsgraph.path", "params": {"source": "incident:inc_*", "target": "alert:alert_*", "max_depth": 6}},
                {"kind": "opsgraph.blast_radius", "params": {"node_id": "alert:alert_*", "depth": 2, "direction": "out"}},
                {"kind": "opsgraph.hotspots", "params": {"snapshot_id": "optional", "limit": 20}},
                {"kind": "opsgraph.components", "params": {"snapshot_id": "optional", "limit": 20, "order_by": "risk"}},
                {"kind": "opsgraph.explain_incident", "params": {"incident_id": "inc_*", "depth": 2}},
                {"kind": "advisor.status", "params": {}},
                {"kind": "advisor.profiles", "params": {}},
                {"kind": "advisor.list", "params": {"limit": 20}},
                {"kind": "advisor.analyze", "params": {"profile": "quick", "options": {}}},
                {"kind": "advisor.doctor", "params": {}},
                {"kind": "runbooks.status", "params": {}},
                {"kind": "runbooks.templates", "params": {}},
                {"kind": "runbooks.templates.create", "params": {"name": "sample_ops", "steps": [{"type": "operation", "kind": "status", "params": {}}]}},
                {"kind": "runbooks.runs.list", "params": {"limit": 20}},
                {"kind": "runbooks.run", "params": {"template": "ops_baseline_refresh", "dry_run": True}},
                {"kind": "scheduler.status", "params": {}},
                {"kind": "scheduler.start", "params": {}},
                {"kind": "scheduler.stop", "params": {"timeout_sec": 5.0}},
                {"kind": "scheduler.tick", "params": {}},
                {"kind": "scheduler.list", "params": {}},
                {"kind": "scheduler.create", "params": {"target_kind": "missions.run", "params": {"mission": "quick_intel", "inputs": {}}, "interval_sec": 300}},
                {"kind": "scheduler.update", "params": {"schedule_id": "sched_*", "enabled": False}},
                {"kind": "scheduler.delete", "params": {"schedule_id": "sched_*"}},
                {"kind": "scheduler.run_now", "params": {"schedule_id": "sched_*"}},
                {"kind": "report.profiles", "params": {}},
                {"kind": "report.list", "params": {"limit": 20}},
                {"kind": "report.generate", "params": {"profile": "ops_digest", "options": {}}},
            ],
        }


def create_service(project_root: Path | None = None) -> OmniForgeService:
    paths = discover_paths(project_root)
    return OmniForgeService(
        paths=paths,
        chat=SupermixChatAdapter(paths.supermix_runtime),
        vision=VisionNPUAdapter(paths.npu_easy_repo, paths.cifar_onnx),
        neurodsl=NeuroDSLBridge(paths.neurodsl_repo),
        nexusflow=NexusFlowBridge(paths.nexusflow_repo),
        windhawk=WindhawkBridge(paths.windhawk_dir, paths.windhawk_program_data, paths.windhawk_mod_file),
    )

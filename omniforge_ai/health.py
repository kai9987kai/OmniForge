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


class HealthMonitor:
    def __init__(self, service: "OmniForgeService", root_dir: Path, history: "HistoryStore"):
        self.service = service
        self.root_dir = Path(root_dir).resolve()
        self.history = history
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self.snapshots_dir = self.root_dir / "snapshots"
        self.snapshots_dir.mkdir(parents=True, exist_ok=True)
        self.index_file = self.root_dir / "index.jsonl"
        self._lock = threading.RLock()

    def status(self) -> dict[str, Any]:
        latest = self.latest(optional=True)
        latest_snapshot = latest.get("snapshot") if latest.get("ok") else None
        return {
            "ok": True,
            "root_dir": str(self.root_dir),
            "snapshots_dir": str(self.snapshots_dir),
            "index_file": str(self.index_file),
            "snapshot_count": self.list(limit=1_000_000).get("count"),
            "latest_snapshot_id": (latest_snapshot or {}).get("snapshot_id"),
            "latest_score": ((latest_snapshot or {}).get("summary") or {}).get("score"),
            "latest_level": ((latest_snapshot or {}).get("summary") or {}).get("level"),
        }

    def profiles(self) -> dict[str, Any]:
        return {
            "ok": True,
            "profiles": {
                "quick": {
                    "description": "Fast snapshot from runtime state, jobs, alerts, scheduler, and recent benchmarks.",
                    "options": {"include_adapter_health": False, "recent_jobs": 20, "recent_alerts": 20},
                },
                "full": {
                    "description": "Adds adapter/platform checks and more context for reports and comparisons.",
                    "options": {"include_adapter_health": True, "recent_jobs": 30, "recent_alerts": 30},
                },
                "alert_focus": {
                    "description": "Alert-heavy snapshot for incident response and remediation tuning.",
                    "options": {"include_adapter_health": False, "recent_jobs": 20, "recent_alerts": 100},
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

    def get(self, snapshot_id: str) -> dict[str, Any]:
        sid = str(snapshot_id or "").strip()
        if not sid:
            raise ValueError("snapshot_id is required")
        path = self.snapshots_dir / f"{sid}.json"
        if not path.exists():
            raise FileNotFoundError(f"health snapshot not found: {sid}")
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        return {"ok": True, "snapshot": data, "file": str(path)}

    def latest(self, *, optional: bool = False) -> dict[str, Any]:
        items = (self.list(limit=1) or {}).get("items") or []
        if not items:
            if optional:
                return {"ok": True, "snapshot": None}
            raise FileNotFoundError("no health snapshots found")
        sid = str(items[0].get("snapshot_id") or "")
        if not sid:
            if optional:
                return {"ok": True, "snapshot": None}
            raise FileNotFoundError("latest health snapshot entry is missing snapshot_id")
        return self.get(sid)

    def snapshot(self, *, profile: str = "full", options: dict[str, Any] | None = None) -> dict[str, Any]:
        profile = str(profile or "full").strip() or "full"
        profiles = self.profiles()["profiles"]
        if profile not in profiles:
            raise ValueError(f"unknown health profile: {profile}")
        merged_options = dict(profiles[profile].get("options") or {})
        if isinstance(options, dict):
            merged_options.update(options)

        now = _now_utc()
        sid = f"health_{now.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"

        data = self._collect(profile=profile, options=merged_options)
        summary = self._score(data)
        snapshot = {
            "ok": True,
            "snapshot_id": sid,
            "profile": profile,
            "created_at": now.isoformat(),
            "options": _jsonable(merged_options),
            "summary": summary,
            "data": data,
        }

        path = self.snapshots_dir / f"{sid}.json"
        index_row = {
            "snapshot_id": sid,
            "profile": profile,
            "created_at": snapshot["created_at"],
            "score": summary.get("score"),
            "level": summary.get("level"),
            "file": str(path),
        }
        with self._lock:
            path.write_text(json.dumps(_jsonable(snapshot), indent=2, ensure_ascii=False), encoding="utf-8")
            with self.index_file.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(index_row, ensure_ascii=False))
                fh.write("\n")

        self.history.record(
            "health.snapshot",
            {"snapshot_id": sid, "profile": profile, "summary": summary},
            source="health",
            tags=["health", str(summary.get("level") or "info")],
            summary={"snapshot_id": sid, "score": summary.get("score"), "level": summary.get("level")},
        )

        notify = bool(merged_options.get("notify", False))
        notify_below = merged_options.get("notify_below_score")
        if notify and hasattr(self.service, "notifications"):
            should_notify = True
            if notify_below is not None:
                try:
                    should_notify = float(summary.get("score") or 0.0) <= float(notify_below)
                except Exception:
                    should_notify = True
            if should_notify:
                try:
                    note_res = self.service.notifications.notify_health_snapshot(snapshot)
                    snapshot["notification"] = note_res
                except Exception as exc:
                    snapshot["notification_error"] = str(exc)
        return {"ok": True, "snapshot": snapshot, "file": str(path)}

    def compare(self, baseline: str, candidate: str) -> dict[str, Any]:
        base = self.get(baseline)["snapshot"]
        cand = self.get(candidate)["snapshot"]
        base_sum = dict(base.get("summary") or {})
        cand_sum = dict(cand.get("summary") or {})
        component_diffs = []
        base_components = dict(base_sum.get("components") or {})
        cand_components = dict(cand_sum.get("components") or {})
        all_keys = sorted(set(base_components.keys()) | set(cand_components.keys()))
        for key in all_keys:
            b = base_components.get(key)
            c = cand_components.get(key)
            if not isinstance(b, dict) or not isinstance(c, dict):
                continue
            b_score = b.get("score")
            c_score = c.get("score")
            delta = None
            if b_score is not None and c_score is not None:
                try:
                    delta = round(float(c_score) - float(b_score), 2)
                except Exception:
                    delta = None
            component_diffs.append(
                {
                    "component": key,
                    "baseline_score": b_score,
                    "candidate_score": c_score,
                    "delta": delta,
                    "baseline_status": b.get("status"),
                    "candidate_status": c.get("status"),
                }
            )
        score_delta = None
        if base_sum.get("score") is not None and cand_sum.get("score") is not None:
            score_delta = round(float(cand_sum["score"]) - float(base_sum["score"]), 2)

        return {
            "ok": True,
            "baseline": {"snapshot_id": baseline, "summary": base_sum},
            "candidate": {"snapshot_id": candidate, "summary": cand_sum},
            "score_delta": score_delta,
            "component_diffs": component_diffs,
        }

    def _collect(self, *, profile: str, options: dict[str, Any]) -> dict[str, Any]:
        recent_jobs = max(1, int(options.get("recent_jobs", 30)))
        recent_alerts = max(1, int(options.get("recent_alerts", 30)))
        recent_actions = max(1, int(options.get("recent_actions", 30)))
        bench_limit = max(1, int(options.get("benchmark_limit", 5)))
        include_adapter_health = bool(options.get("include_adapter_health", False))

        scheduler = self.service.scheduler.status()
        jobs = self.service.jobs.list(limit=recent_jobs)
        alerts_status = self.service.alerts.status()
        alerts = self.service.alerts.list_alerts(limit=recent_alerts)
        remediations = self.service.remediations.status()
        remediation_actions = self.service.remediations.list_actions(limit=recent_actions)
        benchmarks = self.service.benchmark_analytics.list_runs(limit=bench_limit)
        history_stats = self.service.history.stats()
        notifications_status = self.service.notifications.status()
        deliveries = self.service.notifications.list_deliveries(limit=recent_actions)
        search_roots = self.service.search.roots()

        adapter_health = None
        if include_adapter_health:
            adapter_health = self._adapter_health()

        return {
            "profile": profile,
            "scheduler": scheduler,
            "jobs": jobs,
            "alerts_status": alerts_status,
            "alerts": alerts,
            "remediations": remediations,
            "remediation_actions": remediation_actions,
            "benchmarks": benchmarks,
            "history": history_stats,
            "notifications_status": notifications_status,
            "notification_deliveries": deliveries,
            "search_roots": search_roots,
            "adapter_health": adapter_health,
        }

    def _adapter_health(self) -> dict[str, Any]:
        checks: dict[str, dict[str, Any]] = {}
        try:
            checks["chat"] = _jsonable(self.service.chat.status())
        except Exception as exc:
            checks["chat"] = {"ok": False, "error": str(exc)}
        try:
            checks["vision"] = _jsonable(self.service.vision.status())
        except Exception as exc:
            checks["vision"] = {"ok": False, "error": str(exc)}
        try:
            checks["neurodsl"] = _jsonable(self.service.neurodsl.platform_health())
        except Exception as exc:
            checks["neurodsl"] = {"ok": False, "error": str(exc)}
        try:
            checks["nexusflow"] = _jsonable(self.service.nexusflow.status())
        except Exception as exc:
            checks["nexusflow"] = {"ok": False, "error": str(exc)}
        try:
            checks["windhawk"] = _jsonable(self.service.windhawk.status())
        except Exception as exc:
            checks["windhawk"] = {"ok": False, "error": str(exc)}
        return {"ok": True, "checks": checks}

    def _score(self, data: dict[str, Any]) -> dict[str, Any]:
        components: dict[str, dict[str, Any]] = {}

        scheduler = dict(data.get("scheduler") or {})
        sched_running = bool(scheduler.get("running"))
        enabled_count = int(scheduler.get("enabled_count") or 0)
        if enabled_count <= 0:
            sched_score, sched_status = 100.0, "idle-ok"
        elif sched_running:
            sched_score, sched_status = 100.0, "running"
        else:
            sched_score, sched_status = 35.0, "stopped-with-enabled"
        components["scheduler"] = {"score": sched_score, "status": sched_status, "details": {"enabled_count": enabled_count}}

        jobs = (data.get("jobs") or {}).get("items") or []
        fail_count = sum(1 for j in jobs if str(j.get("status")) == "failed")
        completed_count = sum(1 for j in jobs if str(j.get("status")) == "completed")
        total_jobs = len(jobs)
        fail_ratio = (fail_count / total_jobs) if total_jobs else 0.0
        jobs_score = 100.0 - min(100.0, fail_ratio * 120.0)
        if total_jobs == 0:
            jobs_status = "no-jobs"
            jobs_score = 85.0
        elif fail_count == 0:
            jobs_status = "healthy"
        else:
            jobs_status = "degraded" if fail_ratio < 0.5 else "failing"
        components["jobs"] = {
            "score": round(jobs_score, 2),
            "status": jobs_status,
            "details": {"total": total_jobs, "failed": fail_count, "completed": completed_count},
        }

        alerts_status = dict(data.get("alerts_status") or {})
        alerts = (data.get("alerts") or {}).get("items") or []
        unacked = sum(1 for a in alerts if not a.get("acknowledged"))
        critical_unacked = sum(1 for a in alerts if (not a.get("acknowledged")) and str(a.get("severity")) == "critical")
        warning_unacked = sum(1 for a in alerts if (not a.get("acknowledged")) and str(a.get("severity")) == "warning")
        alert_penalty = min(80.0, critical_unacked * 25.0 + warning_unacked * 8.0 + max(0, unacked - critical_unacked - warning_unacked) * 3.0)
        alerts_score = max(0.0, 100.0 - alert_penalty)
        if critical_unacked > 0:
            alerts_component_status = "critical"
        elif unacked > 0:
            alerts_component_status = "warning"
        else:
            alerts_component_status = "clear"
        components["alerts"] = {
            "score": round(alerts_score, 2),
            "status": alerts_component_status,
            "details": {
                "rule_count": alerts_status.get("rule_count"),
                "recent_alert_count": alerts_status.get("recent_alert_count"),
                "recent_unacked": unacked,
                "critical_unacked": critical_unacked,
            },
        }

        rem_status = dict(data.get("remediations") or {})
        rem_actions = (data.get("remediation_actions") or {}).get("items") or []
        rem_fail = sum(1 for a in rem_actions if not a.get("ok"))
        rem_total = len(rem_actions)
        rem_fail_ratio = (rem_fail / rem_total) if rem_total else 0.0
        rem_score = 100.0 - min(70.0, rem_fail_ratio * 100.0)
        if rem_total == 0:
            rem_status_label = "no-actions"
            rem_score = 90.0
        elif rem_fail == 0:
            rem_status_label = "healthy"
        else:
            rem_status_label = "degraded" if rem_fail_ratio < 0.5 else "failing"
        components["remediations"] = {
            "score": round(rem_score, 2),
            "status": rem_status_label,
            "details": {
                "policy_count": rem_status.get("policy_count"),
                "recent_actions": rem_total,
                "recent_failures": rem_fail,
            },
        }

        benches = (data.get("benchmarks") or {}).get("items") or []
        if not benches:
            bench_score = 80.0
            bench_status = "no-benchmarks"
            bench_details = {}
        else:
            latest = benches[0]
            bench_ok = bool(latest.get("ok", False))
            bench_score = 100.0 if bench_ok else 50.0
            bench_status = "ok" if bench_ok else "failed"
            bench_details = {"latest_run_id": latest.get("run_id"), "latest_profile": latest.get("profile"), "latest_ok": bench_ok}
        components["benchmarks"] = {"score": round(bench_score, 2), "status": bench_status, "details": bench_details}

        hist = dict(data.get("history") or {})
        entry_count = int(hist.get("entry_count") or 0)
        hist_score = 100.0 if entry_count > 0 else 70.0
        components["history"] = {"score": hist_score, "status": ("active" if entry_count > 0 else "empty"), "details": {"entry_count": entry_count}}

        notif_status = dict(data.get("notifications_status") or {})
        deliveries = (data.get("notification_deliveries") or {}).get("items") or []
        notif_fail = sum(1 for d in deliveries if not d.get("ok"))
        notif_total = len(deliveries)
        notif_fail_ratio = (notif_fail / notif_total) if notif_total else 0.0
        notif_score = 100.0 - min(60.0, notif_fail_ratio * 100.0)
        if notif_total == 0:
            notif_status_label = "idle"
            notif_score = 95.0
        elif notif_fail == 0:
            notif_status_label = "healthy"
        else:
            notif_status_label = "degraded"
        components["notifications"] = {
            "score": round(notif_score, 2),
            "status": notif_status_label,
            "details": {
                "channel_count": notif_status.get("channel_count"),
                "enabled_channel_count": notif_status.get("enabled_channel_count"),
                "recent_delivery_count": notif_total,
                "recent_failures": notif_fail,
            },
        }

        adapter = data.get("adapter_health")
        if isinstance(adapter, dict):
            checks = dict(adapter.get("checks") or {})
            total = 0
            score_acc = 0.0
            bad = 0
            for _name, row in checks.items():
                total += 1
                ok_val = self._infer_adapter_ok(row)
                if ok_val:
                    score_acc += 100.0
                else:
                    score_acc += 40.0
                    bad += 1
            adapter_score = (score_acc / total) if total else 100.0
            components["adapters"] = {
                "score": round(adapter_score, 2),
                "status": ("healthy" if bad == 0 else ("degraded" if bad < total else "failing")),
                "details": {"checked": total, "bad": bad},
            }

        # Weighted score emphasizes alerting/scheduler/jobs.
        weights = {
            "scheduler": 1.4,
            "jobs": 1.3,
            "alerts": 1.5,
            "remediations": 1.0,
            "benchmarks": 1.0,
            "history": 0.6,
            "notifications": 0.8,
            "adapters": 1.2,
        }
        weighted_sum = 0.0
        weight_total = 0.0
        for key, comp in components.items():
            try:
                score = float(comp.get("score"))
            except Exception:
                continue
            w = float(weights.get(key, 1.0))
            weighted_sum += score * w
            weight_total += w
        overall = round((weighted_sum / weight_total) if weight_total else 0.0, 2)
        if overall >= 85:
            level = "healthy"
        elif overall >= 65:
            level = "warning"
        else:
            level = "critical"

        highlights = []
        for key, comp in sorted(components.items(), key=lambda kv: float((kv[1] or {}).get("score") or 0.0)):
            score_val = float(comp.get("score") or 0.0)
            if score_val < 80:
                highlights.append(f"{key}:{comp.get('status')}({score_val})")
        return {
            "score": overall,
            "level": level,
            "components": components,
            "highlights": highlights[:8],
        }

    @staticmethod
    def _infer_adapter_ok(row: Any) -> bool:
        if isinstance(row, dict):
            if "ok" in row and isinstance(row.get("ok"), bool):
                return bool(row.get("ok"))
            for key in ("ready", "available", "loaded", "model_loaded"):
                if key in row and isinstance(row.get(key), bool):
                    return bool(row.get(key))
            if "error" in row and row.get("error"):
                return False
        return True


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

from __future__ import annotations

import json
import time
from typing import Any

from flask import Flask, Response, g, jsonify, request, send_file, stream_with_context

from .service import OmniForgeService, create_service


def _json_ok(payload: dict[str, Any]) -> Any:
    return jsonify({"ok": True, **payload})


def _json_err(message: str, status: int = 400, **extra: Any):
    return jsonify({"ok": False, "error": message, **extra}), status


def _boolish(value: Any, default: bool = False) -> bool:
    if value is None:
        return bool(default)
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _sse(event: str, data: Any, *, event_id: str | None = None) -> str:
    payload = []
    if event_id:
        payload.append(f"id: {event_id}")
    if event:
        payload.append(f"event: {event}")
    payload.append("data: " + json.dumps(data, ensure_ascii=False))
    payload.append("")
    return "\n".join(payload) + "\n"


def _require_task_or_plan(payload: dict[str, Any]) -> tuple[str | None, str | None]:
    task = payload.get("task")
    plan = payload.get("plan")
    task_text = None
    if task is not None:
        task_text = str(task).strip() or None
    if plan is not None:
        return task_text, "plan"
    if task_text:
        return task_text, "task"
    return None, None


def build_app(service: OmniForgeService | None = None) -> Flask:
    service = service or create_service()
    agentic = service.agentic_engine()
    app = Flask(__name__)

    @app.before_request
    def _omniforge_request_timer():
        g._omniforge_t0 = time.perf_counter()

    @app.after_request
    def _omniforge_request_history(resp):
        path = request.path or ""
        if not path.startswith("/api/"):
            return resp
        if path.startswith("/api/history") or path.startswith("/api/artifacts/download"):
            return resp
        try:
            duration_ms = round((time.perf_counter() - float(getattr(g, "_omniforge_t0", time.perf_counter()))) * 1000, 1)
            qs = {k: request.args.get(k) for k in request.args.keys()}
            payload = {
                "method": request.method,
                "path": path,
                "query": qs,
                "status_code": resp.status_code,
                "duration_ms": duration_ms,
                "content_type": resp.content_type,
            }
            service.history.record(
                "api.request",
                payload,
                source="flask",
                tags=["api"],
                summary={
                    "method": request.method,
                    "path": path,
                    "status_code": resp.status_code,
                    "duration_ms": duration_ms,
                },
            )
        except Exception:
            pass
        return resp

    @app.get("/")
    def index():
        dashboard = service.paths.dashboard_file
        if dashboard.exists():
            return send_file(dashboard)
        return (
            "<h1>OmniForge AI Workbench</h1><p>Dashboard file missing.</p>"
            "<p>Create `omniforge_ai/dashboard/omniforge_dashboard.html`.</p>"
        )

    @app.get("/favicon.ico")
    def favicon():
        # Silence browser favicon 404 noise in logs when no icon is bundled.
        return Response(status=204)

    @app.get("/api/status")
    def api_status():
        return _json_ok({"status": service.status()})

    @app.get("/api/stream/history")
    def api_stream_history():
        event_type = str(request.args.get("event_type") or "").strip() or None
        tail = max(0, int(request.args.get("tail", "10")))
        start_offset = max(0, int(request.args.get("offset", "0")))
        poll_sec = max(0.1, float(request.args.get("poll_sec", "0.5")))
        heartbeat_sec = max(1.0, float(request.args.get("heartbeat_sec", "10")))
        timeout_sec = max(1.0, float(request.args.get("timeout_sec", "60")))
        max_events = max(1, int(request.args.get("max_events", "500")))

        def generate():
            sent = 0
            t0 = time.time()
            last_beat = 0.0
            offset = start_offset
            # Send a hello frame so clients can confirm connection quickly.
            yield _sse("hello", {"ok": True, "stream": "history", "event_type": event_type})
            if tail > 0:
                items = list(reversed(service.history.tail(limit=tail, event_type=event_type)))
                for row in items:
                    yield _sse("history", row, event_id=str(row.get("id") or ""))
                    sent += 1
                    if sent >= max_events:
                        return
                # Sync offset to EOF after initial tail snapshot.
                offset = int(service.history.read_index_delta(offset=0, max_rows=1).get("offset") or 0)

            while time.time() - t0 < timeout_sec and sent < max_events:
                delta = service.history.read_index_delta(offset=offset, max_rows=200, event_type=event_type)
                offset = int(delta.get("offset") or offset)
                rows = delta.get("rows") or []
                if rows:
                    for row in rows:
                        yield _sse("history", row, event_id=str(row.get("id") or ""))
                        sent += 1
                        if sent >= max_events:
                            break
                    continue
                now = time.time()
                if now - last_beat >= heartbeat_sec:
                    yield ": heartbeat\n\n"
                    last_beat = now
                time.sleep(poll_sec)
            yield _sse("end", {"ok": True, "sent": sent, "timeout_sec": timeout_sec})

        return Response(
            stream_with_context(generate()),
            mimetype="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/ops/catalog")
    def api_ops_catalog():
        try:
            return jsonify(service.operation_catalog())
        except Exception as exc:
            return _json_err(str(exc))

    @app.post("/api/ops/run")
    def api_ops_run():
        payload = request.get_json(force=True, silent=True) or {}
        kind = str(payload.get("kind") or "").strip()
        params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
        if not kind:
            return _json_err("kind is required")
        try:
            return jsonify({"ok": True, "kind": kind, "result": service.run_operation(kind, params)})
        except FileNotFoundError as exc:
            return _json_err(str(exc), status=404)
        except Exception as exc:
            return _json_err(str(exc))

    @app.post("/api/ops/submit")
    def api_ops_submit():
        payload = request.get_json(force=True, silent=True) or {}
        kind = str(payload.get("kind") or "").strip()
        params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
        if not kind:
            return _json_err("kind is required")
        try:
            return jsonify({"ok": True, "job": service.jobs.submit(kind, params)})
        except Exception as exc:
            return _json_err(str(exc))

    @app.get("/api/search/roots")
    def api_search_roots():
        try:
            return jsonify(service.search.roots())
        except Exception as exc:
            return _json_err(str(exc))

    @app.get("/api/search/files")
    def api_search_files():
        try:
            query = str(request.args.get("query") or "").strip()
            if not query:
                return _json_err("query is required")
            ext_arg = str(request.args.get("extensions") or "").strip()
            exts = [e for e in (x.strip() for x in ext_arg.split(",")) if e] if ext_arg else None
            return jsonify(
                service.search.find_files(
                    query,
                    root=(str(request.args.get("root")).strip() if request.args.get("root") else None),
                    glob=(str(request.args.get("glob")).strip() if request.args.get("glob") else None),
                    extensions=exts,
                    limit=int(request.args.get("limit", "100")),
                )
            )
        except Exception as exc:
            return _json_err(str(exc))

    @app.get("/api/search/text")
    def api_search_text():
        try:
            pattern = str(request.args.get("pattern") or "").strip()
            if not pattern:
                return _json_err("pattern is required")
            ext_arg = str(request.args.get("extensions") or "").strip()
            exts = [e for e in (x.strip() for x in ext_arg.split(",")) if e] if ext_arg else None
            return jsonify(
                service.search.search_text(
                    pattern,
                    root=(str(request.args.get("root")).strip() if request.args.get("root") else None),
                    case_sensitive=_boolish(request.args.get("case_sensitive"), default=False),
                    regex=_boolish(request.args.get("regex"), default=False),
                    glob=(str(request.args.get("glob")).strip() if request.args.get("glob") else None),
                    extensions=exts,
                    limit_matches=int(request.args.get("limit_matches", "100")),
                    max_file_bytes=int(request.args.get("max_file_bytes", "1500000")),
                    context_lines=int(request.args.get("context_lines", "1")),
                )
            )
        except Exception as exc:
            return _json_err(str(exc))

    @app.get("/api/missions")
    def api_missions_list():
        try:
            return jsonify(service.missions.catalog())
        except Exception as exc:
            return _json_err(str(exc))

    @app.post("/api/missions/<mission_name>/plan")
    def api_mission_plan(mission_name: str):
        payload = request.get_json(force=True, silent=True) or {}
        inputs = payload.get("inputs") if isinstance(payload.get("inputs"), dict) else {}
        try:
            return jsonify(service.missions.plan(mission_name, inputs=inputs))
        except Exception as exc:
            return _json_err(str(exc))

    @app.post("/api/missions/<mission_name>/run")
    def api_mission_run(mission_name: str):
        payload = request.get_json(force=True, silent=True) or {}
        inputs = payload.get("inputs") if isinstance(payload.get("inputs"), dict) else {}
        try:
            if _boolish(payload.get("async"), default=False):
                return jsonify({"ok": True, "job": service.jobs.submit("missions.run", {"mission": mission_name, "inputs": inputs})})
            return jsonify(service.missions.run(mission_name, inputs=inputs))
        except Exception as exc:
            return _json_err(str(exc))

    @app.get("/api/scheduler/status")
    def api_scheduler_status():
        try:
            return jsonify(service.scheduler.status())
        except Exception as exc:
            return _json_err(str(exc))

    @app.post("/api/scheduler/start")
    def api_scheduler_start():
        try:
            return jsonify(service.scheduler.start())
        except Exception as exc:
            return _json_err(str(exc))

    @app.post("/api/scheduler/stop")
    def api_scheduler_stop():
        payload = request.get_json(force=True, silent=True) or {}
        try:
            return jsonify(service.scheduler.stop(timeout_sec=float(payload.get("timeout_sec", 5.0))))
        except Exception as exc:
            return _json_err(str(exc))

    @app.post("/api/scheduler/tick")
    def api_scheduler_tick():
        try:
            return jsonify(service.scheduler.tick_once())
        except Exception as exc:
            return _json_err(str(exc))

    @app.get("/api/schedules")
    def api_schedules_list():
        try:
            return jsonify(service.scheduler.list())
        except Exception as exc:
            return _json_err(str(exc))

    @app.post("/api/schedules")
    def api_schedules_create():
        payload = request.get_json(force=True, silent=True) or {}
        params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
        try:
            return jsonify(
                service.scheduler.create(
                    name=(str(payload["name"]) if payload.get("name") is not None else None),
                    target_kind=str(payload.get("target_kind") or payload.get("kind") or ""),
                    params=params,
                    interval_sec=float(payload.get("interval_sec") or payload.get("interval") or 60.0),
                    enabled=bool(payload.get("enabled", True)),
                    start_immediately=bool(payload.get("start_immediately", False)),
                )
            )
        except Exception as exc:
            return _json_err(str(exc))

    @app.get("/api/schedules/<schedule_id>")
    def api_schedules_get(schedule_id: str):
        try:
            return jsonify(service.scheduler.get(schedule_id))
        except FileNotFoundError as exc:
            return _json_err(str(exc), status=404)
        except Exception as exc:
            return _json_err(str(exc))

    @app.post("/api/schedules/<schedule_id>")
    def api_schedules_update(schedule_id: str):
        payload = request.get_json(force=True, silent=True) or {}
        try:
            return jsonify(
                service.scheduler.update(
                    schedule_id,
                    name=payload.get("name"),
                    target_kind=payload.get("target_kind"),
                    params=(payload.get("params") if isinstance(payload.get("params"), dict) else None),
                    interval_sec=payload.get("interval_sec"),
                    enabled=payload.get("enabled"),
                )
            )
        except FileNotFoundError as exc:
            return _json_err(str(exc), status=404)
        except Exception as exc:
            return _json_err(str(exc))

    @app.delete("/api/schedules/<schedule_id>")
    def api_schedules_delete(schedule_id: str):
        try:
            return jsonify(service.scheduler.delete(schedule_id))
        except FileNotFoundError as exc:
            return _json_err(str(exc), status=404)
        except Exception as exc:
            return _json_err(str(exc))

    @app.post("/api/schedules/<schedule_id>/run")
    def api_schedules_run_now(schedule_id: str):
        try:
            return jsonify(service.scheduler.run_now(schedule_id))
        except FileNotFoundError as exc:
            return _json_err(str(exc), status=404)
        except Exception as exc:
            return _json_err(str(exc))

    @app.get("/api/reports/profiles")
    def api_reports_profiles():
        try:
            return jsonify(service.reports.profiles())
        except Exception as exc:
            return _json_err(str(exc))

    @app.get("/api/reports/list")
    def api_reports_list():
        try:
            return jsonify(service.reports.list(limit=int(request.args.get("limit", "50"))))
        except Exception as exc:
            return _json_err(str(exc))

    @app.post("/api/reports/generate")
    def api_reports_generate():
        payload = request.get_json(force=True, silent=True) or {}
        options = payload.get("options") if isinstance(payload.get("options"), dict) else {}
        try:
            return jsonify(service.reports.generate(profile=str(payload.get("profile") or "ops_digest"), options=options))
        except Exception as exc:
            return _json_err(str(exc))

    @app.get("/api/alerts/status")
    def api_alerts_status():
        try:
            return jsonify(service.alerts.status())
        except Exception as exc:
            return _json_err(str(exc))

    @app.get("/api/alerts/templates")
    def api_alerts_templates():
        try:
            return jsonify(service.alerts.templates())
        except Exception as exc:
            return _json_err(str(exc))

    @app.get("/api/alerts/rules")
    def api_alert_rules_list():
        try:
            return jsonify(service.alerts.list_rules())
        except Exception as exc:
            return _json_err(str(exc))

    @app.post("/api/alerts/rules")
    def api_alert_rules_create():
        payload = request.get_json(force=True, silent=True) or {}
        config = payload.get("config") if isinstance(payload.get("config"), dict) else {}
        try:
            return jsonify(
                service.alerts.create_rule(
                    kind=str(payload.get("kind") or ""),
                    name=(str(payload["name"]) if payload.get("name") is not None else None),
                    config=config,
                    severity=str(payload.get("severity") or "warning"),
                    enabled=bool(payload.get("enabled", True)),
                    cooldown_sec=float(payload.get("cooldown_sec", 300.0)),
                )
            )
        except Exception as exc:
            return _json_err(str(exc))

    @app.get("/api/alerts/rules/<rule_id>")
    def api_alert_rules_get(rule_id: str):
        try:
            return jsonify(service.alerts.get_rule(rule_id))
        except FileNotFoundError as exc:
            return _json_err(str(exc), status=404)
        except Exception as exc:
            return _json_err(str(exc))

    @app.post("/api/alerts/rules/<rule_id>")
    def api_alert_rules_update(rule_id: str):
        payload = request.get_json(force=True, silent=True) or {}
        try:
            return jsonify(
                service.alerts.update_rule(
                    rule_id,
                    name=payload.get("name"),
                    config=(payload.get("config") if isinstance(payload.get("config"), dict) else None),
                    severity=payload.get("severity"),
                    enabled=payload.get("enabled"),
                    cooldown_sec=payload.get("cooldown_sec"),
                )
            )
        except FileNotFoundError as exc:
            return _json_err(str(exc), status=404)
        except Exception as exc:
            return _json_err(str(exc))

    @app.delete("/api/alerts/rules/<rule_id>")
    def api_alert_rules_delete(rule_id: str):
        try:
            return jsonify(service.alerts.delete_rule(rule_id))
        except FileNotFoundError as exc:
            return _json_err(str(exc), status=404)
        except Exception as exc:
            return _json_err(str(exc))

    @app.post("/api/alerts/evaluate")
    def api_alerts_evaluate():
        payload = request.get_json(force=True, silent=True) or {}
        try:
            return jsonify(
                service.alerts.evaluate_all(
                    only_enabled=bool(payload.get("only_enabled", True)),
                    force=bool(payload.get("force", False)),
                )
            )
        except Exception as exc:
            return _json_err(str(exc))

    @app.post("/api/alerts/evaluate/<rule_id>")
    def api_alerts_evaluate_rule(rule_id: str):
        payload = request.get_json(force=True, silent=True) or {}
        try:
            return jsonify(service.alerts.evaluate_rule(rule_id, force=bool(payload.get("force", False))))
        except FileNotFoundError as exc:
            return _json_err(str(exc), status=404)
        except Exception as exc:
            return _json_err(str(exc))

    @app.get("/api/alerts/events")
    def api_alert_events_list():
        try:
            ack_param = request.args.get("acknowledged")
            ack = None if ack_param is None else _boolish(ack_param, default=False)
            return jsonify(
                service.alerts.list_alerts(
                    limit=int(request.args.get("limit", "50")),
                    rule_id=(str(request.args.get("rule_id")).strip() if request.args.get("rule_id") else None),
                    severity=(str(request.args.get("severity")).strip() if request.args.get("severity") else None),
                    acknowledged=ack,
                )
            )
        except Exception as exc:
            return _json_err(str(exc))

    @app.get("/api/alerts/events/<alert_id>")
    def api_alert_events_get(alert_id: str):
        try:
            return jsonify(service.alerts.get_alert(alert_id))
        except FileNotFoundError as exc:
            return _json_err(str(exc), status=404)
        except Exception as exc:
            return _json_err(str(exc))

    @app.post("/api/alerts/events/<alert_id>/ack")
    def api_alert_events_ack(alert_id: str):
        payload = request.get_json(force=True, silent=True) or {}
        try:
            return jsonify(service.alerts.ack_alert(alert_id, note=(str(payload.get("note") or "") or None)))
        except FileNotFoundError as exc:
            return _json_err(str(exc), status=404)
        except Exception as exc:
            return _json_err(str(exc))

    @app.get("/api/remediations/status")
    def api_remediations_status():
        try:
            return jsonify(service.remediations.status())
        except Exception as exc:
            return _json_err(str(exc))

    @app.get("/api/remediations/templates")
    def api_remediations_templates():
        try:
            return jsonify(service.remediations.templates())
        except Exception as exc:
            return _json_err(str(exc))

    @app.get("/api/remediations/policies")
    def api_remediation_policies_list():
        try:
            return jsonify(service.remediations.list_policies())
        except Exception as exc:
            return _json_err(str(exc))

    @app.post("/api/remediations/policies")
    def api_remediation_policies_create():
        payload = request.get_json(force=True, silent=True) or {}
        match = payload.get("match") if isinstance(payload.get("match"), dict) else {}
        actions = payload.get("actions") if isinstance(payload.get("actions"), list) else []
        try:
            return jsonify(
                service.remediations.create_policy(
                    name=(str(payload["name"]) if payload.get("name") is not None else None),
                    match=match,
                    actions=actions,
                    enabled=bool(payload.get("enabled", True)),
                    cooldown_sec=float(payload.get("cooldown_sec", 300.0)),
                )
            )
        except Exception as exc:
            return _json_err(str(exc))

    @app.get("/api/remediations/policies/<policy_id>")
    def api_remediation_policies_get(policy_id: str):
        try:
            return jsonify(service.remediations.get_policy(policy_id))
        except FileNotFoundError as exc:
            return _json_err(str(exc), status=404)
        except Exception as exc:
            return _json_err(str(exc))

    @app.post("/api/remediations/policies/<policy_id>")
    def api_remediation_policies_update(policy_id: str):
        payload = request.get_json(force=True, silent=True) or {}
        try:
            return jsonify(
                service.remediations.update_policy(
                    policy_id,
                    name=payload.get("name"),
                    match=(payload.get("match") if isinstance(payload.get("match"), dict) else None),
                    actions=(payload.get("actions") if isinstance(payload.get("actions"), list) else None),
                    enabled=payload.get("enabled"),
                    cooldown_sec=payload.get("cooldown_sec"),
                )
            )
        except FileNotFoundError as exc:
            return _json_err(str(exc), status=404)
        except Exception as exc:
            return _json_err(str(exc))

    @app.delete("/api/remediations/policies/<policy_id>")
    def api_remediation_policies_delete(policy_id: str):
        try:
            return jsonify(service.remediations.delete_policy(policy_id))
        except FileNotFoundError as exc:
            return _json_err(str(exc), status=404)
        except Exception as exc:
            return _json_err(str(exc))

    @app.get("/api/remediations/actions")
    def api_remediation_actions_list():
        try:
            ok_param = request.args.get("ok")
            ok_filter = None if ok_param is None else _boolish(ok_param, default=False)
            return jsonify(
                service.remediations.list_actions(
                    limit=int(request.args.get("limit", "50")),
                    policy_id=(str(request.args.get("policy_id")).strip() if request.args.get("policy_id") else None),
                    ok=ok_filter,
                )
            )
        except Exception as exc:
            return _json_err(str(exc))

    @app.post("/api/remediations/handle-alert")
    def api_remediation_handle_alert():
        payload = request.get_json(force=True, silent=True) or {}
        force = bool(payload.get("force", False))
        alert_obj = payload.get("alert")
        alert_id = str(payload.get("alert_id") or "").strip()
        if alert_obj is None and not alert_id:
            return _json_err("alert or alert_id is required")
        try:
            target = alert_obj if alert_obj is not None else alert_id
            return jsonify(service.remediations.handle_alert(target, force=force))
        except FileNotFoundError as exc:
            return _json_err(str(exc), status=404)
        except Exception as exc:
            return _json_err(str(exc))

    @app.post("/api/remediations/handle-alert/<alert_id>")
    def api_remediation_handle_alert_id(alert_id: str):
        payload = request.get_json(force=True, silent=True) or {}
        try:
            return jsonify(service.remediations.handle_alert(alert_id, force=bool(payload.get("force", False))))
        except FileNotFoundError as exc:
            return _json_err(str(exc), status=404)
        except Exception as exc:
            return _json_err(str(exc))

    @app.get("/api/notifications/status")
    def api_notifications_status():
        try:
            return jsonify(service.notifications.status())
        except Exception as exc:
            return _json_err(str(exc))

    @app.get("/api/notifications/templates")
    def api_notifications_templates():
        try:
            return jsonify(service.notifications.templates())
        except Exception as exc:
            return _json_err(str(exc))

    @app.get("/api/notifications/channels")
    def api_notification_channels_list():
        try:
            return jsonify(service.notifications.list_channels())
        except Exception as exc:
            return _json_err(str(exc))

    @app.post("/api/notifications/channels")
    def api_notification_channels_create():
        payload = request.get_json(force=True, silent=True) or {}
        try:
            return jsonify(
                service.notifications.create_channel(
                    channel_type=str(payload.get("type") or payload.get("channel_type") or ""),
                    name=(str(payload["name"]) if payload.get("name") is not None else None),
                    config=(payload.get("config") if isinstance(payload.get("config"), dict) else {}),
                    match=(payload.get("match") if isinstance(payload.get("match"), dict) else {}),
                    enabled=bool(payload.get("enabled", True)),
                )
            )
        except Exception as exc:
            return _json_err(str(exc))

    @app.get("/api/notifications/channels/<channel_id>")
    def api_notification_channels_get(channel_id: str):
        try:
            return jsonify(service.notifications.get_channel(channel_id))
        except FileNotFoundError as exc:
            return _json_err(str(exc), status=404)
        except Exception as exc:
            return _json_err(str(exc))

    @app.post("/api/notifications/channels/<channel_id>")
    def api_notification_channels_update(channel_id: str):
        payload = request.get_json(force=True, silent=True) or {}
        try:
            return jsonify(
                service.notifications.update_channel(
                    channel_id,
                    name=payload.get("name"),
                    config=(payload.get("config") if isinstance(payload.get("config"), dict) else None),
                    match=(payload.get("match") if isinstance(payload.get("match"), dict) else None),
                    enabled=payload.get("enabled"),
                )
            )
        except FileNotFoundError as exc:
            return _json_err(str(exc), status=404)
        except Exception as exc:
            return _json_err(str(exc))

    @app.delete("/api/notifications/channels/<channel_id>")
    def api_notification_channels_delete(channel_id: str):
        try:
            return jsonify(service.notifications.delete_channel(channel_id))
        except FileNotFoundError as exc:
            return _json_err(str(exc), status=404)
        except Exception as exc:
            return _json_err(str(exc))

    @app.get("/api/notifications/deliveries")
    def api_notification_deliveries_list():
        try:
            ok_param = request.args.get("ok")
            ok_filter = None if ok_param is None else _boolish(ok_param, default=False)
            return jsonify(
                service.notifications.list_deliveries(
                    limit=int(request.args.get("limit", "50")),
                    channel_id=(str(request.args.get("channel_id")).strip() if request.args.get("channel_id") else None),
                    ok=ok_filter,
                    topic=(str(request.args.get("topic")).strip() if request.args.get("topic") else None),
                    severity=(str(request.args.get("severity")).strip() if request.args.get("severity") else None),
                )
            )
        except Exception as exc:
            return _json_err(str(exc))

    @app.post("/api/notifications/send")
    def api_notifications_send():
        payload = request.get_json(force=True, silent=True) or {}
        topic = str(payload.get("topic") or "").strip()
        message = str(payload.get("message") or "").strip()
        if not topic or not message:
            return _json_err("topic and message are required")
        target_channel_ids = payload.get("target_channel_ids") if isinstance(payload.get("target_channel_ids"), list) else None
        try:
            return jsonify(
                service.notifications.dispatch(
                    topic=topic,
                    message=message,
                    payload=(payload.get("payload") if isinstance(payload.get("payload"), dict) else {}),
                    severity=str(payload.get("severity") or "info"),
                    tags=(payload.get("tags") if isinstance(payload.get("tags"), list) else None),
                    target_channel_ids=target_channel_ids,
                )
            )
        except Exception as exc:
            return _json_err(str(exc))

    @app.get("/api/health/status")
    def api_health_status():
        try:
            return jsonify(service.health.status())
        except Exception as exc:
            return _json_err(str(exc))

    @app.get("/api/health/profiles")
    def api_health_profiles():
        try:
            return jsonify(service.health.profiles())
        except Exception as exc:
            return _json_err(str(exc))

    @app.get("/api/health/snapshots")
    def api_health_snapshots_list():
        try:
            return jsonify(service.health.list(limit=int(request.args.get("limit", "50"))))
        except Exception as exc:
            return _json_err(str(exc))

    @app.post("/api/health/snapshots")
    def api_health_snapshots_create():
        payload = request.get_json(force=True, silent=True) or {}
        options = payload.get("options") if isinstance(payload.get("options"), dict) else {}
        try:
            return jsonify(service.health.snapshot(profile=str(payload.get("profile") or "full"), options=options))
        except Exception as exc:
            return _json_err(str(exc))

    @app.get("/api/health/snapshots/latest")
    def api_health_snapshot_latest():
        try:
            return jsonify(service.health.latest(optional=_boolish(request.args.get("optional"), default=False)))
        except FileNotFoundError as exc:
            return _json_err(str(exc), status=404)
        except Exception as exc:
            return _json_err(str(exc))

    @app.get("/api/health/snapshots/<snapshot_id>")
    def api_health_snapshot_get(snapshot_id: str):
        try:
            return jsonify(service.health.get(snapshot_id))
        except FileNotFoundError as exc:
            return _json_err(str(exc), status=404)
        except Exception as exc:
            return _json_err(str(exc))

    @app.post("/api/health/compare")
    def api_health_compare():
        payload = request.get_json(force=True, silent=True) or {}
        baseline = str(payload.get("baseline") or "").strip()
        candidate = str(payload.get("candidate") or "").strip()
        if not baseline or not candidate:
            return _json_err("baseline and candidate are required")
        try:
            return jsonify(service.health.compare(baseline, candidate))
        except FileNotFoundError as exc:
            return _json_err(str(exc), status=404)
        except Exception as exc:
            return _json_err(str(exc))

    @app.get("/api/incidents/status")
    def api_incidents_status():
        try:
            return jsonify(service.incidents.status())
        except Exception as exc:
            return _json_err(str(exc))

    @app.get("/api/incidents/templates")
    def api_incidents_templates():
        try:
            return jsonify(service.incidents.templates())
        except Exception as exc:
            return _json_err(str(exc))

    @app.get("/api/incidents")
    def api_incidents_list():
        try:
            return jsonify(
                service.incidents.list(
                    limit=int(request.args.get("limit", "50")),
                    status=(str(request.args.get("status")).strip() if request.args.get("status") else None),
                    severity=(str(request.args.get("severity")).strip() if request.args.get("severity") else None),
                    source=(str(request.args.get("source")).strip() if request.args.get("source") else None),
                )
            )
        except Exception as exc:
            return _json_err(str(exc))

    @app.post("/api/incidents")
    def api_incidents_create():
        payload = request.get_json(force=True, silent=True) or {}
        try:
            return jsonify(
                service.incidents.create(
                    title=str(payload.get("title") or ""),
                    severity=str(payload.get("severity") or "warning"),
                    description=(str(payload["description"]) if payload.get("description") is not None else None),
                    source=str(payload.get("source") or "manual"),
                    status=str(payload.get("status") or "open"),
                    tags=(payload.get("tags") if isinstance(payload.get("tags"), list) else None),
                    metadata=(payload.get("metadata") if isinstance(payload.get("metadata"), dict) else None),
                    alert=(payload.get("alert") if isinstance(payload.get("alert"), dict) else None),
                    auto_created=bool(payload.get("auto_created", False)),
                )
            )
        except Exception as exc:
            return _json_err(str(exc))

    @app.get("/api/incidents/<incident_id>")
    def api_incidents_get(incident_id: str):
        try:
            return jsonify(service.incidents.get(incident_id, timeline_limit=int(request.args.get("timeline_limit", "50"))))
        except FileNotFoundError as exc:
            return _json_err(str(exc), status=404)
        except Exception as exc:
            return _json_err(str(exc))

    @app.post("/api/incidents/<incident_id>")
    def api_incidents_update(incident_id: str):
        payload = request.get_json(force=True, silent=True) or {}
        try:
            return jsonify(
                service.incidents.update(
                    incident_id,
                    title=payload.get("title"),
                    severity=payload.get("severity"),
                    status=payload.get("status"),
                    description=payload.get("description"),
                    resolution=payload.get("resolution"),
                    tags=(payload.get("tags") if isinstance(payload.get("tags"), list) else None),
                    metadata_patch=(payload.get("metadata_patch") if isinstance(payload.get("metadata_patch"), dict) else None),
                )
            )
        except FileNotFoundError as exc:
            return _json_err(str(exc), status=404)
        except Exception as exc:
            return _json_err(str(exc))

    @app.post("/api/incidents/<incident_id>/close")
    def api_incidents_close(incident_id: str):
        payload = request.get_json(force=True, silent=True) or {}
        try:
            return jsonify(
                service.incidents.close(
                    incident_id,
                    resolution=(str(payload["resolution"]) if payload.get("resolution") is not None else None),
                    note=(str(payload["note"]) if payload.get("note") is not None else None),
                )
            )
        except FileNotFoundError as exc:
            return _json_err(str(exc), status=404)
        except Exception as exc:
            return _json_err(str(exc))

    @app.post("/api/incidents/<incident_id>/reopen")
    def api_incidents_reopen(incident_id: str):
        payload = request.get_json(force=True, silent=True) or {}
        try:
            return jsonify(service.incidents.reopen(incident_id, note=(str(payload.get("note") or "") or None)))
        except FileNotFoundError as exc:
            return _json_err(str(exc), status=404)
        except Exception as exc:
            return _json_err(str(exc))

    @app.get("/api/incidents/<incident_id>/timeline")
    def api_incidents_timeline(incident_id: str):
        try:
            return jsonify(service.incidents.timeline(incident_id=incident_id, limit=int(request.args.get("limit", "100"))))
        except Exception as exc:
            return _json_err(str(exc))

    @app.post("/api/incidents/<incident_id>/notes")
    def api_incidents_note(incident_id: str):
        payload = request.get_json(force=True, silent=True) or {}
        note = str(payload.get("note") or "").strip()
        if not note:
            return _json_err("note is required")
        try:
            return jsonify(
                service.incidents.add_note(
                    incident_id,
                    note=note,
                    author=(str(payload["author"]) if payload.get("author") is not None else None),
                    kind=str(payload.get("kind") or "note"),
                )
            )
        except FileNotFoundError as exc:
            return _json_err(str(exc), status=404)
        except Exception as exc:
            return _json_err(str(exc))

    @app.post("/api/incidents/<incident_id>/link-alert")
    def api_incidents_link_alert(incident_id: str):
        payload = request.get_json(force=True, silent=True) or {}
        if payload.get("alert") is not None and isinstance(payload.get("alert"), dict):
            target = payload["alert"]
        else:
            alert_id = str(payload.get("alert_id") or "").strip()
            if not alert_id:
                return _json_err("alert or alert_id is required")
            target = alert_id
        try:
            return jsonify(service.incidents.link_alert(incident_id, target))
        except FileNotFoundError as exc:
            return _json_err(str(exc), status=404)
        except Exception as exc:
            return _json_err(str(exc))

    @app.post("/api/incidents/<incident_id>/capture-bundle")
    def api_incidents_capture_bundle(incident_id: str):
        payload = request.get_json(force=True, silent=True) or {}
        try:
            return jsonify(
                service.incidents.capture_bundle(
                    incident_id,
                    health_profile=str(payload.get("health_profile") or "full"),
                    report_profile=str(payload.get("report_profile") or "health_watch"),
                    include_health=bool(payload.get("include_health", True)),
                    include_report=bool(payload.get("include_report", True)),
                )
            )
        except FileNotFoundError as exc:
            return _json_err(str(exc), status=404)
        except Exception as exc:
            return _json_err(str(exc))

    @app.get("/api/runbooks/status")
    def api_runbooks_status():
        try:
            return jsonify(service.runbooks.status())
        except Exception as exc:
            return _json_err(str(exc))

    @app.get("/api/runbooks/templates")
    def api_runbooks_templates():
        try:
            include_steps = _boolish(request.args.get("include_steps"), default=False)
            return jsonify(service.runbooks.list_templates(include_steps=include_steps))
        except Exception as exc:
            return _json_err(str(exc))

    @app.post("/api/runbooks/templates")
    def api_runbooks_templates_create():
        payload = request.get_json(force=True, silent=True) or {}
        try:
            return jsonify(
                service.runbooks.create_template(
                    name=str(payload.get("name") or ""),
                    title=(str(payload["title"]) if payload.get("title") is not None else None),
                    description=(str(payload["description"]) if payload.get("description") is not None else None),
                    steps=(payload.get("steps") if isinstance(payload.get("steps"), list) else None),
                    tags=(payload.get("tags") if isinstance(payload.get("tags"), list) else None),
                    metadata=(payload.get("metadata") if isinstance(payload.get("metadata"), dict) else None),
                )
            )
        except Exception as exc:
            return _json_err(str(exc))

    @app.get("/api/runbooks/templates/<template_name>")
    def api_runbooks_template_get(template_name: str):
        try:
            return jsonify(service.runbooks.get_template(template_name))
        except FileNotFoundError as exc:
            return _json_err(str(exc), status=404)
        except Exception as exc:
            return _json_err(str(exc))

    @app.post("/api/runbooks/templates/<template_name>")
    def api_runbooks_template_update(template_name: str):
        payload = request.get_json(force=True, silent=True) or {}
        try:
            return jsonify(
                service.runbooks.update_template(
                    template_name,
                    title=payload.get("title"),
                    description=payload.get("description"),
                    steps=(payload.get("steps") if isinstance(payload.get("steps"), list) else None),
                    tags=(payload.get("tags") if isinstance(payload.get("tags"), list) else None),
                    metadata=(payload.get("metadata") if isinstance(payload.get("metadata"), dict) else None),
                )
            )
        except FileNotFoundError as exc:
            return _json_err(str(exc), status=404)
        except Exception as exc:
            return _json_err(str(exc))

    @app.delete("/api/runbooks/templates/<template_name>")
    def api_runbooks_template_delete(template_name: str):
        try:
            return jsonify(service.runbooks.delete_template(template_name))
        except FileNotFoundError as exc:
            return _json_err(str(exc), status=404)
        except Exception as exc:
            return _json_err(str(exc))

    @app.get("/api/runbooks/runs")
    def api_runbooks_runs():
        try:
            return jsonify(
                service.runbooks.list_runs(
                    limit=int(request.args.get("limit", "50")),
                    status=(str(request.args.get("status")).strip() if request.args.get("status") else None),
                    template=(str(request.args.get("template")).strip() if request.args.get("template") else None),
                    incident_id=(str(request.args.get("incident_id")).strip() if request.args.get("incident_id") else None),
                )
            )
        except Exception as exc:
            return _json_err(str(exc))

    @app.get("/api/runbooks/runs/<run_id>")
    def api_runbooks_run_get(run_id: str):
        try:
            return jsonify(service.runbooks.get_run(run_id))
        except FileNotFoundError as exc:
            return _json_err(str(exc), status=404)
        except Exception as exc:
            return _json_err(str(exc))

    @app.post("/api/runbooks/run")
    def api_runbooks_run():
        payload = request.get_json(force=True, silent=True) or {}
        try:
            return jsonify(
                service.runbooks.run(
                    str(payload.get("template") or payload.get("name") or ""),
                    context=(payload.get("context") if isinstance(payload.get("context"), dict) else None),
                    incident_id=(str(payload["incident_id"]) if payload.get("incident_id") is not None else None),
                    alert_id=(str(payload["alert_id"]) if payload.get("alert_id") is not None else None),
                    dry_run=bool(payload.get("dry_run", False)),
                    stop_on_error=(bool(payload["stop_on_error"]) if payload.get("stop_on_error") is not None else None),
                )
            )
        except FileNotFoundError as exc:
            return _json_err(str(exc), status=404)
        except Exception as exc:
            return _json_err(str(exc))

    @app.get("/api/opsgraph/status")
    def api_opsgraph_status():
        try:
            return jsonify(service.opsgraph.status())
        except Exception as exc:
            return _json_err(str(exc))

    @app.get("/api/opsgraph/profiles")
    def api_opsgraph_profiles():
        try:
            return jsonify(service.opsgraph.profiles())
        except Exception as exc:
            return _json_err(str(exc))

    @app.get("/api/opsgraph/snapshots")
    def api_opsgraph_snapshots():
        try:
            return jsonify(
                service.opsgraph.list_snapshots(
                    limit=int(request.args.get("limit", "50")),
                    profile=(str(request.args.get("profile")).strip() if request.args.get("profile") else None),
                )
            )
        except Exception as exc:
            return _json_err(str(exc))

    @app.post("/api/opsgraph/snapshots")
    def api_opsgraph_snapshot_create():
        payload = request.get_json(force=True, silent=True) or {}
        try:
            return jsonify(
                service.opsgraph.snapshot(
                    profile=str(payload.get("profile") or "runtime"),
                    options=(payload.get("options") if isinstance(payload.get("options"), dict) else {}),
                    persist=bool(payload.get("persist", True)),
                )
            )
        except Exception as exc:
            return _json_err(str(exc))

    @app.get("/api/opsgraph/snapshots/<snapshot_id>")
    def api_opsgraph_snapshot_get(snapshot_id: str):
        try:
            return jsonify(service.opsgraph.get_snapshot(snapshot_id))
        except FileNotFoundError as exc:
            return _json_err(str(exc), status=404)
        except Exception as exc:
            return _json_err(str(exc))

    @app.get("/api/opsgraph/watch")
    def api_opsgraph_watch_list():
        try:
            return jsonify(
                service.opsgraph.list_watch(
                    limit=int(request.args.get("limit", "50")),
                    profile=(str(request.args.get("profile")).strip() if request.args.get("profile") else None),
                )
            )
        except Exception as exc:
            return _json_err(str(exc))

    @app.post("/api/opsgraph/watch")
    def api_opsgraph_watch_run():
        payload = request.get_json(force=True, silent=True) or {}
        try:
            return jsonify(
                service.opsgraph.watch(
                    profile=str(payload.get("profile") or "runtime"),
                    options=(payload.get("options") if isinstance(payload.get("options"), dict) else {}),
                    compare_with=(str(payload["compare_with"]) if payload.get("compare_with") is not None else "latest"),
                    max_hotspots=int(payload.get("max_hotspots", 10)),
                    max_components=int(payload.get("max_components", 6)),
                    auto_incident=bool(payload.get("auto_incident", False)),
                    incident_threshold=float(payload.get("incident_threshold", 80.0)),
                    incident_cooldown_sec=float(payload.get("incident_cooldown_sec", 3600.0)),
                    persist=bool(payload.get("persist", True)),
                )
            )
        except FileNotFoundError as exc:
            return _json_err(str(exc), status=404)
        except Exception as exc:
            return _json_err(str(exc))

    @app.get("/api/opsgraph/watch/trends")
    def api_opsgraph_watch_trends():
        try:
            return jsonify(
                service.opsgraph.watch_trends(
                    limit=int(request.args.get("limit", "50")),
                    profile=(str(request.args.get("profile")).strip() if request.args.get("profile") else None),
                )
            )
        except Exception as exc:
            return _json_err(str(exc))

    @app.get("/api/opsgraph/watch/anomalies")
    def api_opsgraph_watch_anomalies():
        try:
            return jsonify(
                service.opsgraph.watch_anomalies(
                    watch_id=(str(request.args.get("watch_id")).strip() if request.args.get("watch_id") else None),
                    profile=(str(request.args.get("profile")).strip() if request.args.get("profile") else None),
                    baseline_limit=int(request.args.get("baseline_limit", "20")),
                    z_threshold=float(request.args.get("z_threshold", "1.8")),
                    delta_threshold=float(request.args.get("delta_threshold", "10.0")),
                )
            )
        except FileNotFoundError as exc:
            return _json_err(str(exc), status=404)
        except Exception as exc:
            return _json_err(str(exc))

    @app.get("/api/opsgraph/watch/forecast")
    def api_opsgraph_watch_forecast():
        try:
            return jsonify(
                service.opsgraph.watch_forecast(
                    limit=int(request.args.get("limit", "30")),
                    horizon=int(request.args.get("horizon", "5")),
                    profile=(str(request.args.get("profile")).strip() if request.args.get("profile") else None),
                )
            )
        except Exception as exc:
            return _json_err(str(exc))

    @app.get("/api/opsgraph/watch/<watch_id>")
    def api_opsgraph_watch_get(watch_id: str):
        try:
            return jsonify(service.opsgraph.get_watch(watch_id))
        except FileNotFoundError as exc:
            return _json_err(str(exc), status=404)
        except Exception as exc:
            return _json_err(str(exc))

    @app.get("/api/opsgraph/hotspots")
    def api_opsgraph_hotspots():
        try:
            return jsonify(
                service.opsgraph.hotspots(
                    snapshot_id=(str(request.args.get("snapshot_id")).strip() if request.args.get("snapshot_id") else None),
                    limit=int(request.args.get("limit", "20")),
                    include_zero=_boolish(request.args.get("include_zero"), default=False),
                )
            )
        except FileNotFoundError as exc:
            return _json_err(str(exc), status=404)
        except Exception as exc:
            return _json_err(str(exc))

    @app.get("/api/opsgraph/components")
    def api_opsgraph_components():
        try:
            return jsonify(
                service.opsgraph.components(
                    snapshot_id=(str(request.args.get("snapshot_id")).strip() if request.args.get("snapshot_id") else None),
                    limit=int(request.args.get("limit", "20")),
                    order_by=str(request.args.get("order_by") or "risk"),
                )
            )
        except FileNotFoundError as exc:
            return _json_err(str(exc), status=404)
        except Exception as exc:
            return _json_err(str(exc))

    @app.post("/api/opsgraph/trace")
    def api_opsgraph_trace():
        payload = request.get_json(force=True, silent=True) or {}
        node_id = str(payload.get("node_id") or payload.get("id") or "").strip()
        if not node_id:
            return _json_err("node_id is required")
        try:
            return jsonify(
                service.opsgraph.trace(
                    node_id,
                    snapshot_id=(str(payload["snapshot_id"]) if payload.get("snapshot_id") is not None else None),
                    depth=int(payload.get("depth", 2)),
                    direction=str(payload.get("direction") or "both"),
                    max_nodes=int(payload.get("max_nodes", 200)),
                )
            )
        except FileNotFoundError as exc:
            return _json_err(str(exc), status=404)
        except Exception as exc:
            return _json_err(str(exc))

    @app.post("/api/opsgraph/compare")
    def api_opsgraph_compare():
        payload = request.get_json(force=True, silent=True) or {}
        baseline = str(payload.get("baseline") or "").strip()
        candidate = str(payload.get("candidate") or "").strip()
        if not baseline or not candidate:
            return _json_err("baseline and candidate are required")
        try:
            return jsonify(
                service.opsgraph.compare(
                    baseline,
                    candidate,
                    max_samples=int(payload.get("max_samples", 25)),
                )
            )
        except FileNotFoundError as exc:
            return _json_err(str(exc), status=404)
        except Exception as exc:
            return _json_err(str(exc))

    @app.post("/api/opsgraph/path")
    def api_opsgraph_path():
        payload = request.get_json(force=True, silent=True) or {}
        source = str(payload.get("source") or payload.get("src") or payload.get("src_node_id") or "").strip()
        target = str(payload.get("target") or payload.get("dst") or payload.get("dst_node_id") or "").strip()
        if not source or not target:
            return _json_err("source and target are required")
        try:
            return jsonify(
                service.opsgraph.path_between(
                    source,
                    target,
                    snapshot_id=(str(payload["snapshot_id"]) if payload.get("snapshot_id") is not None else None),
                    direction=str(payload.get("direction") or "both"),
                    max_depth=int(payload.get("max_depth", 8)),
                )
            )
        except FileNotFoundError as exc:
            return _json_err(str(exc), status=404)
        except Exception as exc:
            return _json_err(str(exc))

    @app.post("/api/opsgraph/blast-radius")
    def api_opsgraph_blast_radius():
        payload = request.get_json(force=True, silent=True) or {}
        node_id = str(payload.get("node_id") or payload.get("id") or "").strip()
        if not node_id:
            return _json_err("node_id is required")
        try:
            return jsonify(
                service.opsgraph.blast_radius(
                    node_id,
                    snapshot_id=(str(payload["snapshot_id"]) if payload.get("snapshot_id") is not None else None),
                    depth=int(payload.get("depth", 2)),
                    direction=str(payload.get("direction") or "out"),
                    max_nodes=int(payload.get("max_nodes", 300)),
                )
            )
        except FileNotFoundError as exc:
            return _json_err(str(exc), status=404)
        except Exception as exc:
            return _json_err(str(exc))

    @app.post("/api/opsgraph/explain-incident")
    def api_opsgraph_explain_incident():
        payload = request.get_json(force=True, silent=True) or {}
        incident_id = str(payload.get("incident_id") or payload.get("id") or "").strip()
        if not incident_id:
            return _json_err("incident_id is required")
        try:
            return jsonify(
                service.opsgraph.explain_incident(
                    incident_id,
                    snapshot_id=(str(payload["snapshot_id"]) if payload.get("snapshot_id") is not None else None),
                    depth=int(payload.get("depth", 2)),
                    max_nodes=int(payload.get("max_nodes", 200)),
                )
            )
        except FileNotFoundError as exc:
            return _json_err(str(exc), status=404)
        except Exception as exc:
            return _json_err(str(exc))

    @app.get("/api/advisor/status")
    def api_advisor_status():
        try:
            return jsonify(service.advisor.status())
        except Exception as exc:
            return _json_err(str(exc))

    @app.get("/api/advisor/profiles")
    def api_advisor_profiles():
        try:
            return jsonify(service.advisor.profiles())
        except Exception as exc:
            return _json_err(str(exc))

    @app.get("/api/advisor/list")
    def api_advisor_list():
        try:
            return jsonify(service.advisor.list(limit=int(request.args.get("limit", "50"))))
        except Exception as exc:
            return _json_err(str(exc))

    @app.get("/api/advisor/<analysis_id>")
    def api_advisor_get(analysis_id: str):
        try:
            return jsonify(service.advisor.get(analysis_id))
        except FileNotFoundError as exc:
            return _json_err(str(exc), status=404)
        except Exception as exc:
            return _json_err(str(exc))

    @app.post("/api/advisor/analyze")
    def api_advisor_analyze():
        payload = request.get_json(force=True, silent=True) or {}
        try:
            return jsonify(
                service.advisor.analyze(
                    profile=str(payload.get("profile") or "quick"),
                    incident_id=(str(payload["incident_id"]) if payload.get("incident_id") is not None else None),
                    options=(payload.get("options") if isinstance(payload.get("options"), dict) else {}),
                    persist=bool(payload.get("persist", True)),
                )
            )
        except FileNotFoundError as exc:
            return _json_err(str(exc), status=404)
        except Exception as exc:
            return _json_err(str(exc))

    @app.post("/api/advisor/doctor")
    def api_advisor_doctor():
        payload = request.get_json(force=True, silent=True) or {}
        try:
            return jsonify(
                service.advisor.doctor(
                    incident_id=(str(payload["incident_id"]) if payload.get("incident_id") is not None else None)
                )
            )
        except FileNotFoundError as exc:
            return _json_err(str(exc), status=404)
        except Exception as exc:
            return _json_err(str(exc))

    @app.get("/api/history")
    def api_history_list():
        try:
            limit = int(request.args.get("limit", "50"))
            event_type = request.args.get("event_type")
            return jsonify(service.history.list(limit=limit, event_type=event_type))
        except Exception as exc:
            return _json_err(str(exc))

    @app.get("/api/history/<event_id>")
    def api_history_get(event_id: str):
        try:
            return jsonify(service.history.get(event_id))
        except FileNotFoundError as exc:
            return _json_err(str(exc), status=404)
        except Exception as exc:
            return _json_err(str(exc))

    @app.get("/api/artifacts/roots")
    def api_artifacts_roots():
        try:
            return jsonify(service.artifacts.roots())
        except Exception as exc:
            return _json_err(str(exc))

    @app.get("/api/artifacts/list")
    def api_artifacts_list():
        try:
            root = str(request.args.get("root") or "").strip()
            if not root:
                return _json_err("root is required")
            rel_path = str(request.args.get("path") or "")
            recursive = _boolish(request.args.get("recursive"), default=False)
            limit = int(request.args.get("limit", "200"))
            return jsonify(service.artifacts.list_dir(root, rel_path, recursive=recursive, limit=limit))
        except FileNotFoundError as exc:
            return _json_err(str(exc), status=404)
        except Exception as exc:
            return _json_err(str(exc))

    @app.get("/api/artifacts/read")
    def api_artifacts_read():
        try:
            root = str(request.args.get("root") or "").strip()
            rel_path = str(request.args.get("path") or "").strip()
            if not root or not rel_path:
                return _json_err("root and path are required")
            max_bytes = int(request.args.get("max_bytes", "200000"))
            return jsonify(service.artifacts.read_file(root, rel_path, max_bytes=max_bytes))
        except FileNotFoundError as exc:
            return _json_err(str(exc), status=404)
        except Exception as exc:
            return _json_err(str(exc))

    @app.get("/api/artifacts/download")
    def api_artifacts_download():
        try:
            root = str(request.args.get("root") or "").strip()
            rel_path = str(request.args.get("path") or "").strip()
            if not root or not rel_path:
                return _json_err("root and path are required")
            return send_file(service.artifacts.resolve_download_path(root, rel_path), as_attachment=True)
        except FileNotFoundError as exc:
            return _json_err(str(exc), status=404)
        except Exception as exc:
            return _json_err(str(exc))

    @app.get("/api/jobs")
    def api_jobs_list():
        try:
            limit = int(request.args.get("limit", "50"))
            status = request.args.get("status")
            return jsonify(service.jobs.list(limit=limit, status=status))
        except Exception as exc:
            return _json_err(str(exc))

    @app.post("/api/jobs/submit")
    def api_jobs_submit():
        payload = request.get_json(force=True, silent=True) or {}
        kind = str(payload.get("kind") or "").strip()
        params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
        if not kind:
            return _json_err("kind is required")
        try:
            job = service.jobs.submit(kind, params)
            if _boolish(payload.get("wait"), default=False):
                timeout_sec = payload.get("timeout_sec")
                waited = service.jobs.wait(job["id"], timeout_sec=(float(timeout_sec) if timeout_sec is not None else None))
                return jsonify({"ok": True, "submitted": job, "wait": waited})
            return jsonify({"ok": True, "job": job})
        except Exception as exc:
            return _json_err(str(exc))

    @app.get("/api/jobs/<job_id>")
    def api_jobs_get(job_id: str):
        try:
            return jsonify(service.jobs.get(job_id))
        except FileNotFoundError as exc:
            return _json_err(str(exc), status=404)
        except Exception as exc:
            return _json_err(str(exc))

    @app.post("/api/jobs/<job_id>/wait")
    def api_jobs_wait(job_id: str):
        payload = request.get_json(force=True, silent=True) or {}
        timeout_sec = payload.get("timeout_sec")
        try:
            return jsonify(service.jobs.wait(job_id, timeout_sec=(float(timeout_sec) if timeout_sec is not None else None)))
        except FileNotFoundError as exc:
            return _json_err(str(exc), status=404)
        except Exception as exc:
            return _json_err(str(exc))

    @app.post("/api/jobs/<job_id>/cancel")
    def api_jobs_cancel(job_id: str):
        try:
            return jsonify(service.jobs.cancel(job_id))
        except FileNotFoundError as exc:
            return _json_err(str(exc), status=404)
        except Exception as exc:
            return _json_err(str(exc))

    @app.get("/api/benchmarks/profiles")
    def api_benchmark_profiles():
        try:
            return jsonify(service.benchmarks.profiles())
        except Exception as exc:
            return _json_err(str(exc))

    @app.get("/api/benchmarks/list")
    def api_benchmark_list():
        try:
            return jsonify(service.benchmark_analytics.list_runs(limit=int(request.args.get("limit", "50"))))
        except Exception as exc:
            return _json_err(str(exc))

    @app.get("/api/benchmarks/trends")
    def api_benchmark_trends():
        try:
            suite = str(request.args.get("suite") or "vision_random")
            metric = str(request.args.get("metric") or "duration_ms")
            limit = int(request.args.get("limit", "20"))
            return jsonify(service.benchmark_analytics.trends(suite=suite, metric=metric, limit=limit))
        except Exception as exc:
            return _json_err(str(exc))

    @app.post("/api/benchmarks/compare")
    def api_benchmark_compare():
        payload = request.get_json(force=True, silent=True) or {}
        baseline = str(payload.get("baseline") or "").strip()
        candidate = str(payload.get("candidate") or "").strip()
        if not baseline or not candidate:
            return _json_err("baseline and candidate are required")
        try:
            return jsonify(service.benchmark_analytics.compare(baseline, candidate))
        except FileNotFoundError as exc:
            return _json_err(str(exc), status=404)
        except Exception as exc:
            return _json_err(str(exc))

    @app.post("/api/benchmarks/run")
    def api_benchmarks_run():
        payload = request.get_json(force=True, silent=True) or {}
        profile = payload.get("profile")
        config = payload.get("config") if isinstance(payload.get("config"), dict) else None
        try:
            if _boolish(payload.get("async"), default=False):
                params: dict[str, Any] = {}
                if profile is not None:
                    params["profile"] = str(profile)
                if config is not None:
                    params["config"] = config
                return jsonify({"ok": True, "job": service.jobs.submit("benchmark.arena", params)})
            return jsonify(service.benchmarks.run(profile=(str(profile) if profile is not None else None), config=config))
        except Exception as exc:
            return _json_err(str(exc))

    @app.post("/api/chat/load")
    def api_chat_load():
        try:
            return jsonify(service.chat.load())
        except Exception as exc:
            return _json_err(str(exc))

    @app.post("/api/chat")
    def api_chat():
        payload = request.get_json(force=True, silent=True) or {}
        message = str(payload.get("message") or "").strip()
        session_id = str(payload.get("session_id") or "omniforge").strip() or "omniforge"
        if not message:
            return _json_err("message is required")
        try:
            return jsonify(service.chat.chat(message, session_id=session_id))
        except Exception as exc:
            return _json_err(str(exc))

    @app.post("/api/vision/classify")
    def api_vision_classify():
        if "file" not in request.files:
            return _json_err("multipart file field 'file' is required")
        file = request.files["file"]
        data = file.read()
        if not data:
            return _json_err("empty file upload")
        try:
            top_k = int(request.form.get("top_k", "3"))
            result = service.vision.classify_bytes(data, top_k=top_k)
            result["filename"] = file.filename
            return jsonify(result)
        except Exception as exc:
            return _json_err(str(exc))

    @app.post("/api/vision/classify-path")
    def api_vision_classify_path():
        payload = request.get_json(force=True, silent=True) or {}
        image_path = str(payload.get("image_path") or "").strip()
        if not image_path:
            return _json_err("image_path is required")
        try:
            top_k = int(payload.get("top_k", 3))
            return jsonify(service.vision.classify_path(image_path, top_k=top_k))
        except FileNotFoundError as exc:
            return _json_err(str(exc), status=404)
        except Exception as exc:
            return _json_err(str(exc))

    @app.get("/api/vision/status")
    def api_vision_status():
        try:
            return jsonify(service.vision.status())
        except Exception as exc:
            return _json_err(str(exc))

    @app.get("/api/vision/benchmark")
    def api_vision_benchmark():
        try:
            return jsonify(service.vision.benchmark_random())
        except Exception as exc:
            return _json_err(str(exc))

    @app.get("/api/neurodsl/devices")
    def api_neurodsl_devices():
        return jsonify(service.neurodsl.devices())

    @app.get("/api/neurodsl/agent-manifest")
    def api_neurodsl_manifest():
        return jsonify(service.neurodsl.agent_manifest())

    @app.get("/api/neurodsl/platform-health")
    def api_neurodsl_platform_health():
        return jsonify(service.neurodsl.platform_health())

    @app.get("/api/neurodsl/agent-api/status")
    def api_neurodsl_agent_api_status():
        return jsonify(service.neurodsl.agent_api_status())

    @app.post("/api/neurodsl/agent-api/start")
    def api_neurodsl_agent_api_start():
        payload = request.get_json(force=True, silent=True) or {}
        host = str(payload.get("host") or "127.0.0.1")
        port = int(payload.get("port") or 8860)
        device = str(payload.get("device") or "auto")
        wait_sec = float(payload.get("wait_sec") or 15.0)
        return jsonify(service.neurodsl.start_agent_api(host=host, port=port, device=device, wait_sec=wait_sec))

    @app.post("/api/neurodsl/agent-api/stop")
    def api_neurodsl_agent_api_stop():
        payload = request.get_json(force=True, silent=True) or {}
        timeout_sec = float(payload.get("timeout_sec") or 8.0)
        force = bool(payload.get("force", True))
        return jsonify(service.neurodsl.stop_agent_api(timeout_sec=timeout_sec, force=force))

    @app.post("/api/neurodsl/agent-api/proxy")
    def api_neurodsl_agent_api_proxy():
        payload = request.get_json(force=True, silent=True) or {}
        method = str(payload.get("method") or "GET")
        path = str(payload.get("path") or "/health")
        body = payload.get("body")
        timeout_sec = float(payload.get("timeout_sec") or 20.0)
        result = service.neurodsl.proxy_agent_api(method, path, body, timeout_sec=timeout_sec)
        status_code = int(result.get("status_code") or 200) if result.get("status_code") else 200
        return jsonify(result), status_code if status_code >= 100 else 200

    @app.get("/api/neurodsl/agent-api/health")
    def api_neurodsl_agent_api_health():
        result = service.neurodsl.proxy_agent_api("GET", "/health", timeout_sec=5.0)
        status_code = int(result.get("status_code") or 200) if result.get("status_code") else 200
        return jsonify(result), status_code if status_code >= 100 else 200

    @app.get("/api/neurodsl/agent-api/manifest")
    def api_neurodsl_agent_api_manifest():
        result = service.neurodsl.proxy_agent_api("GET", "/manifest", timeout_sec=5.0)
        status_code = int(result.get("status_code") or 200) if result.get("status_code") else 200
        return jsonify(result), status_code if status_code >= 100 else 200

    @app.post("/api/nexusflow/run-example")
    def api_nexusflow_run_example():
        payload = request.get_json(force=True, silent=True) or {}
        example = str(payload.get("example") or "").strip()
        pipeline = payload.get("pipeline")
        if not example:
            return _json_err("example is required")
        out_dir = payload.get("out_dir")
        return jsonify(
            service.nexusflow.run_example(
                example,
                pipeline=str(pipeline) if pipeline else None,
                out_dir=out_dir,
            )
        )

    @app.post("/api/nexusflow/run-file")
    def api_nexusflow_run_file():
        payload = request.get_json(force=True, silent=True) or {}
        file_path = str(payload.get("file_path") or "").strip()
        if not file_path:
            return _json_err("file_path is required")
        pipeline = payload.get("pipeline")
        out_dir = payload.get("out_dir")
        return jsonify(
            service.nexusflow.run_file(
                file_path,
                pipeline=str(pipeline) if pipeline else None,
                out_dir=out_dir,
            )
        )

    @app.post("/api/windhawk/generate-mod")
    def api_windhawk_generate_mod():
        payload = request.get_json(force=True, silent=True) or {}
        try:
            host = str(payload.get("host") or "127.0.0.1")
            port = int(payload.get("port") or 8787)
            path = str(payload.get("path") or "/")
            copy_to_windhawk = bool(payload.get("copy_to_windhawk", True))
            return jsonify(
                service.windhawk.generate_hotkey_mod(
                    host=host, port=port, path=path, copy_to_windhawk=copy_to_windhawk
                )
            )
        except Exception as exc:
            return _json_err(str(exc))

    @app.post("/api/windhawk/restart")
    def api_windhawk_restart():
        payload = request.get_json(force=True, silent=True) or {}
        tray_only = bool(payload.get("tray_only", True))
        return jsonify(service.windhawk.restart(tray_only=tray_only))

    @app.get("/api/workflows/bootstrap")
    def api_bootstrap_workflow():
        wf = service.paths.workflow_file
        if not wf.exists():
            return _json_err(f"Workflow file not found: {wf}", status=404)
        return _json_ok({"file": str(wf), "text": wf.read_text(encoding="utf-8")})

    @app.post("/api/agentic/plan")
    def api_agentic_plan():
        payload = request.get_json(force=True, silent=True) or {}
        task = str(payload.get("task") or "").strip()
        image_path = str(payload.get("image_path") or "").strip() or None
        session_id = str(payload.get("session_id") or "agentic").strip() or "agentic"
        if not task and not image_path:
            return _json_err("task or image_path is required")
        try:
            return jsonify(agentic.plan(task, image_path=image_path, session_id=session_id))
        except Exception as exc:
            return _json_err(str(exc))

    @app.post("/api/agentic/execute")
    def api_agentic_execute():
        payload = request.get_json(force=True, silent=True) or {}
        image_path = str(payload.get("image_path") or "").strip() or None
        plan = payload.get("plan")
        task = payload.get("task")
        if plan is None and not str(task or "").strip() and not image_path:
            return _json_err("provide plan or task (or image_path)")
        try:
            if plan is not None:
                return jsonify(agentic.execute(plan))
            return jsonify(agentic.execute(str(task or ""), image_path=image_path))
        except Exception as exc:
            return _json_err(str(exc))

    @app.post("/api/agentic/nexusflow-export")
    def api_agentic_nexusflow_export():
        payload = request.get_json(force=True, silent=True) or {}
        image_path = str(payload.get("image_path") or "").strip() or None
        plan = payload.get("plan")
        task = payload.get("task")
        if plan is None and not str(task or "").strip() and not image_path:
            return _json_err("provide plan or task (or image_path)")
        try:
            if plan is not None:
                return jsonify(agentic.export_nexusflow_pipeline(plan))
            return jsonify(agentic.export_nexusflow_pipeline(str(task or ""), image_path=image_path))
        except Exception as exc:
            return _json_err(str(exc))

    @app.post("/api/agentic/nexusflow-run")
    def api_agentic_nexusflow_run():
        payload = request.get_json(force=True, silent=True) or {}
        image_path = str(payload.get("image_path") or "").strip() or None
        plan = payload.get("plan")
        task = payload.get("task")
        if plan is None and not str(task or "").strip() and not image_path:
            return _json_err("provide plan or task (or image_path)")
        try:
            if plan is not None:
                return jsonify(agentic.run_nexusflow_pipeline(plan))
            return jsonify(agentic.run_nexusflow_pipeline(str(task or ""), image_path=image_path))
        except Exception as exc:
            return _json_err(str(exc))

    return app


def run_server(host: str = "127.0.0.1", port: int = 8787, service: OmniForgeService | None = None) -> None:
    app = build_app(service=service)
    print(f"OmniForge AI Workbench: http://{host}:{int(port)}")
    app.run(host=host, port=int(port), threaded=True)

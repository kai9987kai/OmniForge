from __future__ import annotations

import argparse
import json
import time

from .agentic import AgenticWorkflowEngine
from .server import run_server
from .service import create_service


def _print_json(data):
    print(json.dumps(data, indent=2, ensure_ascii=False))


def _parse_json_arg(text: str | None, default):
    if text is None:
        return default
    value = str(text).strip()
    if not value:
        return default
    return json.loads(value)


def _parse_csv_list(text: str | None) -> list[str] | None:
    if text is None:
        return None
    items = [x.strip() for x in str(text).split(",") if x.strip()]
    return items or None


def cmd_serve(args: argparse.Namespace) -> int:
    run_server(host=args.host, port=args.port)
    return 0


def cmd_serve_windhawk(args: argparse.Namespace) -> int:
    svc = create_service()
    setup = svc.windhawk.generate_hotkey_mod(
        host=args.host,
        port=args.port,
        path=args.path,
        copy_to_windhawk=True,
    )
    print("[windhawk mod]")
    _print_json(setup)
    if not args.no_restart:
        print("[windhawk restart]")
        _print_json(svc.windhawk.restart(tray_only=not args.no_tray_only))
    run_server(host=args.host, port=args.port, service=svc)
    return 0


def cmd_status(_args: argparse.Namespace) -> int:
    _print_json(create_service().status())
    return 0


def cmd_chat(args: argparse.Namespace) -> int:
    svc = create_service()
    _print_json(svc.chat.chat(args.message, session_id=args.session_id))
    return 0


def cmd_vision(args: argparse.Namespace) -> int:
    svc = create_service()
    _print_json(svc.vision.classify_path(args.image, top_k=args.top_k))
    return 0


def cmd_neurodsl_devices(_args: argparse.Namespace) -> int:
    _print_json(create_service().neurodsl.devices())
    return 0


def cmd_neurodsl_manifest(_args: argparse.Namespace) -> int:
    _print_json(create_service().neurodsl.agent_manifest())
    return 0


def cmd_nexusflow_example(args: argparse.Namespace) -> int:
    svc = create_service()
    _print_json(svc.nexusflow.run_example(args.example, pipeline=args.pipeline, out_dir=args.out_dir))
    return 0


def cmd_nexusflow_file(args: argparse.Namespace) -> int:
    svc = create_service()
    _print_json(svc.nexusflow.run_file(args.file_path, pipeline=args.pipeline, out_dir=args.out_dir))
    return 0


def cmd_windhawk_generate_mod(args: argparse.Namespace) -> int:
    svc = create_service()
    _print_json(
        svc.windhawk.generate_hotkey_mod(
            host=args.host,
            port=args.port,
            path=args.path,
            copy_to_windhawk=not args.no_copy,
        )
    )
    return 0


def cmd_windhawk_restart(args: argparse.Namespace) -> int:
    _print_json(create_service().windhawk.restart(tray_only=not args.no_tray_only))
    return 0


def cmd_smoke(_args: argparse.Namespace) -> int:
    svc = create_service()
    report = {
        "service": {
            "workspace_root": str(svc.paths.workspace_root),
            "dashboard_exists": svc.paths.dashboard_file.exists(),
            "workflow_exists": svc.paths.workflow_file.exists(),
        },
        "chat_load": svc.chat.load(),
        "chat_sample": svc.chat.chat("hello", session_id="smoke"),
        "vision_benchmark": svc.vision.benchmark_random(),
        "neurodsl_devices": svc.neurodsl.devices(),
        "neurodsl_manifest": svc.neurodsl.agent_manifest(),
        "nexusflow_status": svc.nexusflow.status(),
        "windhawk_status": svc.windhawk.status(),
    }
    _print_json(report)
    return 0


def cmd_agentic_plan(args: argparse.Namespace) -> int:
    svc = create_service()
    engine = AgenticWorkflowEngine(svc)
    _print_json(engine.plan(args.task, image_path=args.image_path, session_id=args.session_id))
    return 0


def cmd_agentic_execute(args: argparse.Namespace) -> int:
    svc = create_service()
    engine = AgenticWorkflowEngine(svc)
    _print_json(engine.execute(args.task, image_path=args.image_path))
    return 0


def cmd_agentic_export_nxf(args: argparse.Namespace) -> int:
    svc = create_service()
    engine = AgenticWorkflowEngine(svc)
    _print_json(engine.export_nexusflow_pipeline(args.task, image_path=args.image_path))
    return 0


def cmd_agentic_run_nxf(args: argparse.Namespace) -> int:
    svc = create_service()
    engine = AgenticWorkflowEngine(svc)
    _print_json(engine.run_nexusflow_pipeline(args.task, image_path=args.image_path))
    return 0


def cmd_benchmark_profiles(_args: argparse.Namespace) -> int:
    svc = create_service()
    _print_json(svc.benchmarks.profiles())
    return 0


def cmd_benchmark_run(args: argparse.Namespace) -> int:
    svc = create_service()
    cfg = _parse_json_arg(args.config_json, None)
    if cfg is not None and not isinstance(cfg, dict):
        raise SystemExit("--config-json must decode to a JSON object")
    _print_json(svc.benchmarks.run(profile=args.profile, config=cfg))
    return 0


def cmd_history_list(args: argparse.Namespace) -> int:
    svc = create_service()
    _print_json(svc.history.list(limit=args.limit, event_type=args.event_type))
    return 0


def cmd_history_get(args: argparse.Namespace) -> int:
    svc = create_service()
    _print_json(svc.history.get(args.event_id))
    return 0


def cmd_artifacts_roots(_args: argparse.Namespace) -> int:
    svc = create_service()
    _print_json(svc.artifacts.roots())
    return 0


def cmd_artifacts_list(args: argparse.Namespace) -> int:
    svc = create_service()
    _print_json(
        svc.artifacts.list_dir(
            args.root,
            args.path,
            recursive=args.recursive,
            limit=args.limit,
        )
    )
    return 0


def cmd_artifacts_read(args: argparse.Namespace) -> int:
    svc = create_service()
    _print_json(svc.artifacts.read_file(args.root, args.path, max_bytes=args.max_bytes))
    return 0


def cmd_jobs_list(args: argparse.Namespace) -> int:
    svc = create_service()
    _print_json(svc.jobs.list(limit=args.limit, status=args.status))
    return 0


def cmd_job_get(args: argparse.Namespace) -> int:
    svc = create_service()
    _print_json(svc.jobs.get(args.job_id))
    return 0


def cmd_job_run(args: argparse.Namespace) -> int:
    svc = create_service()
    params = _parse_json_arg(args.params_json, {})
    if not isinstance(params, dict):
        raise SystemExit("--params-json must decode to a JSON object")
    submitted = svc.jobs.submit(args.kind, params)
    waited = svc.jobs.wait(submitted["id"], timeout_sec=args.timeout_sec)
    _print_json({"ok": True, "submitted": submitted, "wait": waited})
    return 0


def cmd_ops_catalog(_args: argparse.Namespace) -> int:
    svc = create_service()
    _print_json(svc.operation_catalog())
    return 0


def cmd_op_run(args: argparse.Namespace) -> int:
    svc = create_service()
    params = _parse_json_arg(args.params_json, {})
    if not isinstance(params, dict):
        raise SystemExit("--params-json must decode to a JSON object")
    _print_json({"ok": True, "kind": args.kind, "result": svc.run_operation(args.kind, params)})
    return 0


def cmd_op_submit(args: argparse.Namespace) -> int:
    svc = create_service()
    params = _parse_json_arg(args.params_json, {})
    if not isinstance(params, dict):
        raise SystemExit("--params-json must decode to a JSON object")
    _print_json({"ok": True, "job": svc.jobs.submit(args.kind, params)})
    return 0


def cmd_search_roots(_args: argparse.Namespace) -> int:
    svc = create_service()
    _print_json(svc.search.roots())
    return 0


def cmd_search_files(args: argparse.Namespace) -> int:
    svc = create_service()
    _print_json(
        svc.search.find_files(
            args.query,
            root=args.root,
            glob=args.glob,
            extensions=_parse_csv_list(args.extensions),
            limit=args.limit,
        )
    )
    return 0


def cmd_search_text(args: argparse.Namespace) -> int:
    svc = create_service()
    _print_json(
        svc.search.search_text(
            args.pattern,
            root=args.root,
            case_sensitive=args.case_sensitive,
            regex=args.regex,
            glob=args.glob,
            extensions=_parse_csv_list(args.extensions),
            limit_matches=args.limit_matches,
            max_file_bytes=args.max_file_bytes,
            context_lines=args.context_lines,
        )
    )
    return 0


def cmd_missions_list(_args: argparse.Namespace) -> int:
    svc = create_service()
    _print_json(svc.missions.catalog())
    return 0


def cmd_mission_plan(args: argparse.Namespace) -> int:
    svc = create_service()
    inputs = _parse_json_arg(args.inputs_json, {})
    if not isinstance(inputs, dict):
        raise SystemExit("--inputs-json must decode to a JSON object")
    _print_json(svc.missions.plan(args.mission, inputs=inputs))
    return 0


def cmd_mission_run(args: argparse.Namespace) -> int:
    svc = create_service()
    inputs = _parse_json_arg(args.inputs_json, {})
    if not isinstance(inputs, dict):
        raise SystemExit("--inputs-json must decode to a JSON object")
    if args.async_run:
        _print_json({"ok": True, "job": svc.jobs.submit("missions.run", {"mission": args.mission, "inputs": inputs})})
        return 0
    _print_json(svc.missions.run(args.mission, inputs=inputs))
    return 0


def cmd_benchmark_list(args: argparse.Namespace) -> int:
    svc = create_service()
    _print_json(svc.benchmark_analytics.list_runs(limit=args.limit))
    return 0


def cmd_benchmark_compare(args: argparse.Namespace) -> int:
    svc = create_service()
    _print_json(svc.benchmark_analytics.compare(args.baseline, args.candidate))
    return 0


def cmd_benchmark_trends(args: argparse.Namespace) -> int:
    svc = create_service()
    _print_json(svc.benchmark_analytics.trends(suite=args.suite, metric=args.metric, limit=args.limit))
    return 0


def cmd_reports_profiles(_args: argparse.Namespace) -> int:
    svc = create_service()
    _print_json(svc.reports.profiles())
    return 0


def cmd_reports_list(args: argparse.Namespace) -> int:
    svc = create_service()
    _print_json(svc.reports.list(limit=args.limit))
    return 0


def cmd_report_generate(args: argparse.Namespace) -> int:
    svc = create_service()
    options = _parse_json_arg(args.options_json, {})
    if not isinstance(options, dict):
        raise SystemExit("--options-json must decode to a JSON object")
    _print_json(svc.reports.generate(profile=args.profile, options=options))
    return 0


def cmd_scheduler_status(_args: argparse.Namespace) -> int:
    svc = create_service()
    _print_json(svc.scheduler.status())
    return 0


def cmd_scheduler_tick(_args: argparse.Namespace) -> int:
    svc = create_service()
    _print_json(svc.scheduler.tick_once())
    return 0


def cmd_schedules_list(_args: argparse.Namespace) -> int:
    svc = create_service()
    _print_json(svc.scheduler.list())
    return 0


def cmd_schedule_get(args: argparse.Namespace) -> int:
    svc = create_service()
    _print_json(svc.scheduler.get(args.schedule_id))
    return 0


def cmd_schedule_create(args: argparse.Namespace) -> int:
    svc = create_service()
    params = _parse_json_arg(args.params_json, {})
    if not isinstance(params, dict):
        raise SystemExit("--params-json must decode to a JSON object")
    _print_json(
        svc.scheduler.create(
            name=args.name,
            target_kind=args.target_kind,
            params=params,
            interval_sec=args.interval_sec,
            enabled=not args.disabled,
            start_immediately=args.start_immediately,
        )
    )
    return 0


def cmd_schedule_update(args: argparse.Namespace) -> int:
    svc = create_service()
    params_patch = _parse_json_arg(args.params_json, None)
    if params_patch is not None and not isinstance(params_patch, dict):
        raise SystemExit("--params-json must decode to a JSON object")
    enabled = None
    if args.enable:
        enabled = True
    if args.disable:
        enabled = False
    _print_json(
        svc.scheduler.update(
            args.schedule_id,
            name=args.name,
            target_kind=args.target_kind,
            params=params_patch,
            interval_sec=args.interval_sec,
            enabled=enabled,
        )
    )
    return 0


def cmd_schedule_delete(args: argparse.Namespace) -> int:
    svc = create_service()
    _print_json(svc.scheduler.delete(args.schedule_id))
    return 0


def cmd_schedule_run_now(args: argparse.Namespace) -> int:
    svc = create_service()
    _print_json(svc.scheduler.run_now(args.schedule_id))
    return 0


def cmd_history_follow(args: argparse.Namespace) -> int:
    svc = create_service()
    event_type = args.event_type
    tail = max(0, int(args.tail))
    max_events = max(1, int(args.max_events))
    timeout_sec = max(0.1, float(args.timeout_sec))
    poll_sec = max(0.1, float(args.poll_sec))
    t0 = time.time()
    sent = 0
    offset = 0
    if args.from_end:
        offset = int(svc.history.read_index_delta(offset=0, max_rows=1).get("offset") or 0)
    elif tail > 0:
        items = list(reversed(svc.history.tail(limit=tail, event_type=event_type)))
        for row in items:
            _print_json(row)
            sent += 1
            if sent >= max_events:
                return 0
        offset = int(svc.history.read_index_delta(offset=0, max_rows=1).get("offset") or 0)

    while time.time() - t0 < timeout_sec and sent < max_events:
        delta = svc.history.read_index_delta(offset=offset, max_rows=200, event_type=event_type)
        offset = int(delta.get("offset") or offset)
        rows = delta.get("rows") or []
        if rows:
            for row in rows:
                _print_json(row)
                sent += 1
                if sent >= max_events:
                    break
            continue
        time.sleep(poll_sec)
    return 0


def cmd_alert_templates(_args: argparse.Namespace) -> int:
    svc = create_service()
    _print_json(svc.alerts.templates())
    return 0


def cmd_alert_rules(args: argparse.Namespace) -> int:
    svc = create_service()
    _print_json(svc.alerts.list_rules())
    return 0


def cmd_alert_rule_get(args: argparse.Namespace) -> int:
    svc = create_service()
    _print_json(svc.alerts.get_rule(args.rule_id))
    return 0


def cmd_alert_rule_create(args: argparse.Namespace) -> int:
    svc = create_service()
    config = _parse_json_arg(args.config_json, {})
    if not isinstance(config, dict):
        raise SystemExit("--config-json must decode to a JSON object")
    _print_json(
        svc.alerts.create_rule(
            kind=args.kind,
            name=args.name,
            config=config,
            severity=args.severity,
            enabled=not args.disabled,
            cooldown_sec=args.cooldown_sec,
        )
    )
    return 0


def cmd_alert_rule_update(args: argparse.Namespace) -> int:
    svc = create_service()
    config = _parse_json_arg(args.config_json, None)
    if config is not None and not isinstance(config, dict):
        raise SystemExit("--config-json must decode to a JSON object")
    enabled = None
    if args.enable:
        enabled = True
    if args.disable:
        enabled = False
    _print_json(
        svc.alerts.update_rule(
            args.rule_id,
            name=args.name,
            config=config,
            severity=args.severity,
            enabled=enabled,
            cooldown_sec=args.cooldown_sec,
        )
    )
    return 0


def cmd_alert_rule_delete(args: argparse.Namespace) -> int:
    svc = create_service()
    _print_json(svc.alerts.delete_rule(args.rule_id))
    return 0


def cmd_alerts_evaluate(args: argparse.Namespace) -> int:
    svc = create_service()
    _print_json(svc.alerts.evaluate_all(only_enabled=not args.all_rules, force=args.force))
    return 0


def cmd_alert_evaluate_rule(args: argparse.Namespace) -> int:
    svc = create_service()
    _print_json(svc.alerts.evaluate_rule(args.rule_id, force=args.force))
    return 0


def cmd_alerts_list(args: argparse.Namespace) -> int:
    svc = create_service()
    acknowledged = None
    if args.acknowledged:
        acknowledged = True
    if args.unacknowledged:
        acknowledged = False
    _print_json(
        svc.alerts.list_alerts(
            limit=args.limit,
            rule_id=args.rule_id,
            severity=args.severity,
            acknowledged=acknowledged,
        )
    )
    return 0


def cmd_alert_get(args: argparse.Namespace) -> int:
    svc = create_service()
    _print_json(svc.alerts.get_alert(args.alert_id))
    return 0


def cmd_alert_ack(args: argparse.Namespace) -> int:
    svc = create_service()
    _print_json(svc.alerts.ack_alert(args.alert_id, note=args.note))
    return 0


def cmd_remediation_status(_args: argparse.Namespace) -> int:
    svc = create_service()
    _print_json(svc.remediations.status())
    return 0


def cmd_remediation_templates(_args: argparse.Namespace) -> int:
    svc = create_service()
    _print_json(svc.remediations.templates())
    return 0


def cmd_remediation_policies(_args: argparse.Namespace) -> int:
    svc = create_service()
    _print_json(svc.remediations.list_policies())
    return 0


def cmd_remediation_policy_get(args: argparse.Namespace) -> int:
    svc = create_service()
    _print_json(svc.remediations.get_policy(args.policy_id))
    return 0


def cmd_remediation_policy_create(args: argparse.Namespace) -> int:
    svc = create_service()
    match = _parse_json_arg(args.match_json, {})
    actions = _parse_json_arg(args.actions_json, None)
    if not isinstance(match, dict):
        raise SystemExit("--match-json must decode to a JSON object")
    if not isinstance(actions, list) or not actions:
        raise SystemExit("--actions-json must decode to a non-empty JSON array")
    _print_json(
        svc.remediations.create_policy(
            name=args.name,
            match=match,
            actions=actions,
            enabled=not args.disabled,
            cooldown_sec=args.cooldown_sec,
        )
    )
    return 0


def cmd_remediation_policy_update(args: argparse.Namespace) -> int:
    svc = create_service()
    match = _parse_json_arg(args.match_json, None)
    actions = _parse_json_arg(args.actions_json, None)
    if match is not None and not isinstance(match, dict):
        raise SystemExit("--match-json must decode to a JSON object")
    if actions is not None and not isinstance(actions, list):
        raise SystemExit("--actions-json must decode to a JSON array")
    enabled = None
    if args.enable:
        enabled = True
    if args.disable:
        enabled = False
    _print_json(
        svc.remediations.update_policy(
            args.policy_id,
            name=args.name,
            match=match,
            actions=actions,
            enabled=enabled,
            cooldown_sec=args.cooldown_sec,
        )
    )
    return 0


def cmd_remediation_policy_delete(args: argparse.Namespace) -> int:
    svc = create_service()
    _print_json(svc.remediations.delete_policy(args.policy_id))
    return 0


def cmd_remediation_actions(args: argparse.Namespace) -> int:
    svc = create_service()
    ok_filter = None
    if args.ok:
        ok_filter = True
    if args.fail:
        ok_filter = False
    _print_json(svc.remediations.list_actions(limit=args.limit, policy_id=args.policy_id, ok=ok_filter))
    return 0


def cmd_remediation_handle_alert(args: argparse.Namespace) -> int:
    svc = create_service()
    if args.alert_json:
        alert_obj = _parse_json_arg(args.alert_json, None)
        if not isinstance(alert_obj, dict):
            raise SystemExit("--alert-json must decode to a JSON object")
        _print_json(svc.remediations.handle_alert(alert_obj, force=args.force))
        return 0
    if not args.alert_id:
        raise SystemExit("Provide --alert-id or --alert-json")
    _print_json(svc.remediations.handle_alert(args.alert_id, force=args.force))
    return 0


def cmd_notifications_status(_args: argparse.Namespace) -> int:
    svc = create_service()
    _print_json(svc.notifications.status())
    return 0


def cmd_notification_templates(_args: argparse.Namespace) -> int:
    svc = create_service()
    _print_json(svc.notifications.templates())
    return 0


def cmd_notification_channels(_args: argparse.Namespace) -> int:
    svc = create_service()
    _print_json(svc.notifications.list_channels())
    return 0


def cmd_notification_channel_get(args: argparse.Namespace) -> int:
    svc = create_service()
    _print_json(svc.notifications.get_channel(args.channel_id))
    return 0


def cmd_notification_channel_create(args: argparse.Namespace) -> int:
    svc = create_service()
    cfg = _parse_json_arg(args.config_json, {})
    match = _parse_json_arg(args.match_json, {})
    if not isinstance(cfg, dict):
        raise SystemExit("--config-json must decode to a JSON object")
    if not isinstance(match, dict):
        raise SystemExit("--match-json must decode to a JSON object")
    _print_json(
        svc.notifications.create_channel(
            channel_type=args.type,
            name=args.name,
            config=cfg,
            match=match,
            enabled=not args.disabled,
        )
    )
    return 0


def cmd_notification_channel_update(args: argparse.Namespace) -> int:
    svc = create_service()
    cfg = _parse_json_arg(args.config_json, None)
    match = _parse_json_arg(args.match_json, None)
    if cfg is not None and not isinstance(cfg, dict):
        raise SystemExit("--config-json must decode to a JSON object")
    if match is not None and not isinstance(match, dict):
        raise SystemExit("--match-json must decode to a JSON object")
    enabled = None
    if args.enable:
        enabled = True
    if args.disable:
        enabled = False
    _print_json(
        svc.notifications.update_channel(
            args.channel_id,
            name=args.name,
            config=cfg,
            match=match,
            enabled=enabled,
        )
    )
    return 0


def cmd_notification_channel_delete(args: argparse.Namespace) -> int:
    svc = create_service()
    _print_json(svc.notifications.delete_channel(args.channel_id))
    return 0


def cmd_notification_deliveries(args: argparse.Namespace) -> int:
    svc = create_service()
    ok_filter = None
    if args.ok:
        ok_filter = True
    if args.fail:
        ok_filter = False
    _print_json(
        svc.notifications.list_deliveries(
            limit=args.limit,
            channel_id=args.channel_id,
            ok=ok_filter,
            topic=args.topic,
            severity=args.severity,
        )
    )
    return 0


def cmd_notification_send(args: argparse.Namespace) -> int:
    svc = create_service()
    payload = _parse_json_arg(args.payload_json, {})
    if not isinstance(payload, dict):
        raise SystemExit("--payload-json must decode to a JSON object")
    tags = _parse_csv_list(args.tags)
    targets = _parse_csv_list(args.target_channel_ids)
    _print_json(
        svc.notifications.dispatch(
            topic=args.topic,
            message=args.message,
            payload=payload,
            severity=args.severity,
            tags=tags,
            target_channel_ids=targets,
        )
    )
    return 0


def cmd_health_status(_args: argparse.Namespace) -> int:
    svc = create_service()
    _print_json(svc.health.status())
    return 0


def cmd_health_profiles(_args: argparse.Namespace) -> int:
    svc = create_service()
    _print_json(svc.health.profiles())
    return 0


def cmd_health_list(args: argparse.Namespace) -> int:
    svc = create_service()
    _print_json(svc.health.list(limit=args.limit))
    return 0


def cmd_health_snapshot(args: argparse.Namespace) -> int:
    svc = create_service()
    options = _parse_json_arg(args.options_json, {})
    if not isinstance(options, dict):
        raise SystemExit("--options-json must decode to a JSON object")
    _print_json(svc.health.snapshot(profile=args.profile, options=options))
    return 0


def cmd_health_get(args: argparse.Namespace) -> int:
    svc = create_service()
    _print_json(svc.health.get(args.snapshot_id))
    return 0


def cmd_health_latest(args: argparse.Namespace) -> int:
    svc = create_service()
    _print_json(svc.health.latest(optional=args.optional))
    return 0


def cmd_health_compare(args: argparse.Namespace) -> int:
    svc = create_service()
    _print_json(svc.health.compare(args.baseline, args.candidate))
    return 0


def cmd_incidents_status(_args: argparse.Namespace) -> int:
    svc = create_service()
    _print_json(svc.incidents.status())
    return 0


def cmd_incident_templates(_args: argparse.Namespace) -> int:
    svc = create_service()
    _print_json(svc.incidents.templates())
    return 0


def cmd_incidents_list(args: argparse.Namespace) -> int:
    svc = create_service()
    _print_json(svc.incidents.list(limit=args.limit, status=args.status, severity=args.severity, source=args.source))
    return 0


def cmd_incident_get(args: argparse.Namespace) -> int:
    svc = create_service()
    _print_json(svc.incidents.get(args.incident_id, timeline_limit=args.timeline_limit))
    return 0


def cmd_incident_create(args: argparse.Namespace) -> int:
    svc = create_service()
    tags = _parse_csv_list(args.tags)
    metadata = _parse_json_arg(args.metadata_json, {})
    if not isinstance(metadata, dict):
        raise SystemExit("--metadata-json must decode to a JSON object")
    _print_json(
        svc.incidents.create(
            title=args.title,
            severity=args.severity,
            description=args.description,
            source=args.source,
            status=args.status,
            tags=tags,
            metadata=metadata,
            auto_created=args.auto_created,
        )
    )
    return 0


def cmd_incident_update(args: argparse.Namespace) -> int:
    svc = create_service()
    tags = _parse_csv_list(args.tags) if args.tags is not None else None
    metadata_patch = _parse_json_arg(args.metadata_patch_json, None)
    if metadata_patch is not None and not isinstance(metadata_patch, dict):
        raise SystemExit("--metadata-patch-json must decode to a JSON object")
    _print_json(
        svc.incidents.update(
            args.incident_id,
            title=args.title,
            severity=args.severity,
            status=args.status,
            description=args.description,
            resolution=args.resolution,
            tags=tags,
            metadata_patch=metadata_patch,
        )
    )
    return 0


def cmd_incident_close(args: argparse.Namespace) -> int:
    svc = create_service()
    _print_json(svc.incidents.close(args.incident_id, resolution=args.resolution, note=args.note))
    return 0


def cmd_incident_reopen(args: argparse.Namespace) -> int:
    svc = create_service()
    _print_json(svc.incidents.reopen(args.incident_id, note=args.note))
    return 0


def cmd_incident_note(args: argparse.Namespace) -> int:
    svc = create_service()
    _print_json(svc.incidents.add_note(args.incident_id, note=args.note, author=args.author, kind=args.kind))
    return 0


def cmd_incident_link_alert(args: argparse.Namespace) -> int:
    svc = create_service()
    if args.alert_json:
        alert_obj = _parse_json_arg(args.alert_json, None)
        if not isinstance(alert_obj, dict):
            raise SystemExit("--alert-json must decode to a JSON object")
        _print_json(svc.incidents.link_alert(args.incident_id, alert_obj))
        return 0
    if not args.alert_id:
        raise SystemExit("Provide --alert-id or --alert-json")
    _print_json(svc.incidents.link_alert(args.incident_id, args.alert_id))
    return 0


def cmd_incident_timeline(args: argparse.Namespace) -> int:
    svc = create_service()
    _print_json(svc.incidents.timeline(incident_id=args.incident_id if args.incident_id else None, limit=args.limit))
    return 0


def cmd_incident_capture_bundle(args: argparse.Namespace) -> int:
    svc = create_service()
    _print_json(
        svc.incidents.capture_bundle(
            args.incident_id,
            health_profile=args.health_profile,
            report_profile=args.report_profile,
            include_health=not args.no_health,
            include_report=not args.no_report,
        )
    )
    return 0


def cmd_opsgraph_status(_args: argparse.Namespace) -> int:
    svc = create_service()
    _print_json(svc.opsgraph.status())
    return 0


def cmd_opsgraph_profiles(_args: argparse.Namespace) -> int:
    svc = create_service()
    _print_json(svc.opsgraph.profiles())
    return 0


def cmd_opsgraph_list(args: argparse.Namespace) -> int:
    svc = create_service()
    _print_json(svc.opsgraph.list_snapshots(limit=args.limit, profile=args.profile))
    return 0


def cmd_opsgraph_get(args: argparse.Namespace) -> int:
    svc = create_service()
    _print_json(svc.opsgraph.get_snapshot(args.snapshot_id))
    return 0


def cmd_opsgraph_snapshot(args: argparse.Namespace) -> int:
    svc = create_service()
    options = _parse_json_arg(args.options_json, {})
    if not isinstance(options, dict):
        raise SystemExit("--options-json must decode to a JSON object")
    _print_json(svc.opsgraph.snapshot(profile=args.profile, options=options, persist=not args.no_persist))
    return 0


def cmd_opsgraph_watch_list(args: argparse.Namespace) -> int:
    svc = create_service()
    _print_json(svc.opsgraph.list_watch(limit=args.limit, profile=args.profile))
    return 0


def cmd_opsgraph_watch_get(args: argparse.Namespace) -> int:
    svc = create_service()
    _print_json(svc.opsgraph.get_watch(args.watch_id))
    return 0


def cmd_opsgraph_watch(args: argparse.Namespace) -> int:
    svc = create_service()
    options = _parse_json_arg(args.options_json, {})
    if not isinstance(options, dict):
        raise SystemExit("--options-json must decode to a JSON object")
    _print_json(
        svc.opsgraph.watch(
            profile=args.profile,
            options=options,
            compare_with=args.compare_with,
            max_hotspots=args.max_hotspots,
            max_components=args.max_components,
            auto_incident=args.auto_incident,
            incident_threshold=args.incident_threshold,
            incident_cooldown_sec=args.incident_cooldown_sec,
            persist=not args.no_persist,
        )
    )
    return 0


def cmd_opsgraph_watch_trends(args: argparse.Namespace) -> int:
    svc = create_service()
    _print_json(svc.opsgraph.watch_trends(limit=args.limit, profile=args.profile))
    return 0


def cmd_opsgraph_watch_anomalies(args: argparse.Namespace) -> int:
    svc = create_service()
    _print_json(
        svc.opsgraph.watch_anomalies(
            watch_id=args.watch_id,
            profile=args.profile,
            baseline_limit=args.baseline_limit,
            z_threshold=args.z_threshold,
            delta_threshold=args.delta_threshold,
        )
    )
    return 0


def cmd_opsgraph_watch_forecast(args: argparse.Namespace) -> int:
    svc = create_service()
    _print_json(
        svc.opsgraph.watch_forecast(
            limit=args.limit,
            horizon=args.horizon,
            profile=args.profile,
        )
    )
    return 0


def cmd_opsgraph_trace(args: argparse.Namespace) -> int:
    svc = create_service()
    _print_json(
        svc.opsgraph.trace(
            args.node_id,
            snapshot_id=args.snapshot_id,
            depth=args.depth,
            direction=args.direction,
            max_nodes=args.max_nodes,
        )
    )
    return 0


def cmd_opsgraph_compare(args: argparse.Namespace) -> int:
    svc = create_service()
    _print_json(svc.opsgraph.compare(args.baseline, args.candidate, max_samples=args.max_samples))
    return 0


def cmd_opsgraph_path(args: argparse.Namespace) -> int:
    svc = create_service()
    _print_json(
        svc.opsgraph.path_between(
            args.source,
            args.target,
            snapshot_id=args.snapshot_id,
            direction=args.direction,
            max_depth=args.max_depth,
        )
    )
    return 0


def cmd_opsgraph_blast_radius(args: argparse.Namespace) -> int:
    svc = create_service()
    _print_json(
        svc.opsgraph.blast_radius(
            args.node_id,
            snapshot_id=args.snapshot_id,
            depth=args.depth,
            direction=args.direction,
            max_nodes=args.max_nodes,
        )
    )
    return 0


def cmd_opsgraph_hotspots(args: argparse.Namespace) -> int:
    svc = create_service()
    _print_json(
        svc.opsgraph.hotspots(
            snapshot_id=args.snapshot_id,
            limit=args.limit,
            include_zero=args.include_zero,
        )
    )
    return 0


def cmd_opsgraph_components(args: argparse.Namespace) -> int:
    svc = create_service()
    _print_json(
        svc.opsgraph.components(
            snapshot_id=args.snapshot_id,
            limit=args.limit,
            order_by=args.order_by,
        )
    )
    return 0


def cmd_opsgraph_explain_incident(args: argparse.Namespace) -> int:
    svc = create_service()
    _print_json(
        svc.opsgraph.explain_incident(
            args.incident_id,
            snapshot_id=args.snapshot_id,
            depth=args.depth,
            max_nodes=args.max_nodes,
        )
    )
    return 0


def cmd_advisor_status(_args: argparse.Namespace) -> int:
    svc = create_service()
    _print_json(svc.advisor.status())
    return 0


def cmd_advisor_profiles(_args: argparse.Namespace) -> int:
    svc = create_service()
    _print_json(svc.advisor.profiles())
    return 0


def cmd_advisor_list(args: argparse.Namespace) -> int:
    svc = create_service()
    _print_json(svc.advisor.list(limit=args.limit))
    return 0


def cmd_advisor_get(args: argparse.Namespace) -> int:
    svc = create_service()
    _print_json(svc.advisor.get(args.analysis_id))
    return 0


def cmd_advisor_analyze(args: argparse.Namespace) -> int:
    svc = create_service()
    options = _parse_json_arg(args.options_json, {})
    if not isinstance(options, dict):
        raise SystemExit("--options-json must decode to a JSON object")
    _print_json(
        svc.advisor.analyze(
            profile=args.profile,
            incident_id=args.incident_id,
            options=options,
            persist=not args.no_persist,
        )
    )
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    svc = create_service()
    _print_json(svc.advisor.doctor(incident_id=args.incident_id))
    return 0


def cmd_runbooks_status(_args: argparse.Namespace) -> int:
    svc = create_service()
    _print_json(svc.runbooks.status())
    return 0


def cmd_runbook_templates(args: argparse.Namespace) -> int:
    svc = create_service()
    _print_json(svc.runbooks.list_templates(include_steps=args.include_steps))
    return 0


def cmd_runbook_template_get(args: argparse.Namespace) -> int:
    svc = create_service()
    _print_json(svc.runbooks.get_template(args.name))
    return 0


def cmd_runbook_template_create(args: argparse.Namespace) -> int:
    svc = create_service()
    steps = _parse_json_arg(args.steps_json, None)
    metadata = _parse_json_arg(args.metadata_json, {})
    if not isinstance(steps, list) or not steps:
        raise SystemExit("--steps-json must decode to a non-empty JSON array")
    if not isinstance(metadata, dict):
        raise SystemExit("--metadata-json must decode to a JSON object")
    _print_json(
        svc.runbooks.create_template(
            name=args.name,
            title=args.title,
            description=args.description,
            steps=steps,
            tags=_parse_csv_list(args.tags),
            metadata=metadata,
        )
    )
    return 0


def cmd_runbook_template_update(args: argparse.Namespace) -> int:
    svc = create_service()
    steps = _parse_json_arg(args.steps_json, None)
    metadata = _parse_json_arg(args.metadata_json, None)
    if steps is not None and (not isinstance(steps, list) or not steps):
        raise SystemExit("--steps-json must decode to a non-empty JSON array")
    if metadata is not None and not isinstance(metadata, dict):
        raise SystemExit("--metadata-json must decode to a JSON object")
    tags = _parse_csv_list(args.tags) if args.tags is not None else None
    _print_json(
        svc.runbooks.update_template(
            args.name,
            title=args.title,
            description=args.description,
            steps=steps,
            tags=tags,
            metadata=metadata,
        )
    )
    return 0


def cmd_runbook_template_delete(args: argparse.Namespace) -> int:
    svc = create_service()
    _print_json(svc.runbooks.delete_template(args.name))
    return 0


def cmd_runbook_runs(args: argparse.Namespace) -> int:
    svc = create_service()
    _print_json(
        svc.runbooks.list_runs(
            limit=args.limit,
            status=args.status,
            template=args.template,
            incident_id=args.incident_id,
        )
    )
    return 0


def cmd_runbook_run_get(args: argparse.Namespace) -> int:
    svc = create_service()
    _print_json(svc.runbooks.get_run(args.run_id))
    return 0


def cmd_runbook_run(args: argparse.Namespace) -> int:
    svc = create_service()
    ctx = _parse_json_arg(args.context_json, {})
    if not isinstance(ctx, dict):
        raise SystemExit("--context-json must decode to a JSON object")
    _print_json(
        svc.runbooks.run(
            args.template,
            context=ctx,
            incident_id=args.incident_id,
            alert_id=args.alert_id,
            dry_run=args.dry_run,
            stop_on_error=(args.stop_on_error if args.stop_on_error is not None else None),
        )
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="omniforge_ai",
        description="Unified local AI workbench built from Supermix, NeuroDSL, NexusFlow, npu_easy, CIFAR ONNX, and Windhawk.",
    )
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("serve", help="Run the Flask API and dashboard.")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8787)
    s.set_defaults(func=cmd_serve)

    s = sub.add_parser("serve-windhawk", help="Generate/copy Windhawk hotkey mod, optionally restart Windhawk, then run the server.")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8787)
    s.add_argument("--path", default="/")
    s.add_argument("--no-restart", action="store_true")
    s.add_argument("--no-tray-only", action="store_true")
    s.set_defaults(func=cmd_serve_windhawk)

    s = sub.add_parser("status", help="Print adapter and repo status.")
    s.set_defaults(func=cmd_status)

    s = sub.add_parser("chat", help="Send a message to the Supermix local chat engine.")
    s.add_argument("message")
    s.add_argument("--session-id", default="omniforge-cli")
    s.set_defaults(func=cmd_chat)

    s = sub.add_parser("vision", help="Classify an image with npu_easy + local ONNX model.")
    s.add_argument("--image", required=True)
    s.add_argument("--top-k", type=int, default=3)
    s.set_defaults(func=cmd_vision)

    s = sub.add_parser("neurodsl-devices", help="Run NeuroDSL device discovery.")
    s.set_defaults(func=cmd_neurodsl_devices)

    s = sub.add_parser("neurodsl-manifest", help="Print NeuroDSL agent manifest.")
    s.set_defaults(func=cmd_neurodsl_manifest)

    s = sub.add_parser("nexusflow-example", help="Run a NexusFlow example from the repo examples folder.")
    s.add_argument("example")
    s.add_argument("--pipeline")
    s.add_argument("--out-dir")
    s.set_defaults(func=cmd_nexusflow_example)

    s = sub.add_parser("nexusflow-file", help="Run an arbitrary NexusFlow .nxf file.")
    s.add_argument("file_path")
    s.add_argument("--pipeline")
    s.add_argument("--out-dir")
    s.set_defaults(func=cmd_nexusflow_file)

    s = sub.add_parser("windhawk-generate-mod", help="Generate and optionally copy a Windhawk hotkey mod.")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8787)
    s.add_argument("--path", default="/")
    s.add_argument("--no-copy", action="store_true")
    s.set_defaults(func=cmd_windhawk_generate_mod)

    s = sub.add_parser("windhawk-restart", help="Restart Windhawk.")
    s.add_argument("--no-tray-only", action="store_true")
    s.set_defaults(func=cmd_windhawk_restart)

    s = sub.add_parser("smoke", help="Run a local smoke test across adapters.")
    s.set_defaults(func=cmd_smoke)

    s = sub.add_parser("agentic-plan", help="Create an agentic workflow plan for a task.")
    s.add_argument("task")
    s.add_argument("--image-path")
    s.add_argument("--session-id", default="agentic-cli")
    s.set_defaults(func=cmd_agentic_plan)

    s = sub.add_parser("agentic-execute", help="Plan and execute an agentic workflow for a task.")
    s.add_argument("task")
    s.add_argument("--image-path")
    s.set_defaults(func=cmd_agentic_execute)

    s = sub.add_parser("agentic-export-nxf", help="Plan task and export a NexusFlow pipeline that calls OmniForge CLI tools.")
    s.add_argument("task")
    s.add_argument("--image-path")
    s.set_defaults(func=cmd_agentic_export_nxf)

    s = sub.add_parser("agentic-run-nxf", help="Plan task, export a NexusFlow pipeline, and run it.")
    s.add_argument("task")
    s.add_argument("--image-path")
    s.set_defaults(func=cmd_agentic_run_nxf)

    s = sub.add_parser("benchmark-profiles", help="List built-in benchmark arena profiles.")
    s.set_defaults(func=cmd_benchmark_profiles)

    s = sub.add_parser("benchmark-run", help="Run the benchmark arena and save a benchmark artifact.")
    s.add_argument("--profile", help="Benchmark profile name (e.g. fast_local, chat_vision, workflow_probe).")
    s.add_argument("--config-json", help="Override config as JSON object.")
    s.set_defaults(func=cmd_benchmark_run)

    s = sub.add_parser("benchmark-list", help="List benchmark run artifacts.")
    s.add_argument("--limit", type=int, default=50)
    s.set_defaults(func=cmd_benchmark_list)

    s = sub.add_parser("benchmark-compare", help="Compare two benchmark runs.")
    s.add_argument("baseline")
    s.add_argument("candidate")
    s.set_defaults(func=cmd_benchmark_compare)

    s = sub.add_parser("benchmark-trends", help="Show benchmark trend points for a suite metric.")
    s.add_argument("--suite", default="vision_random")
    s.add_argument("--metric", default="duration_ms")
    s.add_argument("--limit", type=int, default=20)
    s.set_defaults(func=cmd_benchmark_trends)

    s = sub.add_parser("reports-profiles", help="List report generator profiles.")
    s.set_defaults(func=cmd_reports_profiles)

    s = sub.add_parser("reports-list", help="List generated report artifacts.")
    s.add_argument("--limit", type=int, default=50)
    s.set_defaults(func=cmd_reports_list)

    s = sub.add_parser("report-generate", help="Generate an intelligence report artifact (JSON + Markdown).")
    s.add_argument("--profile", default="ops_digest")
    s.add_argument("--options-json", default="{}")
    s.set_defaults(func=cmd_report_generate)

    s = sub.add_parser("alert-templates", help="List built-in alert rule templates.")
    s.set_defaults(func=cmd_alert_templates)

    s = sub.add_parser("alert-rules", help="List persisted alert rules.")
    s.set_defaults(func=cmd_alert_rules)

    s = sub.add_parser("alert-rule-get", help="Get an alert rule by id.")
    s.add_argument("rule_id")
    s.set_defaults(func=cmd_alert_rule_get)

    s = sub.add_parser("alert-rule-create", help="Create a persisted alert rule.")
    s.add_argument("--kind", required=True, choices=["benchmark_regression", "job_failure_recent", "history_event_recent", "scheduler_not_running"])
    s.add_argument("--name")
    s.add_argument("--severity", default="warning")
    s.add_argument("--cooldown-sec", type=float, default=300.0)
    s.add_argument("--config-json", default="{}")
    s.add_argument("--disabled", action="store_true")
    s.set_defaults(func=cmd_alert_rule_create)

    s = sub.add_parser("alert-rule-update", help="Update a persisted alert rule.")
    s.add_argument("rule_id")
    s.add_argument("--name")
    s.add_argument("--severity")
    s.add_argument("--cooldown-sec", type=float)
    s.add_argument("--config-json")
    s.add_argument("--enable", action="store_true")
    s.add_argument("--disable", action="store_true")
    s.set_defaults(func=cmd_alert_rule_update)

    s = sub.add_parser("alert-rule-delete", help="Delete a persisted alert rule.")
    s.add_argument("rule_id")
    s.set_defaults(func=cmd_alert_rule_delete)

    s = sub.add_parser("alerts-evaluate", help="Evaluate all alert rules.")
    s.add_argument("--all-rules", action="store_true", help="Include disabled rules.")
    s.add_argument("--force", action="store_true", help="Ignore cooldown suppression.")
    s.set_defaults(func=cmd_alerts_evaluate)

    s = sub.add_parser("alert-evaluate-rule", help="Evaluate a single alert rule.")
    s.add_argument("rule_id")
    s.add_argument("--force", action="store_true")
    s.set_defaults(func=cmd_alert_evaluate_rule)

    s = sub.add_parser("alerts-list", help="List recent alert events.")
    s.add_argument("--limit", type=int, default=50)
    s.add_argument("--rule-id")
    s.add_argument("--severity")
    s.add_argument("--acknowledged", action="store_true")
    s.add_argument("--unacknowledged", action="store_true")
    s.set_defaults(func=cmd_alerts_list)

    s = sub.add_parser("alert-get", help="Get an alert event by id.")
    s.add_argument("alert_id")
    s.set_defaults(func=cmd_alert_get)

    s = sub.add_parser("alert-ack", help="Acknowledge an alert event.")
    s.add_argument("alert_id")
    s.add_argument("--note")
    s.set_defaults(func=cmd_alert_ack)

    s = sub.add_parser("remediation-status", help="Show auto-remediation engine status.")
    s.set_defaults(func=cmd_remediation_status)

    s = sub.add_parser("remediation-templates", help="List remediation policy/action templates.")
    s.set_defaults(func=cmd_remediation_templates)

    s = sub.add_parser("remediation-policies", help="List remediation policies.")
    s.set_defaults(func=cmd_remediation_policies)

    s = sub.add_parser("remediation-policy-get", help="Get a remediation policy.")
    s.add_argument("policy_id")
    s.set_defaults(func=cmd_remediation_policy_get)

    s = sub.add_parser("remediation-policy-create", help="Create a remediation policy.")
    s.add_argument("--name")
    s.add_argument("--match-json", default="{}")
    s.add_argument("--actions-json", required=True, help="JSON array of remediation actions.")
    s.add_argument("--cooldown-sec", type=float, default=300.0)
    s.add_argument("--disabled", action="store_true")
    s.set_defaults(func=cmd_remediation_policy_create)

    s = sub.add_parser("remediation-policy-update", help="Update a remediation policy.")
    s.add_argument("policy_id")
    s.add_argument("--name")
    s.add_argument("--match-json")
    s.add_argument("--actions-json")
    s.add_argument("--cooldown-sec", type=float)
    s.add_argument("--enable", action="store_true")
    s.add_argument("--disable", action="store_true")
    s.set_defaults(func=cmd_remediation_policy_update)

    s = sub.add_parser("remediation-policy-delete", help="Delete a remediation policy.")
    s.add_argument("policy_id")
    s.set_defaults(func=cmd_remediation_policy_delete)

    s = sub.add_parser("remediation-actions", help="List remediation action executions.")
    s.add_argument("--limit", type=int, default=50)
    s.add_argument("--policy-id")
    s.add_argument("--ok", action="store_true")
    s.add_argument("--fail", action="store_true")
    s.set_defaults(func=cmd_remediation_actions)

    s = sub.add_parser("remediation-handle-alert", help="Manually run remediation matching for an alert.")
    s.add_argument("--alert-id")
    s.add_argument("--alert-json")
    s.add_argument("--force", action="store_true")
    s.set_defaults(func=cmd_remediation_handle_alert)

    s = sub.add_parser("notifications-status", help="Show notification subsystem status.")
    s.set_defaults(func=cmd_notifications_status)

    s = sub.add_parser("notification-templates", help="List notification channel templates and match fields.")
    s.set_defaults(func=cmd_notification_templates)

    s = sub.add_parser("notification-channels", help="List notification channels.")
    s.set_defaults(func=cmd_notification_channels)

    s = sub.add_parser("notification-channel-get", help="Get a notification channel.")
    s.add_argument("channel_id")
    s.set_defaults(func=cmd_notification_channel_get)

    s = sub.add_parser("notification-channel-create", help="Create a notification channel.")
    s.add_argument("--type", required=True, choices=["console", "history_event", "file_append", "webhook"])
    s.add_argument("--name")
    s.add_argument("--config-json", default="{}")
    s.add_argument("--match-json", default="{}")
    s.add_argument("--disabled", action="store_true")
    s.set_defaults(func=cmd_notification_channel_create)

    s = sub.add_parser("notification-channel-update", help="Update a notification channel.")
    s.add_argument("channel_id")
    s.add_argument("--name")
    s.add_argument("--config-json")
    s.add_argument("--match-json")
    s.add_argument("--enable", action="store_true")
    s.add_argument("--disable", action="store_true")
    s.set_defaults(func=cmd_notification_channel_update)

    s = sub.add_parser("notification-channel-delete", help="Delete a notification channel.")
    s.add_argument("channel_id")
    s.set_defaults(func=cmd_notification_channel_delete)

    s = sub.add_parser("notification-deliveries", help="List notification deliveries.")
    s.add_argument("--limit", type=int, default=50)
    s.add_argument("--channel-id")
    s.add_argument("--topic")
    s.add_argument("--severity")
    s.add_argument("--ok", action="store_true")
    s.add_argument("--fail", action="store_true")
    s.set_defaults(func=cmd_notification_deliveries)

    s = sub.add_parser("notification-send", help="Dispatch a notification through matching channels.")
    s.add_argument("--topic", required=True)
    s.add_argument("--message", required=True)
    s.add_argument("--severity", default="info")
    s.add_argument("--payload-json", default="{}")
    s.add_argument("--tags", help="Comma-separated tags")
    s.add_argument("--target-channel-ids", help="Comma-separated channel ids to target directly")
    s.set_defaults(func=cmd_notification_send)

    s = sub.add_parser("health-status", help="Show health subsystem status.")
    s.set_defaults(func=cmd_health_status)

    s = sub.add_parser("health-profiles", help="List health snapshot profiles.")
    s.set_defaults(func=cmd_health_profiles)

    s = sub.add_parser("health-list", help="List persisted health snapshots.")
    s.add_argument("--limit", type=int, default=50)
    s.set_defaults(func=cmd_health_list)

    s = sub.add_parser("health-snapshot", help="Create a new health snapshot.")
    s.add_argument("--profile", default="full")
    s.add_argument("--options-json", default="{}")
    s.set_defaults(func=cmd_health_snapshot)

    s = sub.add_parser("health-get", help="Get a health snapshot by id.")
    s.add_argument("snapshot_id")
    s.set_defaults(func=cmd_health_get)

    s = sub.add_parser("health-latest", help="Get the latest health snapshot.")
    s.add_argument("--optional", action="store_true", help="Return ok with null snapshot when none exist.")
    s.set_defaults(func=cmd_health_latest)

    s = sub.add_parser("health-compare", help="Compare two health snapshots.")
    s.add_argument("baseline")
    s.add_argument("candidate")
    s.set_defaults(func=cmd_health_compare)

    s = sub.add_parser("incidents-status", help="Show incident response center status.")
    s.set_defaults(func=cmd_incidents_status)

    s = sub.add_parser("incident-templates", help="List incident statuses/severities and bundle templates.")
    s.set_defaults(func=cmd_incident_templates)

    s = sub.add_parser("incidents-list", help="List incidents.")
    s.add_argument("--limit", type=int, default=50)
    s.add_argument("--status")
    s.add_argument("--severity")
    s.add_argument("--source")
    s.set_defaults(func=cmd_incidents_list)

    s = sub.add_parser("incident-get", help="Get an incident with timeline entries.")
    s.add_argument("incident_id")
    s.add_argument("--timeline-limit", type=int, default=50)
    s.set_defaults(func=cmd_incident_get)

    s = sub.add_parser("incident-create", help="Create a manual incident.")
    s.add_argument("title")
    s.add_argument("--severity", default="warning")
    s.add_argument("--description")
    s.add_argument("--source", default="manual")
    s.add_argument("--status", default="open")
    s.add_argument("--tags", help="Comma-separated tags")
    s.add_argument("--metadata-json", default="{}")
    s.add_argument("--auto-created", action="store_true")
    s.set_defaults(func=cmd_incident_create)

    s = sub.add_parser("incident-update", help="Update an incident.")
    s.add_argument("incident_id")
    s.add_argument("--title")
    s.add_argument("--severity")
    s.add_argument("--status")
    s.add_argument("--description")
    s.add_argument("--resolution")
    s.add_argument("--tags", help="Comma-separated tags (replace)")
    s.add_argument("--metadata-patch-json")
    s.set_defaults(func=cmd_incident_update)

    s = sub.add_parser("incident-close", help="Close an incident.")
    s.add_argument("incident_id")
    s.add_argument("--resolution")
    s.add_argument("--note")
    s.set_defaults(func=cmd_incident_close)

    s = sub.add_parser("incident-reopen", help="Reopen a closed incident.")
    s.add_argument("incident_id")
    s.add_argument("--note")
    s.set_defaults(func=cmd_incident_reopen)

    s = sub.add_parser("incident-note", help="Append a note to an incident timeline.")
    s.add_argument("incident_id")
    s.add_argument("note")
    s.add_argument("--author")
    s.add_argument("--kind", default="note")
    s.set_defaults(func=cmd_incident_note)

    s = sub.add_parser("incident-link-alert", help="Link an alert event to an incident.")
    s.add_argument("incident_id")
    s.add_argument("--alert-id")
    s.add_argument("--alert-json")
    s.set_defaults(func=cmd_incident_link_alert)

    s = sub.add_parser("incident-timeline", help="Show incident timeline events (or global timeline if no id).")
    s.add_argument("--incident-id")
    s.add_argument("--limit", type=int, default=100)
    s.set_defaults(func=cmd_incident_timeline)

    s = sub.add_parser("incident-capture-bundle", help="Capture a health snapshot and/or report linked to an incident.")
    s.add_argument("incident_id")
    s.add_argument("--health-profile", default="full")
    s.add_argument("--report-profile", default="health_watch")
    s.add_argument("--no-health", action="store_true")
    s.add_argument("--no-report", action="store_true")
    s.set_defaults(func=cmd_incident_capture_bundle)

    s = sub.add_parser("opsgraph-status", help="Show OpsGraph Center status.")
    s.set_defaults(func=cmd_opsgraph_status)

    s = sub.add_parser("opsgraph-profiles", help="List OpsGraph snapshot profiles.")
    s.set_defaults(func=cmd_opsgraph_profiles)

    s = sub.add_parser("opsgraph-list", help="List persisted OpsGraph snapshots.")
    s.add_argument("--limit", type=int, default=50)
    s.add_argument("--profile")
    s.set_defaults(func=cmd_opsgraph_list)

    s = sub.add_parser("opsgraph-watch-list", help="List persisted OpsGraph watch runs.")
    s.add_argument("--limit", type=int, default=50)
    s.add_argument("--profile")
    s.set_defaults(func=cmd_opsgraph_watch_list)

    s = sub.add_parser("opsgraph-get", help="Get an OpsGraph snapshot by id.")
    s.add_argument("snapshot_id")
    s.set_defaults(func=cmd_opsgraph_get)

    s = sub.add_parser("opsgraph-watch-get", help="Get an OpsGraph watch run by id.")
    s.add_argument("watch_id")
    s.set_defaults(func=cmd_opsgraph_watch_get)

    s = sub.add_parser("opsgraph-snapshot", help="Build and optionally persist an OpsGraph snapshot.")
    s.add_argument("--profile", default="runtime")
    s.add_argument("--options-json", default="{}")
    s.add_argument("--no-persist", action="store_true")
    s.set_defaults(func=cmd_opsgraph_snapshot)

    s = sub.add_parser("opsgraph-watch", help="Run an OpsGraph watch cycle (snapshot + analysis + optional compare).")
    s.add_argument("--profile", default="runtime")
    s.add_argument("--options-json", default="{}")
    s.add_argument("--compare-with", default="latest")
    s.add_argument("--max-hotspots", type=int, default=10)
    s.add_argument("--max-components", type=int, default=6)
    s.add_argument("--auto-incident", action="store_true")
    s.add_argument("--incident-threshold", type=float, default=80.0)
    s.add_argument("--incident-cooldown-sec", type=float, default=3600.0)
    s.add_argument("--no-persist", action="store_true")
    s.set_defaults(func=cmd_opsgraph_watch)

    s = sub.add_parser("opsgraph-watch-trends", help="Show score trends from persisted OpsGraph watch runs.")
    s.add_argument("--limit", type=int, default=50)
    s.add_argument("--profile")
    s.set_defaults(func=cmd_opsgraph_watch_trends)

    s = sub.add_parser("opsgraph-watch-anomalies", help="Detect anomalous watch runs versus rolling baseline.")
    s.add_argument("--watch-id")
    s.add_argument("--profile")
    s.add_argument("--baseline-limit", type=int, default=20)
    s.add_argument("--z-threshold", type=float, default=1.8)
    s.add_argument("--delta-threshold", type=float, default=10.0)
    s.set_defaults(func=cmd_opsgraph_watch_anomalies)

    s = sub.add_parser("opsgraph-watch-forecast", help="Forecast future watch risk scores from recent trend.")
    s.add_argument("--limit", type=int, default=30)
    s.add_argument("--horizon", type=int, default=5)
    s.add_argument("--profile")
    s.set_defaults(func=cmd_opsgraph_watch_forecast)

    s = sub.add_parser("opsgraph-trace", help="Trace a node neighborhood in an OpsGraph snapshot.")
    s.add_argument("node_id")
    s.add_argument("--snapshot-id")
    s.add_argument("--depth", type=int, default=2)
    s.add_argument("--direction", choices=["both", "out", "in"], default="both")
    s.add_argument("--max-nodes", type=int, default=200)
    s.set_defaults(func=cmd_opsgraph_trace)

    s = sub.add_parser("opsgraph-compare", help="Compare two persisted OpsGraph snapshots.")
    s.add_argument("baseline")
    s.add_argument("candidate")
    s.add_argument("--max-samples", type=int, default=25)
    s.set_defaults(func=cmd_opsgraph_compare)

    s = sub.add_parser("opsgraph-path", help="Find a shortest path between two nodes in an OpsGraph snapshot.")
    s.add_argument("source")
    s.add_argument("target")
    s.add_argument("--snapshot-id")
    s.add_argument("--direction", choices=["both", "out", "in"], default="both")
    s.add_argument("--max-depth", type=int, default=8)
    s.set_defaults(func=cmd_opsgraph_path)

    s = sub.add_parser("opsgraph-blast-radius", help="Estimate impacted entities around a node using graph traversal.")
    s.add_argument("node_id")
    s.add_argument("--snapshot-id")
    s.add_argument("--depth", type=int, default=2)
    s.add_argument("--direction", choices=["both", "out", "in"], default="out")
    s.add_argument("--max-nodes", type=int, default=300)
    s.set_defaults(func=cmd_opsgraph_blast_radius)

    s = sub.add_parser("opsgraph-hotspots", help="Rank top risk hotspots in an OpsGraph snapshot.")
    s.add_argument("--snapshot-id")
    s.add_argument("--limit", type=int, default=20)
    s.add_argument("--include-zero", action="store_true")
    s.set_defaults(func=cmd_opsgraph_hotspots)

    s = sub.add_parser("opsgraph-components", help="Summarize connected graph components with risk scoring.")
    s.add_argument("--snapshot-id")
    s.add_argument("--limit", type=int, default=20)
    s.add_argument("--order-by", choices=["risk", "size"], default="risk")
    s.set_defaults(func=cmd_opsgraph_components)

    s = sub.add_parser("opsgraph-explain-incident", help="Produce an RCA-style explanation from an incident graph neighborhood.")
    s.add_argument("incident_id")
    s.add_argument("--snapshot-id")
    s.add_argument("--depth", type=int, default=2)
    s.add_argument("--max-nodes", type=int, default=200)
    s.set_defaults(func=cmd_opsgraph_explain_incident)

    s = sub.add_parser("advisor-status", help="Show SmartOps advisor subsystem status.")
    s.set_defaults(func=cmd_advisor_status)

    s = sub.add_parser("advisor-profiles", help="List SmartOps advisor analysis profiles.")
    s.set_defaults(func=cmd_advisor_profiles)

    s = sub.add_parser("advisor-list", help="List persisted advisor analyses.")
    s.add_argument("--limit", type=int, default=50)
    s.set_defaults(func=cmd_advisor_list)

    s = sub.add_parser("advisor-get", help="Get a persisted advisor analysis by id.")
    s.add_argument("analysis_id")
    s.set_defaults(func=cmd_advisor_get)

    s = sub.add_parser("advisor-analyze", help="Run SmartOps advisor analysis and persist results.")
    s.add_argument("--profile", default="quick")
    s.add_argument("--incident-id")
    s.add_argument("--options-json", default="{}")
    s.add_argument("--no-persist", action="store_true")
    s.set_defaults(func=cmd_advisor_analyze)

    s = sub.add_parser("doctor", help="Run a quick SmartOps diagnostic with prioritized recommendations.")
    s.add_argument("--incident-id")
    s.set_defaults(func=cmd_doctor)

    s = sub.add_parser("runbooks-status", help="Show Runbook Center status.")
    s.set_defaults(func=cmd_runbooks_status)

    s = sub.add_parser("runbook-templates", help="List builtin and custom runbook templates.")
    s.add_argument("--include-steps", action="store_true")
    s.set_defaults(func=cmd_runbook_templates)

    s = sub.add_parser("runbook-template-get", help="Get a runbook template by name.")
    s.add_argument("name")
    s.set_defaults(func=cmd_runbook_template_get)

    s = sub.add_parser("runbook-template-create", help="Create a custom runbook template.")
    s.add_argument("name")
    s.add_argument("--title")
    s.add_argument("--description")
    s.add_argument("--steps-json", required=True, help="JSON array of runbook steps.")
    s.add_argument("--tags", help="Comma-separated tags")
    s.add_argument("--metadata-json", default="{}")
    s.set_defaults(func=cmd_runbook_template_create)

    s = sub.add_parser("runbook-template-update", help="Update a custom runbook template.")
    s.add_argument("name")
    s.add_argument("--title")
    s.add_argument("--description")
    s.add_argument("--steps-json")
    s.add_argument("--tags", help="Comma-separated tags (replace)")
    s.add_argument("--metadata-json")
    s.set_defaults(func=cmd_runbook_template_update)

    s = sub.add_parser("runbook-template-delete", help="Delete a custom runbook template.")
    s.add_argument("name")
    s.set_defaults(func=cmd_runbook_template_delete)

    s = sub.add_parser("runbook-runs", help="List runbook execution runs.")
    s.add_argument("--limit", type=int, default=50)
    s.add_argument("--status")
    s.add_argument("--template")
    s.add_argument("--incident-id")
    s.set_defaults(func=cmd_runbook_runs)

    s = sub.add_parser("runbook-run-get", help="Get a runbook execution run by id.")
    s.add_argument("run_id")
    s.set_defaults(func=cmd_runbook_run_get)

    s = sub.add_parser("runbook-run", help="Execute a runbook template.")
    s.add_argument("template")
    s.add_argument("--incident-id")
    s.add_argument("--alert-id")
    s.add_argument("--context-json", default="{}")
    s.add_argument("--dry-run", action="store_true")
    s.add_argument("--stop-on-error", dest="stop_on_error", action="store_true")
    s.add_argument("--continue-on-error", dest="stop_on_error", action="store_false")
    s.set_defaults(func=cmd_runbook_run, stop_on_error=None)

    s = sub.add_parser("history-list", help="List persistent OmniForge history events.")
    s.add_argument("--limit", type=int, default=50)
    s.add_argument("--event-type")
    s.set_defaults(func=cmd_history_list)

    s = sub.add_parser("history-get", help="Get a history event by id.")
    s.add_argument("event_id")
    s.set_defaults(func=cmd_history_get)

    s = sub.add_parser("history-follow", help="Follow new history events from the local history log.")
    s.add_argument("--event-type")
    s.add_argument("--tail", type=int, default=10)
    s.add_argument("--from-end", action="store_true", help="Skip initial tail and start from end of file.")
    s.add_argument("--timeout-sec", type=float, default=30.0)
    s.add_argument("--poll-sec", type=float, default=0.5)
    s.add_argument("--max-events", type=int, default=100)
    s.set_defaults(func=cmd_history_follow)

    s = sub.add_parser("artifacts-roots", help="List artifact roots available for browsing.")
    s.set_defaults(func=cmd_artifacts_roots)

    s = sub.add_parser("artifacts-list", help="List files in an artifact root.")
    s.add_argument("--root", required=True)
    s.add_argument("--path", default="")
    s.add_argument("--recursive", action="store_true")
    s.add_argument("--limit", type=int, default=200)
    s.set_defaults(func=cmd_artifacts_list)

    s = sub.add_parser("artifacts-read", help="Read a text/JSON artifact file.")
    s.add_argument("--root", required=True)
    s.add_argument("--path", required=True)
    s.add_argument("--max-bytes", type=int, default=200000)
    s.set_defaults(func=cmd_artifacts_read)

    s = sub.add_parser("jobs-list", help="List job queue records (completed and in-memory).")
    s.add_argument("--limit", type=int, default=50)
    s.add_argument("--status")
    s.set_defaults(func=cmd_jobs_list)

    s = sub.add_parser("job-get", help="Inspect a job by id.")
    s.add_argument("job_id")
    s.set_defaults(func=cmd_job_get)

    s = sub.add_parser("job-run", help="Submit an operation to the local async queue and wait for completion.")
    s.add_argument("kind", help="Operation kind, e.g. benchmark.arena or vision.benchmark")
    s.add_argument("--params-json", default="{}")
    s.add_argument("--timeout-sec", type=float)
    s.set_defaults(func=cmd_job_run)

    s = sub.add_parser("ops-catalog", help="List generic operation kinds supported by OmniForge.")
    s.set_defaults(func=cmd_ops_catalog)

    s = sub.add_parser("op-run", help="Run any supported operation kind via the generic operation router.")
    s.add_argument("kind")
    s.add_argument("--params-json", default="{}")
    s.set_defaults(func=cmd_op_run)

    s = sub.add_parser("op-submit", help="Queue any supported operation kind as an async job.")
    s.add_argument("kind")
    s.add_argument("--params-json", default="{}")
    s.set_defaults(func=cmd_op_submit)

    s = sub.add_parser("search-roots", help="List local workspace search roots.")
    s.set_defaults(func=cmd_search_roots)

    s = sub.add_parser("search-files", help="Find files by keyword across local repos/workspace.")
    s.add_argument("query")
    s.add_argument("--root")
    s.add_argument("--glob")
    s.add_argument("--extensions", help="Comma-separated extensions, e.g. py,md,json")
    s.add_argument("--limit", type=int, default=100)
    s.set_defaults(func=cmd_search_files)

    s = sub.add_parser("search-text", help="Search text across local repos/workspace.")
    s.add_argument("pattern")
    s.add_argument("--root")
    s.add_argument("--glob")
    s.add_argument("--extensions", help="Comma-separated extensions, e.g. py,md")
    s.add_argument("--regex", action="store_true")
    s.add_argument("--case-sensitive", action="store_true")
    s.add_argument("--limit-matches", type=int, default=100)
    s.add_argument("--max-file-bytes", type=int, default=1_500_000)
    s.add_argument("--context-lines", type=int, default=1)
    s.set_defaults(func=cmd_search_text)

    s = sub.add_parser("missions-list", help="List built-in mission packs.")
    s.set_defaults(func=cmd_missions_list)

    s = sub.add_parser("mission-plan", help="Preview a mission pack with rendered inputs.")
    s.add_argument("mission")
    s.add_argument("--inputs-json", default="{}")
    s.set_defaults(func=cmd_mission_plan)

    s = sub.add_parser("mission-run", help="Run a mission pack (sync by default).")
    s.add_argument("mission")
    s.add_argument("--inputs-json", default="{}")
    s.add_argument("--async-run", action="store_true")
    s.set_defaults(func=cmd_mission_run)

    s = sub.add_parser("scheduler-status", help="Show scheduler status (for current process/persisted schedules).")
    s.set_defaults(func=cmd_scheduler_status)

    s = sub.add_parser("scheduler-tick", help="Process due schedules once and enqueue jobs.")
    s.set_defaults(func=cmd_scheduler_tick)

    s = sub.add_parser("schedules-list", help="List persisted autonomous schedules.")
    s.set_defaults(func=cmd_schedules_list)

    s = sub.add_parser("schedule-get", help="Get a persisted autonomous schedule.")
    s.add_argument("schedule_id")
    s.set_defaults(func=cmd_schedule_get)

    s = sub.add_parser("schedule-create", help="Create a persisted autonomous schedule.")
    s.add_argument("--target-kind", required=True, help="Operation kind to run (e.g. missions.run, report.generate)")
    s.add_argument("--name")
    s.add_argument("--params-json", default="{}")
    s.add_argument("--interval-sec", type=float, default=300.0)
    s.add_argument("--disabled", action="store_true")
    s.add_argument("--start-immediately", action="store_true")
    s.set_defaults(func=cmd_schedule_create)

    s = sub.add_parser("schedule-update", help="Update a persisted autonomous schedule.")
    s.add_argument("schedule_id")
    s.add_argument("--name")
    s.add_argument("--target-kind")
    s.add_argument("--params-json")
    s.add_argument("--interval-sec", type=float)
    s.add_argument("--enable", action="store_true")
    s.add_argument("--disable", action="store_true")
    s.set_defaults(func=cmd_schedule_update)

    s = sub.add_parser("schedule-delete", help="Delete a persisted autonomous schedule.")
    s.add_argument("schedule_id")
    s.set_defaults(func=cmd_schedule_delete)

    s = sub.add_parser("schedule-run-now", help="Submit a schedule immediately as a job.")
    s.add_argument("schedule_id")
    s.set_defaults(func=cmd_schedule_run_now)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))

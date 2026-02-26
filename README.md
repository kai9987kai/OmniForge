# OmniForge AI Workbench

Windows-first local AI orchestration platform that unifies multiple repos/tools into one CLI + REST API + dashboard.

It combines:

- `Supermix_27` (local chat runtime)
- `NeuroDSL-Infinity-Studio2` (NeuroDSL CLI + agent API bridge)
- `nexusflow` (workflow DSL execution)
- `npu_easy` + `CIFAR-100/cifar100_model.onnx` (local ONNX vision inference via DirectML/CPU)
- `single-file-lab-studio` (dashboard style/pattern)
- Windhawk (hotkey mod generation/restart integration)

This is an orchestration/workbench system, not a newly trained foundation model.

## What It Does

OmniForge exposes a single local interface for:

- Chat + vision inference
- NeuroDSL tooling and hosted `serve-agent-api` proxying
- NexusFlow workflow execution
- Agentic planning/execution and NexusFlow export/run
- Benchmarks, comparisons, trends
- Async job queue + scheduler
- Search and mission packs
- Reports, alerts, remediations
- Notifications, health snapshots, and incident response workflows

## Current Feature Set

### Core runtime

- Flask API + local dashboard (`/`)
- CLI (`python -m omniforge_ai ...`)
- Generic operation router (`ops-catalog`, `op-run`, `op-submit`)
- Persistent history + artifact browser

### AI / workflow integrations

- Supermix local chat adapter
- ONNX vision inference (`npu_easy` + DirectML/CPU fallback)
- NeuroDSL bridge + agent API subprocess manager/proxy
- NexusFlow example/file runner
- Windhawk mod generation + restart helpers
- Agentic planner/executor + NexusFlow pipeline export/run

### Observability / automation

- Benchmark arena + analytics (list/compare/trends)
- Async jobs + persistent job records
- Scheduler (persisted recurring tasks)
- Reports (`ops_digest`, `alert_watch`, `health_watch`, `incident_watch`, etc.)
- Alerts + acknowledgements
- Auto-remediations (job submit / report / scheduler / generic op / notification / health snapshot)
- Notifications (console, history-event, file append, webhook)
- Health monitor (snapshots, scoring, compare)
- Incident Response Center (manual + auto-open from critical alerts, timeline, bundle capture)
- OpsGraph intelligence (snapshots, graph trace/path/blast-radius, incident explainers, watch runs, hotspots, component risk mapping)

## Expected Workspace Layout

`omniforge_ai` expects sibling repos in the parent workspace directory (see `omniforge_ai/omniforge_ai/config.py`):

```text
<workspace>\
  omniforge_ai\
  Supermix_27\
  NeuroDSL-Infinity-Studio2\
  nexusflow\
  npu_easy\
  CIFAR-100\
  single-file-lab-studio\
```

It also expects a local Windhawk install at:

- `C:\Program Files\Windhawk` (optional but recommended for hotkey integration)
- `C:\ProgramData\Windhawk` (for writable mods)

## Requirements

- Windows (tested on local Windows environment)
- Python 3.10+ (3.13 has been used successfully)
- Local clones of the repos listed above
- Python dependencies:

```powershell
cd omniforge_ai
python -m pip install -r requirements.txt
```

`requirements.txt` currently includes:

- `flask`
- `numpy`
- `pillow`
- `torch`
- `onnxruntime`

## Quick Start

### Start API + dashboard

```powershell
cd omniforge_ai
python -m omniforge_ai serve --host 127.0.0.1 --port 8787
```

Open `http://127.0.0.1:8787`.

### Start with Windhawk hotkey integration

```powershell
python -m omniforge_ai serve-windhawk --host 127.0.0.1 --port 8787
```

This generates/copies the Windhawk hotkey mod and optionally restarts Windhawk before starting the server.

## CLI Quick Examples

### Core / adapters

```powershell
python -m omniforge_ai status
python -m omniforge_ai smoke
python -m omniforge_ai chat "hello"
python -m omniforge_ai vision --image C:\path\to\image.jpg
python -m omniforge_ai neurodsl-devices
python -m omniforge_ai neurodsl-manifest
python -m omniforge_ai nexusflow-example npu_workflow_demo.nxf --pipeline build
```

### Agentic / workflow

```powershell
python -m omniforge_ai agentic-plan "benchmark npu workflow and explain the results"
python -m omniforge_ai agentic-execute "benchmark npu workflow and explain the results"
python -m omniforge_ai agentic-export-nxf "say hello and answer briefly"
python -m omniforge_ai agentic-run-nxf "say hello and answer briefly"
```

### Benchmarks / jobs / scheduler

```powershell
python -m omniforge_ai benchmark-profiles
python -m omniforge_ai benchmark-run --profile fast_local
python -m omniforge_ai benchmark-list --limit 10
python -m omniforge_ai benchmark-trends --suite vision_random --metric infer_ms_stats.avg
python -m omniforge_ai job-run benchmark.profiles
python -m omniforge_ai schedule-create --target-kind alerts.evaluate --params-json "{\"only_enabled\":true}" --interval-sec 300
python -m omniforge_ai scheduler-tick
```

### Alerts / remediations / notifications / health / incidents

```powershell
python -m omniforge_ai alert-templates
python -m omniforge_ai alerts-evaluate --force
python -m omniforge_ai remediation-templates
python -m omniforge_ai notifications-status
python -m omniforge_ai health-snapshot --profile full
python -m omniforge_ai incidents-status
python -m omniforge_ai incident-create "NPU throughput regression" --severity warning --tags perf,triage
```

### OpsGraph analytics

```powershell
python -m omniforge_ai opsgraph-snapshot --profile runtime
python -m omniforge_ai opsgraph-watch --profile runtime --compare-with latest
python -m omniforge_ai opsgraph-watch --profile runtime --auto-incident --incident-threshold 80
python -m omniforge_ai opsgraph-watch-list --limit 10
python -m omniforge_ai opsgraph-watch-trends --limit 20
python -m omniforge_ai opsgraph-watch-anomalies --baseline-limit 20
python -m omniforge_ai opsgraph-watch-forecast --limit 30 --horizon 5
python -m omniforge_ai opsgraph-hotspots --limit 10
python -m omniforge_ai opsgraph-components --limit 10 --order-by risk
python -m omniforge_ai opsgraph-path incident:inc_* alert:alert_* --max-depth 6
python -m omniforge_ai opsgraph-blast-radius alert:alert_* --depth 2 --direction out
```

### Reports / artifacts / history

```powershell
python -m omniforge_ai reports-profiles
python -m omniforge_ai report-generate --profile health_watch
python -m omniforge_ai history-list --limit 20
python -m omniforge_ai artifacts-roots
python -m omniforge_ai artifacts-list --root reports
```

## REST API Overview

Selected endpoint groups (all local, JSON unless noted):

- `/api/status`
- `/api/chat`, `/api/chat/load`
- `/api/vision/*`
- `/api/neurodsl/*` and `/api/neurodsl/agent-api/*`
- `/api/nexusflow/*`
- `/api/agentic/*`
- `/api/ops/*` (generic op catalog/run/submit)
- `/api/jobs*`
- `/api/benchmarks/*`
- `/api/search/*`
- `/api/missions*`
- `/api/scheduler/*` and `/api/schedules*`
- `/api/reports/*`
- `/api/alerts/*`
- `/api/remediations/*`
- `/api/notifications/*`
- `/api/health/*`
- `/api/incidents/*`
- `/api/opsgraph/*`
- `/api/history*`
- `/api/artifacts/*`
- `/api/stream/history` (SSE)

Example:

```powershell
Invoke-RestMethod http://127.0.0.1:8787/api/status
Invoke-RestMethod http://127.0.0.1:8787/api/health/status
Invoke-RestMethod http://127.0.0.1:8787/api/incidents/status
```

## Incident / Alert Flow (AutoOps)

Current automation stack supports:

1. `alert` rules detect regressions/failures/event patterns.
2. `remediations` can auto-run actions (jobs, reports, scheduler ops, notifications, health snapshots).
3. `notifications` can fan out alert/health messages to console/history/file/webhook.
4. `incidents` can auto-open/link incidents from critical alerts and store timelines.
5. `incident-capture-bundle` links health snapshots and reports to incidents for triage.

## Persistent Data (Under `out/`)

OmniForge stores runtime state locally in `omniforge_ai/out/`, including:

- `history/`
- `jobs/`
- `benchmarks/`
- `schedules/`
- `reports/`
- `alerts/`
- `remediations/`
- `notifications/`
- `health/`
- `incidents/`
- `missions/`
- `agentic_nxf_runs/`
- `runtime/` (server stdout/stderr logs)

## Windhawk Notes

- `windhawk-generate-mod` creates a `.wh.cpp` mod source and can copy it to Windhawk writable mods.
- `serve-windhawk` can auto-generate and restart Windhawk, but local Windhawk UI settings may still require manual compile/enable confirmation.

## Known Notes / Limitations

- Supermix/vision/NeuroDSL/NexusFlow availability depends on your local repo state and models.
- Vision may use DirectML when available and fall back to CPU otherwise.
- NeuroDSL integration uses subprocess isolation for stability.
- Some features are Windows-specific (Windhawk, DirectML-focused paths).

## Developer Notes

- Entry point: `omniforge_ai/omniforge_ai/__main__.py`
- Main CLI command table: `omniforge_ai/omniforge_ai/cli.py`
- Flask app/routes: `omniforge_ai/omniforge_ai/server.py`
- Core orchestration service + ops router: `omniforge_ai/omniforge_ai/service.py`

## License / Usage

This README describes the local integration layer in this folder. Refer to each upstream repo for its own license and model/runtime usage terms.

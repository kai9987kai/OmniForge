from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class BenchmarkAnalytics:
    def __init__(self, benchmarks_dir: Path):
        self.benchmarks_dir = Path(benchmarks_dir).resolve()
        self.benchmarks_dir.mkdir(parents=True, exist_ok=True)

    def list_runs(self, *, limit: int = 50) -> dict[str, Any]:
        items = []
        for path in sorted(self.benchmarks_dir.glob("bench_*.json"), reverse=True):
            run = self._load(path)
            if not isinstance(run, dict):
                continue
            items.append(self._run_summary(run, path))
            if len(items) >= max(1, int(limit)):
                break
        return {"ok": True, "count": len(items), "items": items}

    def compare(self, baseline: str, candidate: str) -> dict[str, Any]:
        a_path = self._resolve_run(baseline)
        b_path = self._resolve_run(candidate)
        a = self._load(a_path)
        b = self._load(b_path)
        if not isinstance(a, dict) or not isinstance(b, dict):
            raise ValueError("invalid benchmark run file(s)")
        suites_a = {str(s.get("suite")): s for s in (a.get("suites") or []) if isinstance(s, dict) and s.get("suite")}
        suites_b = {str(s.get("suite")): s for s in (b.get("suites") or []) if isinstance(s, dict) and s.get("suite")}
        suite_names = sorted(set(suites_a) | set(suites_b))
        suite_diffs = []
        regressions = []
        improvements = []
        for name in suite_names:
            sa = suites_a.get(name)
            sb = suites_b.get(name)
            da = _num(sa.get("duration_ms")) if isinstance(sa, dict) else None
            db = _num(sb.get("duration_ms")) if isinstance(sb, dict) else None
            delta_ms = (None if da is None or db is None else round(db - da, 3))
            pct = (None if da in (None, 0.0) or db is None else round((db - da) / da * 100.0, 3))
            row = {
                "suite": name,
                "baseline_duration_ms": da,
                "candidate_duration_ms": db,
                "delta_ms": delta_ms,
                "delta_pct": pct,
            }
            # Extra metric for vision suite.
            if isinstance(sa, dict):
                row["baseline_infer_avg_ms"] = _dot(sa, "infer_ms_stats.avg")
            if isinstance(sb, dict):
                row["candidate_infer_avg_ms"] = _dot(sb, "infer_ms_stats.avg")
            suite_diffs.append(row)
            if pct is not None:
                if pct >= 10.0:
                    regressions.append({"suite": name, "delta_pct": pct})
                elif pct <= -10.0:
                    improvements.append({"suite": name, "delta_pct": pct})
        return {
            "ok": True,
            "baseline": self._run_summary(a, a_path),
            "candidate": self._run_summary(b, b_path),
            "suite_diffs": suite_diffs,
            "regressions": regressions,
            "improvements": improvements,
        }

    def trends(self, *, suite: str = "vision_random", metric: str = "duration_ms", limit: int = 20) -> dict[str, Any]:
        runs = []
        for path in sorted(self.benchmarks_dir.glob("bench_*.json")):
            run = self._load(path)
            if not isinstance(run, dict):
                continue
            run_id = str(run.get("run_id") or path.stem)
            created = str(run.get("finished_at") or run.get("started_at") or "")
            suites = run.get("suites") or []
            val = None
            for s in suites:
                if isinstance(s, dict) and str(s.get("suite")) == str(suite):
                    if metric == "duration_ms":
                        val = _num(s.get("duration_ms"))
                    else:
                        val = _num(_dot(s, metric))
                    break
            if val is None:
                continue
            runs.append({"run_id": run_id, "ts": created, "value": val, "file": str(path)})
        runs = runs[-max(1, int(limit)) :]
        values = [float(r["value"]) for r in runs]
        return {
            "ok": True,
            "suite": suite,
            "metric": metric,
            "count": len(runs),
            "points": runs,
            "summary": {
                "latest": (values[-1] if values else None),
                "min": (min(values) if values else None),
                "max": (max(values) if values else None),
                "delta_from_first": (round(values[-1] - values[0], 3) if len(values) >= 2 else None),
            },
        }

    def _resolve_run(self, ref: str) -> Path:
        s = str(ref or "").strip()
        if not s:
            raise ValueError("benchmark run reference is required")
        p = Path(s)
        if p.exists():
            return p.resolve()
        candidate = self.benchmarks_dir / (s if s.endswith(".json") else f"{s}.json")
        if candidate.exists():
            return candidate.resolve()
        if not s.startswith("bench_"):
            candidate = self.benchmarks_dir / f"bench_{s}.json"
            if candidate.exists():
                return candidate.resolve()
        raise FileNotFoundError(f"benchmark run not found: {ref}")

    @staticmethod
    def _load(path: Path) -> Any:
        try:
            return json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            return None

    @staticmethod
    def _run_summary(run: dict[str, Any], path: Path) -> dict[str, Any]:
        suites = run.get("suites") or []
        return {
            "run_id": run.get("run_id") or path.stem,
            "profile": run.get("profile"),
            "started_at": run.get("started_at"),
            "finished_at": run.get("finished_at"),
            "total_ms": run.get("total_ms"),
            "suite_count": len(suites) if isinstance(suites, list) else None,
            "ok": run.get("ok"),
            "file": str(path),
        }


def _dot(data: dict[str, Any] | None, path: str) -> Any:
    cur: Any = data
    for part in str(path).split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _num(v: Any) -> float | None:
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v))
    except Exception:
        return None

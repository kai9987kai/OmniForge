from __future__ import annotations

import json
import threading
import uuid
from collections import Counter, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .history_store import HistoryStore
    from .service import OmniForgeService


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


class OpsGraphCenter:
    def __init__(self, service: "OmniForgeService", root_dir: Path, history: "HistoryStore"):
        self.service = service
        self.root_dir = Path(root_dir).resolve()
        self.history = history
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self.snapshots_dir = self.root_dir / "snapshots"
        self.snapshots_dir.mkdir(parents=True, exist_ok=True)
        self.watch_dir = self.root_dir / "watch"
        self.watch_dir.mkdir(parents=True, exist_ok=True)
        self.index_file = self.root_dir / "index.jsonl"
        self.watch_index_file = self.root_dir / "watch_index.jsonl"
        self._lock = threading.RLock()

    def status(self) -> dict[str, Any]:
        latest = self.latest(optional=True)
        snap = (latest.get("snapshot") or {}) if isinstance(latest, dict) else {}
        summary = dict(snap.get("summary") or {})
        return {
            "ok": True,
            "root_dir": str(self.root_dir),
            "snapshots_dir": str(self.snapshots_dir),
            "index_file": str(self.index_file),
            "watch_dir": str(self.watch_dir),
            "watch_index_file": str(self.watch_index_file),
            "snapshot_count": self.list_snapshots(limit=100000).get("count"),
            "latest_snapshot_id": snap.get("snapshot_id"),
            "latest_profile": snap.get("profile"),
            "latest_node_count": summary.get("node_count"),
            "latest_edge_count": summary.get("edge_count"),
            "latest_component_count": summary.get("connected_component_count"),
            "watch_count": self.list_watch(limit=100000).get("count"),
            "latest_watch_id": (self.list_watch(limit=1).get("items") or [{}])[0].get("watch_id"),
        }

    def profiles(self) -> dict[str, Any]:
        return {
            "ok": True,
            "profiles": {
                "compact": {
                    "description": "Small graph snapshot for quick diagnostics.",
                    "options": {"limit": 25, "timeline_limit": 40, "include_timeline_events": True},
                },
                "runtime": {
                    "description": "Balanced operational graph (alerts/incidents/jobs/automation).",
                    "options": {"limit": 75, "timeline_limit": 150, "include_timeline_events": True},
                },
                "incident_focus": {
                    "description": "Richer graph for RCA with deeper incident/timeline coverage.",
                    "options": {"limit": 150, "timeline_limit": 300, "include_timeline_events": True},
                },
            },
        }

    def list_snapshots(self, *, limit: int = 50, profile: str | None = None) -> dict[str, Any]:
        rows: list[dict[str, Any]] = []
        if self.index_file.exists():
            with self._lock:
                lines = self.index_file.read_text(encoding="utf-8", errors="replace").splitlines()
            for line in reversed(lines):
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                if profile and str(row.get("profile")) != str(profile):
                    continue
                rows.append(row)
                if len(rows) >= max(1, int(limit)):
                    break
        return {"ok": True, "count": len(rows), "items": rows}

    def list_watch(self, *, limit: int = 50, profile: str | None = None) -> dict[str, Any]:
        rows: list[dict[str, Any]] = []
        if self.watch_index_file.exists():
            with self._lock:
                lines = self.watch_index_file.read_text(encoding="utf-8", errors="replace").splitlines()
            for line in reversed(lines):
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                if profile and str(row.get("profile")) != str(profile):
                    continue
                rows.append(row)
                if len(rows) >= max(1, int(limit)):
                    break
        return {"ok": True, "count": len(rows), "items": rows}

    def get_snapshot(self, snapshot_id: str) -> dict[str, Any]:
        sid = str(snapshot_id or "").strip()
        if not sid:
            raise ValueError("snapshot_id is required")
        path = self.snapshots_dir / f"{sid}.json"
        if not path.exists():
            raise FileNotFoundError(f"opsgraph snapshot not found: {sid}")
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        return {"ok": True, "snapshot": data, "file": str(path)}

    def get_watch(self, watch_id: str) -> dict[str, Any]:
        wid = str(watch_id or "").strip()
        if not wid:
            raise ValueError("watch_id is required")
        path = self.watch_dir / f"{wid}.json"
        if not path.exists():
            raise FileNotFoundError(f"opsgraph watch not found: {wid}")
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        return {"ok": True, "watch": data, "file": str(path)}

    def latest(self, *, optional: bool = False) -> dict[str, Any]:
        items = (self.list_snapshots(limit=1) or {}).get("items") or []
        if not items:
            if optional:
                return {"ok": True, "snapshot": None}
            raise FileNotFoundError("no opsgraph snapshots found")
        sid = str(items[0].get("snapshot_id") or "")
        if not sid:
            if optional:
                return {"ok": True, "snapshot": None}
            raise FileNotFoundError("latest opsgraph snapshot entry is missing snapshot_id")
        return self.get_snapshot(sid)

    def snapshot(
        self,
        *,
        profile: str = "runtime",
        options: dict[str, Any] | None = None,
        persist: bool = True,
    ) -> dict[str, Any]:
        profile = str(profile or "runtime").strip() or "runtime"
        profiles = self.profiles()["profiles"]
        if profile not in profiles:
            raise ValueError(f"unknown opsgraph profile: {profile}")
        merged_options = dict(profiles[profile].get("options") or {})
        if isinstance(options, dict):
            merged_options.update(options)

        limit = max(1, int(merged_options.get("limit", 75)))
        timeline_limit = max(0, int(merged_options.get("timeline_limit", 150)))
        include_timeline = bool(merged_options.get("include_timeline_events", True))

        builder = _GraphBuilder()
        collected = self._collect(limit=limit, timeline_limit=timeline_limit)
        self._populate_graph(builder, collected, include_timeline_events=include_timeline)
        graph = builder.build()

        now = _now_utc()
        sid = f"graph_{now.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
        summary = self._summarize_graph(graph)
        snapshot = {
            "ok": True,
            "snapshot_id": sid,
            "profile": profile,
            "created_at": now.isoformat(),
            "options": _jsonable(merged_options),
            "summary": summary,
            "graph": graph,
            "sources": {
                "alerts": {"rules": (collected["alert_rules"] or {}).get("count"), "events": (collected["alert_events"] or {}).get("count")},
                "incidents": {
                    "items": (collected["incidents"] or {}).get("count"),
                    "timeline": (collected["incident_timeline"] or {}).get("count"),
                },
                "jobs": (collected["jobs"] or {}).get("count"),
                "runbooks": (collected["runbook_runs"] or {}).get("count"),
                "reports": (collected["reports"] or {}).get("count"),
                "health": (collected["health"] or {}).get("count"),
            },
        }

        out = {"ok": True, "snapshot": snapshot}
        if persist:
            path = self._persist_snapshot(snapshot)
            out["file"] = str(path)
            self.history.record(
                "opsgraph.snapshot",
                {"snapshot_id": sid, "profile": profile, "summary": summary},
                source="opsgraph",
                tags=["opsgraph", "snapshot"],
                summary={"snapshot_id": sid, "profile": profile, "nodes": summary.get("node_count"), "edges": summary.get("edge_count")},
            )
        return out

    def trace(
        self,
        node_id: str,
        *,
        snapshot_id: str | None = None,
        depth: int = 2,
        direction: str = "both",
        max_nodes: int = 200,
    ) -> dict[str, Any]:
        nid = str(node_id or "").strip()
        if not nid:
            raise ValueError("node_id is required")
        snap = self._resolve_snapshot_for_query(snapshot_id)
        graph = (snap.get("graph") or {}) if isinstance(snap, dict) else {}
        nodes = [n for n in (graph.get("nodes") or []) if isinstance(n, dict)]
        edges = [e for e in (graph.get("edges") or []) if isinstance(e, dict)]
        node_map = {str(n.get("id")): n for n in nodes if n.get("id")}
        if nid not in node_map:
            raise FileNotFoundError(f"node not found in graph snapshot: {nid}")

        mode = str(direction or "both").strip().lower()
        if mode not in {"both", "out", "in"}:
            raise ValueError("direction must be one of: both, out, in")
        depth = max(0, int(depth))
        max_nodes = max(1, int(max_nodes))

        out_adj: dict[str, list[dict[str, Any]]] = {}
        in_adj: dict[str, list[dict[str, Any]]] = {}
        for edge in edges:
            src = str(edge.get("src") or "")
            dst = str(edge.get("dst") or "")
            if not src or not dst:
                continue
            out_adj.setdefault(src, []).append(edge)
            in_adj.setdefault(dst, []).append(edge)

        q: deque[tuple[str, int]] = deque([(nid, 0)])
        visited = {nid}
        dist = {nid: 0}
        while q and len(visited) < max_nodes:
            cur, d = q.popleft()
            if d >= depth:
                continue
            candidates: list[tuple[str, dict[str, Any]]] = []
            if mode in {"both", "out"}:
                candidates.extend((str(e.get("dst") or ""), e) for e in out_adj.get(cur, []))
            if mode in {"both", "in"}:
                candidates.extend((str(e.get("src") or ""), e) for e in in_adj.get(cur, []))
            for nxt, _edge in candidates:
                if not nxt or nxt in visited or nxt not in node_map:
                    continue
                if len(visited) >= max_nodes:
                    break
                visited.add(nxt)
                dist[nxt] = d + 1
                q.append((nxt, d + 1))

        sub_nodes = []
        for vid in visited:
            n = dict(node_map[vid])
            n["distance"] = dist.get(vid)
            sub_nodes.append(n)
        sub_nodes.sort(key=lambda x: (int(x.get("distance") or 0), str(x.get("kind") or ""), str(x.get("id") or "")))
        sub_edges = [e for e in edges if str(e.get("src") or "") in visited and str(e.get("dst") or "") in visited]

        return {
            "ok": True,
            "snapshot_id": snap.get("snapshot_id"),
            "root_node_id": nid,
            "depth": depth,
            "direction": mode,
            "max_nodes": max_nodes,
            "subgraph": {"nodes": sub_nodes, "edges": sub_edges, "summary": self._summarize_graph({"nodes": sub_nodes, "edges": sub_edges})},
        }

    def compare(
        self,
        baseline: str,
        candidate: str,
        *,
        max_samples: int = 25,
    ) -> dict[str, Any]:
        base = self.get_snapshot(str(baseline or "").strip())["snapshot"]
        cand = self.get_snapshot(str(candidate or "").strip())["snapshot"]
        base_graph = (base.get("graph") or {}) if isinstance(base, dict) else {}
        cand_graph = (cand.get("graph") or {}) if isinstance(cand, dict) else {}
        base_nodes = {str(n.get("id")): n for n in (base_graph.get("nodes") or []) if isinstance(n, dict) and n.get("id")}
        cand_nodes = {str(n.get("id")): n for n in (cand_graph.get("nodes") or []) if isinstance(n, dict) and n.get("id")}
        base_edges = {_edge_key(e): e for e in (base_graph.get("edges") or []) if isinstance(e, dict) and _edge_key(e) is not None}
        cand_edges = {_edge_key(e): e for e in (cand_graph.get("edges") or []) if isinstance(e, dict) and _edge_key(e) is not None}

        added_node_ids = sorted(set(cand_nodes) - set(base_nodes))
        removed_node_ids = sorted(set(base_nodes) - set(cand_nodes))
        common_node_ids = sorted(set(base_nodes) & set(cand_nodes))
        added_edge_keys = sorted(set(cand_edges) - set(base_edges))
        removed_edge_keys = sorted(set(base_edges) - set(cand_edges))

        changed_nodes = []
        interesting_fields = (
            "label",
            "kind",
            "status",
            "severity",
            "acknowledged",
            "enabled",
            "score",
            "level",
            "risk_score",
            "profile",
            "job_kind",
            "rule_kind",
            "last_tick_at",
        )
        for nid in common_node_ids:
            b = base_nodes[nid]
            c = cand_nodes[nid]
            changes = {}
            for key in interesting_fields:
                if b.get(key) != c.get(key):
                    changes[key] = {"baseline": b.get(key), "candidate": c.get(key)}
            if changes:
                changed_nodes.append(
                    {
                        "id": nid,
                        "kind": c.get("kind") or b.get("kind"),
                        "label": c.get("label") or b.get("label"),
                        "changes": changes,
                    }
                )
        total_changed_nodes = len(changed_nodes)
        changed_nodes = changed_nodes[: max(1, int(max_samples))]

        added_nodes = [cand_nodes[nid] for nid in added_node_ids[: max(1, int(max_samples))]]
        removed_nodes = [base_nodes[nid] for nid in removed_node_ids[: max(1, int(max_samples))]]
        added_edges = [cand_edges[k] for k in added_edge_keys[: max(1, int(max_samples))]]
        removed_edges = [base_edges[k] for k in removed_edge_keys[: max(1, int(max_samples))]]

        added_kind_counts = Counter(str(cand_nodes[nid].get("kind") or "unknown") for nid in added_node_ids)
        removed_kind_counts = Counter(str(base_nodes[nid].get("kind") or "unknown") for nid in removed_node_ids)

        risk_signals = {
            "new_critical_alerts": sum(
                1
                for nid in added_node_ids
                if str(cand_nodes[nid].get("kind")) == "alert" and str(cand_nodes[nid].get("severity")) == "critical"
            ),
            "new_open_critical_incidents": sum(
                1
                for nid in added_node_ids
                if str(cand_nodes[nid].get("kind")) == "incident"
                and str(cand_nodes[nid].get("severity")) == "critical"
                and str(cand_nodes[nid].get("status")) != "closed"
            ),
            "new_failed_jobs": sum(
                1
                for nid in added_node_ids
                if str(cand_nodes[nid].get("kind")) == "job" and str(cand_nodes[nid].get("status")) == "failed"
            ),
            "new_failed_runbooks": sum(
                1
                for nid in added_node_ids
                if str(cand_nodes[nid].get("kind")) == "runbook_run" and str(cand_nodes[nid].get("status")) == "failed"
            ),
            "new_failed_remediation_actions": sum(
                1
                for nid in added_node_ids
                if str(cand_nodes[nid].get("kind")) == "remediation_action" and not bool(cand_nodes[nid].get("ok", True))
            ),
        }

        base_summary = dict(base.get("summary") or {})
        cand_summary = dict(cand.get("summary") or {})
        compare = {
            "ok": True,
            "baseline": {
                "snapshot_id": base.get("snapshot_id"),
                "profile": base.get("profile"),
                "created_at": base.get("created_at"),
                "summary": base_summary,
            },
            "candidate": {
                "snapshot_id": cand.get("snapshot_id"),
                "profile": cand.get("profile"),
                "created_at": cand.get("created_at"),
                "summary": cand_summary,
            },
            "delta": {
                "node_count": _num_delta(base_summary.get("node_count"), cand_summary.get("node_count")),
                "edge_count": _num_delta(base_summary.get("edge_count"), cand_summary.get("edge_count")),
                "connected_component_count": _num_delta(base_summary.get("connected_component_count"), cand_summary.get("connected_component_count")),
                "largest_component_size": _num_delta(base_summary.get("largest_component_size"), cand_summary.get("largest_component_size")),
                "added_nodes": len(added_node_ids),
                "removed_nodes": len(removed_node_ids),
                "changed_nodes": total_changed_nodes,
                "added_edges": len(added_edge_keys),
                "removed_edges": len(removed_edge_keys),
            },
            "node_kind_changes": {
                "added": dict(added_kind_counts),
                "removed": dict(removed_kind_counts),
            },
            "risk_signals": risk_signals,
            "samples": {
                "added_nodes": [_brief_node(n) for n in added_nodes],
                "removed_nodes": [_brief_node(n) for n in removed_nodes],
                "changed_nodes": changed_nodes,
                "added_edges": [_brief_edge(e) for e in added_edges],
                "removed_edges": [_brief_edge(e) for e in removed_edges],
            },
        }
        self.history.record(
            "opsgraph.compared",
            {
                "baseline": base.get("snapshot_id"),
                "candidate": cand.get("snapshot_id"),
                "delta": compare.get("delta"),
                "risk_signals": risk_signals,
            },
            source="opsgraph",
            tags=["opsgraph", "compare"],
            summary={
                "baseline": base.get("snapshot_id"),
                "candidate": cand.get("snapshot_id"),
                "added_nodes": len(added_node_ids),
                "removed_nodes": len(removed_node_ids),
            },
        )
        return compare

    def path_between(
        self,
        src_node_id: str,
        dst_node_id: str,
        *,
        snapshot_id: str | None = None,
        direction: str = "both",
        max_depth: int = 8,
    ) -> dict[str, Any]:
        src = str(src_node_id or "").strip()
        dst = str(dst_node_id or "").strip()
        if not src or not dst:
            raise ValueError("src_node_id and dst_node_id are required")
        snap = self._resolve_snapshot_for_query(snapshot_id)
        graph = (snap.get("graph") or {}) if isinstance(snap, dict) else {}
        nodes = [n for n in (graph.get("nodes") or []) if isinstance(n, dict)]
        edges = [e for e in (graph.get("edges") or []) if isinstance(e, dict)]
        node_map, out_adj, in_adj = self._graph_index(nodes, edges)
        if src not in node_map:
            raise FileNotFoundError(f"source node not found in graph snapshot: {src}")
        if dst not in node_map:
            raise FileNotFoundError(f"destination node not found in graph snapshot: {dst}")

        mode = str(direction or "both").strip().lower()
        if mode not in {"both", "out", "in"}:
            raise ValueError("direction must be one of: both, out, in")
        max_depth = max(1, int(max_depth))
        if src == dst:
            return {
                "ok": True,
                "snapshot_id": snap.get("snapshot_id"),
                "source": _brief_node(node_map[src]),
                "target": _brief_node(node_map[dst]),
                "found": True,
                "distance": 0,
                "path": {"nodes": [_brief_node(node_map[src])], "edges": []},
            }

        q: deque[str] = deque([src])
        prev_node: dict[str, str | None] = {src: None}
        prev_edge: dict[str, dict[str, Any]] = {}
        depth_map: dict[str, int] = {src: 0}
        found = False
        while q and not found:
            cur = q.popleft()
            d = depth_map.get(cur, 0)
            if d >= max_depth:
                continue
            for nxt, edge in self._iter_neighbors(cur, out_adj, in_adj, direction=mode):
                if nxt not in node_map or nxt in prev_node:
                    continue
                prev_node[nxt] = cur
                prev_edge[nxt] = edge
                depth_map[nxt] = d + 1
                if nxt == dst:
                    found = True
                    break
                q.append(nxt)

        if not found:
            return {
                "ok": True,
                "snapshot_id": snap.get("snapshot_id"),
                "source": _brief_node(node_map[src]),
                "target": _brief_node(node_map[dst]),
                "found": False,
                "direction": mode,
                "max_depth": max_depth,
            }

        path_nodes_ids: list[str] = []
        path_edges: list[dict[str, Any]] = []
        cur = dst
        while cur is not None:
            path_nodes_ids.append(cur)
            if cur in prev_edge:
                path_edges.append(prev_edge[cur])
            cur = prev_node.get(cur)
        path_nodes_ids.reverse()
        path_edges.reverse()

        chain = []
        for idx, node_id in enumerate(path_nodes_ids):
            chain.append(f"{node_id}")
            if idx < len(path_edges):
                edge = path_edges[idx]
                nxt_node = path_nodes_ids[idx + 1]
                ek = str(edge.get("kind") or "rel")
                es = str(edge.get("src") or "")
                ed = str(edge.get("dst") or "")
                if es == node_id and ed == nxt_node:
                    chain.append(f"-[{ek}]->")
                elif es == nxt_node and ed == node_id:
                    chain.append(f"<-[{ek}]-")
                else:
                    chain.append(f"-[{ek}]-?")

        result = {
            "ok": True,
            "snapshot_id": snap.get("snapshot_id"),
            "source": _brief_node(node_map[src]),
            "target": _brief_node(node_map[dst]),
            "found": True,
            "direction": mode,
            "max_depth": max_depth,
            "distance": len(path_nodes_ids) - 1,
            "path": {
                "nodes": [_brief_node(node_map[n]) for n in path_nodes_ids],
                "edges": [_brief_edge(e) for e in path_edges],
                "chain": " ".join(chain),
            },
        }
        self.history.record(
            "opsgraph.path",
            {"snapshot_id": snap.get("snapshot_id"), "source": src, "target": dst, "distance": result.get("distance")},
            source="opsgraph",
            tags=["opsgraph", "path"],
            summary={"source": src, "target": dst, "distance": result.get("distance")},
        )
        return result

    def blast_radius(
        self,
        node_id: str,
        *,
        snapshot_id: str | None = None,
        depth: int = 2,
        direction: str = "out",
        max_nodes: int = 300,
    ) -> dict[str, Any]:
        trace = self.trace(
            node_id,
            snapshot_id=snapshot_id,
            depth=depth,
            direction=direction,
            max_nodes=max_nodes,
        )
        sub = (trace.get("subgraph") or {})
        nodes = [n for n in (sub.get("nodes") or []) if isinstance(n, dict)]
        root = str(trace.get("root_node_id") or "")
        impacted = [n for n in nodes if str(n.get("id") or "") != root]
        kind_counts = Counter(str(n.get("kind") or "unknown") for n in impacted)
        severity_counts = Counter(str(n.get("severity") or "none") for n in impacted if n.get("severity") is not None)
        status_counts = Counter(str(n.get("status") or "none") for n in impacted if n.get("status") is not None)
        risky = [
            n
            for n in impacted
            if (str(n.get("severity")) == "critical")
            or (str(n.get("status")) in {"failed", "open", "investigating"})
            or (str(n.get("kind")) == "job" and str(n.get("status")) == "failed")
        ]
        risky.sort(key=lambda n: (int(n.get("distance") or 0), str(n.get("kind") or ""), str(n.get("id") or "")))
        summary = {
            "impacted_node_count": len(impacted),
            "kind_counts": dict(kind_counts),
            "severity_counts": dict(severity_counts),
            "status_counts": dict(status_counts),
            "max_distance": max((int(n.get("distance") or 0) for n in impacted), default=0),
            "risky_node_count": len(risky),
        }
        result = {
            "ok": True,
            "snapshot_id": trace.get("snapshot_id"),
            "root_node_id": root,
            "direction": trace.get("direction"),
            "depth": trace.get("depth"),
            "max_nodes": trace.get("max_nodes"),
            "summary": summary,
            "top_risks": [_brief_node(n) for n in risky[:25]],
            "subgraph": sub,
        }
        self.history.record(
            "opsgraph.blast_radius",
            {"snapshot_id": trace.get("snapshot_id"), "node_id": root, "summary": summary},
            source="opsgraph",
            tags=["opsgraph", "blast_radius"],
            summary={"node_id": root, "impacted": len(impacted), "risky": len(risky)},
        )
        return result

    def hotspots(
        self,
        *,
        snapshot_id: str | None = None,
        limit: int = 20,
        include_zero: bool = False,
    ) -> dict[str, Any]:
        snap = self._resolve_snapshot_for_query(snapshot_id)
        result = self._hotspots_from_snapshot(snap, limit=limit, include_zero=include_zero)
        self.history.record(
            "opsgraph.hotspots",
            {
                "snapshot_id": snap.get("snapshot_id"),
                "limit": max(1, int(limit)),
                "include_zero": bool(include_zero),
                "summary": result.get("summary"),
            },
            source="opsgraph",
            tags=["opsgraph", "hotspots"],
            summary={
                "snapshot_id": snap.get("snapshot_id"),
                "returned": len(result.get("hotspots") or []),
                "max_risk_score": (result.get("summary") or {}).get("max_risk_score"),
            },
        )
        return result

    def components(
        self,
        *,
        snapshot_id: str | None = None,
        limit: int = 20,
        order_by: str = "risk",
    ) -> dict[str, Any]:
        snap = self._resolve_snapshot_for_query(snapshot_id)
        result = self._components_from_snapshot(snap, limit=limit, order_by=order_by)
        self.history.record(
            "opsgraph.components",
            {
                "snapshot_id": snap.get("snapshot_id"),
                "limit": max(1, int(limit)),
                "order_by": str(order_by or "risk"),
                "summary": result.get("summary"),
            },
            source="opsgraph",
            tags=["opsgraph", "components"],
            summary={
                "snapshot_id": snap.get("snapshot_id"),
                "returned": len(result.get("components") or []),
                "largest_component_size": (result.get("summary") or {}).get("largest_component_size"),
            },
        )
        return result

    def watch(
        self,
        *,
        profile: str = "runtime",
        options: dict[str, Any] | None = None,
        compare_with: str | None = "latest",
        max_hotspots: int = 10,
        max_components: int = 6,
        auto_incident: bool = False,
        incident_threshold: float = 80.0,
        incident_cooldown_sec: float = 3600.0,
        persist: bool = True,
    ) -> dict[str, Any]:
        profile = str(profile or "runtime").strip() or "runtime"
        mode_raw = str(compare_with or "latest").strip()
        mode = mode_raw.lower()
        baseline_snapshot_id: str | None = None
        if mode in {"", "latest"}:
            rows = (self.list_snapshots(limit=1, profile=profile) or {}).get("items") or []
            if rows:
                baseline_snapshot_id = str(rows[0].get("snapshot_id") or "") or None
        elif mode in {"none", "off", "skip"}:
            baseline_snapshot_id = None
        else:
            baseline_snapshot_id = mode_raw
            # Validate early so callers get a clear error.
            self.get_snapshot(baseline_snapshot_id)

        snapshot_out = self.snapshot(profile=profile, options=options, persist=persist)
        snap = snapshot_out.get("snapshot") or {}
        candidate_snapshot_id = str(snap.get("snapshot_id") or "")
        max_hotspots = max(1, int(max_hotspots))
        max_components = max(1, int(max_components))

        compare_result: dict[str, Any] | None = None
        compare_note: str | None = None
        if baseline_snapshot_id:
            if persist and candidate_snapshot_id:
                compare_result = self.compare(
                    baseline_snapshot_id,
                    candidate_snapshot_id,
                    max_samples=max(10, max(max_hotspots, max_components)),
                )
            else:
                compare_note = "compare skipped because persist=False (candidate snapshot is not persisted)"

        hotspots = self._hotspots_from_snapshot(snap, limit=max_hotspots, include_zero=False)
        components = self._components_from_snapshot(snap, limit=max_components, order_by="risk")
        risk_signals = dict((compare_result or {}).get("risk_signals") or {})
        max_hotspot_score = float((hotspots.get("summary") or {}).get("max_risk_score") or 0.0)
        avg_component_score = float((components.get("summary") or {}).get("avg_risk_score") or 0.0)
        signal_bonus = (
            float(risk_signals.get("new_critical_alerts") or 0) * 10.0
            + float(risk_signals.get("new_open_critical_incidents") or 0) * 14.0
            + float(risk_signals.get("new_failed_jobs") or 0) * 5.0
            + float(risk_signals.get("new_failed_runbooks") or 0) * 6.0
            + float(risk_signals.get("new_failed_remediation_actions") or 0) * 4.0
        )
        score = round(min(100.0, max(0.0, max_hotspot_score * 0.62 + avg_component_score * 0.38 + signal_bonus)), 3)
        level = self._risk_level(score)
        incident = self._maybe_open_watch_incident(
            enabled=bool(auto_incident),
            profile=profile,
            score=score,
            level=level,
            threshold=float(incident_threshold),
            cooldown_sec=float(incident_cooldown_sec),
            risk_signals=risk_signals,
            hotspots=(hotspots.get("hotspots") or []),
            snapshot_id=str(snap.get("snapshot_id") or ""),
        )

        now = _now_utc()
        watch_id = f"watch_{now.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
        watch = {
            "ok": True,
            "watch_id": watch_id,
            "created_at": now.isoformat(),
            "profile": profile,
            "persisted_snapshot": bool(persist),
            "snapshot": {
                "snapshot_id": snap.get("snapshot_id"),
                "created_at": snap.get("created_at"),
                "summary": snap.get("summary") or {},
            },
            "baseline_snapshot_id": baseline_snapshot_id,
            "compare_mode": mode or "latest",
            "compare_note": compare_note,
            "delta": (compare_result or {}).get("delta"),
            "risk_signals": risk_signals,
            "score": score,
            "level": level,
            "incident": incident,
            "summary": {
                "watch_score": score,
                "watch_level": level,
                "hotspot_count": len(hotspots.get("hotspots") or []),
                "component_count": len(components.get("components") or []),
                "snapshot_node_count": ((snap.get("summary") or {}).get("node_count")),
                "snapshot_edge_count": ((snap.get("summary") or {}).get("edge_count")),
                "compare_available": compare_result is not None,
                "auto_incident_enabled": bool(auto_incident),
                "auto_incident_created": bool(incident.get("created")),
                "incident_id": incident.get("incident_id"),
            },
            "hotspots": hotspots.get("hotspots") or [],
            "components": components.get("components") or [],
        }

        out = {"ok": True, "watch": watch}
        if persist:
            watch_file = self._persist_watch(watch)
            out["file"] = str(watch_file)

        self.history.record(
            "opsgraph.watch",
            {
                "watch_id": watch_id,
                "profile": profile,
                "snapshot_id": snap.get("snapshot_id"),
                "baseline_snapshot_id": baseline_snapshot_id,
                "score": score,
                "level": level,
                "incident": incident,
                "summary": watch.get("summary"),
            },
            source="opsgraph",
            tags=["opsgraph", "watch", str(level)],
            summary={
                "watch_id": watch_id,
                "score": score,
                "level": level,
                "snapshot_id": snap.get("snapshot_id"),
                "incident_id": incident.get("incident_id"),
            },
        )
        return out

    def watch_trends(
        self,
        *,
        limit: int = 50,
        profile: str | None = None,
    ) -> dict[str, Any]:
        rows = (self.list_watch(limit=max(2, int(limit)), profile=profile) or {}).get("items") or []
        chrono = list(reversed(rows))
        points = []
        for row in chrono:
            try:
                score = float(row.get("score") or 0.0)
            except Exception:
                score = 0.0
            points.append(
                {
                    "watch_id": row.get("watch_id"),
                    "created_at": row.get("created_at"),
                    "profile": row.get("profile"),
                    "score": round(score, 3),
                    "level": self._risk_level(score),
                    "summary": row.get("summary") or {},
                }
            )

        scores = [float(p.get("score") or 0.0) for p in points]
        level_counts = Counter(str(p.get("level") or "low") for p in points)
        score_delta = round(scores[-1] - scores[0], 3) if len(scores) >= 2 else 0.0
        trend = "flat"
        if score_delta > 5.0:
            trend = "rising"
        elif score_delta < -5.0:
            trend = "falling"

        return {
            "ok": True,
            "profile": profile,
            "count": len(points),
            "trend": trend,
            "score_delta": score_delta,
            "summary": {
                "avg_score": round(sum(scores) / len(scores), 3) if scores else 0.0,
                "max_score": round(max(scores), 3) if scores else 0.0,
                "min_score": round(min(scores), 3) if scores else 0.0,
                "level_counts": dict(level_counts),
            },
            "points": points,
        }

    def watch_anomalies(
        self,
        *,
        watch_id: str | None = None,
        profile: str | None = None,
        baseline_limit: int = 20,
        z_threshold: float = 1.8,
        delta_threshold: float = 10.0,
    ) -> dict[str, Any]:
        if watch_id:
            candidate = (self.get_watch(watch_id) or {}).get("watch") or {}
            profile = str(profile or candidate.get("profile") or "").strip() or None
        else:
            latest_rows = (self.list_watch(limit=1, profile=profile) or {}).get("items") or []
            if not latest_rows:
                raise FileNotFoundError("no watch runs found")
            candidate = (self.get_watch(str(latest_rows[0].get("watch_id") or "")) or {}).get("watch") or {}
            profile = str(profile or candidate.get("profile") or "").strip() or None

        candidate_watch_id = str(candidate.get("watch_id") or "")
        candidate_score = float(candidate.get("score") or 0.0)
        candidate_level = str(candidate.get("level") or self._risk_level(candidate_score))
        candidate_summary = candidate.get("summary") if isinstance(candidate.get("summary"), dict) else {}
        candidate_hotspots = int(candidate_summary.get("hotspot_count") or len(candidate.get("hotspots") or []))
        candidate_components = int(candidate_summary.get("component_count") or len(candidate.get("components") or []))
        candidate_risk_signals = dict(candidate.get("risk_signals") or {})

        rows = (self.list_watch(limit=max(2, int(baseline_limit) + 5), profile=profile) or {}).get("items") or []
        baseline_rows = [r for r in rows if str(r.get("watch_id") or "") != candidate_watch_id][: max(1, int(baseline_limit))]
        baseline_scores = [float(r.get("score") or 0.0) for r in baseline_rows]
        baseline_hotspots = [float((r.get("summary") or {}).get("hotspot_count") or 0.0) for r in baseline_rows]
        baseline_components = [float((r.get("summary") or {}).get("component_count") or 0.0) for r in baseline_rows]

        mean_score = (sum(baseline_scores) / len(baseline_scores)) if baseline_scores else 0.0
        std_score = _stddev(baseline_scores)
        abs_delta = candidate_score - mean_score
        z_score = (abs_delta / std_score) if std_score > 0 else 0.0
        pct_delta = ((abs_delta / mean_score) * 100.0) if mean_score > 0 else 0.0

        mean_hotspots = (sum(baseline_hotspots) / len(baseline_hotspots)) if baseline_hotspots else 0.0
        mean_components = (sum(baseline_components) / len(baseline_components)) if baseline_components else 0.0
        anomalies: list[dict[str, Any]] = []
        if baseline_scores and abs(z_score) >= float(z_threshold):
            anomalies.append(
                {
                    "kind": "score_zscore",
                    "severity": ("high" if z_score > 0 else "medium"),
                    "metric": "watch_score",
                    "value": round(candidate_score, 3),
                    "baseline_mean": round(mean_score, 3),
                    "z_score": round(z_score, 3),
                    "delta": round(abs_delta, 3),
                    "note": "Watch score diverges from rolling baseline.",
                }
            )
        if baseline_scores and abs(abs_delta) >= float(delta_threshold):
            anomalies.append(
                {
                    "kind": "score_delta",
                    "severity": ("high" if abs_delta > 0 else "low"),
                    "metric": "watch_score",
                    "value": round(candidate_score, 3),
                    "baseline_mean": round(mean_score, 3),
                    "delta": round(abs_delta, 3),
                    "pct_delta": round(pct_delta, 3),
                    "note": "Watch score absolute delta crossed threshold.",
                }
            )
        if mean_hotspots > 0 and candidate_hotspots >= int(round(mean_hotspots + 2.0)):
            anomalies.append(
                {
                    "kind": "hotspot_density",
                    "severity": "medium",
                    "metric": "hotspot_count",
                    "value": candidate_hotspots,
                    "baseline_mean": round(mean_hotspots, 3),
                    "note": "Current run has materially more risky hotspots.",
                }
            )
        if mean_components > 0 and candidate_components >= int(round(mean_components + 2.0)):
            anomalies.append(
                {
                    "kind": "component_spread",
                    "severity": "medium",
                    "metric": "component_count",
                    "value": candidate_components,
                    "baseline_mean": round(mean_components, 3),
                    "note": "Risky structure spread across more components.",
                }
            )
        if int(candidate_risk_signals.get("new_open_critical_incidents") or 0) > 0 or int(candidate_risk_signals.get("new_critical_alerts") or 0) > 0:
            anomalies.append(
                {
                    "kind": "critical_signal_burst",
                    "severity": "critical",
                    "metric": "risk_signals",
                    "value": candidate_risk_signals,
                    "note": "New critical operational signals detected in compare phase.",
                }
            )
        if candidate_level in {"high", "critical"} and candidate_score >= 75.0:
            anomalies.append(
                {
                    "kind": "sustained_high_risk",
                    "severity": "high",
                    "metric": "watch_level",
                    "value": candidate_level,
                    "watch_score": round(candidate_score, 3),
                    "note": "Current watch risk is high enough to justify immediate triage.",
                }
            )

        top_anomalies = sorted(
            anomalies,
            key=lambda a: (
                {"critical": 4, "high": 3, "medium": 2, "low": 1}.get(str(a.get("severity") or "low"), 1),
                float(a.get("delta") or 0.0),
            ),
            reverse=True,
        )
        summary = {
            "watch_id": candidate_watch_id,
            "profile": profile,
            "baseline_count": len(baseline_rows),
            "watch_score": round(candidate_score, 3),
            "watch_level": candidate_level,
            "baseline_mean_score": round(mean_score, 3),
            "baseline_std_score": round(std_score, 3),
            "delta": round(abs_delta, 3),
            "z_score": round(z_score, 3),
            "anomaly_count": len(top_anomalies),
        }
        recommendations = [
            {
                "priority": 100,
                "kind": "opsgraph.hotspots",
                "params": {"snapshot_id": (candidate.get("snapshot") or {}).get("snapshot_id"), "limit": 15},
                "why": "Inspect highest-risk entities behind the anomaly.",
            },
            {
                "priority": 92,
                "kind": "opsgraph.watch.forecast",
                "params": {"profile": profile, "limit": 30, "horizon": 6},
                "why": "Estimate whether risk trajectory is rising or stabilizing.",
            },
            {
                "priority": 88,
                "kind": "advisor.analyze",
                "params": {"profile": "quick"},
                "why": "Generate actionable next steps from current operational context.",
            },
        ]
        if candidate_level in {"high", "critical"}:
            recommendations.insert(
                0,
                {
                    "priority": 108,
                    "kind": "alerts.list",
                    "params": {"severity": "critical", "acknowledged": False, "limit": 20},
                    "why": "Prioritize unresolved critical alerts correlated with elevated watch score.",
                },
            )

        out = {
            "ok": True,
            "summary": summary,
            "anomalies": top_anomalies,
            "recommendations": recommendations,
        }
        self.history.record(
            "opsgraph.watch_anomalies",
            {"summary": summary, "top_anomaly": ((top_anomalies[0] or {}).get("kind") if top_anomalies else None)},
            source="opsgraph",
            tags=["opsgraph", "watch", "anomalies", str(candidate_level)],
            summary={
                "watch_id": candidate_watch_id,
                "anomaly_count": len(top_anomalies),
                "watch_score": round(candidate_score, 3),
            },
        )
        return out

    def watch_forecast(
        self,
        *,
        limit: int = 30,
        horizon: int = 5,
        profile: str | None = None,
    ) -> dict[str, Any]:
        trends = self.watch_trends(limit=max(3, int(limit)), profile=profile)
        points = [p for p in (trends.get("points") or []) if isinstance(p, dict)]
        if len(points) < 2:
            return {
                "ok": True,
                "profile": profile,
                "count": len(points),
                "horizon": max(1, int(horizon)),
                "forecast": [],
                "trend": "unknown",
                "message": "at least two watch points are required for forecasting",
            }

        scores = [float(p.get("score") or 0.0) for p in points]
        n = len(scores)
        xs = list(range(n))
        sum_x = float(sum(xs))
        sum_y = float(sum(scores))
        sum_xx = float(sum(x * x for x in xs))
        sum_xy = float(sum(float(x) * float(y) for x, y in zip(xs, scores, strict=False)))
        den = float(n) * sum_xx - sum_x * sum_x
        slope = ((float(n) * sum_xy - sum_x * sum_y) / den) if den != 0 else 0.0
        intercept = ((sum_y - slope * sum_x) / float(n)) if n else 0.0

        fit = [intercept + slope * float(x) for x in xs]
        mean_y = (sum_y / float(n)) if n else 0.0
        ss_tot = sum((float(y) - mean_y) ** 2 for y in scores)
        ss_res = sum((float(y) - float(yh)) ** 2 for y, yh in zip(scores, fit, strict=False))
        r2 = (1.0 - ss_res / ss_tot) if ss_tot > 1e-9 else 0.0

        horizon = max(1, int(horizon))
        preds = []
        for step in range(1, horizon + 1):
            x = n - 1 + step
            raw = intercept + slope * float(x)
            bounded = round(min(100.0, max(0.0, raw)), 3)
            preds.append(
                {
                    "step": step,
                    "score": bounded,
                    "level": self._risk_level(bounded),
                }
            )

        current = round(scores[-1], 3)
        end_score = float((preds[-1] or {}).get("score") or current)
        trend = "flat"
        if slope > 0.75:
            trend = "rising"
        elif slope < -0.75:
            trend = "falling"

        out = {
            "ok": True,
            "profile": profile,
            "count": n,
            "horizon": horizon,
            "trend": trend,
            "current_score": current,
            "current_level": self._risk_level(current),
            "predicted_end_score": round(end_score, 3),
            "predicted_end_level": self._risk_level(end_score),
            "model": {
                "slope_per_run": round(slope, 4),
                "intercept": round(intercept, 4),
                "r2": round(r2, 4),
                "confidence": (
                    "high"
                    if (n >= 8 and r2 >= 0.65)
                    else ("medium" if (n >= 4 and r2 >= 0.3) else "low")
                ),
            },
            "threshold_projection": {
                "rise_to_high_55": _estimate_steps_to_rise_above(current, slope, 55.0),
                "rise_to_critical_80": _estimate_steps_to_rise_above(current, slope, 80.0),
                "fall_to_high_55": _estimate_steps_to_fall_below(current, slope, 55.0),
                "fall_to_medium_30": _estimate_steps_to_fall_below(current, slope, 30.0),
            },
            "forecast": preds,
        }
        self.history.record(
            "opsgraph.watch_forecast",
            {"profile": profile, "count": n, "horizon": horizon, "trend": trend, "model": out.get("model")},
            source="opsgraph",
            tags=["opsgraph", "watch", "forecast", trend],
            summary={
                "profile": profile,
                "trend": trend,
                "current_score": current,
                "predicted_end_score": round(end_score, 3),
            },
        )
        return out

    def explain_incident(
        self,
        incident_id: str,
        *,
        snapshot_id: str | None = None,
        depth: int = 2,
        max_nodes: int = 200,
    ) -> dict[str, Any]:
        iid = str(incident_id or "").strip()
        if not iid:
            raise ValueError("incident_id is required")
        incident_get = self.service.incidents.get(iid, timeline_limit=30)
        incident = (incident_get or {}).get("incident") or {}
        timeline = ((incident_get or {}).get("timeline") or {}).get("items") or []
        trace = self.trace(f"incident:{iid}", snapshot_id=snapshot_id, depth=depth, max_nodes=max_nodes)
        subgraph = (trace.get("subgraph") or {})
        nodes = [n for n in (subgraph.get("nodes") or []) if isinstance(n, dict)]
        edges = [e for e in (subgraph.get("edges") or []) if isinstance(e, dict)]

        alerts = [n for n in nodes if str(n.get("kind")) == "alert"]
        jobs = [n for n in nodes if str(n.get("kind")) == "job"]
        runbooks = [n for n in nodes if str(n.get("kind")) == "runbook_run"]
        rem_actions = [n for n in nodes if str(n.get("kind")) == "remediation_action"]
        rules = [n for n in nodes if str(n.get("kind")) == "alert_rule"]
        rule_kind_counts = Counter(str(n.get("rule_kind") or "") for n in alerts if str(n.get("rule_kind") or ""))

        failed_jobs = [n for n in jobs if str(n.get("status")) == "failed"]
        failed_runbooks = [n for n in runbooks if str(n.get("status")) == "failed"]
        failed_rem_actions = [n for n in rem_actions if not bool(n.get("ok", True))]
        critical_unacked = [n for n in alerts if str(n.get("severity")) == "critical" and not bool(n.get("acknowledged"))]

        hypotheses: list[dict[str, Any]] = []
        if rule_kind_counts:
            top_kind, _count = rule_kind_counts.most_common(1)[0]
            hypotheses.append(
                {
                    "kind": "alert_rule_cluster",
                    "confidence": 0.78,
                    "title": f"Incident correlates with {top_kind} alert activity",
                    "evidence": {"rule_kind_counts": dict(rule_kind_counts), "linked_alert_count": len(alerts), "linked_rule_count": len(rules)},
                }
            )
        if failed_jobs:
            hypotheses.append(
                {
                    "kind": "job_failures_present",
                    "confidence": 0.64,
                    "title": "Connected failed jobs may have contributed to or been caused by the incident",
                    "evidence": {"failed_jobs": [_brief_node(n) for n in failed_jobs[:10]]},
                }
            )
        if failed_runbooks or failed_rem_actions:
            hypotheses.append(
                {
                    "kind": "automation_followup_failed",
                    "confidence": 0.61,
                    "title": "Automation attempted follow-up but some actions failed",
                    "evidence": {
                        "failed_runbooks": [_brief_node(n) for n in failed_runbooks[:10]],
                        "failed_remediation_actions": [_brief_node(n) for n in failed_rem_actions[:10]],
                    },
                }
            )
        if not hypotheses:
            hypotheses.append(
                {
                    "kind": "weak_correlation",
                    "confidence": 0.32,
                    "title": "Few automated links exist for this incident in the current graph snapshot",
                    "evidence": {"linked_alert_count": len(alerts), "timeline_count": len(timeline)},
                }
            )

        next_actions = [
            {"priority": 100, "kind": "advisor.analyze", "params": {"profile": "incident_triage", "incident_id": iid}, "why": "Generate incident-focused advisor guidance."},
            {"priority": 92, "kind": "opsgraph.trace", "params": {"node_id": f"incident:{iid}", "snapshot_id": trace.get("snapshot_id"), "depth": 3}, "why": "Expand neighborhood to inspect dependencies and automation links."},
            {"priority": 88, "kind": "runbooks.run", "params": {"template": "incident_bundle_refresh", "incident_id": iid, "dry_run": True}, "why": "Preview a refreshed diagnostic bundle."},
        ]
        if critical_unacked:
            next_actions.insert(0, {"priority": 110, "kind": "alerts.list", "params": {"severity": "critical", "acknowledged": False, "limit": 20}, "why": "Review and acknowledge critical alerts tied to the incident."})

        result = {
            "ok": True,
            "incident_id": iid,
            "snapshot_id": trace.get("snapshot_id"),
            "created_at": _now_utc().isoformat(),
            "incident": _jsonable(incident),
            "graph_summary": (subgraph.get("summary") or {}),
            "timeline_summary": {
                "count": len(timeline),
                "recent_kinds": dict(Counter(str(ev.get("kind") or "event") for ev in timeline if isinstance(ev, dict))),
                "last_events": timeline[:10],
            },
            "neighborhood": {
                "alerts": [_brief_node(n) for n in alerts[:25]],
                "rules": [_brief_node(n) for n in rules[:25]],
                "jobs": [_brief_node(n) for n in jobs[:25]],
                "runbook_runs": [_brief_node(n) for n in runbooks[:25]],
                "remediation_actions": [_brief_node(n) for n in rem_actions[:25]],
            },
            "hypotheses": hypotheses,
            "next_actions": sorted(next_actions, key=lambda x: int(x.get("priority") or 0), reverse=True),
            "stats": {
                "edge_count": len(edges),
                "alert_count": len(alerts),
                "critical_unacked_alert_count": len(critical_unacked),
                "failed_job_count": len(failed_jobs),
                "failed_runbook_count": len(failed_runbooks),
                "failed_remediation_action_count": len(failed_rem_actions),
            },
        }
        self.history.record(
            "opsgraph.incident_explained",
            {"incident_id": iid, "snapshot_id": result.get("snapshot_id"), "stats": result.get("stats"), "top_hypothesis": (hypotheses[0]["kind"] if hypotheses else None)},
            source="opsgraph",
            tags=["opsgraph", "incident", str(incident.get("severity") or "warning")],
            summary={"incident_id": iid, "edge_count": len(edges), "hypothesis_count": len(hypotheses)},
        )
        return result

    def _resolve_snapshot_for_query(self, snapshot_id: str | None) -> dict[str, Any]:
        sid = (str(snapshot_id).strip() if snapshot_id else "") or None
        if sid:
            return self.get_snapshot(sid)["snapshot"]
        latest = self.latest(optional=True)
        snap = latest.get("snapshot")
        if isinstance(snap, dict):
            return snap
        return self.snapshot(profile="compact", persist=False)["snapshot"]

    def _collect(self, *, limit: int, timeline_limit: int) -> dict[str, Any]:
        return {
            "scheduler_status": self.service.scheduler.status(),
            "schedules": self.service.scheduler.list(),
            "jobs": self.service.jobs.list(limit=limit),
            "alert_rules": self.service.alerts.list_rules(),
            "alert_events": self.service.alerts.list_alerts(limit=limit),
            "incidents": self.service.incidents.list(limit=limit),
            "incident_timeline": self.service.incidents.timeline(limit=timeline_limit) if timeline_limit > 0 else {"ok": True, "count": 0, "items": []},
            "remediation_policies": self.service.remediations.list_policies(),
            "remediation_actions": self.service.remediations.list_actions(limit=limit),
            "notification_channels": self.service.notifications.list_channels(),
            "notification_deliveries": self.service.notifications.list_deliveries(limit=limit),
            "health": self.service.health.list(limit=limit),
            "reports": self.service.reports.list(limit=limit),
            "runbook_templates": self.service.runbooks.list_templates(include_steps=False),
            "runbook_runs": self.service.runbooks.list_runs(limit=limit),
            "advisor_analyses": self.service.advisor.list(limit=limit),
            "benchmarks": self.service.benchmark_analytics.list_runs(limit=limit),
        }

    def _populate_graph(self, g: "_GraphBuilder", data: dict[str, Any], *, include_timeline_events: bool) -> None:
        for subsystem in ("scheduler", "jobs", "alerts", "incidents", "remediations", "notifications", "health", "reports", "runbooks", "advisor", "benchmarks"):
            g.node(f"subsystem:{subsystem}", "subsystem", subsystem, subsystem=subsystem)

        scheduler_status = data.get("scheduler_status") if isinstance(data.get("scheduler_status"), dict) else {}
        g.node(
            "scheduler:status",
            "scheduler_status",
            "Scheduler Status",
            running=bool(scheduler_status.get("running")),
            enabled_count=scheduler_status.get("enabled_count"),
            schedule_count=scheduler_status.get("schedule_count"),
            last_tick_at=scheduler_status.get("last_tick_at"),
        )
        g.edge("subsystem:scheduler", "scheduler:status", "has_status")

        def items(key: str) -> list[dict[str, Any]]:
            res = data.get(key)
            return [x for x in ((res or {}).get("items") or []) if isinstance(x, dict)] if isinstance(res, dict) else []

        for row in items("schedules"):
            sid = str(row.get("id") or "")
            if not sid:
                continue
            nid = f"schedule:{sid}"
            g.node(nid, "schedule", str(row.get("name") or sid), schedule_id=sid, enabled=bool(row.get("enabled")), target_kind=row.get("target_kind"), next_run_at=row.get("next_run_at"), run_count=row.get("run_count"))
            g.edge("subsystem:scheduler", nid, "contains")
            g.edge("scheduler:status", nid, "manages")
            self._link_operation_kind(g, nid, row.get("target_kind"), relation="targets")

        for row in items("jobs"):
            jid = str(row.get("id") or "")
            if not jid:
                continue
            nid = f"job:{jid}"
            g.node(nid, "job", jid, job_id=jid, job_kind=row.get("kind"), status=row.get("status"), created_at=row.get("created_at"), duration_ms=row.get("duration_ms"), error=row.get("error"))
            g.edge("subsystem:jobs", nid, "contains")
            self._link_operation_kind(g, nid, row.get("kind"), relation="executes")
            params = row.get("params") if isinstance(row.get("params"), dict) else {}
            self._maybe_link_incident_from_payload(g, nid, params, relation="references_incident")
            self._maybe_link_alert_from_payload(g, nid, params, relation="references_alert")

        for row in items("alert_rules"):
            rid = str(row.get("id") or "")
            if not rid:
                continue
            nid = f"alert_rule:{rid}"
            g.node(nid, "alert_rule", str(row.get("name") or rid), rule_id=rid, rule_kind=row.get("kind"), severity=row.get("severity"), enabled=bool(row.get("enabled")), last_triggered_at=row.get("last_triggered_at"))
            g.edge("subsystem:alerts", nid, "contains")

        for row in items("alert_events"):
            aid = str(row.get("id") or "")
            if not aid:
                continue
            nid = f"alert:{aid}"
            g.node(
                nid,
                "alert",
                str(row.get("rule_name") or row.get("rule_kind") or aid),
                alert_id=aid,
                rule_id=row.get("rule_id"),
                rule_kind=row.get("rule_kind"),
                severity=row.get("severity"),
                acknowledged=bool(row.get("acknowledged")),
                created_at=row.get("created_at"),
                message=(str(row.get("message") or "")[:300] if row.get("message") is not None else None),
            )
            g.edge("subsystem:alerts", nid, "contains")
            rid = str(row.get("rule_id") or "")
            if rid:
                g.edge(nid, f"alert_rule:{rid}", "triggered_by_rule")

        for row in items("incidents"):
            iid = str(row.get("id") or "")
            if not iid:
                continue
            nid = f"incident:{iid}"
            g.node(nid, "incident", str(row.get("title") or iid), incident_id=iid, severity=row.get("severity"), status=row.get("status"), source=row.get("source"), created_at=row.get("created_at"), updated_at=row.get("updated_at"))
            g.edge("subsystem:incidents", nid, "contains")
            for alert_id in row.get("linked_alert_ids") or []:
                g.edge(nid, f"alert:{alert_id}", "linked_alert")
            for report_id in row.get("linked_report_ids") or []:
                g.edge(nid, f"report:{report_id}", "linked_report")
            for snap_id in row.get("linked_health_snapshot_ids") or []:
                g.edge(nid, f"health_snapshot:{snap_id}", "linked_health_snapshot")

        if include_timeline_events:
            for row in items("incident_timeline"):
                ev_id = str(row.get("id") or "")
                iid = str(row.get("incident_id") or "")
                if not ev_id or not iid:
                    continue
                nid = f"incident_event:{ev_id}"
                g.node(nid, "incident_event", str(row.get("kind") or ev_id), incident_event_id=ev_id, incident_id=iid, kind_event=row.get("kind"), created_at=row.get("created_at"))
                g.edge(f"incident:{iid}", nid, "has_timeline_event")
                data_obj = row.get("data") if isinstance(row.get("data"), dict) else {}
                if isinstance(data_obj, dict):
                    alert_id = str(data_obj.get("alert_id") or "")
                    if not alert_id and isinstance(data_obj.get("alert"), dict):
                        alert_id = str((data_obj.get("alert") or {}).get("id") or "")
                    if alert_id:
                        g.edge(nid, f"alert:{alert_id}", "references_alert")

        for row in items("remediation_policies"):
            pid = str(row.get("id") or "")
            if not pid:
                continue
            nid = f"remediation_policy:{pid}"
            g.node(nid, "remediation_policy", str(row.get("name") or pid), policy_id=pid, enabled=bool(row.get("enabled")), cooldown_sec=row.get("cooldown_sec"), last_triggered_at=row.get("last_triggered_at"))
            g.edge("subsystem:remediations", nid, "contains")

        for row in items("remediation_actions"):
            rid = str(row.get("id") or "")
            if not rid:
                continue
            nid = f"remediation_action:{rid}"
            g.node(nid, "remediation_action", str(row.get("policy_name") or rid), action_run_id=rid, policy_id=row.get("policy_id"), alert_id=row.get("alert_id"), severity=row.get("severity"), ok=bool(row.get("ok")), suppressed=bool(row.get("suppressed")), created_at=row.get("created_at"))
            g.edge("subsystem:remediations", nid, "contains")
            if row.get("policy_id"):
                g.edge(nid, f"remediation_policy:{row.get('policy_id')}", "executed_policy")
            if row.get("alert_id"):
                g.edge(nid, f"alert:{row.get('alert_id')}", "handled_alert")
            for action_row in row.get("actions") or []:
                if not isinstance(action_row, dict):
                    continue
                result = action_row.get("result") if isinstance(action_row.get("result"), dict) else {}
                if isinstance(result, dict):
                    if isinstance(result.get("job"), dict) and (result.get("job") or {}).get("id"):
                        g.edge(nid, f"job:{(result.get('job') or {}).get('id')}", "submitted_job")
                    if result.get("report_id"):
                        g.edge(nid, f"report:{result.get('report_id')}", "generated_report")
                    if isinstance(result.get("snapshot"), dict) and (result.get("snapshot") or {}).get("snapshot_id"):
                        g.edge(nid, f"health_snapshot:{(result.get('snapshot') or {}).get('snapshot_id')}", "generated_health_snapshot")
                    if isinstance(result.get("run"), dict) and (result.get("run") or {}).get("run_id"):
                        g.edge(nid, f"runbook_run:{(result.get('run') or {}).get('run_id')}", "executed_runbook")

        for row in items("notification_channels"):
            cid = str(row.get("id") or "")
            if not cid:
                continue
            nid = f"notification_channel:{cid}"
            g.node(nid, "notification_channel", str(row.get("name") or cid), channel_id=cid, channel_type=row.get("type"), enabled=bool(row.get("enabled")), last_delivery_id=row.get("last_delivery_id"))
            g.edge("subsystem:notifications", nid, "contains")

        for row in items("notification_deliveries"):
            did = str(row.get("id") or "")
            if not did:
                continue
            nid = f"notification_delivery:{did}"
            g.node(nid, "notification_delivery", str(row.get("topic") or did), delivery_id=did, channel_id=row.get("channel_id"), topic=row.get("topic"), severity=row.get("severity"), ok=bool(row.get("ok")), created_at=row.get("created_at"))
            g.edge("subsystem:notifications", nid, "contains")
            if row.get("channel_id"):
                g.edge(nid, f"notification_channel:{row.get('channel_id')}", "delivered_via")
            self._link_topic_node(g, nid, row.get("topic"))

        self._add_simple_items(g, "health", items("health"), "health_snapshot", "snapshot_id", label_key="snapshot_id", extra_keys=("profile", "score", "level", "created_at"))
        self._add_simple_items(g, "reports", items("reports"), "report", "report_id", label_key="title", extra_keys=("profile", "created_at"))
        self._add_simple_items(g, "benchmarks", items("benchmarks"), "benchmark_run", "run_id", label_key="run_id", extra_keys=("profile", "ok", "total_ms", "started_at", "finished_at"))

        for row in items("runbook_templates"):
            name = str(row.get("name") or "")
            if not name:
                continue
            nid = f"runbook_template:{name}"
            g.node(nid, "runbook_template", str(row.get("title") or name), template=name, builtin=bool(row.get("builtin")), step_count=row.get("step_count"))
            g.edge("subsystem:runbooks", nid, "contains")

        for row in items("runbook_runs"):
            run_id = str(row.get("run_id") or row.get("id") or "")
            if not run_id:
                continue
            nid = f"runbook_run:{run_id}"
            g.node(nid, "runbook_run", str(row.get("template") or run_id), run_id=run_id, template=row.get("template"), status=row.get("status"), incident_id=row.get("incident_id"), alert_id=row.get("alert_id"), created_at=row.get("started_at") or row.get("created_at"))
            g.edge("subsystem:runbooks", nid, "contains")
            if row.get("template"):
                g.edge(nid, f"runbook_template:{row.get('template')}", "uses_template")
            if row.get("incident_id"):
                g.edge(nid, f"incident:{row.get('incident_id')}", "targets_incident")
            if row.get("alert_id"):
                g.edge(nid, f"alert:{row.get('alert_id')}", "targets_alert")

        for row in items("advisor_analyses"):
            aid = str(row.get("analysis_id") or "")
            if not aid:
                continue
            nid = f"advisor_analysis:{aid}"
            g.node(nid, "advisor_analysis", str(row.get("profile") or aid), analysis_id=aid, profile=row.get("profile"), risk_score=row.get("risk_score"), created_at=row.get("created_at"))
            g.edge("subsystem:advisor", nid, "contains")
            if row.get("incident_id"):
                g.edge(nid, f"incident:{row.get('incident_id')}", "analyzes_incident")

    def _add_simple_items(
        self,
        g: "_GraphBuilder",
        subsystem: str,
        rows: list[dict[str, Any]],
        kind: str,
        id_key: str,
        *,
        label_key: str,
        extra_keys: tuple[str, ...] = (),
    ) -> None:
        for row in rows:
            rid = str(row.get(id_key) or "")
            if not rid:
                continue
            nid = f"{kind}:{rid}"
            attrs = {k: row.get(k) for k in extra_keys if row.get(k) is not None}
            attrs[id_key] = rid
            g.node(nid, kind, str(row.get(label_key) or rid), **attrs)
            g.edge(f"subsystem:{subsystem}", nid, "contains")

    @staticmethod
    def _graph_index(
        nodes: list[dict[str, Any]],
        edges: list[dict[str, Any]],
    ) -> tuple[dict[str, dict[str, Any]], dict[str, list[dict[str, Any]]], dict[str, list[dict[str, Any]]]]:
        node_map = {str(n.get("id")): n for n in nodes if isinstance(n, dict) and n.get("id")}
        out_adj: dict[str, list[dict[str, Any]]] = {}
        in_adj: dict[str, list[dict[str, Any]]] = {}
        for edge in edges:
            if not isinstance(edge, dict):
                continue
            src = str(edge.get("src") or "")
            dst = str(edge.get("dst") or "")
            if not src or not dst:
                continue
            out_adj.setdefault(src, []).append(edge)
            in_adj.setdefault(dst, []).append(edge)
        return node_map, out_adj, in_adj

    @staticmethod
    def _iter_neighbors(
        node_id: str,
        out_adj: dict[str, list[dict[str, Any]]],
        in_adj: dict[str, list[dict[str, Any]]],
        *,
        direction: str,
    ) -> list[tuple[str, dict[str, Any]]]:
        out: list[tuple[str, dict[str, Any]]] = []
        if direction in {"both", "out"}:
            for edge in out_adj.get(node_id, []):
                nxt = str(edge.get("dst") or "")
                if nxt:
                    out.append((nxt, edge))
        if direction in {"both", "in"}:
            for edge in in_adj.get(node_id, []):
                nxt = str(edge.get("src") or "")
                if nxt:
                    out.append((nxt, edge))
        return out

    def _hotspots_from_snapshot(
        self,
        snap: dict[str, Any],
        *,
        limit: int,
        include_zero: bool,
    ) -> dict[str, Any]:
        graph = (snap.get("graph") or {}) if isinstance(snap, dict) else {}
        nodes = [n for n in (graph.get("nodes") or []) if isinstance(n, dict)]
        edges = [e for e in (graph.get("edges") or []) if isinstance(e, dict)]
        node_map, out_adj, in_adj = self._graph_index(nodes, edges)

        limit = max(1, int(limit))
        rows: list[dict[str, Any]] = []
        scores: list[float] = []
        nonzero = 0
        for node_id, node in node_map.items():
            out_degree = len(out_adj.get(node_id, []))
            in_degree = len(in_adj.get(node_id, []))
            score = self._node_risk_score(node, out_degree=out_degree, in_degree=in_degree)
            scores.append(score)
            if score > 0:
                nonzero += 1
            if not include_zero and score <= 0:
                continue
            row = dict(_brief_node(node))
            row.update(
                {
                    "risk_score": score,
                    "risk_level": self._risk_level(score),
                    "degree_out": out_degree,
                    "degree_in": in_degree,
                    "degree_total": out_degree + in_degree,
                }
            )
            rows.append(row)

        rows.sort(
            key=lambda r: (
                -float(r.get("risk_score") or 0.0),
                -int(r.get("degree_total") or 0),
                str(r.get("kind") or ""),
                str(r.get("id") or ""),
            )
        )
        rows = rows[:limit]

        summary = {
            "node_count": len(node_map),
            "edge_count": len(edges),
            "nonzero_risk_node_count": nonzero,
            "max_risk_score": round(max(scores), 3) if scores else 0.0,
            "avg_risk_score": round(sum(scores) / len(scores), 3) if scores else 0.0,
        }
        return {
            "ok": True,
            "snapshot_id": snap.get("snapshot_id"),
            "limit": limit,
            "include_zero": bool(include_zero),
            "summary": summary,
            "hotspots": rows,
        }

    def _components_from_snapshot(
        self,
        snap: dict[str, Any],
        *,
        limit: int,
        order_by: str,
    ) -> dict[str, Any]:
        graph = (snap.get("graph") or {}) if isinstance(snap, dict) else {}
        nodes = [n for n in (graph.get("nodes") or []) if isinstance(n, dict)]
        edges = [e for e in (graph.get("edges") or []) if isinstance(e, dict)]
        node_map, out_adj, in_adj = self._graph_index(nodes, edges)
        mode = str(order_by or "risk").strip().lower()
        if mode not in {"risk", "size"}:
            raise ValueError("order_by must be one of: risk, size")

        neighbors: dict[str, set[str]] = {nid: set() for nid in node_map}
        for edge in edges:
            src = str(edge.get("src") or "")
            dst = str(edge.get("dst") or "")
            if src in neighbors and dst in neighbors:
                neighbors[src].add(dst)
                neighbors[dst].add(src)

        limit = max(1, int(limit))
        visited: set[str] = set()
        components: list[dict[str, Any]] = []
        for nid in neighbors:
            if nid in visited:
                continue
            q = deque([nid])
            visited.add(nid)
            members: list[str] = []
            while q:
                cur = q.popleft()
                members.append(cur)
                for nxt in neighbors.get(cur, set()):
                    if nxt not in visited:
                        visited.add(nxt)
                        q.append(nxt)

            member_set = set(members)
            member_nodes = [node_map[mid] for mid in members if mid in node_map]
            edge_count = 0
            for edge in edges:
                src = str(edge.get("src") or "")
                dst = str(edge.get("dst") or "")
                if src in member_set and dst in member_set:
                    edge_count += 1

            kind_counts = Counter(str(n.get("kind") or "unknown") for n in member_nodes)
            severity_counts = Counter(str(n.get("severity") or "none") for n in member_nodes if n.get("severity") is not None)
            status_counts = Counter(str(n.get("status") or "none") for n in member_nodes if n.get("status") is not None)

            scored_nodes = []
            scores = []
            for node in member_nodes:
                node_id = str(node.get("id") or "")
                out_degree = len(out_adj.get(node_id, []))
                in_degree = len(in_adj.get(node_id, []))
                score = self._node_risk_score(node, out_degree=out_degree, in_degree=in_degree)
                scores.append(score)
                scored_nodes.append((score, out_degree + in_degree, node))
            scored_nodes.sort(key=lambda item: (-item[0], -item[1], str((item[2] or {}).get("id") or "")))

            top_score = max(scores) if scores else 0.0
            avg_score = (sum(scores) / len(scores)) if scores else 0.0
            component_score = round(min(100.0, max(0.0, top_score * 0.6 + avg_score * 0.4)), 3)
            anchors = []
            for score, degree, node in scored_nodes[:5]:
                row = dict(_brief_node(node))
                row["risk_score"] = round(float(score), 3)
                row["risk_level"] = self._risk_level(score)
                row["degree_total"] = int(degree)
                anchors.append(row)

            components.append(
                {
                    "component_id": f"component_{str(members[0]).replace(':', '_')}",
                    "node_count": len(member_nodes),
                    "edge_count": edge_count,
                    "risk_score": component_score,
                    "risk_level": self._risk_level(component_score),
                    "kind_counts": dict(kind_counts),
                    "severity_counts": dict(severity_counts),
                    "status_counts": dict(status_counts),
                    "anchors": anchors,
                }
            )

        if mode == "risk":
            components.sort(key=lambda r: (-float(r.get("risk_score") or 0.0), -int(r.get("node_count") or 0), str(r.get("component_id") or "")))
        else:
            components.sort(key=lambda r: (-int(r.get("node_count") or 0), -float(r.get("risk_score") or 0.0), str(r.get("component_id") or "")))
        total_component_count = len(components)
        all_component_scores = [float(c.get("risk_score") or 0.0) for c in components]
        largest_component_size = max((int(c.get("node_count") or 0) for c in components), default=0)
        components = components[:limit]

        summary = {
            "component_count": total_component_count,
            "returned_component_count": len(components),
            "largest_component_size": largest_component_size,
            "max_risk_score": round(max(all_component_scores), 3) if all_component_scores else 0.0,
            "avg_risk_score": round(sum(all_component_scores) / len(all_component_scores), 3) if all_component_scores else 0.0,
            "order_by": mode,
        }
        return {
            "ok": True,
            "snapshot_id": snap.get("snapshot_id"),
            "limit": limit,
            "order_by": mode,
            "summary": summary,
            "components": components,
        }

    def _maybe_open_watch_incident(
        self,
        *,
        enabled: bool,
        profile: str,
        score: float,
        level: str,
        threshold: float,
        cooldown_sec: float,
        risk_signals: dict[str, Any],
        hotspots: list[dict[str, Any]],
        snapshot_id: str,
    ) -> dict[str, Any]:
        if not enabled:
            return {"enabled": False, "created": False, "reason": "disabled"}
        score = float(score or 0.0)
        threshold = float(threshold or 0.0)
        if score < threshold:
            return {"enabled": True, "created": False, "reason": "below_threshold", "threshold": threshold, "score": round(score, 3)}
        if str(level) not in {"high", "critical"}:
            return {"enabled": True, "created": False, "reason": "risk_level_not_high_enough", "level": level, "score": round(score, 3)}

        rows = (self.service.incidents.list(limit=500, source="opsgraph_watch_auto") or {}).get("items") or []
        profile_key = str(profile or "")
        now = _now_utc()
        open_match: dict[str, Any] | None = None
        recent_any = None
        for row in rows:
            if not isinstance(row, dict):
                continue
            md = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            corr = md.get("correlation") if isinstance(md.get("correlation"), dict) else {}
            if str(corr.get("watch_profile") or "") != profile_key:
                continue
            created = _parse_iso_datetime(row.get("created_at"))
            if created is not None:
                age = (now - created).total_seconds()
                if age <= float(max(0.0, cooldown_sec)):
                    recent_any = row
            if str(row.get("status") or "") != "closed":
                open_match = row
                break

        if open_match is not None:
            incident_id = str(open_match.get("id") or "")
            try:
                self.service.incidents.add_note(
                    incident_id,
                    note=f"OpsGraph watch update: score={round(score, 3)} level={level} snapshot={snapshot_id}",
                    author="opsgraph",
                    kind="watch_update",
                )
            except Exception:
                pass
            return {
                "enabled": True,
                "created": False,
                "reason": "linked_existing_open",
                "incident_id": incident_id,
            }

        if recent_any is not None:
            return {
                "enabled": True,
                "created": False,
                "reason": "cooldown_active",
                "incident_id": recent_any.get("id"),
                "cooldown_sec": float(max(0.0, cooldown_sec)),
            }

        top_hotspots = [h for h in hotspots[:3] if isinstance(h, dict)]
        severity = "critical" if (level == "critical" or score >= 90.0) else "warning"
        title = f"[{str(level).upper()}] OpsGraph watch risk ({profile_key}) score {round(score, 1)}"
        description = "Automated watch incident opened due to sustained elevated OpsGraph risk score."
        if top_hotspots:
            labels = [str(h.get("label") or h.get("id") or "node") for h in top_hotspots]
            description = f"{description} Top hotspots: {', '.join(labels[:3])}."
        metadata = {
            "correlation": {"watch_profile": profile_key, "watch_source": "opsgraph.watch"},
            "watch": {
                "score": round(score, 3),
                "level": str(level),
                "threshold": threshold,
                "snapshot_id": snapshot_id,
                "risk_signals": _jsonable(risk_signals),
                "top_hotspots": [_brief_node(h) for h in top_hotspots],
            },
        }
        created = self.service.incidents.create(
            title=title,
            severity=severity,
            description=description,
            source="opsgraph_watch_auto",
            status="open",
            tags=["auto", "opsgraph", "watch", profile_key, str(level)],
            metadata=metadata,
            auto_created=True,
        )
        incident = (created or {}).get("incident") or {}
        return {
            "enabled": True,
            "created": True,
            "reason": "created",
            "incident_id": incident.get("id"),
            "severity": severity,
        }

    @staticmethod
    def _risk_level(score: float) -> str:
        s = float(score or 0.0)
        if s >= 80.0:
            return "critical"
        if s >= 55.0:
            return "high"
        if s >= 30.0:
            return "medium"
        return "low"

    @staticmethod
    def _node_risk_score(node: dict[str, Any], *, out_degree: int, in_degree: int) -> float:
        kind = str(node.get("kind") or "")
        severity = str(node.get("severity") or "").lower()
        status = str(node.get("status") or "").lower()
        degree_total = max(0, int(out_degree)) + max(0, int(in_degree))

        severity_weights = {"critical": 40.0, "high": 24.0, "warning": 12.0, "info": 4.0, "low": 2.0}
        status_weights = {
            "failed": 26.0,
            "open": 20.0,
            "investigating": 17.0,
            "degraded": 13.0,
            "running": 2.0,
            "queued": 3.0,
        }
        kind_weights = {
            "incident": 12.0,
            "alert": 10.0,
            "job": 7.0,
            "runbook_run": 6.0,
            "remediation_action": 6.0,
            "health_snapshot": 4.0,
            "notification_delivery": 3.0,
        }

        score = float(degree_total * 1.8)
        score += severity_weights.get(severity, 0.0)
        score += status_weights.get(status, 0.0)
        score += kind_weights.get(kind, 0.0)

        if kind == "alert" and severity == "critical" and not bool(node.get("acknowledged")):
            score += 22.0
        if kind == "job" and status == "failed":
            score += 12.0
        if kind == "runbook_run" and status == "failed":
            score += 10.0
        if kind == "remediation_action" and not bool(node.get("ok", True)):
            score += 9.0

        return round(min(100.0, max(0.0, score)), 3)

    def _link_operation_kind(self, g: "_GraphBuilder", source_node_id: str, op_kind: Any, *, relation: str) -> None:
        kind = str(op_kind or "").strip()
        if not kind:
            return
        nid = f"op_kind:{kind}"
        g.node(nid, "operation_kind", kind, op_kind=kind)
        g.edge(source_node_id, nid, relation)

    def _link_topic_node(self, g: "_GraphBuilder", source_node_id: str, topic: Any) -> None:
        t = str(topic or "").strip()
        if not t:
            return
        nid = f"notification_topic:{t}"
        g.node(nid, "notification_topic", t, topic=t)
        g.edge(source_node_id, nid, "topic")

    def _maybe_link_incident_from_payload(self, g: "_GraphBuilder", source_node_id: str, payload: dict[str, Any], *, relation: str) -> None:
        if not isinstance(payload, dict):
            return
        iid = str(payload.get("incident_id") or "")
        if iid.startswith("inc_"):
            g.edge(source_node_id, f"incident:{iid}", relation)

    def _maybe_link_alert_from_payload(self, g: "_GraphBuilder", source_node_id: str, payload: dict[str, Any], *, relation: str) -> None:
        if not isinstance(payload, dict):
            return
        aid = str(payload.get("alert_id") or "")
        if aid.startswith("alert_"):
            g.edge(source_node_id, f"alert:{aid}", relation)

    def _persist_snapshot(self, snapshot: dict[str, Any]) -> Path:
        sid = str(snapshot.get("snapshot_id") or "").strip()
        if not sid:
            raise ValueError("snapshot missing snapshot_id")
        path = self.snapshots_dir / f"{sid}.json"
        index_row = {
            "snapshot_id": sid,
            "profile": snapshot.get("profile"),
            "created_at": snapshot.get("created_at"),
            "summary": snapshot.get("summary") or {},
            "file": str(path),
        }
        with self._lock:
            path.write_text(json.dumps(_jsonable(snapshot), indent=2, ensure_ascii=False), encoding="utf-8")
            with self.index_file.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(_jsonable(index_row), ensure_ascii=False))
                fh.write("\n")
        return path

    def _persist_watch(self, watch: dict[str, Any]) -> Path:
        wid = str(watch.get("watch_id") or "").strip()
        if not wid:
            raise ValueError("watch missing watch_id")
        path = self.watch_dir / f"{wid}.json"
        index_row = {
            "watch_id": wid,
            "created_at": watch.get("created_at"),
            "profile": watch.get("profile"),
            "snapshot_id": ((watch.get("snapshot") or {}).get("snapshot_id")),
            "baseline_snapshot_id": watch.get("baseline_snapshot_id"),
            "score": watch.get("score"),
            "level": watch.get("level"),
            "summary": watch.get("summary") or {},
            "file": str(path),
        }
        with self._lock:
            path.write_text(json.dumps(_jsonable(watch), indent=2, ensure_ascii=False), encoding="utf-8")
            with self.watch_index_file.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(_jsonable(index_row), ensure_ascii=False))
                fh.write("\n")
        return path

    def _summarize_graph(self, graph: dict[str, Any]) -> dict[str, Any]:
        nodes = [n for n in (graph.get("nodes") or []) if isinstance(n, dict)]
        edges = [e for e in (graph.get("edges") or []) if isinstance(e, dict)]
        node_kind_counts = Counter(str(n.get("kind") or "unknown") for n in nodes)
        edge_kind_counts = Counter(str(e.get("kind") or "unknown") for e in edges)

        neighbors: dict[str, set[str]] = {}
        for n in nodes:
            nid = str(n.get("id") or "")
            if nid:
                neighbors.setdefault(nid, set())
        for e in edges:
            src = str(e.get("src") or "")
            dst = str(e.get("dst") or "")
            if not src or not dst:
                continue
            neighbors.setdefault(src, set()).add(dst)
            neighbors.setdefault(dst, set()).add(src)

        visited: set[str] = set()
        component_sizes: list[int] = []
        for nid in neighbors:
            if nid in visited:
                continue
            q = deque([nid])
            visited.add(nid)
            size = 0
            while q:
                cur = q.popleft()
                size += 1
                for nxt in neighbors.get(cur, set()):
                    if nxt not in visited:
                        visited.add(nxt)
                        q.append(nxt)
            component_sizes.append(size)
        component_sizes.sort(reverse=True)

        degree = Counter()
        for e in edges:
            src = str(e.get("src") or "")
            dst = str(e.get("dst") or "")
            if src:
                degree[src] += 1
            if dst:
                degree[dst] += 1
        lookup = {str(n.get("id") or ""): n for n in nodes}
        top_degree_nodes = []
        for nid, deg in degree.most_common(10):
            row = lookup.get(nid) or {}
            top_degree_nodes.append({"id": nid, "kind": row.get("kind"), "label": row.get("label"), "degree": deg})

        return {
            "node_count": len(nodes),
            "edge_count": len(edges),
            "node_kind_counts": dict(node_kind_counts),
            "edge_kind_counts": dict(edge_kind_counts),
            "connected_component_count": len(component_sizes),
            "largest_component_size": (component_sizes[0] if component_sizes else 0),
            "top_degree_nodes": top_degree_nodes,
        }


class _GraphBuilder:
    def __init__(self) -> None:
        self._nodes: dict[str, dict[str, Any]] = {}
        self._edges: dict[tuple[str, str, str], dict[str, Any]] = {}

    def node(self, node_id: str, kind: str, label: str, **attrs: Any) -> dict[str, Any]:
        nid = str(node_id or "").strip()
        if not nid:
            raise ValueError("node id is required")
        row = self._nodes.get(nid)
        if row is None:
            row = {"id": nid, "kind": str(kind or "entity"), "label": str(label or nid)}
            self._nodes[nid] = row
        if kind:
            row["kind"] = str(kind)
        if label:
            row["label"] = str(label)
        for key, value in attrs.items():
            if value is not None:
                row[str(key)] = _jsonable(value)
        return row

    def edge(self, src: str, dst: str, kind: str, **attrs: Any) -> dict[str, Any]:
        s = str(src or "").strip()
        d = str(dst or "").strip()
        k = str(kind or "rel").strip()
        if not s or not d or not k:
            raise ValueError("edge requires src, dst, kind")
        key = (s, d, k)
        row = self._edges.get(key)
        if row is None:
            row = {"id": f"edge_{uuid.uuid4().hex[:10]}", "src": s, "dst": d, "kind": k}
            self._edges[key] = row
        for attr_key, value in attrs.items():
            if value is not None:
                row[str(attr_key)] = _jsonable(value)
        return row

    def build(self) -> dict[str, Any]:
        return {
            "nodes": sorted(self._nodes.values(), key=lambda x: (str(x.get("kind") or ""), str(x.get("id") or ""))),
            "edges": sorted(self._edges.values(), key=lambda x: (str(x.get("kind") or ""), str(x.get("src") or ""), str(x.get("dst") or ""))),
        }


def _brief_node(node: dict[str, Any]) -> dict[str, Any]:
    out = {"id": node.get("id"), "kind": node.get("kind"), "label": node.get("label")}
    for key in ("severity", "status", "rule_kind", "rule_id", "incident_id", "alert_id", "run_id", "policy_id", "score", "level", "profile", "distance", "created_at"):
        if key in node:
            out[key] = node.get(key)
    return out


def _brief_edge(edge: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": edge.get("id"),
        "src": edge.get("src"),
        "dst": edge.get("dst"),
        "kind": edge.get("kind"),
    }


def _edge_key(edge: dict[str, Any]) -> tuple[str, str, str] | None:
    if not isinstance(edge, dict):
        return None
    src = str(edge.get("src") or "")
    dst = str(edge.get("dst") or "")
    kind = str(edge.get("kind") or "")
    if not src or not dst or not kind:
        return None
    return (src, dst, kind)


def _num_delta(a: Any, b: Any) -> int | float | None:
    try:
        if a is None or b is None:
            return None
        if isinstance(a, bool) or isinstance(b, bool):
            return None
        fa = float(a)
        fb = float(b)
        d = fb - fa
        if float(int(d)) == d:
            return int(d)
        return round(d, 3)
    except Exception:
        return None


def _stddev(values: list[float]) -> float:
    vals = [float(v) for v in values]
    if not vals:
        return 0.0
    mean = sum(vals) / float(len(vals))
    var = sum((v - mean) ** 2 for v in vals) / float(len(vals))
    return float(var**0.5)


def _estimate_steps_to_rise_above(current: float, slope: float, threshold: float) -> int | None:
    cur = float(current)
    sl = float(slope)
    tgt = float(threshold)
    if cur >= tgt:
        return 0
    if sl <= 1e-9:
        return None
    steps = (tgt - cur) / sl
    if steps < 0:
        return None
    iv = int(steps)
    if float(iv) < steps:
        iv += 1
    return max(0, iv)


def _estimate_steps_to_fall_below(current: float, slope: float, threshold: float) -> int | None:
    cur = float(current)
    sl = float(slope)
    tgt = float(threshold)
    if cur <= tgt:
        return 0
    if sl >= -1e-9:
        return None
    steps = (tgt - cur) / sl
    if steps < 0:
        return None
    iv = int(steps)
    if float(iv) < steps:
        iv += 1
    return max(0, iv)


def _parse_iso_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text)
    except Exception:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


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
    try:
        json.dumps(value)
        return value
    except Exception:
        return str(value)

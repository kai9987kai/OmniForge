from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class HistoryStore:
    def __init__(self, root_dir: Path):
        self.root_dir = Path(root_dir).resolve()
        self.entries_dir = self.root_dir / "entries"
        self.index_file = self.root_dir / "events.jsonl"
        self._lock = threading.RLock()
        self.entries_dir.mkdir(parents=True, exist_ok=True)
        self.index_file.parent.mkdir(parents=True, exist_ok=True)

    def record(
        self,
        event_type: str,
        data: Any,
        *,
        source: str | None = None,
        tags: list[str] | None = None,
        summary: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        event_id = f"evt_{now.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
        entry = {
            "id": event_id,
            "event_type": str(event_type),
            "created_at": now.isoformat(),
            "source": source,
            "tags": list(tags or []),
            "summary": self._jsonable(summary) if summary is not None else None,
            "data": self._jsonable(data),
        }
        entry_path = self.entries_dir / f"{event_id}.json"
        index_row = {
            "id": event_id,
            "event_type": entry["event_type"],
            "created_at": entry["created_at"],
            "source": source,
            "tags": entry["tags"],
            "summary": entry["summary"],
            "file": str(entry_path),
        }
        with self._lock:
            entry_path.write_text(json.dumps(entry, ensure_ascii=False, indent=2), encoding="utf-8")
            with self.index_file.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(index_row, ensure_ascii=False))
                fh.write("\n")
        return index_row

    def list(self, *, limit: int = 50, event_type: str | None = None) -> dict[str, Any]:
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
                    if event_type and str(row.get("event_type")) != str(event_type):
                        continue
                    rows.append(row)
        rows = list(reversed(rows))[: max(1, int(limit))]
        return {"ok": True, "count": len(rows), "items": rows}

    def get(self, event_id: str) -> dict[str, Any]:
        event_id = str(event_id).strip()
        if not event_id:
            raise ValueError("event_id is required")
        path = self.entries_dir / f"{event_id}.json"
        if not path.exists():
            raise FileNotFoundError(f"history entry not found: {event_id}")
        text = path.read_text(encoding="utf-8", errors="replace")
        parsed = json.loads(text)
        return {"ok": True, "entry": parsed, "file": str(path)}

    def read_index_delta(
        self,
        *,
        offset: int = 0,
        max_rows: int = 200,
        event_type: str | None = None,
    ) -> dict[str, Any]:
        rows: list[dict[str, Any]] = []
        start_offset = max(0, int(offset))
        end_offset = start_offset
        if not self.index_file.exists():
            return {"ok": True, "rows": rows, "offset": 0}
        with self._lock:
            with self.index_file.open("rb") as fh:
                try:
                    fh.seek(start_offset)
                except Exception:
                    fh.seek(0)
                    start_offset = 0
                chunk = fh.read()
                end_offset = fh.tell()
        if not chunk:
            return {"ok": True, "rows": rows, "offset": end_offset}
        text = chunk.decode("utf-8", errors="replace")
        lines = text.splitlines()
        # If we started mid-line, drop the first partial line.
        if start_offset > 0 and not text.startswith("{"):
            lines = lines[1:] if len(lines) > 1 else []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            if event_type and str(row.get("event_type")) != str(event_type):
                continue
            rows.append(row)
            if len(rows) >= max(1, int(max_rows)):
                break
        return {"ok": True, "rows": rows, "offset": end_offset}

    def tail(self, *, limit: int = 50, event_type: str | None = None) -> list[dict[str, Any]]:
        return list((self.list(limit=limit, event_type=event_type) or {}).get("items") or [])

    def stats(self) -> dict[str, Any]:
        count = 0
        if self.index_file.exists():
            with self._lock:
                for line in self.index_file.read_text(encoding="utf-8", errors="replace").splitlines():
                    if line.strip():
                        count += 1
        return {
            "ok": True,
            "root_dir": str(self.root_dir),
            "entries_dir": str(self.entries_dir),
            "index_file": str(self.index_file),
            "entry_count": count,
        }

    @classmethod
    def _jsonable(cls, value: Any) -> Any:
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, Path):
            return str(value)
        if isinstance(value, bytes):
            return {"type": "bytes", "len": len(value)}
        if isinstance(value, list):
            return [cls._jsonable(v) for v in value]
        if isinstance(value, tuple):
            return [cls._jsonable(v) for v in value]
        if isinstance(value, dict):
            return {str(k): cls._jsonable(v) for k, v in value.items()}
        try:
            json.dumps(value)
            return value
        except Exception:
            return str(value)

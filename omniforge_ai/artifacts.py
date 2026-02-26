from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class ArtifactStore:
    def __init__(self, roots: dict[str, Path]):
        self._roots = {str(k): Path(v).resolve() for k, v in roots.items()}
        for name, root in self._roots.items():
            if name in {"out", "benchmarks", "jobs", "history"}:
                root.mkdir(parents=True, exist_ok=True)

    def roots(self) -> dict[str, Any]:
        items = []
        for name, root in sorted(self._roots.items()):
            items.append({"name": name, "path": str(root), "exists": root.exists()})
        return {"ok": True, "roots": items}

    def list_dir(
        self,
        root_name: str,
        rel_path: str = "",
        *,
        recursive: bool = False,
        limit: int = 200,
    ) -> dict[str, Any]:
        base, target = self._resolve(root_name, rel_path)
        if not base.exists():
            return {"ok": True, "root": root_name, "base_path": str(base), "path": "", "items": []}
        if not target.exists():
            raise FileNotFoundError(f"artifact path not found: {target}")
        if not target.is_dir():
            raise NotADirectoryError(f"artifact path is not a directory: {target}")
        limit_i = max(1, int(limit))
        items = []
        iterator = target.rglob("*") if recursive else target.iterdir()
        for p in sorted(iterator):
            if len(items) >= limit_i:
                break
            try:
                stat = p.stat()
            except Exception:
                continue
            rel = p.relative_to(base)
            items.append(
                {
                    "path": str(rel).replace("\\", "/"),
                    "name": p.name,
                    "is_dir": p.is_dir(),
                    "size": (None if p.is_dir() else int(stat.st_size)),
                    "mtime": int(stat.st_mtime),
                }
            )
        return {
            "ok": True,
            "root": root_name,
            "base_path": str(base),
            "path": str(target.relative_to(base)).replace("\\", "/") if target != base else "",
            "recursive": bool(recursive),
            "limit": limit_i,
            "items": items,
        }

    def read_file(self, root_name: str, rel_path: str, *, max_bytes: int = 200_000) -> dict[str, Any]:
        base, path = self._resolve(root_name, rel_path)
        if not path.exists():
            raise FileNotFoundError(f"artifact file not found: {path}")
        if path.is_dir():
            raise IsADirectoryError(f"artifact path is a directory: {path}")
        raw = path.read_bytes()
        truncated = len(raw) > int(max_bytes)
        raw = raw[: int(max_bytes)]
        try:
            text = raw.decode("utf-8")
            encoding = "utf-8"
        except UnicodeDecodeError:
            text = raw.decode("utf-8", errors="replace")
            encoding = "utf-8-replace"
        parsed_json = None
        if path.suffix.lower() == ".json":
            try:
                parsed_json = json.loads(text)
            except Exception:
                parsed_json = None
        return {
            "ok": True,
            "root": root_name,
            "path": str(path.relative_to(base)).replace("\\", "/"),
            "absolute_path": str(path),
            "size_bytes": path.stat().st_size,
            "truncated": truncated,
            "max_bytes": int(max_bytes),
            "encoding": encoding,
            "text": text,
            "json": parsed_json,
        }

    def resolve_download_path(self, root_name: str, rel_path: str) -> Path:
        _base, path = self._resolve(root_name, rel_path)
        if not path.exists():
            raise FileNotFoundError(f"artifact file not found: {path}")
        if path.is_dir():
            raise IsADirectoryError(f"artifact path is a directory: {path}")
        return path

    def _resolve(self, root_name: str, rel_path: str) -> tuple[Path, Path]:
        name = str(root_name or "").strip()
        if name not in self._roots:
            raise ValueError(f"unknown artifact root: {name}")
        base = self._roots[name]
        rel_clean = str(rel_path or "").replace("\\", "/").lstrip("/")
        target = (base / rel_clean).resolve()
        try:
            target.relative_to(base)
        except Exception as exc:
            raise ValueError("artifact path escapes root") from exc
        return base, target

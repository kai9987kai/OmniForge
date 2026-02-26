from __future__ import annotations

import fnmatch
import re
from pathlib import Path
from typing import Any, Iterable


class LocalWorkspaceSearch:
    def __init__(self, roots: dict[str, Path]):
        self._roots = {str(k): Path(v).resolve() for k, v in roots.items()}
        self._ignore_dirs = {
            ".git",
            "__pycache__",
            ".venv",
            "venv",
            "node_modules",
            "dist",
            "build",
            ".next",
            ".idea",
            ".vscode",
        }

    def roots(self) -> dict[str, Any]:
        return {
            "ok": True,
            "roots": [
                {"name": name, "path": str(path), "exists": path.exists()}
                for name, path in sorted(self._roots.items())
            ],
        }

    def find_files(
        self,
        query: str,
        *,
        root: str | None = None,
        glob: str | None = None,
        extensions: list[str] | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        q = str(query or "").strip().lower()
        if not q:
            raise ValueError("query is required")
        exts = _normalize_exts(extensions)
        hits: list[dict[str, Any]] = []
        scanned = 0
        for root_name, base in self._iter_roots(root):
            for path in self._walk_files(base):
                scanned += 1
                if exts and path.suffix.lower() not in exts:
                    continue
                rel = str(path.relative_to(base)).replace("\\", "/")
                if glob and not fnmatch.fnmatch(rel, glob):
                    continue
                hay = (path.name + " " + rel).lower()
                if q not in hay:
                    continue
                try:
                    st = path.stat()
                    size = int(st.st_size)
                except Exception:
                    size = None
                hits.append(
                    {
                        "root": root_name,
                        "path": rel,
                        "name": path.name,
                        "size": size,
                    }
                )
                if len(hits) >= max(1, int(limit)):
                    return {"ok": True, "query": q, "scanned_files": scanned, "count": len(hits), "items": hits}
        return {"ok": True, "query": q, "scanned_files": scanned, "count": len(hits), "items": hits}

    def search_text(
        self,
        pattern: str,
        *,
        root: str | None = None,
        case_sensitive: bool = False,
        regex: bool = False,
        glob: str | None = None,
        extensions: list[str] | None = None,
        limit_matches: int = 100,
        max_file_bytes: int = 1_500_000,
        context_lines: int = 1,
    ) -> dict[str, Any]:
        raw_pat = str(pattern or "")
        if not raw_pat:
            raise ValueError("pattern is required")
        flags = 0 if case_sensitive else re.IGNORECASE
        if regex:
            compiled = re.compile(raw_pat, flags)
        else:
            compiled = None
            pat_cmp = raw_pat if case_sensitive else raw_pat.lower()
        exts = _normalize_exts(extensions)
        hits: list[dict[str, Any]] = []
        scanned = 0
        ctx = max(0, int(context_lines))

        for root_name, base in self._iter_roots(root):
            for path in self._walk_files(base):
                scanned += 1
                if exts and path.suffix.lower() not in exts:
                    continue
                rel = str(path.relative_to(base)).replace("\\", "/")
                if glob and not fnmatch.fnmatch(rel, glob):
                    continue
                try:
                    st = path.stat()
                    if st.st_size > int(max_file_bytes):
                        continue
                    data = path.read_bytes()
                except Exception:
                    continue
                if b"\x00" in data[:4096]:
                    continue
                try:
                    text = data.decode("utf-8")
                except UnicodeDecodeError:
                    text = data.decode("utf-8", errors="replace")
                lines = text.splitlines()
                for i, line in enumerate(lines):
                    matched = False
                    if regex:
                        matched = compiled.search(line) is not None  # type: ignore[union-attr]
                    else:
                        hay = line if case_sensitive else line.lower()
                        matched = pat_cmp in hay
                    if not matched:
                        continue
                    start = max(0, i - ctx)
                    end = min(len(lines), i + ctx + 1)
                    snippet = "\n".join(lines[start:end])
                    hits.append(
                        {
                            "root": root_name,
                            "path": rel,
                            "line": i + 1,
                            "snippet": snippet[:4000],
                        }
                    )
                    if len(hits) >= max(1, int(limit_matches)):
                        return {
                            "ok": True,
                            "pattern": raw_pat,
                            "regex": bool(regex),
                            "case_sensitive": bool(case_sensitive),
                            "scanned_files": scanned,
                            "count": len(hits),
                            "items": hits,
                        }
        return {
            "ok": True,
            "pattern": raw_pat,
            "regex": bool(regex),
            "case_sensitive": bool(case_sensitive),
            "scanned_files": scanned,
            "count": len(hits),
            "items": hits,
        }

    def _iter_roots(self, root: str | None) -> Iterable[tuple[str, Path]]:
        if root:
            name = str(root).strip()
            if name not in self._roots:
                raise ValueError(f"unknown search root: {name}")
            base = self._roots[name]
            if base.exists():
                yield name, base
            return
        for name, base in sorted(self._roots.items()):
            if base.exists():
                yield name, base

    def _walk_files(self, base: Path) -> Iterable[Path]:
        stack = [base]
        while stack:
            cur = stack.pop()
            try:
                entries = list(cur.iterdir())
            except Exception:
                continue
            for e in entries:
                if e.is_dir():
                    if e.name in self._ignore_dirs:
                        continue
                    stack.append(e)
                elif e.is_file():
                    yield e


def _normalize_exts(extensions: list[str] | None) -> set[str]:
    out: set[str] = set()
    for e in extensions or []:
        s = str(e).strip().lower()
        if not s:
            continue
        if not s.startswith("."):
            s = "." + s
        out.add(s)
    return out

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


class NeuroDSLBridge:
    def __init__(self, repo_dir: Path):
        self.repo_dir = Path(repo_dir).resolve()
        self._lock = threading.RLock()
        self._agent_proc: subprocess.Popen[str] | None = None
        self._agent_host = "127.0.0.1"
        self._agent_port = 8860
        self._agent_device = "auto"
        self._agent_stdout_path = self.repo_dir / "_omniforge_agent_api_stdout.log"
        self._agent_stderr_path = self.repo_dir / "_omniforge_agent_api_stderr.log"
        self._agent_stdout_fh = None
        self._agent_stderr_fh = None

    def _run(self, *args: str, timeout_sec: float = 90.0) -> dict[str, Any]:
        cmd = [sys.executable, "omni_cli.py", *args]
        proc = subprocess.run(
            cmd,
            cwd=self.repo_dir,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            check=False,
        )
        stdout = (proc.stdout or "").strip()
        stderr = (proc.stderr or "").strip()
        parsed: Any = None
        if stdout:
            try:
                parsed = json.loads(stdout)
            except Exception:
                parsed = stdout
        return {
            "ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "command": cmd,
            "stdout": stdout,
            "stderr": stderr,
            "json": parsed if isinstance(parsed, (dict, list)) else None,
            "parsed": parsed,
        }

    def status(self) -> dict[str, Any]:
        return {
            "repo_exists": self.repo_dir.exists(),
            "omni_cli_exists": (self.repo_dir / "omni_cli.py").exists(),
        }

    def devices(self) -> dict[str, Any]:
        return self._run("devices")

    def agent_manifest(self) -> dict[str, Any]:
        return self._run("agent-manifest")

    def platform_health(self) -> dict[str, Any]:
        return self._run("platform-health")

    def _agent_base_url(self) -> str:
        return f"http://{self._agent_host}:{int(self._agent_port)}"

    def _agent_proc_status_locked(self) -> dict[str, Any]:
        proc = self._agent_proc
        running = proc is not None and proc.poll() is None
        return {
            "running": running,
            "pid": (proc.pid if proc is not None else None),
            "returncode": (proc.poll() if proc is not None else None),
            "host": self._agent_host,
            "port": int(self._agent_port),
            "device": self._agent_device,
            "base_url": self._agent_base_url(),
            "stdout_log": str(self._agent_stdout_path),
            "stderr_log": str(self._agent_stderr_path),
        }

    def agent_api_status(self) -> dict[str, Any]:
        with self._lock:
            status = self._agent_proc_status_locked()
        # Try a lightweight health probe if running.
        if status.get("running"):
            try:
                probe = self.proxy_agent_api("GET", "/health", timeout_sec=2.0)
                status["health_probe"] = {"ok": probe.get("ok"), "status": probe.get("status_code"), "json": probe.get("json")}
            except Exception as exc:
                status["health_probe_error"] = str(exc)
        return status

    def start_agent_api(
        self,
        *,
        host: str = "127.0.0.1",
        port: int = 8860,
        device: str = "auto",
        wait_sec: float = 15.0,
    ) -> dict[str, Any]:
        with self._lock:
            if self._agent_proc is not None and self._agent_proc.poll() is None:
                status = self._agent_proc_status_locked()
                status["already_running"] = True
                status["requested"] = {"host": host, "port": int(port), "device": device}
                return status

            self._agent_host = host
            self._agent_port = int(port)
            self._agent_device = device
            self._agent_stdout_path.parent.mkdir(parents=True, exist_ok=True)
            self._agent_stdout_fh = open(self._agent_stdout_path, "a", encoding="utf-8", errors="replace")
            self._agent_stderr_fh = open(self._agent_stderr_path, "a", encoding="utf-8", errors="replace")
            cmd = [
                sys.executable,
                "omni_cli.py",
                "serve-agent-api",
                "--host",
                str(host),
                "--port",
                str(int(port)),
                "--device",
                str(device),
            ]
            self._agent_proc = subprocess.Popen(
                cmd,
                cwd=self.repo_dir,
                stdout=self._agent_stdout_fh,
                stderr=self._agent_stderr_fh,
                text=True,
            )
            status = self._agent_proc_status_locked()
            status["command"] = cmd

        deadline = time.time() + max(1.0, float(wait_sec))
        last_err: str | None = None
        while time.time() < deadline:
            with self._lock:
                proc = self._agent_proc
                if proc is None:
                    break
                if proc.poll() is not None:
                    status = self._agent_proc_status_locked()
                    status["ok"] = False
                    status["error"] = "serve-agent-api exited early"
                    return status
            try:
                probe = self.proxy_agent_api("GET", "/health", timeout_sec=2.0)
                if not probe.get("ok"):
                    last_err = str(probe.get("error") or probe.get("text") or "health probe failed")
                    time.sleep(0.3)
                    continue
                status = self.agent_api_status()
                status["ok"] = True
                status["health"] = probe.get("json") or probe.get("text")
                return status
            except Exception as exc:
                last_err = str(exc)
                time.sleep(0.3)

        status = self.agent_api_status()
        status["ok"] = False
        status["error"] = f"agent API did not become healthy within {wait_sec}s"
        if last_err:
            status["last_error"] = last_err
        return status

    def stop_agent_api(self, *, timeout_sec: float = 8.0, force: bool = True) -> dict[str, Any]:
        with self._lock:
            proc = self._agent_proc
            if proc is None:
                return {"ok": True, "running": False, "message": "not running"}
            if proc.poll() is None:
                try:
                    proc.terminate()
                except Exception:
                    pass
            pid = proc.pid

        deadline = time.time() + max(0.5, float(timeout_sec))
        while time.time() < deadline:
            with self._lock:
                proc = self._agent_proc
                if proc is None:
                    break
                if proc.poll() is not None:
                    break
            time.sleep(0.2)

        with self._lock:
            proc = self._agent_proc
            if proc is not None and proc.poll() is None and force:
                try:
                    proc.kill()
                except Exception:
                    pass
            rc = proc.poll() if proc is not None else None
            self._agent_proc = None
            for fh_name in ("_agent_stdout_fh", "_agent_stderr_fh"):
                fh = getattr(self, fh_name, None)
                if fh is not None:
                    try:
                        fh.flush()
                        fh.close()
                    except Exception:
                        pass
                setattr(self, fh_name, None)

        return {
            "ok": True,
            "running": False,
            "pid": pid,
            "returncode": rc,
            "base_url": self._agent_base_url(),
        }

    def proxy_agent_api(
        self,
        method: str,
        path: str,
        body: Any | None = None,
        *,
        timeout_sec: float = 20.0,
    ) -> dict[str, Any]:
        if not path.startswith("/"):
            path = "/" + path
        url = self._agent_base_url() + path
        data_bytes = None
        headers = {"Accept": "application/json"}
        if body is not None:
            data_bytes = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data_bytes, method=method.upper(), headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=float(timeout_sec)) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                parsed = None
                try:
                    parsed = json.loads(raw)
                except Exception:
                    parsed = None
                return {
                    "ok": True,
                    "status_code": int(resp.getcode()),
                    "url": url,
                    "method": method.upper(),
                    "json": parsed,
                    "text": raw,
                }
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            parsed = None
            try:
                parsed = json.loads(raw)
            except Exception:
                parsed = None
            return {
                "ok": False,
                "status_code": int(exc.code),
                "url": url,
                "method": method.upper(),
                "json": parsed,
                "text": raw,
                "error": str(exc),
            }
        except Exception as exc:
            return {
                "ok": False,
                "status_code": None,
                "url": url,
                "method": method.upper(),
                "error": str(exc),
            }

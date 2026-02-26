from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any


class WindhawkBridge:
    def __init__(self, install_dir: Path, program_data_dir: Path, project_mod_path: Path):
        self.install_dir = Path(install_dir)
        self.program_data_dir = Path(program_data_dir)
        self.project_mod_path = Path(project_mod_path)
        self.exe = self.install_dir / "windhawk.exe"
        self.ini = self.install_dir / "windhawk.ini"
        self.template = self.install_dir / "UI" / "resources" / "app" / "extensions" / "windhawk" / "files" / "mod_template.wh.cpp"
        self.project_mod_template = self.project_mod_path.parent / "omniforge_hotkey.template.wh.cpp"

    def _mods_writable_dir(self) -> Path:
        return self.program_data_dir / "Engine" / "ModsWritable" / "mod-task"

    def status(self) -> dict[str, Any]:
        return {
            "installed": self.install_dir.exists() and self.exe.exists(),
            "windhawk_dir": str(self.install_dir),
            "exe": str(self.exe),
            "ini_exists": self.ini.exists(),
            "program_data": str(self.program_data_dir),
            "mods_writable_dir": str(self._mods_writable_dir()),
            "mods_writable_exists": self._mods_writable_dir().exists(),
            "windhawk_template_exists": self.template.exists(),
            "project_template_exists": self.project_mod_template.exists(),
            "project_mod_path": str(self.project_mod_path),
        }

    def restart(self, tray_only: bool = True) -> dict[str, Any]:
        if not self.exe.exists():
            return {"ok": False, "error": f"Windhawk not found: {self.exe}"}
        cmd = [str(self.exe), "-restart"]
        if tray_only:
            cmd.append("-tray-only")
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        return {
            "ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "command": cmd,
            "stdout": (proc.stdout or "").strip(),
            "stderr": (proc.stderr or "").strip(),
        }

    def _render_template(self, url: str) -> str:
        if self.project_mod_template.exists():
            text = self.project_mod_template.read_text(encoding="utf-8", errors="ignore")
            return text.replace("__OMNIFORGE_URL__", url.replace("\\", "/"))
        # Minimal valid fallback.
        return f"""// ==WindhawkMod==
// @id              omniforge-dashboard-hotkey
// @name            OmniForge Dashboard Hotkey
// @description     Open the local OmniForge AI dashboard with Win+Shift+A
// @version         0.1
// @author          kai99 + Codex
// @include         explorer.exe
// @compilerOptions -lshell32
// @license         MIT
// ==/WindhawkMod==

// ==WindhawkModReadme==
/*
# OmniForge Dashboard Hotkey
Press Win+Shift+A in Explorer to open the OmniForge dashboard.
*/
// ==/WindhawkModReadme==

#include <windows.h>
#include <shellapi.h>

BOOL Wh_ModInit() {{
    ShellExecuteW(nullptr, L"open", L"{url}", nullptr, nullptr, SW_SHOWNORMAL);
    return TRUE;
}}

void Wh_ModUninit() {{}}
"""

    def generate_hotkey_mod(
        self,
        *,
        host: str = "127.0.0.1",
        port: int = 8787,
        path: str = "/",
        copy_to_windhawk: bool = True,
    ) -> dict[str, Any]:
        self.project_mod_path.parent.mkdir(parents=True, exist_ok=True)
        url = f"http://{host}:{int(port)}{path}"
        source = self._render_template(url)
        self.project_mod_path.write_text(source, encoding="utf-8")

        copied_to = None
        if copy_to_windhawk:
            dest_dir = self._mods_writable_dir()
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest_file = dest_dir / self.project_mod_path.name
            dest_file.write_text(source, encoding="utf-8")
            copied_to = str(dest_file)

        return {
            "ok": True,
            "project_mod_path": str(self.project_mod_path),
            "copied_to": copied_to,
            "url": url,
        }


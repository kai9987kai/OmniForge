// ==WindhawkMod==
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

Press Win+Shift+A (default) while Explorer is running to open the local
OmniForge dashboard URL.

The dashboard URL is configurable in settings.
*/
// ==/WindhawkModReadme==

// ==WindhawkModSettings==
/*
- hotkey:
  - win: true
  - shift: true
  - ctrl: false
  - alt: false
  - vk: 65
  $name: Hotkey (Win+Shift+A by default)
- url: "http://127.0.0.1:8787/"
  $name: Dashboard URL
  $description: URL opened when the hotkey is pressed.
*/
// ==/WindhawkModSettings==

#include <windows.h>
#include <shellapi.h>

static HANDLE g_thread = nullptr;
static DWORD g_threadId = 0;
static volatile bool g_running = false;
static UINT g_hotkeyModifiers = MOD_WIN | MOD_SHIFT;
static UINT g_hotkeyVk = 'A';
static wchar_t g_url[512] = L"http://127.0.0.1:8787/";

DWORD WINAPI HotkeyThreadProc(LPVOID) {
    if (!RegisterHotKey(nullptr, 1, g_hotkeyModifiers, g_hotkeyVk)) {
        Wh_Log(L"RegisterHotKey failed: %lu", GetLastError());
        return 1;
    }

    MSG msg;
    while (g_running) {
        BOOL ok = GetMessageW(&msg, nullptr, 0, 0);
        if (ok <= 0) {
            break;
        }
        if (msg.message == WM_HOTKEY && msg.wParam == 1) {
            Wh_Log(L"Opening OmniForge dashboard");
            ShellExecuteW(nullptr, L"open", g_url, nullptr, nullptr, SW_SHOWNORMAL);
        } else if (msg.message == WM_APP + 7) {
            break;
        }
    }

    UnregisterHotKey(nullptr, 1);
    return 0;
}

void LoadSettings() {
    BOOL win = Wh_GetIntSetting(L"hotkey.win") != 0;
    BOOL shift = Wh_GetIntSetting(L"hotkey.shift") != 0;
    BOOL ctrl = Wh_GetIntSetting(L"hotkey.ctrl") != 0;
    BOOL alt = Wh_GetIntSetting(L"hotkey.alt") != 0;
    int vk = Wh_GetIntSetting(L"hotkey.vk");

    UINT mods = 0;
    if (win) mods |= MOD_WIN;
    if (shift) mods |= MOD_SHIFT;
    if (ctrl) mods |= MOD_CONTROL;
    if (alt) mods |= MOD_ALT;
    if (mods == 0) mods = MOD_WIN | MOD_SHIFT;
    if (vk <= 0 || vk > 255) vk = 'A';

    g_hotkeyModifiers = mods;
    g_hotkeyVk = (UINT)vk;

    PCWSTR url = Wh_GetStringSetting(L"url");
    if (url && *url) {
        lstrcpynW(g_url, url, ARRAYSIZE(g_url));
    }
}

BOOL Wh_ModInit() {
    Wh_Log(L"OmniForge hotkey mod init");
    LoadSettings();
    g_running = true;
    g_thread = CreateThread(nullptr, 0, HotkeyThreadProc, nullptr, 0, &g_threadId);
    if (!g_thread) {
        Wh_Log(L"CreateThread failed: %lu", GetLastError());
        g_running = false;
        return FALSE;
    }
    return TRUE;
}

void Wh_ModUninit() {
    Wh_Log(L"OmniForge hotkey mod uninit");
    g_running = false;
    if (g_threadId) {
        PostThreadMessageW(g_threadId, WM_APP + 7, 0, 0);
    }
    if (g_thread) {
        WaitForSingleObject(g_thread, 2000);
        CloseHandle(g_thread);
        g_thread = nullptr;
    }
    g_threadId = 0;
}

void Wh_ModSettingsChanged() {
    Wh_Log(L"OmniForge hotkey mod settings changed");
    LoadSettings();
}


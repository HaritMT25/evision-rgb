"""Windows integration: conflicting RGB software detection and start-with-Windows."""
from __future__ import annotations

import os
import sys

import psutil

# programs that also talk to the keyboard's vendor channel and fight over it
CONFLICTS = {
    "ajazz": "AJAZZ driver (its writes override ours; close it)",
    "openrgb": "OpenRGB (sends the wrong V1 protocol to this keyboard)",
    "nexus": "Nexus RGB (runs OpenRGB-headless in the background; stop NexusService)",
    "signalrgb": "SignalRGB",
    "audiorecord": "AJAZZ AudioRecord helper",
}

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "EVisionRGB"
OLD_RUN_NAMES = ("AK820Control",)  # names used by earlier versions


def conflicts() -> list[dict]:
    out = []
    for p in psutil.process_iter(["pid", "name"]):
        n = (p.info["name"] or "").lower()
        for k, why in CONFLICTS.items():
            if k in n:
                out.append({"pid": p.info["pid"], "name": p.info["name"], "why": why,
                            "closable": k in ("ajazz", "audiorecord", "openrgb", "signalrgb")})
    return out


def close_process(pid: int) -> bool:
    """Close a conflicting program (only names from CONFLICTS; never elevated services)."""
    p = psutil.Process(pid)
    if not any(k in p.name().lower() for k in CONFLICTS):
        raise ValueError("not a known conflicting program")
    try:
        p.terminate()
    except psutil.AccessDenied:
        raise PermissionError(f"{p.name()} runs as administrator; close it from its tray icon or Task Manager")
    try:
        p.wait(3)
    except psutil.TimeoutExpired:
        p.kill()
    except psutil.AccessDenied:  # elevated process: terminate worked but we may not wait on it
        pass
    return True


def launch_command(background: bool = True) -> str:
    if getattr(sys, "frozen", False):
        cmd = f'"{sys.executable}"'
    else:
        pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        cmd = f'"{pyw if os.path.exists(pyw) else sys.executable}" -m evision_rgb.gui'
    return cmd + (" --background" if background else "")


def autostart_enabled() -> bool:
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, RUN_NAME)
            return True
    except OSError:
        return False


def set_autostart(enabled: bool) -> bool:
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        for old in OLD_RUN_NAMES:  # never leave a stale entry pointing at an old exe
            try:
                winreg.DeleteValue(k, old)
            except FileNotFoundError:
                pass
        if enabled:
            winreg.SetValueEx(k, RUN_NAME, 0, winreg.REG_SZ, launch_command(True))
        else:
            try:
                winreg.DeleteValue(k, RUN_NAME)
            except FileNotFoundError:
                pass
    return autostart_enabled()


def promote_tray_icon(exe: str | None = None) -> bool:
    """Windows 11 hides new tray icons under the ^ overflow. Make ours visible once, but never
    override a choice the user already made (only sets IsPromoted when it is absent)."""
    import winreg
    exe = os.path.normcase(exe or sys.executable)
    base = r"Control Panel\NotifyIconSettings"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, base) as root:
            for i in range(winreg.QueryInfoKey(root)[0]):
                sub = winreg.EnumKey(root, i)
                with winreg.OpenKey(root, sub, 0, winreg.KEY_READ | winreg.KEY_SET_VALUE) as k:
                    try:
                        path = winreg.QueryValueEx(k, "ExecutablePath")[0]
                    except OSError:
                        continue
                    if os.path.normcase(path) != exe:
                        continue
                    try:
                        winreg.QueryValueEx(k, "IsPromoted")
                        return False  # user already decided
                    except OSError:
                        winreg.SetValueEx(k, "IsPromoted", 0, winreg.REG_DWORD, 1)
                        return True
    except OSError:
        pass
    return False

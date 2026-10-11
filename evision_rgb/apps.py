"""App-aware lighting: switch live effect / key highlights based on the foreground program.

Profile file (JSON):
{
  "default": {"effect": "rainbow", "params": {"speed": 0.15}},
  "rules": [
    {"match": "code.exe",    "effect": "reactive", "params": {"base": "000818"}},
    {"match": "spotify.exe", "effect": "music"},
    {"match": "valorant",    "effect": "solid", "params": {"color": "100000",
                              "keys": {"W": "ff0000", "A": "ff0000", "S": "ff0000", "D": "ff0000"}}}
  ]
}
`match` is a case-insensitive substring of the process name or window title. First match wins.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import json
import os
import threading
import time

import psutil

from .effects import Engine

_user32 = ctypes.WinDLL("user32") if os.name == "nt" else None


def foreground() -> tuple[str, str]:
    """(process name, window title) of the foreground window (Windows only; ('', '') elsewhere)."""
    if _user32 is None:
        return "", ""
    hwnd = _user32.GetForegroundWindow()
    pid = wt.DWORD()
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    buf = ctypes.create_unicode_buffer(512)
    _user32.GetWindowTextW(hwnd, buf, 512)
    try:
        name = psutil.Process(pid.value).name()
    except (psutil.Error, ValueError):
        name = ""
    return name, buf.value


class AppProfiles:
    def __init__(self, engine: Engine, profile: dict, poll_s: float = 0.5):
        self.engine, self.profile, self.poll = engine, profile, poll_s
        self.current: dict | None = None
        self.app = ""
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    @classmethod
    def from_file(cls, engine: Engine, path: str) -> "AppProfiles":
        with open(path, encoding="utf-8") as f:
            return cls(engine, json.load(f))

    def pick(self, name: str, title: str) -> dict | None:
        hay = f"{name}\n{title}".lower()
        for rule in self.profile.get("rules", []):
            if rule["match"].lower() in hay:
                return rule
        return self.profile.get("default")

    def _apply(self, rule: dict | None):
        if rule is None or not rule.get("effect"):  # keep the keyboard's own (hardware) effect
            self.engine.stop()
        else:
            self.engine.run(rule["effect"], **rule.get("params", {}))

    def _run(self):
        while not self._stop.is_set():
            name, title = foreground()
            rule = self.pick(name, title)
            if rule is not self.current:
                self.current, self.app = rule, name
                try:
                    self._apply(rule)
                except Exception as e:  # bad rule: log and keep watching
                    print(f"profile error for {name}: {e!r}")
            self._stop.wait(self.poll)

    def start(self):
        self._thread.start()
        return self

    def stop(self):
        self._stop.set()
        self._thread.join(2)

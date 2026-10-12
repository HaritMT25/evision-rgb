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


def check(cfg) -> dict:
    """Refuse app profile settings with a rule the app could not run (raises ValueError naming it): every rule needs
    a match, and the default's and the rules' effects are built with their options (nothing is started). An effect
    of null means the keyboard's own effect."""
    from .profiles import validate_live
    if not isinstance(cfg, dict) or not isinstance(cfg.get("rules", []), list):
        raise ValueError('app profiles look like {"default": {...} or null, "rules": [{"match": ..., "effect": ...}]}')
    parts = [("the default", cfg.get("default"))]
    for n, r in enumerate(cfg.get("rules", []), 1):
        m = r.get("match") if isinstance(r, dict) else None
        if not isinstance(m, str) or not m.strip():
            raise ValueError(f"rule {n}: needs a match (part of a program's name or window title)")
        parts.append((f"rule {n} ({m})", r))
    for where, part in parts:
        if part is None:
            continue
        if not isinstance(part, dict):
            raise ValueError(f"{where}: must be an object")
        if part.get("effect"):
            try:
                validate_live({"effect": part["effect"], "params": part.get("params") or {}})
            except ValueError as e:
                raise ValueError(f"{where}: {e}") from None
    return cfg


class AppProfiles:
    def __init__(self, engine: Engine, profile: dict, poll_s: float = 0.5, on_error=None):
        """on_error(app, exception) for a rule that fails (default: printed)."""
        self.engine, self.profile, self.poll = engine, profile, poll_s
        self.on_error = on_error or (lambda app, e: print(f"profile error for {app}: {e!r}"))
        self.current: dict | None = None
        self._applied = False  # nothing applied yet: the first look applies even "the keyboard's own effect" (None)
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
            m = rule.get("match") if isinstance(rule, dict) else None
            if isinstance(m, str) and m.strip() and m.lower() in hay:  # (a rule without one matches nothing)
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
            if rule is not self.current or not self._applied:
                self.current, self.app, self._applied = rule, name, True
                try:
                    self._apply(rule)
                except Exception as e:  # bad rule: report it and keep watching
                    self.on_error(name, e)
            self._stop.wait(self.poll)

    def start(self):
        self._thread.start()
        return self

    def stop(self):
        self._stop.set()
        self._thread.join(2)

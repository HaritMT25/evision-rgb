"""Smart indicators & automation, layered over whatever the keyboard shows.

Order of composition each frame:
  effect output → night dimming → timer / notification overlays → indicators (Caps Lock, mic mute)
  → idle / lock dimming (applies to everything)

Inputs come from probes (callables) so behaviour is testable; `default_probes()` reads Windows.
"""
from __future__ import annotations

import copy
import datetime as dt
import os
import re
import threading
import time

import numpy as np

from .effects import INDEX, N, X, Y
from .keyboard import parse_color

DEFAULTS = {
    "caps_lock": {"enabled": True, "key": "CapsLock", "color": "ffffff"},
    "mic_mute": {"enabled": False, "key": "F4", "color": "ff0000"},
    "idle": {"enabled": False, "after_min": 10, "brightness": 0.0},
    "lock": {"enabled": True, "brightness": 0.0},
    "night": {"enabled": False, "start": "22:00", "end": "07:00", "brightness": 0.3},
    "timer": {"color": "00c0ff", "done_color": "ff3000"},
}
NUMBER_ROW = np.array([i for i in range(N) if 0.1 < Y[i] < 0.35])
_HHMM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


# ------------------------------------------------------------------------------ probes (Windows)
def default_probes() -> dict:
    if os.name != "nt":
        return {"caps": lambda: False, "mic_muted": lambda: False, "idle": lambda: 0.0,
                "locked": lambda: False, "now": dt.datetime.now}
    import ctypes
    import ctypes.wintypes as wt
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32

    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", wt.UINT), ("dwTime", wt.DWORD)]

    def caps():
        return bool(user32.GetKeyState(0x14) & 1)

    def idle():
        li = LASTINPUTINFO(ctypes.sizeof(LASTINPUTINFO), 0)
        if not user32.GetLastInputInfo(ctypes.byref(li)):
            return 0.0
        return ((kernel32.GetTickCount() - li.dwTime) & 0xFFFFFFFF) / 1000.0

    def locked():
        h = user32.OpenInputDesktop(0, False, 0x0100)  # fails on the secure (lock) desktop
        if not h:
            return True
        user32.CloseDesktop(h)
        return False

    return {"caps": caps, "mic_muted": _MicProbe(), "idle": idle, "locked": locked, "now": dt.datetime.now}


class _MicProbe:
    """Default capture device mute state via Core Audio (pycaw); False if unavailable."""

    def __init__(self):
        self.vol = None
        self.failed = False
        self.thread = None

    def __call__(self) -> bool:
        if self.failed:
            return False
        try:
            if self.vol is None or self.thread is not threading.current_thread():
                import comtypes
                from ctypes import POINTER, cast
                from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
                comtypes.CoInitialize()
                mic = AudioUtilities.GetMicrophone()
                iface = mic.Activate(IAudioEndpointVolume._iid_, comtypes.CLSCTX_ALL, None)
                self.vol, self.thread = cast(iface, POINTER(IAudioEndpointVolume)), threading.current_thread()
            return bool(self.vol.GetMute())
        except Exception:
            self.failed = True  # no pycaw / no microphone: indicator stays off
            return False


# ------------------------------------------------------------------------------ automation
def _merge(base: dict, patch: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (patch or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = {**out[k], **v}
        else:
            out[k] = v
    return out


def validate(cfg: dict) -> dict:
    for section in ("caps_lock", "mic_mute"):
        if cfg[section]["key"].lower() not in INDEX:
            raise ValueError(f"{section}: unknown key {cfg[section]['key']!r}")
        parse_color(cfg[section]["color"])
    for section in ("idle", "lock", "night"):
        b = float(cfg[section]["brightness"])
        if not 0.0 <= b <= 1.0:
            raise ValueError(f"{section}: brightness must be 0..1")
    if float(cfg["idle"]["after_min"]) <= 0:
        raise ValueError("idle: after_min must be > 0")
    for f in ("start", "end"):
        if not _HHMM.match(str(cfg["night"][f])):
            raise ValueError(f"night: {f} must be HH:MM")
    parse_color(cfg["timer"]["color"]); parse_color(cfg["timer"]["done_color"])
    return cfg


def _rgb(c) -> np.ndarray:
    return np.array(parse_color(c), dtype=float) / 255.0


class Automation:
    DONE_SECS = 2.0

    def __init__(self, config: dict | None = None, probes: dict | None = None):
        self.cfg = validate(_merge(DEFAULTS, config or {}))
        given = dict(probes or {})
        keys = ("caps", "mic_muted", "idle", "locked", "now")
        self.probes = given if all(k in given for k in keys) else {**default_probes(), **given}
        self.lock = threading.Lock()
        self.flashes: list[dict] = []
        self.timer: dict | None = None
        self.done_until = -1e9

    # -- configuration
    def configure(self, patch: dict) -> dict:
        cfg = validate(_merge(self.cfg, patch))
        with self.lock:
            self.cfg = cfg
        return cfg

    # -- notifications
    def notify(self, color="ffffff", keys=None, flashes: int = 3, period: float = 0.35, t: float | None = None):
        idx = None if keys is None else [INDEX[k.lower()] for k in keys]  # KeyError for unknown keys
        with self.lock:
            self.flashes.append({"t0": time.monotonic() if t is None else t, "rgb": _rgb(color), "idx": idx,
                                 "n": max(1, int(flashes)), "period": max(0.05, float(period))})

    # -- timer
    def start_timer(self, minutes: float, label: str = "", t: float | None = None):
        if float(minutes) <= 0:
            raise ValueError("minutes must be > 0")
        with self.lock:
            self.timer = {"t0": time.monotonic() if t is None else t, "dur": float(minutes) * 60, "label": label}

    def cancel_timer(self):
        with self.lock:
            self.timer = None
            self.done_until = -1e9

    def timer_status(self, t: float | None = None) -> dict | None:
        t = time.monotonic() if t is None else t
        tm = self.timer
        if not tm or t - tm["t0"] >= tm["dur"]:
            return None
        return {"label": tm["label"], "total_s": tm["dur"], "remaining_s": tm["dur"] - (t - tm["t0"]),
                "progress": (t - tm["t0"]) / tm["dur"]}

    # -- state
    def _tick(self, t: float):
        tm = self.timer
        if tm and t - tm["t0"] >= tm["dur"]:
            self.done_until = tm["t0"] + tm["dur"] + self.DONE_SECS
            self.timer = None
        self.flashes = [f for f in self.flashes if t - f["t0"] < f["n"] * f["period"]]

    def _night_factor(self) -> float:
        n = self.cfg["night"]
        if not n["enabled"]:
            return 1.0
        now = self.probes["now"]()
        cur = now.hour * 60 + now.minute
        s = int(n["start"][:2]) * 60 + int(n["start"][3:])
        e = int(n["end"][:2]) * 60 + int(n["end"][3:])
        inside = s <= cur < e if s <= e else (cur >= s or cur < e)
        return float(n["brightness"]) if inside else 1.0

    def _power_factor(self) -> float:
        f = 1.0
        if self.cfg["lock"]["enabled"] and self.probes["locked"]():
            f = min(f, float(self.cfg["lock"]["brightness"]))
        idle = self.cfg["idle"]
        if idle["enabled"] and self.probes["idle"]() >= float(idle["after_min"]) * 60:
            f = min(f, float(idle["brightness"]))
        return f

    def _indicators(self) -> list[tuple[int, np.ndarray]]:
        out = []
        c = self.cfg
        if c["caps_lock"]["enabled"] and self.probes["caps"]():
            out.append((INDEX[c["caps_lock"]["key"].lower()], _rgb(c["caps_lock"]["color"])))
        if c["mic_mute"]["enabled"] and self.probes["mic_muted"]():
            out.append((INDEX[c["mic_mute"]["key"].lower()], _rgb(c["mic_mute"]["color"])))
        return out

    def active(self, t: float | None = None) -> bool:
        """True while automation needs control of the LEDs."""
        t = time.monotonic() if t is None else t
        with self.lock:
            self._tick(t)
            return bool(self.flashes or self.timer or t < self.done_until or self._indicators()
                        or self._power_factor() < 1.0 or self._night_factor() < 1.0)

    def apply(self, colors, t: float) -> np.ndarray:
        out = np.array(colors, dtype=float, copy=True)
        with self.lock:
            self._tick(t)
            out *= self._night_factor()
            tm = self.timer
            if tm:
                xs = X[NUMBER_ROW]
                pos = (xs - xs.min()) / (xs.max() - xs.min())
                prog = (t - tm["t0"]) / tm["dur"]
                lit = NUMBER_ROW[pos <= prog + 1e-9]
                out[lit] = _rgb(self.cfg["timer"]["color"])
            elif t < self.done_until and ((t - (self.done_until - self.DONE_SECS)) % 0.5) < 0.3:
                out[:] = _rgb(self.cfg["timer"]["done_color"])
            for f in self.flashes:
                if ((t - f["t0"]) % f["period"]) < f["period"] / 2:
                    if f["idx"] is None:
                        out[:] = f["rgb"]
                    else:
                        out[f["idx"]] = f["rgb"]
            for i, rgb in self._indicators():
                out[i] = rgb
            out *= self._power_factor()
        return out

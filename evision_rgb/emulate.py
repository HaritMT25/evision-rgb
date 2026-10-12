"""PC-side emulation of the keyboard's built-in (firmware) effects.

The firmware renders its animations internally and the host cannot read them back, so the app
approximates each one: for the on-screen preview, and as the base layer under automation overlays
(Caps Lock on a rainbow effect stays a rainbow). The look is an approximation, not pixel-exact.
"""
from __future__ import annotations

import math
import threading
import time

import numpy as np

from .effects import EFFECTS, INDEX, N, X, Y, Effect, KeyEvents, hsv, rgbf
from .protocol import MODES, ProtocolError

ID = {v: k for k, v in MODES.items()}
RATE = (2.0, 1.4, 1.0, 0.7, 0.45)  # device speed 0 (fastest) .. 4 (slowest)
RAINBOW = {"corrugated", "spectrum", "wave", "roll", "serpentine", "speed", "cloud"}
YY = Y * 0.35  # physical aspect: rows are closer together than columns are wide


class Emulated(Effect):
    """Approximate a firmware effect from its config block fields."""
    name = "emulate"
    fps = 30

    def __init__(self, mode: int = 6, brightness: int = 4, speed: int = 2, direction: int = 0, random: int = 0,
                 color: str = "ffffff", custom: dict | None = None, seed: int = 7, dim: bool = True,
                 variant: int = 0):
        self.mode = ID.get(int(mode), "normal")
        # dim=True: show the brightness setting (on-screen preview). Under overlays the keyboard applies
        # its own brightness, so the base is streamed at full level (dim=False), and as LED values: the stored
        # colours are LED values (like the exact engine's output), not colours for the live-effect gamma.
        self.level = 0.2 + 0.8 * max(0, min(4, int(brightness))) / 4 if dim else 1.0
        self.raw = not dim
        self.rate = RATE[max(0, min(4, int(speed)))]
        self.x = (1 - X) if int(direction) == 1 else X
        self.random = bool(random)
        self.rgb = rgbf(color)
        self.variant = int(variant)
        self.hue0 = self._hue(self.rgb)
        self.custom = np.zeros((N, 3))
        for k, c in (custom or {}).items():
            if k.lower() in INDEX:
                self.custom[INDEX[k.lower()]] = rgbf(c)
        self.rng = np.random.default_rng(seed)
        self.pressed = np.full(N, -1e9)
        self.press_xy: list[tuple[float, float, float]] = []
        self.lock = threading.Lock()
        self.events = KeyEvents(self.press) if self.mode in ("reaction", "ripples", "traverse") else None
        self.sparks = np.zeros(N)
        self.drops: list[list[float]] = []  # rain: [column x, head y, speed, hue]
        self.last_t = 0.0

    @staticmethod
    def _hue(rgb) -> float:
        import colorsys
        return colorsys.rgb_to_hsv(*rgb)[0]

    # reactive modes need key presses (preview: none; automation base: real keyboard hook)
    def press(self, name: str):
        i = INDEX[name.lower()]
        with self.lock:
            self.pressed[i] = time.monotonic()
            self.press_xy.append((time.monotonic(), X[i], YY[i]))

    def start(self):
        if self.events:
            self.events.start()

    def stop(self):
        if self.events:
            self.events.stop()

    def _colour(self, t: float, k: float = 0.0) -> np.ndarray:
        """The effect's colour; cycles through hues when the random flag is set."""
        if not self.random:
            return self.rgb
        return hsv(np.array([self.hue0 + 0.37 * math.floor(k)]))[0]

    def render(self, t: float) -> np.ndarray:
        r, x, m = self.rate, self.x, self.mode
        dt = max(0.0, t - self.last_t)
        self.last_t = t
        if m == "custom":
            out = self.custom
        elif m == "normal":
            out = np.tile(self._colour(t, t * r / 4), (N, 1))
        elif m == "breath":
            period = 4.0 / r
            v = 0.5 - 0.5 * math.cos(2 * math.pi * t / period)
            out = np.tile(self._colour(t, t / period) * v, (N, 1))
        elif m == "corrugated":
            out = hsv(x * 0.9 - t * 0.35 * r)
        elif m == "wave":
            out = hsv(x * 0.6 + YY * 0.8 - t * 0.3 * r)
        elif m == "spectrum":
            out = hsv((x + YY) * 0.7 - t * 0.25 * r)
        elif m == "roll":
            out = hsv(Y * 0.8 - t * 0.3 * r)
        elif m == "speed":  # observed: colour pulses enter from one edge and ripple across, gaps between
            period, v = 0.7 / r, 1.2 * r
            out = np.zeros((N, 3))
            for k in range(int(t // period) - 2, int(t // period) + 1):
                if k < 0:
                    continue
                pos = (t - k * period) * v
                band = np.exp(-((x - pos) / 0.06) ** 2)
                out = np.maximum(out, band[:, None] * hsv(np.array([self.hue0 + 0.17 * k]))[0])
        elif m == "serpentine":
            path = np.where((np.round(Y * 5) % 2) == 0, x, 1 - x) / 6 + np.round(Y * 5) / 6
            out = hsv(path * 1.5 - t * 0.3 * r)
        elif m == "cloud":
            n = (np.sin(x * 6 + t * 0.7 * r) + np.sin(YY * 14 - t * 0.5 * r) + np.sin((x + YY) * 4 + t * 0.3 * r)) / 3
            out = hsv(0.5 + n * 0.5 + t * 0.05 * r, 1.0, 0.6 + 0.4 * np.abs(n))
        elif m == "traverse":  # observed: a key press sends light both ways along its row
            now = time.monotonic()
            with self.lock:
                self.press_xy = [p for p in self.press_xy if now - p[0] < 2.0]
                presses = list(self.press_xy)
            out = np.zeros((N, 3))
            for t0, px, py in presses:
                age = now - t0
                row = np.abs(YY - py) < 0.02
                dx, front = np.abs(X - px), age * 0.9 * r
                trail = np.where(dx <= front, np.exp(-(front - dx) / 0.15), 0.0)  # front with a fading trail
                v = trail * row * max(0.0, 1 - age / 2.0)
                col = hsv(X * 0.8 + age) if self.random else np.tile(self.rgb, (N, 1))
                out = np.maximum(out, v[:, None] * col)
        elif m == "scan":
            pos = (t * 0.5 * r) % 2
            pos = pos if pos <= 1 else 2 - pos
            band = np.exp(-((x - pos) / 0.07) ** 2)
            out = band[:, None] * (hsv(np.full(N, t * 0.1)) if self.random else self.rgb)
        elif m == "cartoon":  # observed: a cluster around H/J/K grows, fades away, comes back (new colour)
            period = 3.0 / r
            cycle, p = divmod(t, period)
            p /= period
            radius = 0.06 + 0.3 * min(1.0, p / 0.6)
            fade = 1.0 if p < 0.6 else max(0.0, 1 - (p - 0.6) / 0.35)
            j = INDEX["j"]
            d = np.hypot(X - X[j], YY - YY[j])
            v = np.clip(1 - d / radius, 0, 1) * fade
            out = v[:, None] * hsv(np.full(N, self.hue0 + 0.23 * cycle + d))
        elif m == "rain":  # drops fall down the columns with a short trail
            if self.rng.random() < dt * 2.5 * r:
                self.drops.append([self.rng.choice(X), -0.05, 0.9 + 0.4 * self.rng.random(), self.rng.random()])
            out = np.zeros((N, 3))
            for d in self.drops:
                d[1] += dt * d[2] * r
                above = d[1] - Y
                v = np.where((np.abs(X - d[0]) < 0.04) & (above >= -0.05) & (above < 0.35),
                             np.exp(-np.maximum(above, 0) / 0.12), 0.0)
                col = hsv(np.array([d[3]]))[0] if self.random else self.rgb
                out = np.maximum(out, v[:, None] * col)
            self.drops = [d for d in self.drops if d[1] < 1.4]
        elif m in ("stars", "flowers"):
            decay = math.exp(-dt * 2.5 * r)
            self.sparks *= decay
            births = self.rng.random(N) < dt * (0.25 if m == "stars" else 0.6) * r
            self.sparks[births] = 1.0
            col = hsv(self.rng.random(N)) if (self.random or m == "flowers") else np.tile(self.rgb, (N, 1))
            if not hasattr(self, "_col") or self._col.shape != col.shape:
                self._col = col
            self._col[births] = col[births]
            out = self._col * self.sparks[:, None]
        elif m == "surmount":  # observed: static; byte 18 picks a preset (0 light pink, 1-2 shades) or 3 = chosen colour
            presets = {0: np.array([1.0, 0.6, 0.75]), 1: (self.rgb + 1) / 2, 2: self.rgb * 0.55}
            out = np.tile(presets.get(self.variant, self.rgb), (N, 1))
        elif m in ("reaction", "ripples"):
            now = time.monotonic()
            base = np.zeros((N, 3))
            with self.lock:
                flash = np.exp(-np.maximum(now - self.pressed, 0) * 2.5 * r)[:, None]
                rings = [p for p in self.press_xy if now - p[0] < 1.5]
                self.press_xy = rings
            base = np.maximum(base, flash * self._colour(t))
            for t0, px, py in rings if m == "ripples" else []:
                d = np.hypot(X - px, YY - py)
                ring = np.exp(-((d - (now - t0) * 0.9 * r) / 0.06) ** 2) * (1 - (now - t0) / 1.5)
                base = np.maximum(base, ring[:, None] * self._colour(t))
            out = base
        else:
            out = np.tile(self.rgb, (N, 1))
        return np.clip(np.asarray(out, dtype=float) * self.level, 0, 1)


def emulator(cfg: dict, custom: dict | None = None, dim: bool = True) -> Emulated:
    """Build an (approximate) emulator from Keyboard.info()-style fields."""
    return Emulated(mode=cfg.get("mode_id", cfg.get("mode", 6)), brightness=cfg.get("brightness", 4),
                    speed=cfg.get("speed", 2), direction=cfg.get("direction", 0), random=cfg.get("random", 0),
                    color=cfg.get("color", "ffffff"), custom=custom, variant=cfg.get("variant", 0), dim=dim)


EFFECTS["emulate"] = Emulated


# ---------------------------------------------------------------------------------------------
# Exact emulation: run the keyboard firmware's own effect code (when the firmware is available)
# ---------------------------------------------------------------------------------------------
_FW = {"base": None, "tried": False, "caps": None, "source": None, "disabled": False}
_FW_LOCK = threading.Lock()
FULL_SCALE = 200  # firmware output at brightness 4 (255 * ~0.78)
SETTLE_S = 2.0    # emulated settling time after (re)configuring an effect


def set_device_caps(caps: bytes):
    """Remember the connected keyboard's capability block; the firmware image must match it."""
    with _FW_LOCK:
        if _FW["caps"] != caps:
            _FW.update(caps=caps, tried=False, base=None)


def firmware_base():
    """Booted firmware engine (shared template), or None if the firmware isn't on this PC."""
    with _FW_LOCK:
        if _FW["disabled"]:
            return None
        if not _FW["tried"]:
            _FW["tried"] = True
            try:
                from .firmware import FirmwareLighting, find_firmware
                img = find_firmware(_FW["caps"])
                if img is not None:
                    _FW["base"] = FirmwareLighting(img, time_scale=_FW.get("time_scale", 1.0))
                    _FW["source"] = "AJAZZ updater"
            except Exception as e:  # numba/pefile missing, corrupt updater...
                _FW["base"] = None
                _FW["source"] = f"unavailable: {e!r}"
        return _FW["base"]


REACTIVE_MODES = {7, 8, 9, 16, 17, 18, 20}   # modes whose firmware handler reacts to key presses


def set_time_scale(scale: float):
    """Calibration: stretch emulated firmware time (1.0 = datasheet timing)."""
    with _FW_LOCK:
        _FW["time_scale"] = float(scale)
        if _FW["base"] is not None:
            _FW["base"].time_scale = float(scale)


def firmware_ready():
    """The firmware engine if it has already been booted (never blocks)."""
    return _FW["base"] if _FW["tried"] and not _FW["disabled"] else None


def firmware_status() -> dict:
    if not _FW["tried"] and not _FW["disabled"]:
        return {"exact": False, "source": "starting…"}
    return {"exact": _FW["base"] is not None, "source": _FW["source"]}


class FirmwareEmulated(Effect):
    """A built-in effect rendered by the keyboard's own firmware code (exact)."""
    name = "emulate"
    fps = 60

    def __init__(self, base, mode=6, brightness=4, speed=2, direction=0, random=0, color="ffffff",
                 custom: dict | None = None, variant: int = 0, dim: bool = True, config: bytes | None = None):
        import copy
        self.fl = copy.deepcopy(base)
        cfg = bytearray(config or bytes(0x1D))
        cfg[1], cfg[2], cfg[3], cfg[4], cfg[5] = int(mode), int(brightness), int(speed), int(direction), int(random)
        cfg[6:9] = bytes(int(v * 255 + 0.5) for v in rgbf(color))
        cfg[18] = int(variant)
        self.fl.configure(bytes(cfg))
        if custom:
            frame = bytearray(384)
            for k, c in custom.items():
                if k.lower() in INDEX:
                    s = SLOTS_BY_INDEX[INDEX[k.lower()]]
                    frame[s * 3:s * 3 + 3] = bytes(int(v * 255 + 0.5) for v in rgbf(c))
            self.fl.set_custom(bytes(frame), cfg[25] if len(cfg) > 25 else 0)
        self.dim = dim
        self.raw = not dim          # streamed to the keyboard as-is (already device LED values)
        self.mode = ID.get(int(mode), "normal")
        self.last_t = None
        self.lock = threading.Lock()
        self.events = KeyEvents(self.press)
        self.fl.advance(SETTLE_S)   # settle like the keyboard does after a mode change (left-over values decay)

    def press(self, name: str):
        with self.lock:
            self.fl.press(int(SLOTS_BY_INDEX[INDEX[name.lower()]]))

    def start(self):
        self.events.start()

    def stop(self):
        self.events.stop()

    def render(self, t):
        with self.lock:
            if self.last_t is not None and t > self.last_t:
                self.fl.advance(min(0.25, t - self.last_t))
            self.last_t = t
            f = self.fl.frame()[SLOTS_BY_INDEX].astype(float)
        return np.clip(f / FULL_SCALE, 0, 1) if self.dim else f / 255.0


from .effects import SLOTS as SLOTS_BY_INDEX  # noqa: E402  (LED slot per key, in NAMES order)

_approx_emulator = emulator


def emulator(cfg: dict, custom: dict | None = None, dim: bool = True, exact: bool = True):
    """Exact firmware emulation when available, otherwise the approximation."""
    base = (firmware_ready() if _FW_LOCK.locked() else firmware_base()) if exact else None
    if base is not None:
        return FirmwareEmulated(base, mode=cfg.get("mode_id", cfg.get("mode", 6)), brightness=cfg.get("brightness", 4),
                                speed=cfg.get("speed", 2), direction=cfg.get("direction", 0), random=cfg.get("random", 0),
                                color=cfg.get("color", "ffffff"), custom=custom, variant=cfg.get("variant", 0), dim=dim)
    return _approx_emulator(cfg, custom, dim=dim)


def _make_emulate(mode=6, brightness=4, speed=2, direction=0, random=0, color="ffffff", custom=None,
                  variant=0, dim=True, **_):
    """Engine entry point for EFFECTS['emulate']."""
    return emulator({"mode": mode, "brightness": brightness, "speed": speed, "direction": direction,
                     "random": random, "color": color, "variant": variant}, custom, dim=dim)


_make_emulate.name = "emulate"
EFFECTS["emulate"] = _make_emulate


# ---------------------------------------------------------------------------------------------
# 'keyboard' layer: whatever effect is stored on the keyboard, emulated (the app knows the config)
# ---------------------------------------------------------------------------------------------
_STORED = {"fn": None}  # fn() -> _make_emulate params of the stored effect; registered by server.Controller


def set_stored_effect(fn):
    """Register (None clears) where the keyboard's stored effect comes from; returns the previous source."""
    prev, _STORED["fn"] = _STORED["fn"], fn
    return prev


class StoredEffect(Effect):
    """The keyboard's own stored (built-in) effect, emulated on this PC (exact when the firmware is
    available). Use it as the bottom layer to put live layers on top of a built-in effect. Read from the
    keyboard when the effect starts (black until the keyboard is plugged in, asked again every RETRY_S);
    only available inside the app / `evrgb serve`."""
    name = "keyboard"
    fps = 60
    RETRY_S = 1.0

    def __init__(self):
        self.source = _STORED["fn"]
        if self.source is None:
            raise ValueError("the keyboard's stored effect is only known while the EVision RGB app (or `evrgb serve`) runs")
        self.emu = None
        self.raw = False
        self.started = False
        self._next_try = 0.0

    def _emulator(self):
        """The emulator of what the keyboard stores, or None while the keyboard can't be asked (not plugged in)."""
        if self.emu is None and time.monotonic() >= self._next_try:
            try:
                emu = _make_emulate(**self.source())
            except (ProtocolError, OSError):
                self._next_try = time.monotonic() + self.RETRY_S
                return None
            self.raw = getattr(emu, "raw", False)
            if self.started:
                emu.start()
            self.emu = emu
        return self.emu

    def start(self):
        self.started = True
        self._emulator()

    def stop(self):
        self.started = False
        if self.emu is not None:
            self.emu.stop()

    def render(self, t):
        emu = self._emulator()
        return np.zeros((N, 3)) if emu is None else emu.render(t)


EFFECTS["keyboard"] = StoredEffect

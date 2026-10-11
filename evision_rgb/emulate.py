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
from .protocol import MODES

ID = {v: k for k, v in MODES.items()}
RATE = (2.0, 1.4, 1.0, 0.7, 0.45)  # device speed 0 (fastest) .. 4 (slowest)
RAINBOW = {"corrugated", "spectrum", "wave", "roll", "serpentine", "speed", "cloud"}
YY = Y * 0.35  # physical aspect: rows are closer together than columns are wide


class Emulated(Effect):
    """Approximate a firmware effect from its config block fields."""
    name = "emulate"
    fps = 30

    def __init__(self, mode: int = 6, brightness: int = 4, speed: int = 2, direction: int = 0, random: int = 0,
                 color: str = "ffffff", custom: dict | None = None, seed: int = 7, dim: bool = True):
        self.mode = ID.get(int(mode), "normal")
        # dim=True: show the brightness setting (on-screen preview). Under overlays the keyboard applies
        # its own brightness, so the base is streamed at full level (dim=False).
        self.level = 0.2 + 0.8 * max(0, min(4, int(brightness))) / 4 if dim else 1.0
        self.rate = RATE[max(0, min(4, int(speed)))]
        self.x = (1 - X) if int(direction) == 1 else X
        self.random = bool(random)
        self.rgb = rgbf(color)
        self.hue0 = self._hue(self.rgb)
        self.custom = np.zeros((N, 3))
        for k, c in (custom or {}).items():
            if k.lower() in INDEX:
                self.custom[INDEX[k.lower()]] = rgbf(c)
        self.rng = np.random.default_rng(seed)
        self.pressed = np.full(N, -1e9)
        self.press_xy: list[tuple[float, float, float]] = []
        self.lock = threading.Lock()
        self.events = KeyEvents(self.press) if self.mode in ("reaction", "ripples") else None
        self.sparks = np.zeros(N)
        self.below = [np.array([j for j in range(N) if abs(X[j] - X[i]) < 0.03 and 0 < Y[j] - Y[i] < 0.25], dtype=int)
                      for i in range(N)] if self.mode == "rain" else None
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
        elif m == "speed":
            out = hsv(x * 2.0 - t * 1.2 * r)
        elif m == "serpentine":
            path = np.where((np.round(Y * 5) % 2) == 0, x, 1 - x) / 6 + np.round(Y * 5) / 6
            out = hsv(path * 1.5 - t * 0.3 * r)
        elif m == "cloud":
            n = (np.sin(x * 6 + t * 0.7 * r) + np.sin(YY * 14 - t * 0.5 * r) + np.sin((x + YY) * 4 + t * 0.3 * r)) / 3
            out = hsv(0.5 + n * 0.5 + t * 0.05 * r, 1.0, 0.6 + 0.4 * np.abs(n))
        elif m in ("traverse", "scan"):
            pos = (t * 0.5 * r) % 2
            pos = pos if m == "traverse" or pos <= 1 else 2 - pos
            pos = pos % 1 if m == "traverse" else pos
            band = np.exp(-((x - pos) / 0.07) ** 2)
            out = band[:, None] * (hsv(np.full(N, t * 0.1)) if self.random else self.rgb)
        elif m in ("stars", "flowers", "cartoon", "rain"):
            decay = math.exp(-dt * 2.5 * r)
            self.sparks *= decay
            births = self.rng.random(N) < dt * (0.25 if m == "stars" else 0.6) * r
            if m == "rain":  # drops start on the top row and fall down their column
                births &= Y < 0.05
                falling = self.sparks > 0.05
                for i in np.where(falling & (self.rng.random(N) < dt * 6 * r))[0]:
                    js = self.below[i]
                    self.sparks[js] = np.maximum(self.sparks[js], self.sparks[i])
            self.sparks[births] = 1.0
            col = hsv(self.rng.random(N)) if (self.random or m in ("flowers", "cartoon")) else np.tile(self.rgb, (N, 1))
            if not hasattr(self, "_col") or self._col.shape != col.shape:
                self._col = col
            self._col[births] = col[births]
            out = self._col * self.sparks[:, None]
        elif m in ("reaction", "ripples", "surmount"):
            now = time.monotonic()
            base = np.zeros((N, 3))
            if m == "surmount":  # colour fill rising and falling
                lvl = 0.5 - 0.5 * math.cos(t * 0.8 * r)
                base = (np.clip((lvl - (1 - Y)) * 6 + 1, 0, 1))[:, None] * self._colour(t, t * 0.13 * r)
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


def emulator(cfg: dict, custom: dict | None = None) -> Emulated:
    """Build an emulator from Keyboard.info()-style fields."""
    return Emulated(mode=cfg.get("mode_id", cfg.get("mode", 6)), brightness=cfg.get("brightness", 4),
                    speed=cfg.get("speed", 2), direction=cfg.get("direction", 0), random=cfg.get("random", 0),
                    color=cfg.get("color", "ffffff"), custom=custom)


EFFECTS["emulate"] = Emulated

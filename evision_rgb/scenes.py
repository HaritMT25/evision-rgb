"""Classic LED effects, laid out on the keyboard's grid: Fire, Ocean, Twinkle and Digital rain.

After FastLED's well-known examples (Fire2012, Pacifica and TwinkleFox by Mark Kriegsman, MIT licence), written
again for numpy and this keyboard: keys sit on a 17 x 6 grid (column from X, row from Y). Simulations run in fixed
steps (independent of the frame rate) from a seed, so the same seed gives the same show.
"""
from __future__ import annotations

import math

import numpy as np

from .effects import EFFECTS, Effect, N, X, Y, rgbf

COLS, ROWS = 17, 6
COL = np.minimum(np.round(X * (COLS - 1)).astype(int), COLS - 1)
ROW = np.minimum(np.round(Y * (ROWS - 1)).astype(int), ROWS - 1)  # 0 = F-row (top) .. 5 = bottom row


def _number(name, v, lo, hi):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not lo <= v <= hi:
        raise ValueError(f"{name} must be a number from {lo} to {hi}")
    return float(v)


def _seed(v):
    if isinstance(v, bool) or not isinstance(v, int):
        raise ValueError("seed must be a whole number")
    return v


class _Stepped(Effect):
    """Runs step() at a fixed rate up to the frame's time (a jump back in time starts over)."""
    RATE = 30.0

    def __init__(self, seed: int):
        self.seed = _seed(seed)
        self._reset()

    def _reset(self):
        self.rng = np.random.default_rng(self.seed)
        self.done = 0
        self.setup()

    def setup(self):
        pass

    def advance(self, t: float):
        target = int(max(0.0, t) * self.RATE)
        if target < self.done:
            self._reset()
        for _ in range(min(target - self.done, int(self.RATE * 2))):  # never hang after a long pause
            self.step()
        self.done = target


class Fire(_Stepped):
    """Fire: a flame in every column, rising from the bottom row (after FastLED's Fire2012). cooling = how fast
    flames die down, sparking = how often new flames start, brightness."""
    name = "fire"

    def __init__(self, cooling: float = 0.55, sparking: float = 0.5, brightness: float = 1.0, seed: int = 0):
        self.cooling = _number("cooling", cooling, 0, 1)
        self.sparking = _number("sparking", sparking, 0, 1)
        self.brightness = _number("brightness", brightness, 0, 1)
        super().__init__(seed)

    def setup(self):
        self.heat = np.zeros((COLS, ROWS))  # rows from the bottom (0) up

    def step(self):
        h = self.heat
        # cool (more higher up), let heat rise one blend per step (Fire2012's drift is made for long strips: on six
        # rows it floods the board), then light new sparks at the bottom
        h -= self.rng.random(h.shape) * (0.04 + 0.25 * self.cooling) * (0.6 + 1.2 * np.arange(ROWS) / ROWS)
        np.maximum(h, 0, out=h)
        h[:, 1:] = 0.55 * h[:, 1:] + 0.45 * h[:, :-1]
        sparks = self.rng.random(COLS) < self.sparking * 0.5
        h[sparks, 0] = np.minimum(1.0, h[sparks, 0] + 0.5 + 0.5 * self.rng.random(int(sparks.sum())))

    def render(self, t):
        self.advance(t)
        heat = self.heat[COL, ROWS - 1 - ROW]  # key row 5 (bottom) = flame row 0
        c = np.stack([np.clip(3 * heat, 0, 1), np.clip(3 * heat - 1, 0, 1), np.clip(3 * heat - 2, 0, 1)], axis=1)
        return c * self.brightness


class Ocean(Effect):
    """Ocean: layered blue-green waves rolling across the keys, whitecaps where they meet (after FastLED's
    Pacifica). speed, brightness."""
    name = "ocean"
    fps = 60
    LAYERS = ((0.9, 0.45, 0.28, 0.00, (0.00, 0.14, 0.42)), (1.6, -0.6, 0.17, 0.37, (0.00, 0.40, 0.42)),
              (2.7, 0.9, -0.12, 0.71, (0.03, 0.58, 0.48)), (0.5, 1.3, 0.07, 0.13, (0.00, 0.22, 0.58)))
    # (x frequency, y frequency, cycles per second, offset, colour) per wave layer

    def __init__(self, speed: float = 1.0, brightness: float = 1.0, seed: int = 0):
        self.speed = _number("speed", speed, 0, 5)
        self.brightness = _number("brightness", brightness, 0, 1)
        rng = np.random.default_rng(_seed(seed))
        self.offsets = rng.random(len(self.LAYERS))
        self.yy = Y * 0.35

    def render(self, t):
        t = t * self.speed
        out = np.tile([0.0, 0.025, 0.07], (N, 1))
        level = np.zeros(N)
        for (fx, fy, s, off, col), o in zip(self.LAYERS, self.offsets):
            swell = 0.5 + 0.5 * math.sin(2 * math.pi * (t * 0.031 + o))  # each layer comes and goes slowly
            w = 0.5 + 0.5 * np.sin(2 * math.pi * (X * fx + self.yy * fy + t * s + off + o))
            level += w * swell
            out += np.outer(w * swell, col) * 0.7
        caps = np.clip(level / len(self.LAYERS) * 1.7 - 0.68, 0, 1) ** 2  # where the waves pile up
        out += caps[:, None] * np.array([0.55, 0.75, 0.75])
        return np.clip(out, 0, 1) * self.brightness


class Twinkle(Effect):
    """Twinkle: keys light up and fade one by one like stars (after FastLED's TwinkleFox). colors = the colours they
    pick from, density = how much of the time each key twinkles, speed, background colour, brightness."""
    name = "twinkle"
    fps = 60

    def __init__(self, colors=None, density: float = 0.4, speed: float = 1.0, background="000000",
                 brightness: float = 1.0, seed: int = 0):
        colors = ["ffd8a0", "ffffff", "a0c8ff", "ffe0f0"] if colors is None else colors
        if not isinstance(colors, list) or not 1 <= len(colors) <= 16:
            raise ValueError("colors must be a list of 1 to 16 colours")
        self.palette = np.array([rgbf(c) for c in colors])
        self.density = _number("density", density, 0.05, 1)
        self.speed = _number("speed", speed, 0, 5)
        self.background = rgbf(background)
        self.brightness = _number("brightness", brightness, 0, 1)
        rng = np.random.default_rng(_seed(seed))
        self.rate = 0.2 + 0.4 * rng.random(N)  # twinkles per second, per key
        self.phase = rng.random(N)
        self.pick = rng.integers(0, 1 << 30, N)

    def render(self, t):
        c = t * self.speed * self.rate + self.phase
        cycle, x = np.floor(c), (c - np.floor(c)) / self.density
        v = np.where(x < 1, np.sin(np.pi * np.minimum(x, 1)) ** 2, 0.0)  # rises and fades back to zero
        colour = self.palette[(self.pick + cycle.astype(np.int64) * 7919) % len(self.palette)]  # new colour per twinkle
        out = self.background + (colour - self.background) * v[:, None]
        return np.clip(out, 0, 1) * self.brightness


class DigitalRain(_Stepped):
    """Digital rain: green trails falling down the columns, bright heads and fading tails. density = how many
    columns rain at once, speed, brightness."""
    name = "matrix"
    fps = 30

    def __init__(self, density: float = 0.15, speed: float = 1.0, brightness: float = 1.0, seed: int = 0):
        self.density = _number("density", density, 0, 1)
        self.speed = _number("speed", speed, 0.1, 5)
        self.brightness = _number("brightness", brightness, 0, 1)
        super().__init__(seed)

    def setup(self):
        self.drops: list[list[float]] = []  # [column, head row, rows per step, tail length]

    def step(self):
        for d in self.drops:
            d[1] += d[2] * self.speed
        self.drops = [d for d in self.drops if d[1] - d[3] < ROWS]
        busy = {int(d[0]) for d in self.drops if d[1] < 2}
        for c in range(COLS):
            if c not in busy and self.rng.random() < self.density * 0.1:
                self.drops.append([c, -1.0, 0.12 + 0.12 * self.rng.random(), 2.0 + 3.0 * self.rng.random()])

    def render(self, t):
        self.advance(t)
        green = np.zeros(N)
        head = np.zeros(N)
        for c, h, _, length in self.drops:
            here = COL == c
            dist = h - ROW  # rows above the head
            head = np.maximum(head, np.where(here & (np.abs(dist) < 0.5), 1.0, 0.0))
            tail = np.where(here & (dist >= 0.5) & (dist < length), 1 - dist / length, 0.0)
            green = np.maximum(green, tail)
        out = np.stack([0.7 * head, np.maximum(green * 0.85, head), 0.7 * head], axis=1)
        return out * self.brightness


for _cls in (Fire, Ocean, Twinkle, DigitalRain):
    EFFECTS[_cls.name] = _cls

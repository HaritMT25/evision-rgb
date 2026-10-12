"""Typing heatmap: how often each key is pressed.

Privacy: OFF unless enabled; stores only per-key totals and the date counting started — never
key sequences, text or per-press timestamps. Clearing deletes the file contents.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import os
import threading

import numpy as np

from .effects import EFFECTS, INDEX, N, Effect, hsv
from .layout import KEYS


class KeyCounter:
    def __init__(self, path: str, enabled: bool = False):
        self.path, self.enabled = path, bool(enabled)
        self.lock = threading.Lock()
        self._counts: dict[str, int] = {}
        self.since = dt.date.today().isoformat()
        self.unsaved = False  # counted since the last save
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            self._counts = {k: int(v) for k, v in data.get("counts", {}).items() if k in KEYS}
            self.since = data.get("since", self.since)
        except (OSError, ValueError):
            pass

    def record(self, key: str):
        if self.enabled and key in KEYS:
            with self.lock:
                self._counts[key] = self._counts.get(key, 0) + 1
                self.unsaved = True

    def counts(self) -> dict[str, int]:
        with self.lock:
            return dict(self._counts)

    def total(self) -> int:
        with self.lock:
            return sum(self._counts.values())

    def save(self):
        with self.lock:
            data = {"since": self.since, "counts": dict(self._counts)}
            self.unsaved = False
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, self.path)

    def clear(self):
        with self.lock:
            self._counts = {}
            self.since = dt.date.today().isoformat()
        self.save()


_SOURCE = {"counter": None}  # the app's KeyCounter (server.Controller registers it)


def set_counter(counter):
    """Register (None clears) the counter a heatmap without its own counts shows; returns the previous one."""
    prev, _SOURCE["counter"] = _SOURCE["counter"], counter
    return prev


class Heatmap(Effect):
    """Typing heatmap: blue (rarely pressed) to red (most pressed); unused keys stay dark. Without `counts` it shows
    the app's own count, and follows your typing (redrawn every REFRESH_S)."""
    name = "heatmap"
    fps = 5
    REFRESH_S = 1.0

    def __init__(self, counts: dict | None = None):
        self.counter = _SOURCE["counter"] if counts is None else None
        self.out = np.zeros((N, 3))
        self._drawn = -1e9
        if counts is not None:
            self._paint(counts)

    def _paint(self, counts: dict):
        out = np.zeros((N, 3))
        counts = {k: int(v) for k, v in (counts or {}).items() if k.lower() in INDEX and int(v) > 0}
        if counts:
            top = math.log1p(max(counts.values()))
            for k, c in counts.items():
                v = math.log1p(c) / top if top else 1.0
                out[INDEX[k.lower()]] = hsv(np.array([0.66 * (1 - v)]))[0] * (0.25 + 0.75 * v)
        self.out = out

    def render(self, t):
        if self.counter is not None and t - self._drawn >= self.REFRESH_S:
            self._drawn = t
            self._paint(self.counter.counts())
        return self.out


EFFECTS["heatmap"] = Heatmap

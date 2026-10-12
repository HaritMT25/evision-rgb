"""Event log: what happened to the keyboard, the power and the lighting, with times.

One "YYYY-MM-DD HH:MM:SS text" line per event in a small text file next to the settings, so a drop-out can be
explained afterwards ("keyboard disconnected" at 20:19, just after "on battery"). The file is capped: when it
grows past max_bytes it becomes <name>.1 (replacing the older one) and a new file starts. Writing is best-effort:
a file that can't be written never breaks the app; the events are still listed from memory.
"""
from __future__ import annotations

import collections
import os
import threading
import time

KEEP = 100           # events listed by recent() / GET /events
MAX_BYTES = 256 * 1024
ERROR_EVERY_S = 60.0  # the same error is logged at most once a minute


class EventLog:
    def __init__(self, path: str, clock=time.time, max_bytes: int = MAX_BYTES):
        self.path, self.clock, self.max_bytes = path, clock, max_bytes
        self.lock = threading.Lock()
        self.events: collections.deque = collections.deque(maxlen=KEEP)
        self._errors: dict[tuple, float] = {}
        for line in self._tail():
            stamp, text = line[:19], line[20:]
            if len(line) > 20 and stamp[4] == "-" and stamp[13] == ":":
                self.events.append({"time": stamp, "text": text})

    def _tail(self) -> list[str]:
        try:
            with open(self.path, encoding="utf-8", errors="replace") as f:
                f.seek(max(0, os.path.getsize(self.path) - 16 * 1024))
                return f.read().splitlines()[-KEEP:]
        except OSError:
            return []

    def add(self, text: str) -> dict:
        ev = {"time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.clock())), "text": str(text)}
        with self.lock:
            self.events.append(ev)
            try:
                if os.path.isfile(self.path) and os.path.getsize(self.path) >= self.max_bytes:
                    os.replace(self.path, self.path + ".1")
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(f"{ev['time']} {ev['text']}\n")
            except OSError:
                pass
        return ev

    def error(self, where: str, exc: BaseException) -> None:
        """Log an error from background work, each distinct one at most once a minute."""
        key, now = (where, repr(exc)), self.clock()
        with self.lock:
            if now - self._errors.get(key, -1e18) < ERROR_EVERY_S:
                return
            self._errors[key] = now
            if len(self._errors) > 200:
                self._errors = {k: t for k, t in self._errors.items() if now - t < ERROR_EVERY_S}
        self.add(f"error in {where}: {exc!r}")

    def recent(self, n: int | None = None) -> list[dict]:
        """The newest n events (default: all kept, at most KEEP), newest last."""
        with self.lock:
            ev = list(self.events)
        return ev[-n:] if n else ev

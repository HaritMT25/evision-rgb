"""High-level AK820 API: hardware effects, persistent per-key custom colours, live streaming."""
from __future__ import annotations

import threading
import time
from typing import Iterable, Mapping

from .layout import KEYS
from .protocol import AK820, CMD_WRITE_CONFIG, CMD_WRITE_CUSTOM, MAP_SIZE, MODE_ALIASES, MODES, ProtocolError

CONFIG_LEN = 0x1D  # block the official app writes for profile 0 (cmd 0x06, offset 0)
# offsets inside the config block (profile 0)
O_PROFILE, O_MODE, O_BRIGHT, O_SPEED, O_DIR, O_RANDOM, O_RGB, O_COLORSET = 0, 1, 2, 3, 4, 5, 6, 18
MODE_NAMES = {v: k for k, v in MODES.items()}
MAX_BRIGHTNESS = 4  # 5 = LEDs off on AK820 (Marvo accepts 5)
MAX_SPEED = 4
COLORSETS = 3  # custom colour memory areas (0..2). NOTE: AK820 V0103 always DISPLAYS area 0 and ignores
               # the colorset byte, so area 0 is the live per-key layout; 1..2 are only storage.

RGB = tuple[int, int, int]


def parse_color(c) -> RGB:
    """Accept (r,g,b), 'ff8800', '#ff8800' or a few names."""
    names = {"red": "ff0000", "green": "00ff00", "blue": "0000ff", "white": "ffffff", "black": "000000",
             "off": "000000", "yellow": "ffff00", "cyan": "00ffff", "magenta": "ff00ff",
             "orange": "ff6000", "purple": "8000ff", "pink": "ff3080"}
    if isinstance(c, str):
        c = names.get(c.lower(), c).lstrip("#")
        if len(c) != 6:
            raise ValueError(f"bad colour {c!r}")
        return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
    r, g, b = c
    return int(r) & 255, int(g) & 255, int(b) & 255


def slot_of(key: str | int) -> int:
    if isinstance(key, int):
        return key
    try:
        return KEYS[key][0]
    except KeyError:
        low = {k.lower(): v for k, v in KEYS.items()}
        if key.lower() in low:
            return low[key.lower()][0]
        raise KeyError(f"unknown key {key!r}; known: {', '.join(KEYS)}")


def frame_from(colors: Mapping[str | int, object] | None = None, default=(0, 0, 0)) -> bytearray:
    buf = bytearray(bytes(parse_color(default)) * MAP_SIZE)
    for k, c in (colors or {}).items():
        s = slot_of(k)
        buf[s * 3:s * 3 + 3] = bytes(parse_color(c))
    return buf


class Keyboard:
    def __init__(self, verbose: bool = False, wait: bool = False):
        """wait=True: work without the keyboard plugged in yet (commands fail until it is, then it is opened)."""
        self.dev = AK820(allow_writes=True, verbose=verbose, wait=wait)

    def close(self):
        self.dev.close()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()

    # ---- info / hardware effects ------------------------------------------
    def info(self) -> dict:
        caps = self.dev.capabilities()
        cfg = self.dev.read_config(CONFIG_LEN)
        return {"caps": caps.hex(), "map_size": caps[5], **self._decode(cfg)}

    @staticmethod
    def _decode(cfg: bytes) -> dict:
        return {"profile": cfg[O_PROFILE], "mode": MODE_NAMES.get(cfg[O_MODE], hex(cfg[O_MODE])),
                "mode_id": cfg[O_MODE], "brightness": cfg[O_BRIGHT], "speed": cfg[O_SPEED],
                "direction": cfg[O_DIR], "random": cfg[O_RANDOM],
                "color": "%02x%02x%02x" % tuple(cfg[O_RGB:O_RGB + 3]), "colorset": cfg[O_COLORSET],
                "variant": cfg[O_COLORSET]}

    def set_effect(self, mode: str | int | None = None, brightness: int | None = None,
                   speed: int | None = None, color=None, direction: int | None = None,
                   random: bool | None = None, colorset: int | None = None, variant: int | None = None,
                   custom: Mapping[str | int, object] | None = None, _refresh_custom: bool = True) -> dict:
        """Persistent hardware effect (survives replug). Read-modify-write of the config block.
        custom: per-key colours for custom area 0, written in the same transaction (no stale frame)."""
        self.apply_fields(bytearray(CONFIG_LEN), mode, brightness, speed, color, direction, random, colorset, variant)
        new_frame = bytes(frame_from(custom)) if custom is not None else None  # validate before any I/O
        cfg = bytearray(self.dev.read_config(CONFIG_LEN))
        self.apply_fields(cfg, mode, brightness, speed, color, direction, random, colorset, variant)
        if cfg[O_MODE] == MODES["custom"]:
            cfg[O_COLORSET] = 0  # firmware shows area 0 regardless; keep the byte consistent with that
        frame = new_frame
        if frame is None and cfg[O_MODE] == MODES["custom"] and _refresh_custom:
            frame = self.dev.read_custom(0)
        self._commit(cfg, frame)
        return self._decode(bytes(cfg))

    def _commit(self, cfg: bytearray, frame: bytes | None = None):
        """Write config (+ custom colours for area 0) in ONE begin/end transaction, then verify.
        Doing both together switches cleanly into custom mode with the new colours (verified on
        hardware: no flash of the previous effect, no stale per-key buffer)."""
        self.dev.begin()
        self.dev.write_block(CMD_WRITE_CONFIG, bytes(cfg), 0)
        if frame is not None:
            self.dev.write_block(CMD_WRITE_CUSTOM, frame, 0)
        self.dev.end()
        back = self.dev.read_config(CONFIG_LEN)
        if back != bytes(cfg):
            raise ProtocolError(f"config readback mismatch: wrote {cfg.hex()} read {back.hex()}")
        if frame is not None and self.dev.read_custom(0) != frame:
            raise ProtocolError("custom colour readback mismatch")

    def _write_custom(self, frame: bytes, colorset: int):
        self.dev.begin()
        self.dev.write_block(CMD_WRITE_CUSTOM, frame, 512 * colorset)
        self.dev.end()
        if self.dev.read_custom(colorset) != frame:
            raise ProtocolError("custom colour readback mismatch")

    @staticmethod
    def apply_fields(cfg: bytearray, mode=None, brightness=None, speed=None, color=None,
                     direction=None, random=None, colorset=None, variant=None) -> bytearray:
        """Validate and apply effect fields to a config block (no I/O; raises ValueError)."""
        if mode is not None:
            if isinstance(mode, str):
                key = mode.lower().replace(" ", "_")
                if key not in MODES and key not in MODE_ALIASES:
                    raise ValueError(f"unknown mode {mode!r}; choose from {list(MODES)}")
                mid = MODES.get(key, MODE_ALIASES.get(key))
            else:
                mid = int(mode)
            if mid not in MODE_NAMES:
                raise ValueError(f"mode {mode!r} not supported")
            cfg[O_MODE] = mid
        if brightness is not None:
            if not 0 <= brightness <= MAX_BRIGHTNESS:
                raise ValueError(f"brightness 0..{MAX_BRIGHTNESS}")
            cfg[O_BRIGHT] = brightness
        if speed is not None:
            if not 0 <= speed <= MAX_SPEED:
                raise ValueError(f"speed 0..{MAX_SPEED}")
            cfg[O_SPEED] = speed
        if direction is not None:
            cfg[O_DIR] = int(direction) & 0xFF
        if random is not None:
            cfg[O_RANDOM] = 1 if random else 0
        if color is not None:
            cfg[O_RGB:O_RGB + 3] = bytes(parse_color(color))
        if colorset is not None:
            if not 0 <= colorset < COLORSETS:
                raise ValueError(f"colorset 0..{COLORSETS - 1}")
            cfg[O_COLORSET] = colorset
        if variant is not None:  # byte 18 for built-in effects, e.g. Surmount: 0-2 presets, 3 = chosen colour
            if not 0 <= int(variant) <= 3:
                raise ValueError("variant 0..3")
            cfg[O_COLORSET] = int(variant)
        cfg[O_PROFILE] = 0
        return cfg

    # ---- persistent per-key colours ---------------------------------------
    def set_custom(self, colors: Mapping[str | int, object] | bytes, default=(0, 0, 0),
                   colorset: int = 0, activate: bool = True):
        """Store per-key colours on the keyboard and (activate=True) show them in custom mode.

        With activate=True the colours always go to area 0 (the only one the firmware displays);
        a non-zero `colorset` additionally mirrors them into that storage area."""
        frame = bytes(colors) if isinstance(colors, (bytes, bytearray)) else bytes(frame_from(colors, default))
        if len(frame) != 3 * MAP_SIZE:
            raise ValueError("frame must be 384 bytes")
        if not activate:
            self._write_custom(frame, colorset)
            return
        cfg = self.apply_fields(bytearray(self.dev.read_config(CONFIG_LEN)), mode="custom", colorset=0)
        self._commit(cfg, frame)
        if colorset:
            self._write_custom(frame, colorset)

    def get_custom(self, colorset: int = 0) -> dict[str, str]:
        raw = self.dev.read_custom(colorset)
        return {k: raw[s * 3:s * 3 + 3].hex() for k, (s, _, _) in KEYS.items()}

    # ---- live streaming ----------------------------------------------------
    def stream(self) -> "Stream":
        return Stream(self.dev)


class Stream:
    """Live, non-persistent per-key control (cmd 0x12). Keeps the frame alive in a background
    thread; on close sends 0x13 so the keyboard returns to its stored effect.

        with kb.stream() as s:
            s.set("W", "ff0000"); s.fill("000010"); s.show()
    """

    def __init__(self, dev: AK820, keepalive_s: float = 0.15):
        self.dev = dev
        self.frame = bytearray(3 * MAP_SIZE)
        self._dirty = threading.Event()
        self._stop = threading.Event()
        self._keepalive = keepalive_s
        self._lock = threading.Lock()
        self.frames_sent = 0
        self.paused = False  # stop sending (the keyboard keeps showing the last frame until it times out)
        self.error: Exception | None = None
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        last = 0.0
        while not self._stop.is_set():
            self._dirty.wait(self._keepalive)
            if self._stop.is_set():
                break
            if self.paused:
                self._dirty.clear()
                continue
            if self._dirty.is_set() or time.monotonic() - last > self._keepalive:
                self._dirty.clear()
                with self._lock:
                    snap = bytes(self.frame)
                try:
                    self.dev.direct(snap)
                    self.frames_sent += 1
                    self.error = None
                except Exception as e:  # keep streaming (the transport reconnects); surface the last error
                    self.error = e
                    time.sleep(0.05)
                last = time.monotonic()

    def set(self, key: str | int, color):
        s = slot_of(key)
        with self._lock:
            self.frame[s * 3:s * 3 + 3] = bytes(parse_color(color))

    def set_many(self, colors: Mapping[str | int, object]):
        with self._lock:
            for k, c in colors.items():
                s = slot_of(k)
                self.frame[s * 3:s * 3 + 3] = bytes(parse_color(c))

    def fill(self, color, keys: Iterable[str] | None = None):
        c = bytes(parse_color(color))
        with self._lock:
            slots = [slot_of(k) for k in keys] if keys is not None else range(MAP_SIZE)
            for s in slots:
                self.frame[s * 3:s * 3 + 3] = c

    def set_frame(self, frame: bytes):
        with self._lock:
            self.frame[:] = frame

    def show(self):
        self._dirty.set()

    def close(self):
        self._stop.set()
        self._dirty.set()
        self._thread.join(2)
        try:
            self.dev.direct_end()
        except ProtocolError:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()

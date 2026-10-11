"""Simulated AK820 (320F:505B, fw V0103) for behaviour tests — models what we OBSERVED on hardware:

- report id 4, 64-byte packets, uint16 checksum over bytes 3..63, every packet echoed
- commands 01/02/03/05/06/07/0A/0B/12/13/1B; anything else is echoed with status 0xFF
- config writes take effect on END (0x02); END also drops direct (streaming) mode
- direct mode (0x12) shows the streamed frame; falls back to the stored effect after a timeout or 0x13
- custom mode always DISPLAYS memory area 0 (the colorset byte is ignored)
- brightness 5 turns the LEDs off
`history` records every change of what the keyboard visibly shows, so tests can assert on flicker.
"""
from __future__ import annotations

import os
import threading
import time
from collections import deque

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
BACKUP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")  # keymap/table read from a real AK820
PATH = b"\\\\?\\HID#VID_320F&PID_505B&MI_01&Col04#fake"
CAPS = bytes.fromhex("aa5500000d805000020000000000000000000000000f320150000200000000010203")


def _load(name, size):
    try:
        with open(os.path.join(BACKUP, name), "rb") as f:
            return bytearray(f.read())
    except OSError:
        return bytearray(size)


class FakeKeyboard:
    DIRECT_TIMEOUT = 0.35

    def __init__(self):
        self.config = bytearray(0x80)
        self.config[:9] = bytes([0, 0x06, 3, 0, 0, 0, 0x00, 0xFF, 0x00])  # normal, green, brightness 3
        self.areas = [bytearray(384) for _ in range(8)]
        self.keymap = _load("keymap.bin", 384)
        self.table1b = _load("table1b.bin", 128) or bytearray(range(128))
        self.applied = (bytes(self.config), bytes(self.areas[0]))  # what the LEDs use (committed on END)
        self.direct = bytearray(384)
        self.direct_on = False
        self.direct_t = 0.0
        self.in_config = False
        self.lock = threading.RLock()
        self.rx: deque[bytes] = deque()      # replies waiting to be read (all handles see them)
        self.writes: list[bytes] = []        # every packet the host sent
        self.history: list[tuple] = []       # visible-state changes
        self.drop_echoes = 0                 # simulate lost echoes
        self.bad_checksums = 0
        self.foreign_before_reply: list[bytes] = []  # echoes of other programs arriving mid-transaction
        self._last_visible = None
        self._record()

    # ---- what the keyboard shows -------------------------------------------
    def visible(self) -> tuple:
        with self.lock:
            if self.direct_on and time.monotonic() - self.direct_t < self.DIRECT_TIMEOUT:
                return ("direct", bytes(self.direct))
            self.direct_on = False
            cfg, area0 = self.applied
            mode, bright = cfg[1], cfg[2]
            if bright >= 5:
                return ("off",)
            if mode == 0x14:
                return ("custom", area0)
            return ("effect", mode, bytes(cfg[6:9]), bright)

    def _record(self):
        v = self.visible()
        if v != self._last_visible:
            self.history.append(v)
            self._last_visible = v

    # ---- HID transport -----------------------------------------------------
    def handle(self, pkt: bytes):
        with self.lock:
            self.writes.append(pkt)
            if len(pkt) != 64 or pkt[0] != 4:
                return
            if (sum(pkt[3:64]) & 0xFFFF) != pkt[1] | pkt[2] << 8:
                self.bad_checksums += 1
                return
            cmd, ln, off = pkt[3], pkt[4], pkt[5] | pkt[6] << 8
            reply = bytearray(pkt)
            data = pkt[8:8 + ln]
            if cmd == 0x01:
                self.in_config = True
            elif cmd == 0x02:
                self.in_config = False
                self.applied = (bytes(self.config), bytes(self.areas[0]))
                self.direct_on = False        # END leaves streaming mode (observed as a flash)
            elif cmd == 0x03:
                reply[8:8 + ln] = CAPS[:ln]
            elif cmd == 0x05:
                reply[8:8 + ln] = self.config[off:off + ln]
            elif cmd == 0x06:
                self.config[off:off + ln] = data
                if not self.in_config:
                    self.applied = (bytes(self.config), self.applied[1])
            elif cmd == 0x07:
                reply[8:8 + ln] = self.keymap[off:off + ln]
            elif cmd == 0x0A:
                a, o = divmod(off, 512)
                reply[8:8 + ln] = self.areas[a][o:o + ln]
            elif cmd == 0x0B:
                a, o = divmod(off, 512)
                self.areas[a][o:o + ln] = data
                if not self.in_config and a == 0:
                    self.applied = (self.applied[0], bytes(self.areas[0]))
            elif cmd == 0x12:
                self.direct[off:off + ln] = data
                self.direct_on, self.direct_t = True, time.monotonic()
            elif cmd == 0x13:
                self.direct_on = False
            elif cmd == 0x1B:
                reply[8:8 + ln] = self.table1b[off:off + ln]
            else:
                reply[7] = 0xFF
            if cmd in (0x02, 0x0B, 0x12, 0x13) or (cmd == 0x06 and not self.in_config):
                self._record()
            while self.foreign_before_reply:
                self.rx.append(self.foreign_before_reply.pop(0))
            if self.drop_echoes > 0:
                self.drop_echoes -= 1
                return
            self.rx.append(bytes(reply))

    def inject(self, pkt: bytes):
        """A packet echoed for some OTHER program (e.g. OpenRGB spamming cmd 0x11)."""
        with self.lock:
            self.rx.append(pkt)


class FakeHidDevice:
    def __init__(self, kb: FakeKeyboard):
        self.kb = kb
        self.nonblocking = False

    def open_path(self, path):
        pass

    def set_nonblocking(self, v):
        self.nonblocking = bool(v)

    def write(self, data):
        self.kb.handle(bytes(data))
        return len(data)

    def read(self, n, timeout_ms=0):
        deadline = time.monotonic() + (timeout_ms / 1000 if timeout_ms else 0)
        while True:
            with self.kb.lock:
                if self.kb.rx:
                    return list(self.kb.rx.popleft())
            if not timeout_ms or time.monotonic() >= deadline:
                return []
            time.sleep(0.0005)

    def error(self):
        return "fake error"

    def close(self):
        pass


class FakeHidModule:
    """Drop-in for the `hid` module used by evision_rgb.protocol."""

    def __init__(self, kb: FakeKeyboard):
        self.kb = kb

    def device(self):
        return FakeHidDevice(self.kb)

    def enumerate(self, vid=0, pid=0):
        return [{"usage_page": 0xFF1C, "usage": 0x92, "path": PATH, "interface_number": 1}]


def install(test_case) -> FakeKeyboard:
    """Patch evision_rgb.protocol.hid with a fresh simulated keyboard for the duration of a test."""
    import evision_rgb.protocol as proto
    kb = FakeKeyboard()
    orig = proto.hid
    proto.hid = FakeHidModule(kb)
    test_case.addCleanup(setattr, proto, "hid", orig)
    return kb

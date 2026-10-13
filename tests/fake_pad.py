"""Simulated AJAZZ / Mirabox stream-dock macro pad for behaviour tests. Models the protocol as implemented by
the open-source drivers (mirajazz, Bitfocus Companion) and is STRICT: anything a real pad would not accept
is recorded in `violations`.

- output reports: report id 0x00 + `packet` bytes (512 for protocol 1, 1024 for 2/3), zero padded
- commands: "CRT\\0\\0" + verb + args; after BAT <len32 BE> <id> the next packets are raw JPEG data
- STP shows the uploaded images; input reports: "ACK\\0\\0OK\\0\\0" + code (byte 9) + state (byte 10)
- protocol 1/2 report one event per click; protocol 3 reports press (state 1) and release (state 0)
"""
from __future__ import annotations

import io
import threading
import time
from collections import deque

VERBS = (b"CONNECT", b"DIS", b"LIG", b"BAT", b"STP", b"CLE", b"HAN")  # longest first where prefixes overlap
USAGE_PAGE = 0xFFA0


class FakePad:
    def __init__(self, model, path: bytes = b"fakepad-1"):
        from evision_rgb import streamdock
        self.model = streamdock.MODELS[model] if isinstance(model, str) else model
        self.vid, self.pid = self.model.usb[0]
        self.path = path
        self.lock = threading.RLock()
        self.rx: deque[bytes] = deque()
        self.commands: list[tuple[str, bytes, float]] = []   # (verb, args, time)
        self.violations: list[str] = []
        self.uploads: list[tuple[int, bytes]] = []           # (image id, jpeg) in upload order
        self.shown: dict[int, bytes] = {}                    # image id -> jpeg on screen (after STP)
        self.pending: dict[int, bytes] = {}
        self.brightness = None
        self.awake = False
        self.unplugged = False
        self.opened = 0
        self._img = None                                     # (image id, expected length, bytearray)

    # ---- host -> pad
    def write(self, data: bytes):
        with self.lock:
            if self.unplugged:
                raise OSError("device disconnected")
            n = self.model.packet + 1
            if len(data) != n:
                self.violations.append(f"packet of {len(data)} bytes (expected {n})")
            if not data or data[0] != 0:
                self.violations.append("report id must be 0")
            body = bytes(data[1:])
            if self._img is not None:
                key, want, buf = self._img
                buf += body[:want - len(buf)]
                if len(buf) >= want:
                    self._img = None
                    self._image_done(key, bytes(buf))
                return len(data)
            if not body.startswith(b"CRT\x00\x00"):
                self.violations.append(f"packet without CRT prefix: {body[:12].hex()}")
                return len(data)
            rest = body[5:]
            verb = next((v for v in VERBS if rest.startswith(v)), None)
            if verb is None:
                self.violations.append(f"unknown command {rest[:8]!r}")
                return len(data)
            args = rest[len(verb):]
            self.commands.append((verb.decode(), args, time.monotonic()))
            if verb == b"BAT":
                length, key = int.from_bytes(args[0:4], "big"), args[4]
                if not 0 < length <= 10240:
                    self.violations.append(f"BAT length {length}")
                self._img = (key, length, bytearray())
            elif verb == b"STP":
                self.shown.update(self.pending)
                self.pending.clear()
            elif verb == b"LIG":
                if args[:2] != b"\x00\x00" or args[2] > 100:
                    self.violations.append(f"bad brightness args {args[:3].hex()}")
                self.brightness = args[2]
            elif verb == b"DIS":
                self.awake = True
            elif verb == b"HAN":
                self.awake = False
            elif verb == b"CLE":
                if args[:3] == b"\x00\x00\x00":
                    key = args[3]
                    if key == 0xFF:
                        self.shown.clear(); self.pending.clear()
                    else:
                        self.shown.pop(key, None); self.pending.pop(key, None)
                elif args[:4] != b"\x00\x00DC":
                    self.violations.append(f"bad CLE args {args[:4].hex()}")
            return len(data)

    def _image_done(self, key: int, jpeg: bytes):
        from PIL import Image
        ctl = next((c for c in self.model.keys if c.image_id == key), None)
        if ctl is None:
            self.violations.append(f"image for id {key:#x}, which has no screen")
            return
        try:
            im = Image.open(io.BytesIO(jpeg))
            im.load()
        except Exception as e:  # noqa: BLE001
            self.violations.append(f"image for {key:#x} is not a JPEG: {e!r}")
            return
        w, h = ctl.size
        if self.model.rotate in (90, 270):
            w, h = h, w
        if im.size != (w, h) or im.format != "JPEG":
            self.violations.append(f"image for {key:#x} is {im.format} {im.size}, expected JPEG {(w, h)}")
        self.uploads.append((key, jpeg))
        self.pending[key] = jpeg

    # ---- pad -> host
    def _report(self, code: int, state: int):
        r = bytearray(512)
        r[0:9] = b"ACK\x00\x00OK\x00\x00"
        r[9], r[10] = code, state
        with self.lock:
            self.rx.append(bytes(r))

    def press_key(self, index: int):
        c = self.model.keys[index]
        if self.model.protocol >= 3:
            self._report(c.input_id, 1); self._report(c.input_id, 0)
        else:
            self._report(c.input_id, 1)  # one report per click (on release)

    def hold_key(self, index: int, down: bool = True):
        self._report(self.model.keys[index].input_id, 1 if down else 0)

    def turn_knob(self, index: int, delta: int):
        left, right = self.model.knobs[index].turn_ids
        for _ in range(abs(delta)):
            self._report(right if delta > 0 else left, 0)

    def press_knob(self, index: int):
        c = self.model.knobs[index]
        if self.model.protocol >= 3:
            self._report(c.input_id, 1); self._report(c.input_id, 0)
        else:
            self._report(c.input_id, 1)

    def ack(self):
        self._report(0xFF, 0)

    def garbage(self):
        with self.lock:
            self.rx.append(b"\x55" * 512)

    def read(self, n: int, timeout_ms: int = 0):
        deadline = time.monotonic() + timeout_ms / 1000
        while True:
            with self.lock:
                if self.unplugged:
                    raise OSError("device disconnected")
                if self.rx:
                    return list(self.rx.popleft()[:n])
            if time.monotonic() >= deadline:
                return []
            time.sleep(0.001)

    # helpers for assertions
    def verbs(self) -> list[str]:
        return [v for v, _, _ in self.commands]

    def image(self, index: int):
        """The image currently shown on key `index`, rotated back to how the user sees it."""
        from PIL import Image
        c = self.model.keys[index]
        jpeg = self.shown.get(c.image_id)
        if jpeg is None:
            return None
        im = Image.open(io.BytesIO(jpeg)).convert("RGB")
        back = {90: Image.Transpose.ROTATE_90, 180: Image.Transpose.ROTATE_180, 270: Image.Transpose.ROTATE_270}
        return im.transpose(back[self.model.rotate]) if self.model.rotate else im


class _Handle:
    def __init__(self, hidmod):
        self.hidmod, self.pad = hidmod, None

    def open_path(self, path):
        pad = self.hidmod.pads.get(bytes(path))
        if pad is None or pad.unplugged:
            raise OSError("open failed")
        pad.opened += 1
        self.pad = pad

    def set_nonblocking(self, v):
        pass

    def write(self, data):
        return self.pad.write(bytes(data))

    def read(self, n, timeout_ms=0):
        return self.pad.read(n, timeout_ms)

    def close(self):
        pass


class FakePadHid:
    """Drop-in for the `hid` module as used by evision_rgb.streamdock."""

    def __init__(self, *pads: FakePad, extra: list[dict] | None = None):
        self.pads = {p.path: p for p in pads}
        self.extra = extra or []  # other HID interfaces present on the system
        self.calls = []  # (vendor, product) of every listing; 0 = any, as in hidapi

    def add(self, pad: FakePad):
        self.pads[pad.path] = pad

    def enumerate(self, vid=0, pid=0):
        self.calls.append((vid, pid))
        out = [{"vendor_id": p.vid, "product_id": p.pid, "usage_page": USAGE_PAGE, "usage": 1, "path": p.path,
                "interface_number": 0, "serial_number": "355499441494", "product_string": "fake pad"}
               for p in self.pads.values() if not p.unplugged]
        return [d for d in out + list(self.extra)
                if (not vid or d.get("vendor_id") == vid) and (not pid or d.get("product_id") == pid)]

    def device(self):
        return _Handle(self)


def install_pads(test_case, *pads: FakePad, extra=None) -> FakePadHid:
    import evision_rgb.streamdock as sd
    fake = FakePadHid(*pads, extra=extra)
    orig = sd.hid
    sd.hid = fake
    test_case.addCleanup(setattr, sd, "hid", orig)
    return fake

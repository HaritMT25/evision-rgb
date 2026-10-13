"""AJAZZ / Mirabox "stream dock" macro pads: keys with small screens, plain buttons and knobs on one USB HID
interface (usage page 0xFFA0). The AKP03 family has 6 screen keys, 3 buttons and 3 knobs; the AKP153 family has
15 screen keys and 3 side keys. Mirabox sells the same hardware as N3 / HSV293S.

Wire protocol, as implemented by the open-source drivers mirajazz and Bitfocus Companion (docs/MACROPADS.md):
  output report  report id 0 + `packet` bytes (512 for protocol 1, 1024 for 2 and 3), zero padded
  command        "CRT\\0\\0" + verb + arguments; only the verbs in VERBS are ever sent
  key image      BAT <length, 4 bytes big-endian> <key id>, then the JPEG in `packet`-sized pieces;
                 STP shows everything uploaded since the last STP
  input report   "ACK\\0\\0OK\\0\\0" + event code (byte 9) + state (byte 10); protocol 1 and 2 send one report
                 per click, protocol 3 sends press (state 1) and release (anything else)
Boot-logo (LOG), device-config (QUCMD), mode (MOD) and unknown commands are never sent.
"""
from __future__ import annotations

import io
import os
from collections import namedtuple
from dataclasses import dataclass

import hid

USAGE_PAGE = 0xFFA0
PREFIX = b"CRT\x00\x00"
VERBS = {b"DIS": "wake the screens", b"LIG": "screen brightness", b"BAT": "upload a key image",
         b"STP": "show the uploaded images", b"CLE": "clear key images", b"CONNECT": "keep-alive",
         b"HAN": "screens off"}
MAX_IMAGE = 10240  # bytes per key image (larger ones are not shown reliably)

PadEvent = namedtuple("PadEvent", "control index kind delta", defaults=(0,))  # control: key|knob, kind: down|up|turn


class PadError(Exception):
    pass


@dataclass(frozen=True)
class Control:
    index: int                        # keys: reading order; knobs: left to right
    kind: str                         # "lcd" (key with a screen), "button" (key without one) or "knob"
    row: float
    col: float
    input_id: int                     # event code of a press
    image_id: int | None = None       # id used to draw on it
    size: tuple[int, int] | None = None
    turn_ids: tuple[int, int] | None = None  # knobs: event codes for one step left / right
    name: str = ""


@dataclass(frozen=True)
class Model:
    id: str
    name: str
    family: str                       # models in a family share layout and settings
    usb: tuple[tuple[int, int], ...]
    protocol: int
    keys: tuple[Control, ...]
    knobs: tuple[Control, ...] = ()
    rotate: int = 0                   # degrees clockwise the device needs images turned

    @property
    def packet(self) -> int:
        return 512 if self.protocol == 1 else 1024

    def info(self) -> dict:
        ctl = lambda c: {"index": c.index, "kind": c.kind, "row": c.row, "col": c.col, "name": c.name,
                         "size": list(c.size) if c.size else None}
        return {"id": self.id, "name": self.name, "family": self.family, "protocol": self.protocol,
                "usb": [f"{v:04X}:{p:04X}" for v, p in self.usb],
                "keys": [ctl(c) for c in self.keys], "knobs": [ctl(c) for c in self.knobs]}


def _akp153(id_, name, usb, protocol):
    big = (85, 85) if protocol == 1 else (96, 96)
    ids = ((0x0D, 0x0A, 0x07, 0x04, 0x01, 0x10), (0x0E, 0x0B, 0x08, 0x05, 0x02, 0x11),
           (0x0F, 0x0C, 0x09, 0x06, 0x03, 0x12))
    keys = tuple(Control(r * 6 + c, "lcd", r, c, ids[r][c], ids[r][c], (80, 80) if c == 5 else big,
                         name=f"Side key {r + 1}" if c == 5 else f"Key {r * 5 + c + 1}")
                 for r in range(3) for c in range(6))
    return Model(id_, name, "akp153", usb, protocol, keys, rotate=270)


def _akp03(id_, name, usb, protocol):
    size = (60, 60) if protocol == 2 else (64, 64)
    keys = tuple(Control(i, "lcd", i // 3, i % 3, i + 1, i + 1, size, name=f"Key {i + 1}") for i in range(6))
    keys += tuple(Control(6 + j, "button", 2, j, code, name=f"Button {j + 1}")
                  for j, code in enumerate((0x25, 0x30, 0x31)))
    knobs = (Control(0, "knob", 1.2, 3.2, 0x33, turn_ids=(0x90, 0x91), name="Left knob"),
             Control(1, "knob", 0, 3.7, 0x35, turn_ids=(0x50, 0x51), name="Top knob"),
             Control(2, "knob", 1.2, 4.2, 0x34, turn_ids=(0x60, 0x61), name="Right knob"))
    return Model(id_, name, "akp03", usb, protocol, keys, knobs, rotate=0 if protocol == 2 else 90)


MODELS = {m.id: m for m in (
    _akp03("akp03", "AJAZZ AKP03", ((0x0300, 0x1001),), 2),
    _akp03("akp03e", "AJAZZ AKP03E", ((0x0300, 0x1002),), 2),
    _akp03("akp03r", "AJAZZ AKP03R", ((0x0300, 0x1003),), 2),
    _akp03("akp03e-rev2", "AJAZZ AKP03E (rev. 2)", ((0x0300, 0x3002),), 3),
    _akp03("akp03r-rev2", "AJAZZ AKP03R (rev. 2)", ((0x0300, 0x3003),), 3),
    _akp03("n3", "Mirabox N3", ((0x6602, 0x1000), (0x6602, 0x1002)), 2),
    _akp03("n3-v3", "Mirabox N3 (v3)", ((0x6603, 0x1002), (0x6603, 0x1003)), 3),
    _akp153("akp153", "AJAZZ AKP153", ((0x5548, 0x6674),), 1),
    _akp153("akp153e", "AJAZZ AKP153E", ((0x0300, 0x1010),), 1),
    _akp153("akp153r", "AJAZZ AKP153R", ((0x0300, 0x1020),), 1),
    _akp153("akp153e-rev2", "AJAZZ AKP153E (rev. 2)", ((0x0300, 0x3010),), 3),
    _akp153("akp153r-rev2", "AJAZZ AKP153R (rev. 2)", ((0x0300, 0x3011),), 3),
    _akp153("hsv293s", "Mirabox HSV293S", ((0x5548, 0x6670),), 1),
)}
BY_USB = {usb: m for m in MODELS.values() for usb in m.usb}
VENDORS = sorted({vid for vid, _ in BY_USB})
FAMILIES = {"akp03": MODELS["akp03e-rev2"], "akp153": MODELS["akp153e-rev2"]}  # layout used when none is plugged in


def scan() -> list[tuple[Model, bytes]]:
    """Connected supported pads as (model, HID path). Read-only: nothing is opened. Only the pads' vendors are
    listed: hidapi reads the name strings of every device it lists, and this runs every 2 s while no pad is plugged
    in (listing everything asked every USB input device, earbuds included, for its name all day)."""
    found, seen = [], set()
    for d in (d for vid in VENDORS for d in hid.enumerate(vid, 0)):
        m = BY_USB.get((d.get("vendor_id"), d.get("product_id")))
        page = d.get("usage_page")
        vendor_iface = page == USAGE_PAGE or (not page and d.get("interface_number") == 0)  # no usage pages (Linux)
        if m is not None and vendor_iface and d["path"] not in seen:
            seen.add(d["path"])
            found.append((m, d["path"]))
    return found


class StreamDock:
    """One open pad. Writes only allow-listed commands; read_events() turns input reports into PadEvents."""

    def __init__(self, model: Model, path: bytes):
        self.model, self.path = model, path
        self.dev = hid.device()
        self.dev.open_path(path)
        try:
            self.dev.set_nonblocking(1)
        except Exception:  # noqa: BLE001 - optional
            pass
        self.held: set[tuple[str, int]] = set()  # protocol 3: keys / knobs currently pressed
        self._keys = {c.input_id: c for c in model.keys}
        self._knob_press = {c.input_id: c for c in model.knobs}
        self._knob_turn = {code: (c, d) for c in model.knobs for code, d in zip(c.turn_ids, (-1, 1))}

    # ---- output
    def _write(self, payload: bytes):
        if len(payload) > self.model.packet:
            raise PadError("payload longer than a packet")
        pkt = b"\x00" + payload + bytes(self.model.packet - len(payload))
        n = self.dev.write(pkt)
        if n is None or n < 0:
            raise PadError("write failed (pad unplugged?)")

    def command(self, verb: bytes, args: bytes = b""):
        if verb not in VERBS:
            raise PadError(f"command {verb!r} is not on the allow-list")
        if verb == b"CLE" and not (len(args) == 4 and (args[:3] == b"\x00\x00\x00" or args == b"\x00\x00DC")):
            raise PadError("bad CLE arguments")
        self._write(PREFIX + verb + args)

    def wake(self):
        self.command(b"DIS")

    def set_brightness(self, percent: int):
        if isinstance(percent, bool) or not isinstance(percent, int) or not 0 <= percent <= 100:
            raise ValueError("pad brightness is 0..100")
        self.command(b"LIG", b"\x00\x00" + bytes([percent]))

    def heartbeat(self):
        self.command(b"CONNECT")

    def refresh(self):
        self.command(b"STP")

    def sleep(self):
        self.command(b"HAN")

    def _screen(self, index: int) -> Control:
        c = self.model.keys[index]
        if c.image_id is None:
            raise ValueError(f"{c.name} has no screen")
        return c

    def clear(self, index: int | None = None):
        self.command(b"CLE", b"\x00\x00\x00" + bytes([0xFF if index is None else self._screen(index).image_id]))

    def set_image(self, index: int, jpeg: bytes):
        """Upload a key image (shown on the next refresh()). `jpeg` comes from encode()."""
        c = self._screen(index)
        if not jpeg.startswith(b"\xff\xd8") or len(jpeg) > MAX_IMAGE:
            raise ValueError(f"key images are JPEGs of at most {MAX_IMAGE} bytes")
        self.command(b"BAT", len(jpeg).to_bytes(4, "big") + bytes([c.image_id]))
        for off in range(0, len(jpeg), self.model.packet):
            self._write(jpeg[off:off + self.model.packet])

    def close(self):
        try:
            self.command(b"CLE", b"\x00\x00DC")  # blank the screens: the keys do nothing without the app
        except Exception:  # noqa: BLE001 - unplugged
            pass
        try:
            self.dev.close()
        except Exception:  # noqa: BLE001
            pass

    # ---- input
    def read_events(self, timeout_ms: int = 0) -> list[PadEvent]:
        """Events from every input report waiting now (waits up to timeout_ms for the first)."""
        out = []
        data = self.dev.read(512, timeout_ms) if timeout_ms else self.dev.read(512)
        while data:
            out += self.parse(bytes(data))
            data = self.dev.read(512)
        return out

    def parse(self, data: bytes) -> list[PadEvent]:
        if data[:1] == b"\x00" and data[1:4] == b"ACK":  # some HID stacks keep report id 0
            data = data[1:]
        if data[:3] != b"ACK" or len(data) < 11:
            return []
        code, state = data[9], data[10]
        if code == 0x00:  # "nothing pressed": release whatever we think is held
            up = [PadEvent(c, i, "up") for c, i in sorted(self.held)]
            self.held.clear()
            return up
        if code in self._knob_turn:
            c, d = self._knob_turn[code]
            return [PadEvent("knob", c.index, "turn", d)]
        if code in self._keys:
            control, c = "key", self._keys[code]
        elif code in self._knob_press:
            control, c = "knob", self._knob_press[code]
        else:
            return []  # unknown code, e.g. 0xFF: one of our writes was acknowledged
        if self.model.protocol < 3:  # one report per click
            return [PadEvent(control, c.index, "down"), PadEvent(control, c.index, "up")]
        pressed, k = state == 0x01, (control, c.index)  # releases are 0x00 or 0x02 depending on firmware
        if pressed == (k in self.held):
            return []  # repeated report: nothing changed
        (self.held.add if pressed else self.held.discard)(k)
        return [PadEvent(control, c.index, "down" if pressed else "up")]


# ------------------------------------------------------------------------------------------- key images
_FONTS = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")


def _font(size: int, bold: bool = False, emoji: bool = False):
    from PIL import ImageFont
    names = ("seguiemj.ttf",) if emoji else (("segoeuib.ttf", "arialbd.ttf") if bold else ("segoeui.ttf", "arial.ttf"))
    for n in names:
        try:
            return ImageFont.truetype(os.path.join(_FONTS, n), size)
        except OSError:
            continue
    try:
        return ImageFont.truetype("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf", size)
    except OSError:
        return ImageFont.load_default(size=size)


def render_key(size: tuple[int, int], label: str = "", color: str = "000000", icon: str = "", active: bool = False):
    """A key image as the user sees it: colour background, an icon (emoji or a few letters) and a label.
    Keys whose effect / profile is running get a white frame."""
    from PIL import Image, ImageDraw
    w, h = size
    base = tuple(int(color[i:i + 2], 16) for i in (0, 2, 4)) if color else (0, 0, 0)
    im = Image.new("RGB", size, base)
    d = ImageDraw.Draw(im)
    for y in range(h):  # subtle top-down shading so flat colours look like keys
        f = 0.75 + 0.35 * (1 - y / h)
        d.line([(0, y), (w, y)], fill=tuple(min(255, int(v * f)) for v in base))
    text_fill = (20, 20, 20) if sum(base) > 450 else (245, 245, 245)
    icon_h = 0
    icon = icon.replace("️", "").replace("︎", "")  # variation selectors only skew the measured width
    if icon:
        is_emoji = any(ord(ch) > 0x2000 for ch in icon)
        f = _font(int(h * (0.42 if label else 0.55)), bold=True, emoji=is_emoji)
        box = d.textbbox((0, 0), icon, font=f, embedded_color=is_emoji)
        iw, ih = box[2] - box[0], box[3] - box[1]
        y = (h * (0.40 if label else 0.5)) - ih / 2 - box[1]
        d.text(((w - iw) / 2 - box[0], y), icon, font=f, fill=text_fill, embedded_color=is_emoji)
        icon_h = ih
    if label:
        size_px = int(h * (0.20 if icon else 0.26))
        for s in range(size_px, 7, -1):  # shrink long labels to fit
            f = _font(s, bold=True)
            box = d.textbbox((0, 0), label, font=f)
            if box[2] - box[0] <= w - 6:
                break
        tw, th = box[2] - box[0], box[3] - box[1]
        y = (h * 0.78 - th / 2 - box[1]) if icon_h else (h - th) / 2 - box[1]
        d.text(((w - tw) / 2 - box[0], y), label, font=f, fill=text_fill)
    if active:
        b = max(2, w // 22)
        d.rounded_rectangle([b // 2, b // 2, w - 1 - b // 2, h - 1 - b // 2], radius=w // 8, outline=(255, 255, 255), width=b)
    return im


def render_cover(size: tuple[int, int], cover, playing: bool = True, active: bool = False):
    """A key showing an album cover with a small badge: pause bars while playing (what a press does),
    a play triangle on a dimmed cover while paused."""
    from PIL import Image, ImageDraw
    w, h = size
    im = cover.convert("RGB").resize((w, h), Image.Resampling.LANCZOS)
    if not playing:
        im = Image.eval(im, lambda v: int(v * 0.55))
    d = ImageDraw.Draw(im)
    r = max(7, w // 6)
    cx, cy = w - r - max(3, w // 24), h - r - max(3, h // 24)
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(12, 12, 14), outline=(255, 255, 255), width=max(1, r // 6))
    if playing:
        bw, bh = max(2, r // 3), r
        d.rectangle([cx - r // 2 - bw // 2, cy - bh // 2, cx - r // 2 + bw // 2, cy + bh // 2], fill=(255, 255, 255))
        d.rectangle([cx + r // 2 - bw // 2, cy - bh // 2, cx + r // 2 + bw // 2, cy + bh // 2], fill=(255, 255, 255))
    else:
        d.polygon([(cx - r // 3, cy - r // 2), (cx - r // 3, cy + r // 2), (cx + r // 2, cy)], fill=(255, 255, 255))
    if active:
        b = max(2, w // 22)
        d.rounded_rectangle([b // 2, b // 2, w - 1 - b // 2, h - 1 - b // 2], radius=w // 8, outline=(255, 255, 255), width=b)
    return im


_TURN = {90: "ROTATE_270", 180: "ROTATE_180", 270: "ROTATE_90"}  # PIL rotates counter-clockwise


def encode(model: Model, index: int, image) -> bytes:
    """JPEG for key `index`: its screen size, turned the way the pad needs, at most MAX_IMAGE bytes."""
    from PIL import Image
    c = model.keys[index]
    if c.size is None:
        raise ValueError(f"{c.name} has no screen")
    im = image.convert("RGB")
    if im.size != c.size:
        im = im.resize(c.size, Image.LANCZOS)
    if model.rotate:
        im = im.transpose(getattr(Image.Transpose, _TURN[model.rotate]))
    for quality in (90, 80, 70, 60, 50, 40, 30, 20, 10):
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=quality)
        if buf.tell() <= MAX_IMAGE:
            return buf.getvalue()
    raise ValueError("image does not fit in 10 KB")

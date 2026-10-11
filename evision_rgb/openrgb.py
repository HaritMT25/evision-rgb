"""OpenRGB SDK server: programs that speak OpenRGB's network protocol (Artemis, SignalRGB with its OpenRGB bridge,
Home Assistant, openrgb-python scripts, OpenRGB itself as a client) light the keyboard through this app.

Written from OpenRGB's published SDK documentation (Documentation/OpenRGBSDK.md in the OpenRGB project): protocol
versions 0-4, newer clients negotiate down. One device, the keyboard: a per-key matrix with a "Direct" mode (the
client's colours, drawn by this app as a live effect so indicators stay on top) and the keyboard's built-in effects
(stored on it, like the app's Light Settings). Listens on 127.0.0.1 only.
"""
from __future__ import annotations

import socket
import socketserver
import struct
import threading

import numpy as np

from .effects import EFFECTS, Effect, N, NAMES, X, Y, wake_engines
from .layout import KEYS
from .protocol import MODES

MAX_VERSION = 4
MAX_PACKET = 1 << 20  # a whole-keyboard update is ~400 bytes: anything this large is not a lighting client
COUNT, DATA, VERSION, CLIENT_NAME = 0, 1, 40, 50
PROFILE_LIST, PROFILE_LOAD, PLUGIN_LIST = 150, 152, 200
UPDATE_LEDS, UPDATE_ZONE, UPDATE_ONE, CUSTOM_MODE, UPDATE_MODE, SAVE_MODE = 1050, 1051, 1052, 1100, 1101, 1102
DEVICE_KEYBOARD, ZONE_MATRIX = 5, 2
F_SPEED, F_DIR_LR, F_BRIGHTNESS, F_PER_LED, F_MODE_COLOR, F_RANDOM, F_AUTO_SAVE = 1, 2, 16, 32, 64, 128, 512
COLORS_PER_LED, COLORS_MODE_SPECIFIC, COLORS_RANDOM = 1, 2, 3
DIR_LEFT, DIR_RIGHT = 0, 1
NO_LED = 0xFFFFFFFF

# this layout's key names -> OpenRGB's standard key names (clients place keys by them)
OPENRGB_KEY = {"Esc": "Escape", "Grave": "`", "Minus": "-", "Equal": "=", "LBracket": "[", "RBracket": "]",
               "Backslash": "\\ (ANSI)", "PageUp": "Page Up", "PageDown": "Page Down", "CapsLock": "Caps Lock",
               "Semicolon": ";", "Quote": "'", "LShift": "Left Shift", "RShift": "Right Shift", "Comma": ",",
               "Period": ".", "Slash": "/", "Up": "Up Arrow", "Down": "Down Arrow", "Left": "Left Arrow",
               "Right": "Right Arrow", "LCtrl": "Left Control", "RCtrl": "Right Control", "LWin": "Left Windows",
               "LAlt": "Left Alt", "RAlt": "Right Alt", "Fn": "Right Fn"}
# the keyboard's built-in effects as OpenRGB modes (custom per-key colours: use Direct)
BUILT_IN = {"normal": "Static", "breath": "Breathing", "reaction": "Reactive", "stars": "Starlight"}


def led_name(key: str) -> str:
    return "Key: " + OPENRGB_KEY.get(key, key)


def matrix_map(width: int = 17) -> tuple[int, int, list[int]]:
    """Six rows of `width` cells holding LED indexes (NO_LED where there is no key), from the key positions."""
    grid = [[NO_LED] * (width + 4) for _ in range(6)]
    for i in np.lexsort((X, Y)):
        r, c = int(round(float(Y[i]) * 5)), int(round(float(X[i]) * (width - 1)))
        while grid[r][c] != NO_LED:  # (never happens for the AK820 at width 17)
            c += 1
        grid[r][c] = int(i)
    width = max(c + 1 for row in grid for c, x in enumerate(row) if x != NO_LED)
    return 6, width, [x for row in grid for x in row[:width]]


MATRIX = matrix_map()


# ------------------------------------------------------------------------------ encoding
def _text(s: str) -> bytes:
    b = s.encode() + b"\0"
    return struct.pack("<H", len(b)) + b


def _colors(colors) -> bytes:
    return struct.pack("<H", len(colors)) + b"".join(bytes(int(v) & 255 for v in c[:3]) + b"\0" for c in colors)


def mode_block(m: dict, v: int) -> bytes:
    out = _text(m["name"]) + struct.pack("<iIII", m["value"], m["flags"], m["speed_min"], m["speed_max"])
    if v >= 3:
        out += struct.pack("<II", m["brightness_min"], m["brightness_max"])
    out += struct.pack("<III", m["colors_min"], m["colors_max"], m["speed"])
    if v >= 3:
        out += struct.pack("<I", m["brightness"])
    return out + struct.pack("<II", m["direction"], m["color_mode"]) + _colors(m["colors"])


def parse_mode(data: bytes, at: int, v: int) -> dict:
    """A mode block as a client sends it in UpdateMode / SaveMode (raises struct.error when it is short)."""
    (n,) = struct.unpack_from("<H", data, at)
    m = {"name": data[at + 2:at + 2 + n].rstrip(b"\0").decode(errors="replace")}
    at += 2 + n
    m["value"], m["flags"], m["speed_min"], m["speed_max"] = struct.unpack_from("<iIII", data, at)
    at += 16
    if v >= 3:
        m["brightness_min"], m["brightness_max"] = struct.unpack_from("<II", data, at)
        at += 8
    m["colors_min"], m["colors_max"], m["speed"] = struct.unpack_from("<III", data, at)
    at += 12
    if v >= 3:
        (m["brightness"],) = struct.unpack_from("<I", data, at)
        at += 4
    m["direction"], m["color_mode"], count = struct.unpack_from("<IIH", data, at)
    at += 10
    m["colors"] = [tuple(data[at + 4 * k:at + 4 * k + 3]) for k in range(count)]
    if len(data) < at + 4 * count:
        raise struct.error("mode colours cut short")
    return m


def modes_for(config: dict) -> list[dict]:
    """Direct, then the built-in effects; the stored one shows the keyboard's current settings."""
    rgb = tuple(int(config.get("color", "ffffff")[i:i + 2], 16) for i in (0, 2, 4))
    out = [{"name": "Direct", "value": 0, "flags": F_PER_LED, "speed_min": 0, "speed_max": 0, "brightness_min": 0,
            "brightness_max": 0, "colors_min": 0, "colors_max": 0, "speed": 0, "brightness": 0, "direction": 0,
            "color_mode": COLORS_PER_LED, "colors": []}]
    for key, value in MODES.items():
        if key == "custom":
            continue
        static = key == "normal"
        mine = config.get("mode_id") == value
        out.append({
            "name": BUILT_IN.get(key, key.title()), "value": value,
            "flags": F_BRIGHTNESS | F_MODE_COLOR | F_AUTO_SAVE | (0 if static else F_SPEED | F_DIR_LR | F_RANDOM),
            "speed_min": 0 if static else 4, "speed_max": 0,  # the keyboard's speed 4 is the slowest
            "brightness_min": 0, "brightness_max": 4, "colors_min": 1, "colors_max": 1,
            "speed": config.get("speed", 2) if mine and not static else (0 if static else 2),
            "brightness": config.get("brightness", 4) if mine else 4,
            "direction": (DIR_LEFT if config.get("direction") == 1 else DIR_RIGHT) if mine else DIR_RIGHT,
            "color_mode": COLORS_RANDOM if mine and config.get("random") and not static else COLORS_MODE_SPECIFIC,
            "colors": [rgb if mine else (255, 255, 255)]})
    return out


def device_data(state: dict, v: int) -> bytes:
    """OpenRGB 'Device Data' block for protocol version v (fields added in later versions are left out)."""
    body = struct.pack("<i", DEVICE_KEYBOARD) + _text(state["name"]) + (_text(state["vendor"]) if v >= 1 else b"")
    body += _text(state["description"]) + _text(state["version"]) + _text("") + _text(state["location"])
    modes = state["modes"]
    body += struct.pack("<Hi", len(modes), state["active"]) + b"".join(mode_block(m, v) for m in modes)
    h, w, cells = MATRIX
    zone = _text("Keyboard") + struct.pack("<iIII", ZONE_MATRIX, N, N, N)
    zone += struct.pack("<HII", 8 + 4 * h * w, h, w) + struct.pack(f"<{h * w}I", *cells)
    if v >= 4:
        zone += struct.pack("<H", 0)  # no segments
    body += struct.pack("<H", 1) + zone
    body += struct.pack("<H", N) + b"".join(_text(led_name(n)) + struct.pack("<I", KEYS[n][0]) for n in NAMES)
    body += _colors(state["colors"])
    return struct.pack("<I", 4 + len(body)) + body


# ------------------------------------------------------------------------------ the client's colours
_FRAME = {"colors": np.zeros((N, 3))}
_FRAME_LOCK = threading.Lock()


def set_colors(colors, start: int = 0):
    """Store client colours (0..255 RGB) from LED index `start`; ignores what does not fit."""
    arr = np.asarray(colors, dtype=float).reshape(-1, 3)[:max(0, N - start)] / 255.0
    with _FRAME_LOCK:
        frame = _FRAME["colors"].copy()
        frame[start:start + len(arr)] = arr
        _FRAME["colors"] = frame
    wake_engines()


def current_colors() -> np.ndarray:
    return _FRAME["colors"]


class ExternalColours(Effect):
    """The colours an OpenRGB client sends, passed to the LEDs as they are (like OpenRGB does)."""
    name = "openrgb"
    fps = 60
    raw = True

    def render(self, t):
        return current_colors()


EFFECTS["openrgb"] = ExternalColours


# ------------------------------------------------------------------------------ the server
class Connection:
    def __init__(self, sock):
        self.sock, self.name, self.version = sock, "", 0


class OpenRGBServer:
    """TCP server for OpenRGB clients. Everything that touches the lighting goes through `app`:
    app.orgb_state(), orgb_colors(conn, colors, start), orgb_direct(conn), orgb_mode(conn, index, mode),
    orgb_profiles(), orgb_load_profile(name), orgb_left(conn)."""

    def __init__(self, app):
        self.app = app
        self.srv = None
        self.thread = None
        self.connections: dict[int, Connection] = {}
        self.lock = threading.Lock()
        self.error = None

    @property
    def address(self):
        return self.srv.server_address if self.srv else None

    def start(self, port: int = 6742, host: str = "127.0.0.1"):
        self.stop()
        outer = self

        class Handler(socketserver.BaseRequestHandler):
            def handle(self):
                outer._serve(self.request)

        class Server(socketserver.ThreadingTCPServer):
            daemon_threads = True
            allow_reuse_address = False  # never share a port another program (OpenRGB itself) is using

        try:
            self.srv = Server((host, port), Handler)
        except OSError as e:
            self.srv, self.error = None, f"port {port} is in use (is OpenRGB itself running?)" \
                if getattr(e, "winerror", None) in (10048, 10013) or e.errno in (98, 48, 10048) else repr(e)
            return False
        self.error = None
        self.thread = threading.Thread(target=self.srv.serve_forever, name="openrgb", daemon=True)
        self.thread.start()
        return True

    def stop(self):
        srv, self.srv = self.srv, None
        if srv is not None:
            srv.shutdown()
            srv.server_close()
        with self.lock:
            conns = list(self.connections.values())
        for c in conns:
            try:
                c.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def clients(self) -> list[str]:
        with self.lock:
            return [c.name or "unnamed client" for c in self.connections.values()]

    # -- one connection
    @staticmethod
    def _read(sock, n: int) -> bytes | None:
        out = bytearray()
        while len(out) < n:
            chunk = sock.recv(min(65536, n - len(out)))
            if not chunk:
                return None
            out += chunk
        return bytes(out)

    def _serve(self, sock):
        conn = Connection(sock)
        with self.lock:
            self.connections[id(conn)] = conn
        try:
            while True:
                hdr = self._read(sock, 16)
                if hdr is None or hdr[:4] != b"ORGB":
                    return
                dev, pid, size = struct.unpack("<III", hdr[4:])
                if size > MAX_PACKET:
                    return
                data = self._read(sock, size) if size else b""
                if data is None:
                    return
                try:
                    reply = self._dispatch(conn, dev, pid, data)
                except (struct.error, ValueError, KeyError, TypeError):
                    reply = None  # a malformed packet changes nothing
                if reply is not None:
                    sock.sendall(b"ORGB" + struct.pack("<III", dev, pid, len(reply)) + reply)
        except OSError:
            return
        finally:
            with self.lock:
                self.connections.pop(id(conn), None)
            self.app.orgb_left(conn)

    def _dispatch(self, conn: Connection, dev: int, pid: int, data: bytes):
        if pid == VERSION:
            asked = struct.unpack_from("<I", data)[0] if len(data) >= 4 else 0
            conn.version = min(asked, MAX_VERSION)
            return struct.pack("<I", MAX_VERSION)
        if pid == CLIENT_NAME:
            conn.name = data.split(b"\0", 1)[0].decode(errors="replace")[:64]
            return None
        if pid == COUNT:
            return struct.pack("<I", 1)
        if pid == PROFILE_LIST:
            names = self.app.orgb_profiles()
            body = struct.pack("<H", len(names)) + b"".join(_text(n) for n in names)
            return struct.pack("<I", 4 + len(body)) + body
        if pid == PROFILE_LOAD:
            self.app.orgb_load_profile(data.split(b"\0", 1)[0].decode(errors="replace"))
            return None
        if pid == PLUGIN_LIST:
            return struct.pack("<IH", 6, 0)
        if dev != 0:  # the only device is the keyboard
            return None
        if pid == DATA:
            v = min(struct.unpack_from("<I", data)[0], MAX_VERSION) if len(data) >= 4 else 0
            return device_data(self.app.orgb_state(), v)
        if pid == UPDATE_LEDS:
            (count,) = struct.unpack_from("<H", data, 4)
            self.app.orgb_colors(conn, _rgb(data, 6, count), 0)
        elif pid == UPDATE_ZONE:
            zone, count = struct.unpack_from("<IH", data, 4)
            if zone == 0:
                self.app.orgb_colors(conn, _rgb(data, 10, count), 0)
        elif pid == UPDATE_ONE:
            (index,) = struct.unpack_from("<i", data)
            if 0 <= index < N:
                self.app.orgb_colors(conn, _rgb(data, 4, 1), index)
        elif pid == CUSTOM_MODE:
            self.app.orgb_direct(conn)
        elif pid in (UPDATE_MODE, SAVE_MODE):
            (index,) = struct.unpack_from("<i", data, 4)
            self.app.orgb_mode(conn, index, parse_mode(data, 8, conn.version))
        return None


def _rgb(data: bytes, at: int, count: int) -> list[tuple]:
    if len(data) < at + 4 * count:
        raise struct.error("colours cut short")
    return [tuple(data[at + 4 * k:at + 4 * k + 3]) for k in range(count)]

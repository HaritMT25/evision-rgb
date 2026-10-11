"""Behaviour spec — the app answers as an OpenRGB SDK server. Written BEFORE the implementation.

Programs that speak OpenRGB's network protocol (Artemis, SignalRGB through its OpenRGB bridge, Home Assistant,
openrgb-python scripts, OpenRGB itself as a client) can then light this keyboard through the app. Protocol
versions 0-4 (newer clients negotiate down), written from OpenRGB's published SDK documentation. The keyboard is
one per-key matrix device with a "Direct" mode (live colours, drawn by this app with indicators on top) and its
built-in effects. Off by default, 127.0.0.1 only. When the client goes away, your lighting comes back."""
import json
import os
import socket
import struct
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import warnings

from fake_device import install
from evision_rgb.layout import KEYS

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
WEB = os.path.join(ROOT, "evision_rgb", "gui", "web")
COUNT, DATA, VERSION, NAME, PROFILES, LOAD_PROFILE, PLUGINS = 0, 1, 40, 50, 150, 152, 200
UPDATE_LEDS, UPDATE_ZONE, UPDATE_ONE, CUSTOM_MODE, UPDATE_MODE, SAVE_MODE = 1050, 1051, 1052, 1100, 1101, 1102
KEYBOARD, MATRIX, PER_LED, MODE_SPECIFIC = 5, 2, 1, 2
FLAG_SPEED, FLAG_BRIGHTNESS, FLAG_PER_LED, FLAG_MODE_COLOR = 1 << 0, 1 << 4, 1 << 5, 1 << 6


def wait_for(cond, secs=3.0, step=0.02):
    end = time.monotonic() + secs
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(step)
    return bool(cond())


def key(kb, name):
    v = kb.visible()
    s = KEYS[name][0]
    return v[0], (tuple(v[1][s * 3:s * 3 + 3]) if v[0] == "direct" else None)


# ------------------------------------------------------------------------------ an independent client
class Client:
    def __init__(self, port):
        self.s = socket.create_connection(("127.0.0.1", port), timeout=5)

    def close(self):
        self.s.close()

    def send(self, pkt_id, data=b"", dev=0, magic=b"ORGB", size=None):
        self.s.sendall(magic + struct.pack("<III", dev, pkt_id, len(data) if size is None else size) + data)

    def _read(self, n):
        out = b""
        while len(out) < n:
            chunk = self.s.recv(n - len(out))
            if not chunk:
                raise ConnectionError("closed")
            out += chunk
        return out

    def recv(self):
        hdr = self._read(16)
        assert hdr[:4] == b"ORGB", hdr
        dev, pid, size = struct.unpack("<III", hdr[4:])
        return dev, pid, self._read(size)

    def request(self, pkt_id, data=b"", dev=0):
        self.send(pkt_id, data, dev)
        return self.recv()


def color(r, g, b):
    return bytes([r, g, b, 0])


def leds_packet(colors):
    body = struct.pack("<H", len(colors)) + b"".join(colors)
    return struct.pack("<I", 4 + len(body)) + body


class Reader:
    def __init__(self, data):
        self.d, self.i = data, 0

    def take(self, fmt):
        v = struct.unpack_from("<" + fmt, self.d, self.i)
        self.i += struct.calcsize("<" + fmt)
        return v[0] if len(v) == 1 else v

    def text(self):
        n = self.take("H")
        s = self.d[self.i:self.i + n]
        self.i += n
        assert s.endswith(b"\0"), s
        return s[:-1].decode()


def mode_block(r, v):
    m = {"name": r.text(), "value": r.take("i"), "flags": r.take("I"), "speed_min": r.take("I"), "speed_max": r.take("I")}
    if v >= 3:
        m["brightness_min"], m["brightness_max"] = r.take("I"), r.take("I")
    m["colors_min"], m["colors_max"], m["speed"] = r.take("I"), r.take("I"), r.take("I")
    if v >= 3:
        m["brightness"] = r.take("I")
    m["direction"], m["color_mode"] = r.take("I"), r.take("I")
    n = r.take("H")
    m["colors"] = [tuple(r.d[r.i + 4 * k:r.i + 4 * k + 3]) for k in range(n)]
    r.i += 4 * n
    return m


def parse_controller(data, v):
    """OpenRGB SDK 'Device Data' block, strictly: every byte must be accounted for."""
    r = Reader(data)
    size = r.take("I")
    assert size == len(data), (size, len(data))
    c = {"type": r.take("i"), "name": r.text()}
    if v >= 1:
        c["vendor"] = r.text()
    c["description"], c["version"], c["serial"], c["location"] = r.text(), r.text(), r.text(), r.text()
    n_modes = r.take("H")
    c["active_mode"] = r.take("i")
    c["modes"] = [mode_block(r, v) for _ in range(n_modes)]
    c["zones"] = []
    for _ in range(r.take("H")):
        z = {"name": r.text(), "type": r.take("i"), "leds_min": r.take("I"), "leds_max": r.take("I"), "leds_count": r.take("I")}
        mlen = r.take("H")
        z["matrix"] = None
        if mlen:
            h, w = r.take("I"), r.take("I")
            assert mlen == 8 + 4 * h * w, (mlen, h, w)
            z["matrix"] = (h, w, list(struct.unpack_from(f"<{h * w}I", r.d, r.i)))
            r.i += 4 * h * w
        if v >= 4:
            z["segments"] = r.take("H")
            assert z["segments"] == 0
        c["zones"].append(z)
    c["leds"] = [(r.text(), r.take("I")) for _ in range(r.take("H"))]
    n = r.take("H")
    c["colors"] = [tuple(r.d[r.i + 4 * k:r.i + 4 * k + 3]) for k in range(n)]
    r.i += 4 * n
    assert r.i == len(data), f"{len(data) - r.i} bytes left over"
    return c


def mode_data(m, v, **change):
    """Re-encode a mode block (as a client does for UpdateMode), with some fields changed."""
    m = {**m, **change}
    out = struct.pack("<H", len(m["name"]) + 1) + m["name"].encode() + b"\0"
    out += struct.pack("<iIII", m["value"], m["flags"], m["speed_min"], m["speed_max"])
    if v >= 3:
        out += struct.pack("<II", m["brightness_min"], m["brightness_max"])
    out += struct.pack("<III", m["colors_min"], m["colors_max"], m["speed"])
    if v >= 3:
        out += struct.pack("<I", m["brightness"])
    out += struct.pack("<II", m["direction"], m["color_mode"]) + struct.pack("<H", len(m["colors"]))
    out += b"".join(bytes(c) + b"\0" for c in m["colors"])
    return out


# ------------------------------------------------------------------------------ the app with the server on
class _Server(unittest.TestCase):
    def setUp(self):
        warnings.simplefilter("ignore", ResourceWarning)
        self.kb = install(self)
        import evision_rgb.settings as st
        self.tmp = tempfile.mkdtemp()
        for attr, val in (("DIR", self.tmp), ("PATH", os.path.join(self.tmp, "settings.json"))):
            self.addCleanup(setattr, st, attr, getattr(st, attr))
            setattr(st, attr, val)
        import datetime as _dt
        from evision_rgb.server import Controller, build_server
        self.caps = False
        self.ctl = Controller(probes={"now": lambda: _dt.datetime(2026, 1, 1, 12), "caps": lambda: self.caps},
                              hook_keys=False, automation_hz=20)
        self.httpd = build_server(self.ctl, 0)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(lambda: (self.httpd.shutdown(), self.ctl.close()))
        st_ = self.api("/openrgb", {"enabled": True, "port": 0})[1]
        self.orgb = st_["port"]
        self.clients = []

    def tearDown(self):
        for c in self.clients:
            try:
                c.close()
            except OSError:
                pass

    def api(self, path, body=None):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=data, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            with e:
                return e.code, json.loads(e.read() or b"{}")

    def client(self, version=4):
        c = Client(self.orgb)
        self.clients.append(c)
        c.send(NAME, b"spec client\0")
        _, pid, data = c.request(VERSION, struct.pack("<I", version))
        self.assertEqual(pid, VERSION)
        c.version = min(version, struct.unpack("<I", data)[0])
        return c

    def controller(self, c):
        _, pid, data = c.request(DATA, b"" if c.version == 0 else struct.pack("<I", c.version))
        self.assertEqual(pid, DATA)
        return parse_controller(data, c.version)

    def effect(self):
        e = self.ctl.engine.effect
        return e.name if e is not None else None


class Protocol(_Server):
    def test_the_version_is_negotiated(self):
        c = Client(self.orgb)
        self.clients.append(c)
        for asked in (4, 9, 2):
            _, pid, data = c.request(VERSION, struct.pack("<I", asked))
            self.assertEqual((pid, struct.unpack("<I", data)[0]), (VERSION, 4), f"server's highest is 4 (asked {asked})")

    def test_there_is_one_controller(self):
        c = self.client()
        _, pid, data = c.request(COUNT)
        self.assertEqual((pid, struct.unpack("<I", data)[0]), (COUNT, 1))

    def test_the_keyboard_is_described_in_every_protocol_version(self):
        n = len(KEYS)
        for v in range(5):
            c = self.client(v)
            d = self.controller(c)
            self.assertEqual((d["type"], d["name"]), (KEYBOARD, "AJAZZ AK820"), v)
            self.assertEqual(d.get("vendor"), "AJAZZ" if v >= 1 else None)
            direct = [m for m in d["modes"] if m["name"] == "Direct"]
            self.assertEqual(len(direct), 1, [m["name"] for m in d["modes"]])
            self.assertTrue(direct[0]["flags"] & FLAG_PER_LED)
            self.assertEqual(direct[0]["color_mode"], PER_LED)
            self.assertGreaterEqual(len(d["modes"]), 10, "the built-in effects too")
            (z,) = d["zones"]
            self.assertEqual((z["type"], z["leds_count"], z["leds_min"], z["leds_max"]), (MATRIX, n, n, n))
            h, w, cells = z["matrix"]
            self.assertEqual(h, 6)
            self.assertEqual(sorted(x for x in cells if x != 0xFFFFFFFF), list(range(n)), "every key once")
            self.assertEqual(len(d["leds"]), n)
            self.assertIn("Key: Escape", [name for name, _ in d["leds"]])
            self.assertEqual(len(d["colors"]), n)

    def test_built_in_effects_offer_brightness_from_version_3(self):
        d = self.controller(self.client(4))
        breath = next(m for m in d["modes"] if m["name"] == "Breathing")
        self.assertTrue(breath["flags"] & FLAG_BRIGHTNESS and breath["flags"] & FLAG_SPEED and breath["flags"] & FLAG_MODE_COLOR)
        self.assertEqual((breath["brightness_min"], breath["brightness_max"]), (0, 4))
        self.assertEqual(breath["color_mode"], MODE_SPECIFIC)

    def test_profiles_and_plugins(self):
        self.ctl.update_settings({"profiles": [{"name": "Gaming", "hardware": {"mode": 6, "color": "ff0000"}}]})
        c = self.client(4)
        _, pid, data = c.request(PROFILES)
        r = Reader(data)
        self.assertEqual(r.take("I"), len(data))
        self.assertEqual([r.text() for _ in range(r.take("H"))], ["Gaming"])
        _, pid, data = c.request(PLUGINS)
        self.assertEqual(struct.unpack_from("<IH", data), (len(data), 0))

    def test_loading_a_profile_applies_it(self):
        self.ctl.update_settings({"profiles": [{"name": "Gaming", "hardware": {"mode": 5, "color": "00ff00"}}]})
        c = self.client(4)
        c.send(LOAD_PROFILE, b"Gaming\0")
        self.assertTrue(wait_for(lambda: self.kb.visible()[:2] == ("effect", 0x05)))


class Lighting(_Server):
    def leds(self, c):
        return [name for name, _ in self.controller(c)["leds"]]

    def test_the_client_colours_every_key(self):
        c = self.client()
        n = len(self.leds(c))
        c.send(CUSTOM_MODE)
        c.send(UPDATE_LEDS, leds_packet([color(255, 0, 0)] * n))
        self.assertTrue(wait_for(lambda: key(self.kb, "Q") == ("direct", (255, 0, 0))))
        self.assertEqual(self.effect(), "openrgb")

    def test_colours_reach_the_leds_as_sent_like_openrgb(self):
        c = self.client()
        n = len(self.leds(c))
        c.send(UPDATE_LEDS, leds_packet([color(128, 0, 0)] * n))
        self.assertTrue(wait_for(lambda: key(self.kb, "Q") == ("direct", (128, 0, 0))), "no gamma: OpenRGB sends LED values")

    def test_each_led_is_the_key_its_name_says(self):
        c = self.client()
        names = self.leds(c)
        colors = [color(0, 0, 0)] * len(names)
        colors[names.index("Key: Escape")] = color(255, 0, 0)
        c.send(UPDATE_LEDS, leds_packet(colors))
        self.assertTrue(wait_for(lambda: key(self.kb, "Esc") == ("direct", (255, 0, 0))))
        self.assertEqual(key(self.kb, "Q"), ("direct", (0, 0, 0)))

    def test_zone_and_single_led_updates(self):
        c = self.client()
        names = self.leds(c)
        body = struct.pack("<IH", 0, len(names)) + b"".join([color(0, 0, 255)] * len(names))
        c.send(UPDATE_ZONE, struct.pack("<I", 4 + len(body)) + body)
        self.assertTrue(wait_for(lambda: key(self.kb, "Q") == ("direct", (0, 0, 255))))
        c.send(UPDATE_ONE, struct.pack("<i", names.index("Key: W")) + color(0, 255, 0))
        self.assertTrue(wait_for(lambda: key(self.kb, "W") == ("direct", (0, 255, 0))))
        self.assertEqual(key(self.kb, "Q"), ("direct", (0, 0, 255)))

    def test_indicators_still_show_on_top(self):
        c = self.client()
        c.send(UPDATE_LEDS, leds_packet([color(0, 0, 255)] * len(self.leds(c))))
        self.caps = True
        self.assertTrue(wait_for(lambda: key(self.kb, "CapsLock") == ("direct", (255, 255, 255))))

    def test_choosing_a_built_in_effect_stores_it_on_the_keyboard(self):
        c = self.client(4)
        d = self.controller(c)
        i = next(k for k, m in enumerate(d["modes"]) if m["name"] == "Breathing")
        m = d["modes"][i]
        c.send(UPDATE_MODE, (lambda b: struct.pack("<Ii", 8 + len(b), i) + b)(mode_data(m, 4, brightness=2, colors=[(0, 255, 0)])))
        self.assertTrue(wait_for(lambda: self.kb.visible()[:2] == ("effect", 0x05)))
        info = self.ctl.kb.info()
        self.assertEqual((info["brightness"], info["color"]), (2, "00ff00"))

    def test_when_the_client_leaves_your_lighting_comes_back(self):
        self.ctl.run_live("rainbow", {"speed": 0.2})
        c = self.client()
        c.send(UPDATE_LEDS, leds_packet([color(255, 0, 0)] * len(self.leds(c))))
        self.assertTrue(wait_for(lambda: self.effect() == "openrgb"))
        c.close()
        self.assertTrue(wait_for(lambda: self.effect() == "rainbow"))
        self.assertEqual(self.ctl.engine.params, {"speed": 0.2})

    def test_your_choice_in_the_app_wins_until_the_client_asks_again(self):
        c = self.client()
        n = len(self.leds(c))
        c.send(UPDATE_LEDS, leds_packet([color(255, 0, 0)] * n))
        self.assertTrue(wait_for(lambda: self.effect() == "openrgb"))
        self.ctl.run_live("breathing", {})
        c.send(UPDATE_LEDS, leds_packet([color(0, 255, 0)] * n))
        time.sleep(0.4)
        self.assertEqual(self.effect(), "breathing", "frames alone don't take it back")
        c.send(CUSTOM_MODE)
        c.send(UPDATE_LEDS, leds_packet([color(0, 255, 0)] * n))
        self.assertTrue(wait_for(lambda: key(self.kb, "Q") == ("direct", (0, 255, 0))), "switching to Direct again does")


class Robustness(_Server):
    def closed_soon(self, c):
        """The server hung up on this client (not just silence)."""
        c.s.settimeout(2)
        with self.assertRaises(ConnectionError):
            c.recv()

    def test_garbage_closes_that_connection_only(self):
        bad = Client(self.orgb)
        bad.send(0, magic=b"NOPE")
        self.closed_soon(bad)
        huge = Client(self.orgb)
        huge.send(UPDATE_LEDS, size=64 << 20)  # claims 64 MB
        self.closed_soon(huge)
        c = self.client()
        self.assertEqual(struct.unpack("<I", c.request(COUNT)[2])[0], 1, "the server still serves others")

    def test_unknown_packets_and_devices_are_ignored(self):
        c = self.client()
        c.send(9999, b"x")
        c.send(UPDATE_LEDS, leds_packet([color(1, 2, 3)]), dev=7)
        c.send(UPDATE_ONE, struct.pack("<i", 5000) + color(1, 2, 3))
        self.assertEqual(struct.unpack("<I", c.request(COUNT)[2])[0], 1)

    def test_it_listens_on_this_pc_only(self):
        self.assertEqual(self.ctl.openrgb.address[0], "127.0.0.1")


class Settings(_Server):
    def test_off_by_default_and_switchable(self):
        from evision_rgb import settings as st
        self.assertFalse(st.DEFAULTS["openrgb"]["enabled"])
        self.assertEqual(st.DEFAULTS["openrgb"]["port"], 6742)
        code, s = self.api("/openrgb")
        self.assertEqual((code, s["enabled"], s["listening"]), (200, True, True))
        c = self.client()
        self.assertTrue(wait_for(lambda: "spec client" in self.api("/openrgb")[1]["clients"]))
        self.api("/openrgb", {"enabled": False})
        self.assertFalse(self.api("/openrgb")[1]["listening"])
        with self.assertRaises(OSError):
            Client(self.orgb).request(COUNT)

    def test_a_port_already_in_use_is_reported(self):
        busy = socket.socket()
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        self.addCleanup(busy.close)
        code, s = self.api("/openrgb", {"enabled": True, "port": busy.getsockname()[1]})
        self.assertFalse(s["listening"])
        self.assertIn("in use", s["error"])

    def test_the_app_window_has_the_switch(self):
        with open(os.path.join(WEB, "index.html"), encoding="utf-8") as f:
            html = f.read()
        self.assertIn('id="orgbOn"', html)
        self.assertIn('id="orgbState"', html)
        with open(os.path.join(WEB, "app.js"), encoding="utf-8") as f:
            self.assertIn('"/openrgb"', f.read())


class RealClient(_Server):
    """The openrgb-python library (what Home Assistant uses) as a real third-party client."""

    def setUp(self):
        try:
            import openrgb  # noqa: F401
        except ImportError:
            self.skipTest("openrgb-python not installed")
        super().setUp()

    def test_openrgb_python_sees_and_lights_the_keyboard(self):
        from openrgb import OpenRGBClient
        from openrgb.utils import DeviceType, RGBColor
        cli = OpenRGBClient("127.0.0.1", self.orgb, name="openrgb-python spec")
        self.addCleanup(cli.disconnect)
        (dev,) = cli.devices
        self.assertEqual((dev.name, dev.type), ("AJAZZ AK820", DeviceType.KEYBOARD))
        self.assertEqual(len(dev.leds), len(KEYS))
        dev.set_mode("direct")
        dev.set_color(RGBColor(0, 0, 255))
        self.assertTrue(wait_for(lambda: key(self.kb, "Q") == ("direct", (0, 0, 255))))
        dev.leds[[l.name for l in dev.leds].index("Key: Escape")].set_color(RGBColor(255, 0, 0))
        self.assertTrue(wait_for(lambda: key(self.kb, "Esc") == ("direct", (255, 0, 0))))

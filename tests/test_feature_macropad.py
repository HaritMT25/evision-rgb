"""Behaviour spec — macro pads: AJAZZ AKP03 / AKP153 families and their Mirabox twins ("stream docks" with LCD
keys and knobs), driven by the open-source "CRT" protocol. Written BEFORE the implementation; runs against the
strict pad simulator in fake_pad.py (no pad needed).

The pad's keys and knobs run actions: lighting profiles, live effects, keyboard brightness, media keys, hotkeys...
The local API refuses requests from web pages, because pad actions can launch programs.
"""
import io
import json
import os
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import warnings

from fake_device import install
from fake_pad import FakePad, install_pads

ALLOWED = {"CONNECT", "DIS", "LIG", "BAT", "STP", "CLE", "HAN"}


def sd():
    from evision_rgb import streamdock
    return streamdock


def marker_image(size):
    """Black key image with a red square in the top-left quarter."""
    from PIL import Image, ImageDraw
    im = Image.new("RGB", size, "black")
    ImageDraw.Draw(im).rectangle([0, 0, size[0] // 2 - 4, size[1] // 2 - 4], fill=(255, 0, 0))
    return im


def pixels(im):
    b = im.convert("RGB").tobytes()
    return [tuple(b[i:i + 3]) for i in range(0, len(b), 3)]


def redness(im, box):
    px = pixels(im.crop(box))
    return sum(p[0] - (p[1] + p[2]) / 2 for p in px) / len(px)


# =============================================================================== models & discovery
class Models(unittest.TestCase):
    def test_the_model_table_matches_the_published_layouts(self):
        M = sd().MODELS
        akp03 = M["akp03e"]
        self.assertEqual((akp03.protocol, akp03.packet, akp03.rotate), (2, 1024, 0))
        self.assertEqual([c.kind for c in akp03.keys], ["lcd"] * 6 + ["button"] * 3)
        self.assertEqual([c.input_id for c in akp03.keys], [1, 2, 3, 4, 5, 6, 0x25, 0x30, 0x31])
        self.assertEqual(akp03.keys[0].size, (60, 60))
        self.assertEqual([(k.turn_ids, k.input_id) for k in akp03.knobs],
                         [((0x90, 0x91), 0x33), ((0x50, 0x51), 0x35), ((0x60, 0x61), 0x34)])
        rev2 = M["akp03e-rev2"]
        self.assertEqual((rev2.protocol, rev2.rotate, rev2.keys[0].size), (3, 90, (64, 64)))
        akp153 = M["akp153"]
        self.assertEqual((akp153.protocol, akp153.packet, akp153.rotate), (1, 512, 270))
        self.assertEqual(len(akp153.keys), 18)
        self.assertEqual([c.image_id for c in akp153.keys[:6]], [0x0D, 0x0A, 0x07, 0x04, 0x01, 0x10])
        self.assertEqual([c.image_id for c in akp153.keys[12:]], [0x0F, 0x0C, 0x09, 0x06, 0x03, 0x12])
        self.assertEqual((akp153.keys[0].size, akp153.keys[5].size), ((85, 85), (80, 80)))
        e2 = M["akp153e-rev2"]
        self.assertEqual((e2.protocol, e2.packet, e2.keys[0].size, e2.keys[5].size), (3, 1024, (96, 96), (80, 80)))
        self.assertEqual(M["akp153e"].usb, ((0x0300, 0x1010),))
        self.assertEqual(M["akp03e-rev2"].usb, ((0x0300, 0x3002),))

    def test_supported_pads_are_found_by_usb_id_and_their_vendor_interface(self):
        pad = FakePad("akp03e-rev2")
        install_pads(self, pad, extra=[
            {"vendor_id": 0x0300, "product_id": 0x3002, "usage_page": 0x0001, "usage": 6, "path": b"keyboard-iface"},
            {"vendor_id": 0x0300, "product_id": 0x9999, "usage_page": 0xFFA0, "usage": 1, "path": b"unknown-pid"},
            {"vendor_id": 0x320F, "product_id": 0x505B, "usage_page": 0xFF1C, "usage": 0x92, "path": b"ak820"}])
        found = sd().scan()
        self.assertEqual([(m.id, p) for m, p in found], [("akp03e-rev2", b"fakepad-1")])


# =============================================================================== wire protocol
class Protocol(unittest.TestCase):
    def open(self, model="akp03e-rev2"):
        self.pad = FakePad(model)
        install_pads(self, self.pad)
        dock = sd().StreamDock(sd().MODELS[model], self.pad.path)
        self.addCleanup(dock.close)
        return dock

    def test_every_packet_is_a_framed_allow_listed_command(self):
        for model in ("akp153", "akp03e", "akp03e-rev2", "akp153e-rev2"):
            d = self.open(model)
            d.wake(); d.set_brightness(70)
            d.set_image(0, sd().encode(d.model, 0, marker_image(d.model.keys[0].size)))
            d.refresh(); d.heartbeat(); d.clear(0); d.clear(); d.sleep()
            self.assertEqual(self.pad.violations, [], model)
            self.assertTrue(set(self.pad.verbs()) <= ALLOWED, self.pad.verbs())

    def test_commands_outside_the_allow_list_are_never_sent(self):
        d = self.open()
        for verb, args in ((b"LOG", b"\x00\x12\xc3\xc0\x01"), (b"QUCMD", b""), (b"MOD", b"\x00\x001"), (b"UPG", b"")):
            with self.assertRaises(sd().PadError):
                d.command(verb, args)
        self.assertEqual(self.pad.commands, [])

    def test_brightness_is_0_to_100(self):
        d = self.open()
        for bad in (-1, 101, "50"):
            with self.assertRaises(ValueError):
                d.set_brightness(bad)
        d.set_brightness(55)
        self.assertEqual(self.pad.brightness, 55)

    def test_images_declare_their_exact_length_and_stay_under_10_kb(self):
        import random
        from PIL import Image
        d = self.open("akp153e-rev2")
        noise = Image.frombytes("RGB", (96, 96), bytes(random.getrandbits(8) for _ in range(96 * 96 * 3)))
        jpeg = sd().encode(d.model, 0, noise)
        self.assertLessEqual(len(jpeg), 10240)
        d.set_image(0, jpeg); d.refresh()
        self.assertEqual(self.pad.violations, [])
        self.assertEqual(self.pad.uploads[-1], (0x0D, jpeg))

    def test_key_images_have_the_models_size_and_orientation(self):
        for model in ("akp03e", "akp03e-rev2", "akp153", "akp153e-rev2"):
            d = self.open(model)
            for i in (0, 5):
                d.set_image(i, sd().encode(d.model, i, marker_image(d.model.keys[i].size)))
            d.refresh()
            self.assertEqual(self.pad.violations, [], model)
            for i in (0, 5):
                im = self.pad.image(i)  # as the user sees it on the pad
                w, h = im.size
                self.assertGreater(redness(im, (2, 2, w // 3, h // 3)), 150, f"{model} key {i}: marker top-left")
                self.assertLess(redness(im, (w * 2 // 3, h * 2 // 3, w - 2, h - 2)), 40, f"{model} key {i}")

    def test_images_show_only_after_refresh(self):
        d = self.open()
        for i in range(6):
            d.set_image(i, sd().encode(d.model, i, marker_image((64, 64))))
        self.assertEqual(self.pad.shown, {})
        d.refresh()
        self.assertEqual(len(self.pad.shown), 6)
        self.assertEqual(self.pad.verbs().count("STP"), 1)

    def test_clearing_one_key_or_all(self):
        d = self.open()
        for i in range(2):
            d.set_image(i, sd().encode(d.model, i, marker_image((64, 64))))
        d.refresh(); d.clear(0); d.refresh()
        self.assertEqual(set(self.pad.shown), {2})
        d.clear(); d.refresh()
        self.assertEqual(self.pad.shown, {})

    def test_plain_buttons_have_no_screen(self):
        d = self.open("akp03e")
        with self.assertRaises(ValueError):
            d.set_image(6, b"\xff\xd8")
        self.assertEqual(self.pad.commands, [])


class Input(unittest.TestCase):
    def open(self, model):
        self.pad = FakePad(model)
        install_pads(self, self.pad)
        dock = sd().StreamDock(sd().MODELS[model], self.pad.path)
        self.addCleanup(dock.close)
        return dock

    def events(self, d):
        return [(e.control, e.index, e.kind, e.delta) for e in d.read_events(50)]

    def test_protocol_3_reports_press_and_release_separately(self):
        d = self.open("akp03e-rev2")
        self.pad.hold_key(0, True)
        self.assertEqual(self.events(d), [("key", 0, "down", 0)])
        self.pad.hold_key(0, False)
        self.assertEqual(self.events(d), [("key", 0, "up", 0)])

    def test_protocol_1_and_2_turn_each_click_into_press_and_release(self):
        d = self.open("akp153")
        self.pad.press_key(4)
        self.assertEqual(self.events(d), [("key", 4, "down", 0), ("key", 4, "up", 0)])
        d = self.open("akp03e")
        self.pad.press_key(7)
        self.assertEqual(self.events(d), [("key", 7, "down", 0), ("key", 7, "up", 0)])

    def test_firmware_that_reports_release_as_0x02_is_understood(self):
        # the vendor SDK: 0x01 = press, 0x02 = release (patched / other firmware sends 0x00)
        d = self.open("akp03e-rev2")
        code = d.model.keys[2].input_id
        self.pad._report(code, 0x01); self.pad._report(code, 0x02)
        self.assertEqual(self.events(d), [("key", 2, "down", 0), ("key", 2, "up", 0)])

    def test_write_acknowledgements_and_garbage_are_not_input(self):
        d = self.open("akp03e-rev2")
        self.pad.ack(); self.pad.garbage()
        self.assertEqual(self.events(d), [])

    def test_knob_turns_and_presses(self):
        d = self.open("akp03e-rev2")
        self.pad.turn_knob(1, +2); self.pad.turn_knob(0, -1); self.pad.press_knob(2)
        self.assertEqual(self.events(d), [("knob", 1, "turn", 1), ("knob", 1, "turn", 1), ("knob", 0, "turn", -1),
                                          ("knob", 2, "down", 0), ("knob", 2, "up", 0)])

    def test_akp153_keys_are_reported_in_reading_order(self):
        d = self.open("akp153")
        for i in range(18):
            self.pad.press_key(i)
        got = [e.index for e in d.read_events(50) if e.kind == "down"]
        self.assertEqual(got, list(range(18)))

    def test_a_lost_release_never_leaves_a_key_stuck(self):
        d = self.open("akp03e-rev2")
        self.pad.hold_key(3, True); self.pad.hold_key(3, True)   # repeated press report
        self.assertEqual(self.events(d), [("key", 3, "down", 0)])
        self.pad._report(0x00, 0)                                  # "everything released"
        self.assertEqual(self.events(d), [("key", 3, "up", 0)])


# =============================================================================== the app side
class AppCase(unittest.TestCase):
    MODEL = "akp03e-rev2"

    def setUp(self):
        warnings.simplefilter("ignore", ResourceWarning)
        self.kb = install(self)
        import evision_rgb.settings as st
        tmp = tempfile.mkdtemp()
        for attr, val in (("DIR", tmp), ("PATH", os.path.join(tmp, "settings.json"))):
            self.addCleanup(setattr, st, attr, getattr(st, attr))
            setattr(st, attr, val)
        import datetime as _dt
        neutral = {"caps": lambda: False, "mic_muted": lambda: False, "idle": lambda: 0.0, "locked": lambda: False,
                   "now": lambda: _dt.datetime(2026, 1, 1, 12)}
        self.pad = FakePad(self.MODEL)
        self.hid = install_pads(self, self.pad)
        from evision_rgb.server import Controller, build_server
        self.ctl = Controller(probes=neutral, hook_keys=False)
        self.httpd = build_server(self.ctl, 0)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(lambda: (self.httpd.shutdown(), self.ctl.close()))
        self.m = self.ctl.pads
        self.m.dispatch = lambda fn: fn()       # run actions inline (deterministic)
        self.sent = []
        self.m.send_keys = lambda keys: self.sent.append(tuple(keys))
        self.now = 1000.0
        self.m.clock = lambda: self.now

    def tick(self, dt=0.0):
        self.now += dt
        self.m.tick()

    def call(self, path, body=None, headers=None):
        data = None if body is None else json.dumps(body).encode()
        h = {"Content-Type": "application/json", **(headers or {})}
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=data, headers=h)
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                raw = r.read()
                return r.status, (json.loads(raw) if r.headers.get_content_type() == "application/json" else raw)
        except urllib.error.HTTPError as e:
            with e:
                raw = e.read()
                try:
                    return e.code, json.loads(raw or b"{}")
                except ValueError:
                    return e.code, {}

    def configure(self, **cfg):
        code, res = self.call("/pad", {"model": "akp03", **cfg})
        self.assertEqual(code, 200, res)
        return res


class Manager(AppCase):
    def test_connects_wakes_and_draws_every_screen_in_one_batch(self):
        self.tick()
        self.assertTrue(self.m.status()["connected"])
        self.assertTrue(self.pad.awake)
        self.assertEqual(self.pad.brightness, 80)
        self.assertEqual(sorted(self.pad.shown), [1, 2, 3, 4, 5, 6])
        self.assertEqual(self.pad.verbs().count("STP"), 1)
        self.assertEqual(self.pad.violations, [])

    def test_a_heartbeat_keeps_the_pad_awake(self):
        # newer firmware reboots or re-enumerates after ~35-60 s without one
        self.tick()
        connected_at, beats, n = self.now, [], 0
        for _ in range(40):
            self.tick(1.0)
            c = self.pad.verbs().count("CONNECT")
            if c > n:
                beats.append(self.now)
                n = c
        self.assertGreaterEqual(len(beats), 4)
        gaps = [b - a for a, b in zip([connected_at] + beats, beats)]
        self.assertTrue(all(g <= 10.5 for g in gaps), gaps)

    def test_pressing_a_key_runs_its_action(self):
        self.configure(keys=[{"label": "Rainbow", "action": {"type": "effect", "effect": "rainbow"}}])
        self.tick()
        self.pad.press_key(0)
        self.tick(0.05)
        self.assertEqual(self.ctl.engine.status()["effect"], "rainbow")

    def test_a_key_can_apply_a_lighting_profile(self):
        self.call("/settings", {"profiles": [{"name": "Blue", "hardware": {"mode": 6, "color": "0000ff", "brightness": 4}}]})
        self.configure(keys=[{"label": "x"}, {"label": "Blue", "action": {"type": "profile", "name": "Blue"}}])
        self.tick()
        self.pad.press_key(1)
        self.tick(0.05)
        self.assertEqual(bytes(self.kb.config[6:9]), b"\x00\x00\xff")

    def test_a_knob_steps_the_keyboard_brightness_within_0_to_4(self):
        self.configure(knobs=[{"left": {"type": "brightness", "step": -1}, "right": {"type": "brightness", "step": 1}}])
        self.tick()
        self.pad.turn_knob(0, +3)            # starts at 3: 4, then stays at 4
        self.tick(0.05)
        self.assertEqual(self.kb.config[2], 4)
        self.pad.turn_knob(0, -1)
        self.tick(0.05)
        self.assertEqual(self.kb.config[2], 3)

    def test_a_dial_cycles_through_the_saved_profiles(self):
        ps = [{"name": n, "hardware": {"mode": 6, "color": c, "brightness": 4}}
              for n, c in (("A", "ff0000"), ("B", "00ff00"), ("C", "0000ff"))]
        self.call("/settings", {"profiles": ps})
        self.configure(knobs=[{}, {"left": {"type": "profile_prev"}, "right": {"type": "profile_next"}}])
        self.tick()
        seen = []
        for _ in range(4):
            self.pad.turn_knob(1, +1)
            self.tick(0.05)
            seen.append(bytes(self.kb.config[6:9]).hex())
        self.assertEqual(seen, ["ff0000", "00ff00", "0000ff", "ff0000"])

    def test_the_running_effect_is_highlighted_on_its_key(self):
        self.configure(keys=[{"label": "Rainbow", "action": {"type": "effect", "effect": "rainbow"}},
                             {"label": "Breathe", "action": {"type": "effect", "effect": "breathing"}}])
        self.tick()
        plain0, plain1 = self.pad.image(0), self.pad.image(1)
        self.pad.press_key(0)
        self.tick(0.05); self.tick(1.0)
        self.assertNotEqual(self.pad.image(0).tobytes(), plain0.tobytes(), "rainbow key highlighted")
        self.pad.press_key(1)
        self.tick(0.05); self.tick(1.0)
        lit0, lit1 = self.pad.image(0), self.pad.image(1)
        self.assertNotEqual(lit1.tobytes(), plain1.tobytes(), "breathing key highlighted")
        self.assertLess(sum(abs(a - b) for a, b in zip(lit0.tobytes(), plain0.tobytes())) / (64 * 64 * 3), 6,
                        "rainbow key back to normal")

    def test_unplugging_and_replugging_recovers(self):
        self.tick()
        self.pad.unplugged = True
        self.tick(0.1)
        self.assertFalse(self.m.status()["connected"])
        self.pad.unplugged = False
        self.pad.shown.clear()
        self.tick(3.0)
        self.assertTrue(self.m.status()["connected"])
        self.assertEqual(self.pad.opened, 2)
        self.assertEqual(sorted(self.pad.shown), [1, 2, 3, 4, 5, 6])

    def test_without_a_pad_nothing_is_opened(self):
        self.pad.unplugged = True
        for _ in range(3):
            self.tick(3.0)
        self.assertFalse(self.m.status()["connected"])
        self.assertEqual(self.pad.opened, 0)

    def test_keys_without_an_action_do_nothing(self):
        self.configure(keys=[{"label": "", "action": {"type": "none"}}])
        self.tick()
        before = (bytes(self.kb.config), self.ctl.engine.status()["effect"])
        self.pad.press_key(0)
        self.tick(0.05)
        self.assertEqual((bytes(self.kb.config), self.ctl.engine.status()["effect"]), before)

    def test_hotkey_and_media_actions_send_keys(self):
        self.configure(keys=[{"label": "Mute mic", "action": {"type": "hotkey", "keys": "ctrl+shift+m"}},
                             {"label": "Play", "action": {"type": "media", "key": "play_pause"}}])
        self.tick()
        self.pad.press_key(0); self.pad.press_key(1)
        self.tick(0.05)
        self.assertEqual(self.sent, [("ctrl", "shift", "m"), ("media_play_pause",)])

    def test_the_pad_config_survives_a_restart(self):
        self.configure(brightness=40, keys=[{"label": "Hi", "color": "ff0000", "action": {"type": "stop"}}])
        import evision_rgb.settings as st
        saved = st.load()["pads"]["akp03"]
        self.assertEqual((saved["brightness"], saved["keys"][0]["label"]), (40, "Hi"))


class PadAPI(AppCase):
    def test_status_lists_the_supported_models_and_the_connected_pad(self):
        code, st = self.call("/pad")
        self.assertEqual(code, 200)
        names = [m["name"] for m in st["supported"]]
        self.assertIn("AJAZZ AKP03E (rev. 2)", names)
        self.assertIn("AJAZZ AKP153", names)
        self.tick()
        st = self.call("/pad")[1]
        self.assertEqual((st["connected"], st["model"]["id"], st["model"]["family"]), (True, "akp03e-rev2", "akp03"))
        self.assertEqual(len(st["model"]["keys"]), 9)
        self.assertEqual(len(st["model"]["knobs"]), 3)

    def test_invalid_configs_are_refused_and_change_nothing(self):
        good = self.call("/pad")[1]["configs"]["akp03"]
        bad = [{"model": "nope"},
               {"model": "akp03", "brightness": 101},
               {"model": "akp03", "keys": [{"label": "x"}] * 10},
               {"model": "akp03", "keys": [{"label": "x", "color": "zz0000"}]},
               {"model": "akp03", "keys": [{"label": "x", "action": {"type": "format_disk"}}]},
               {"model": "akp03", "keys": [{"label": "x", "action": {"type": "effect", "effect": "nope"}}]},
               {"model": "akp03", "keys": [{"label": "x", "action": {"type": "hotkey", "keys": "ctrl+nokey"}}]},
               {"model": "akp03", "keys": [{"label": "x", "action": {"type": "media", "key": "eject"}}]},
               {"model": "akp03", "knobs": [{"spin": {"type": "stop"}}]},
               {"model": "akp03", "knobs": [{}, {}, {}, {}]}]
        for body in bad:
            code, res = self.call("/pad", body)
            self.assertEqual(code, 400, body)
            self.assertIn("error", res)
        self.assertEqual(self.call("/pad")[1]["configs"]["akp03"], good)

    def test_press_runs_a_keys_action_even_without_a_pad(self):
        self.pad.unplugged = True
        self.configure(keys=[{"label": "Rainbow", "action": {"type": "effect", "effect": "rainbow"}}])
        code, _ = self.call("/pad/press", {"model": "akp03", "key": 0})
        self.assertEqual(code, 200)
        self.assertEqual(self.ctl.engine.status()["effect"], "rainbow")

    def test_key_previews_are_served_as_png(self):
        code, png = self.call("/pad/key.png?model=akp03e-rev2&i=0")
        self.assertEqual(code, 200)
        from PIL import Image
        im = Image.open(io.BytesIO(png))
        self.assertEqual((im.format, im.size), ("PNG", (64, 64)))


class LocalOnlyAPI(AppCase):
    """Pad actions can start programs, so web pages must not be able to drive the local API."""

    def raw(self, method, path, body=b"{}", headers=None):
        import http.client
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request(method, path, body=body if method == "POST" else None, headers=headers or {})
        r = c.getresponse()
        r.read()
        c.close()
        return r.status

    def test_posts_without_a_json_content_type_are_refused(self):
        code = self.raw("POST", "/run", json.dumps({"effect": "rainbow"}).encode(), {"Content-Type": "text/plain"})
        self.assertEqual(code, 415)
        self.assertIsNone(self.ctl.engine.status()["effect"])

    def test_requests_from_other_web_origins_are_refused(self):
        code = self.raw("POST", "/run", json.dumps({"effect": "rainbow"}).encode(),
                        {"Content-Type": "application/json", "Origin": "https://evil.example"})
        self.assertEqual(code, 403)
        self.assertIsNone(self.ctl.engine.status()["effect"])

    def test_foreign_host_names_are_refused(self):  # DNS rebinding
        for method, path in (("GET", "/settings"), ("POST", "/stop")):
            code = self.raw(method, path, headers={"Host": f"evil.example:{self.port}", "Content-Type": "application/json"})
            self.assertEqual(code, 403, path)

    def test_the_app_itself_still_works(self):
        ok = {"Content-Type": "application/json", "Origin": f"http://127.0.0.1:{self.port}"}
        self.assertEqual(self.raw("POST", "/stop", b"{}", ok), 200)
        self.assertEqual(self.raw("POST", "/stop", b"{}", {**ok, "Origin": f"http://localhost:{self.port}",
                                                           "Host": f"localhost:{self.port}"}), 200)
        self.assertEqual(self.raw("GET", "/"), 200)


class AgentTools(unittest.TestCase):
    def test_mcp_can_see_and_program_the_pad(self):
        import asyncio
        from evision_rgb import mcp_server
        names = {t.name for t in asyncio.run(mcp_server.mcp.list_tools())}
        self.assertTrue({"pad_status", "set_pad_key"} <= names, names)


if __name__ == "__main__":
    unittest.main()

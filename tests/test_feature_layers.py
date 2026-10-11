"""Behaviour spec — feature 4: layer editor & profile sharing (written before the implementation).

Layers stack live effects like an image editor: each layer has a key mask, a blend mode and an opacity,
and the bottom layer can be the keyboard's own stored effect. Profiles (hardware effect + custom colours
+ live effect) can be exported as a file or a short share code, imported on another PC and applied in
one step. The custom-key painter gets undo/redo.
"""
import base64
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import warnings
import zlib

import numpy as np

from fake_device import install
from evision_rgb.effects import INDEX, N
from evision_rgb.keyboard import frame_from

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
OLD_GREEN = ("effect", 0x06, b"\x00\xff\x00", 3)  # what the simulated keyboard shows before each test


def k(c, name):
    return np.asarray(c)[INDEX[name.lower()]]


def layers(*specs):
    from evision_rgb.effects import EFFECTS
    return EFFECTS["layers"](layers=list(specs))


class Stacking(unittest.TestCase):
    def test_a_layer_only_paints_its_keys(self):
        e = layers({"effect": "solid", "params": {"color": "0000ff"}},
                   {"effect": "solid", "params": {"color": "ff0000"}, "keys": ["W", "A", "S", "D"]})
        c = e.render(0.0)
        self.assertTrue(np.allclose(k(c, "W"), [1, 0, 0]))
        self.assertTrue(np.allclose(k(c, "Q"), [0, 0, 1]))

    def test_later_layers_are_on_top(self):
        e = layers({"effect": "solid", "params": {"color": "ff0000"}},
                   {"effect": "solid", "params": {"color": "00ff00"}})
        self.assertTrue(np.allclose(k(e.render(0), "Q"), [0, 1, 0]))

    def test_opacity_mixes_with_the_layers_below(self):
        e = layers({"effect": "solid", "params": {"color": "000000"}},
                   {"effect": "solid", "params": {"color": "ffffff"}, "opacity": 0.5})
        self.assertTrue(np.allclose(k(e.render(0), "Q"), [0.5, 0.5, 0.5], atol=0.01))

    def test_blend_modes(self):
        base = {"effect": "solid", "params": {"color": "808080"}}      # 0.5 grey
        top = {"effect": "solid", "params": {"color": "ff4000"}}        # (1, 0.25, 0)
        g = 128 / 255
        cases = {"normal": [1.0, 0.25, 0.0], "add": [1.0, min(1, g + 0.25), g], "max": [1.0, g, g],
                 "multiply": [g, g * 0.25, 0], "screen": [1.0, 1 - (1 - g) * 0.75, g]}
        for mode, want in cases.items():
            c = k(layers(base, {**top, "blend": mode}).render(0), "Q")
            self.assertTrue(np.allclose(c, want, atol=0.01), f"{mode}: {c} != {want}")

    def test_disabled_layers_are_skipped(self):
        e = layers({"effect": "solid", "params": {"color": "0000ff"}},
                   {"effect": "solid", "params": {"color": "ff0000"}, "enabled": False})
        self.assertTrue(np.allclose(k(e.render(0), "Q"), [0, 0, 1]))

    def test_invalid_layers_are_rejected_up_front(self):
        from evision_rgb.effects import EFFECTS
        for bad in ("nope", [], [{"effect": "nope"}], [{"effect": "solid", "blend": "dodge"}],
                    [{"effect": "solid", "keys": ["NumPad7"]}], [{"effect": "solid", "keys": "W"}],
                    [{"effect": "solid", "opacity": 2}], [{"effect": "solid", "opacity": "half"}],
                    [{"effect": "solid", "colour": "red"}], [{"effect": "layers"}],
                    [{"effect": "solid", "params": {"bogus": 1}}], [{"effect": "music", "params": {"style": "nope"}}],
                    [{"effect": "solid"}] * 17):
            with self.assertRaises(ValueError, msg=str(bad)[:80]):
                EFFECTS["layers"](layers=bad)

    def test_sub_effects_are_started_and_stopped_but_disabled_ones_never_run(self):
        from evision_rgb import effects as E
        calls = []

        class Probe(E.Effect):
            name = "probe"

            def __init__(self, tag="on"):
                self.tag = tag

            def start(self): calls.append("start " + self.tag)
            def stop(self): calls.append("stop " + self.tag)
            def render(self, t): return np.zeros((N, 3))
        E.EFFECTS["probe"] = Probe
        self.addCleanup(E.EFFECTS.pop, "probe")
        e = layers({"effect": "probe"}, {"effect": "probe", "params": {"tag": "off"}, "enabled": False},
                   {"effect": "probe"})
        e.start(); e.render(0); e.stop()
        self.assertEqual(calls, ["start on", "start on", "stop on", "stop on"])

    def test_reports_the_fastest_frame_rate_its_layers_need(self):
        e = layers({"effect": "solid"}, {"effect": "rainbow"})
        self.assertGreaterEqual(e.fps, 60)

    def test_device_value_layers_reach_the_keyboard_unchanged(self):
        # the exact firmware engine renders LED values the keyboard uses as-is (no gamma); stacked under
        # other layers it must still arrive on the keyboard exactly as the firmware would show it
        from evision_rgb import effects as E

        class Raw(E.Effect):
            name = "rawprobe"
            raw = True

            def render(self, t): return np.full((N, 3), 0.6)
        E.EFFECTS["rawprobe"] = Raw
        self.addCleanup(E.EFFECTS.pop, "rawprobe")
        frame = E.to_frame(layers({"effect": "rawprobe"}).render(0))
        slot = int(E.SLOTS[INDEX["q"]])
        self.assertEqual(frame[slot * 3], 153)  # 0.6 * 255

    def test_keyboard_layer_needs_the_app(self):
        from evision_rgb import emulate
        prev = emulate.set_stored_effect(None)
        self.addCleanup(emulate.set_stored_effect, prev)
        with self.assertRaises(ValueError):
            layers({"effect": "keyboard"})


class AppCase(unittest.TestCase):
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
        from evision_rgb.server import Controller, build_server
        self.ctl = Controller(probes=neutral, hook_keys=False)
        self.httpd = build_server(self.ctl, 0)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(lambda: (self.httpd.shutdown(), self.ctl.close()))

    def call(self, path, body=None):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=data,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            with e:
                return e.code, json.loads(e.read() or b"{}")


class LayersInTheApp(AppCase):
    def test_run_layers_through_the_api(self):
        code, st = self.call("/run", {"effect": "layers", "params": {"layers": [
            {"effect": "solid", "params": {"color": "000040"}},
            {"effect": "solid", "params": {"color": "ff0000"}, "keys": ["Esc"]}]}})
        self.assertEqual(code, 200)
        time.sleep(0.3)
        f = self.call("/frame")[1]["colors"]
        self.assertEqual(f["Esc"], "ff0000")
        self.assertEqual(f["Q"], "000040")

    def test_bad_layers_are_400_and_do_not_stop_the_current_effect(self):
        self.call("/run", {"effect": "rainbow"})
        code, body = self.call("/run", {"effect": "layers", "params": {"layers": [{"effect": "nope"}]}})
        self.assertEqual(code, 400)
        self.assertIn("nope", body["error"])
        self.assertEqual(self.call("/status")[1]["effect"], "rainbow")

    def test_keyboard_layer_uses_the_stored_hardware_effect_as_the_base(self):
        self.call("/effect", {"mode": "normal", "color": "00ff00", "brightness": 4})
        code, _ = self.call("/run", {"effect": "layers", "params": {"layers": [
            {"effect": "keyboard"},
            {"effect": "solid", "params": {"color": "ff0000"}, "keys": ["Esc"]}]}})
        self.assertEqual(code, 200)
        time.sleep(0.4)
        f = self.call("/frame")[1]["colors"]
        self.assertEqual(f["Esc"], "ff0000")
        self.assertGreater(int(f["Q"][2:4], 16), 0x80, "keyboard's own green underneath")
        self.assertLess(int(f["Q"][0:2], 16), 0x30)

    def test_changing_the_stored_effect_keeps_a_keyboard_based_layer_stack_on_top(self):
        self.call("/run", {"effect": "layers", "params": {"layers": [
            {"effect": "keyboard"}, {"effect": "solid", "params": {"color": "ff0000"}, "keys": ["Esc"]}]}})
        time.sleep(0.2)
        self.assertEqual(self.call("/effect", {"mode": "normal", "color": "0000ff", "brightness": 4})[0], 200)
        time.sleep(0.4)
        self.assertEqual(self.call("/status")[1]["effect"], "layers")
        f = self.call("/frame")[1]["colors"]
        self.assertEqual(f["Esc"], "ff0000")
        self.assertGreater(int(f["Q"][4:6], 16), 0x80, "the new stored blue shows under the layers")
        self.assertEqual(self.call("/settings")[1]["last_live"]["effect"], "layers")

    def test_other_live_effects_still_end_when_a_hardware_effect_is_chosen(self):
        self.call("/run", {"effect": "rainbow"})
        self.call("/effect", {"mode": "normal", "color": "0000ff"})
        self.assertFalse(self.call("/status")[1]["live"])

    def test_keyboard_layer_shows_the_stored_custom_colours(self):
        self.call("/custom", {"colors": {"W": "ff0000"}, "default": "000000"})
        self.call("/run", {"effect": "layers", "params": {"layers": [
            {"effect": "keyboard"}, {"effect": "solid", "params": {"color": "0000ff"}, "keys": ["Esc"]}]}})
        time.sleep(0.4)
        f = self.call("/frame")[1]["colors"]
        self.assertEqual((f["W"], f["Esc"], f["Q"]), ("ff0000", "0000ff", "000000"))


class Sharing(AppCase):
    PROFILE = {"name": "Gaming", "hardware": {"mode": 6, "brightness": 3, "speed": 2, "color": "ff0000",
                                              "direction": 0, "random": False, "colorset": 0},
               "live": {"effect": "layers", "params": {"layers": [
                   {"effect": "keyboard"}, {"effect": "solid", "params": {"color": "ff0000"}, "keys": ["W"]}]}}}

    def doc(self, profile=None):
        return {"format": "evision-rgb-profile", "version": 1, "profile": profile or self.PROFILE}

    def test_export_produces_a_versioned_self_contained_file(self):
        self.call("/settings", {"profiles": [self.PROFILE]})
        code, doc = self.call("/profiles/export?name=Gaming")
        self.assertEqual(code, 200)
        self.assertEqual(doc["format"], "evision-rgb-profile")
        self.assertEqual(doc["version"], 1)
        self.assertEqual(doc["profile"], self.PROFILE)

    def test_import_adds_the_profile_and_round_trips(self):
        code, res = self.call("/profiles/import", self.doc())
        self.assertEqual(code, 200)
        self.assertEqual(res["name"], "Gaming")
        self.assertEqual(self.call("/profiles/export?name=Gaming")[1]["profile"], self.PROFILE)

    def test_import_never_overwrites_an_existing_profile(self):
        self.call("/settings", {"profiles": [self.PROFILE]})
        self.assertEqual(self.call("/profiles/import", self.doc())[1]["name"], "Gaming (2)")
        self.assertEqual(self.call("/profiles/import", self.doc())[1]["name"], "Gaming (3)")
        names = [p["name"] for p in self.call("/settings")[1]["profiles"]]
        self.assertEqual(names, ["Gaming", "Gaming (2)", "Gaming (3)"])

    def test_invalid_files_are_rejected_with_a_reason(self):
        P = self.PROFILE
        bad = [["not", "an", "object"],
               {"format": "something-else", "version": 1, "profile": P},
               {"format": "evision-rgb-profile", "version": 99, "profile": P},
               {"format": "evision-rgb-profile", "version": 1},
               self.doc({"name": "x"}),                                        # nothing to apply
               self.doc({"name": "x", "hardware": {"mode": 999}}),
               self.doc({"name": "x", "hardware": {"mode": 6, "brightness": 5}}),
               self.doc({"name": "x", "hardware": {"mode": 6, "colour": "ff0000"}}),
               self.doc({"name": "x", "live": {"effect": "layers", "params": {"layers": [{"effect": "nope"}]}}}),
               self.doc({"name": "x", "live": {"effect": "nope"}}),
               self.doc({"name": "x", "custom": {"NumPad7": "ff0000"}}),
               self.doc({"name": "x", "custom": {"W": "not-a-colour"}})]
        before = self.call("/settings")[1]["profiles"]
        for doc in bad:
            code, res = self.call("/profiles/import", doc)
            self.assertEqual(code, 400, doc)
            self.assertIn("error", res)
        self.assertEqual(self.call("/settings")[1]["profiles"], before)

    def test_export_of_unknown_profile_is_404(self):
        self.assertEqual(self.call("/profiles/export?name=Nope")[0], 404)

    def test_importing_a_shared_profile_never_touches_the_keyboard(self):
        n = len(self.kb.writes)
        self.assertEqual(self.call("/profiles/import", self.doc())[0], 200)
        writes = [w for w in self.kb.writes[n:] if w[3] in (0x01, 0x02, 0x06, 0x0B, 0x12, 0x13)]
        self.assertEqual(writes, [], "an imported profile only changes the keyboard when the user applies it")

    def test_share_codes_round_trip(self):
        self.call("/settings", {"profiles": [self.PROFILE]})
        code, res = self.call("/profiles/export?name=Gaming&as=code")
        self.assertEqual(code, 200)
        self.assertTrue(res["code"].startswith("evrgb1:"))
        self.assertLess(len(res["code"]), 600, "short enough to paste into a chat message")
        code, res = self.call("/profiles/import", {"code": "  " + res["code"] + "\n"})  # pasted with whitespace
        self.assertEqual((code, res["name"]), (200, "Gaming (2)"))
        stored = [p for p in self.call("/settings")[1]["profiles"] if p["name"] == "Gaming (2)"][0]
        self.assertEqual({**stored, "name": "Gaming"}, self.PROFILE)

    def test_broken_or_oversized_share_codes_are_rejected(self):
        bomb = {**self.doc(), "profile": {**self.PROFILE, "name": "x" * 5_000_000}}
        packed = base64.urlsafe_b64encode(zlib.compress(json.dumps(bomb).encode(), 9)).decode().rstrip("=")
        for code_str in ("hello", "evrgb1:!!!not-base64!!!", "evrgb1:" + base64.urlsafe_b64encode(b"junk").decode(),
                         "evrgb1:" + packed):
            code, res = self.call("/profiles/import", {"code": code_str})
            self.assertEqual(code, 400, code_str[:40])
            self.assertIn("error", res)
        self.assertIn("large", self.call("/profiles/import", {"code": "evrgb1:" + packed})[1]["error"])

    def test_names_from_shared_files_cannot_inject_markup(self):
        code, res = self.call("/profiles/import", self.doc({**self.PROFILE, "name": "<img src=x onerror=alert(1)>"}))
        self.assertEqual(code, 200)
        self.assertNotIn("<", res["name"])
        self.assertNotIn(">", res["name"])


class ApplyingProfiles(AppCase):
    def test_apply_switches_hardware_custom_and_live_without_showing_the_old_lighting(self):
        custom = {"W": "ff0000", "A": "ff0000", "S": "ff0000", "D": "ff0000"}
        p = {"name": "Typing", "hardware": {"mode": 20, "brightness": 4, "speed": 2, "color": "ffffff",
                                            "direction": 0, "random": False, "colorset": 0},
             "custom": custom,
             "live": {"effect": "layers", "params": {"layers": [
                 {"effect": "keyboard"}, {"effect": "solid", "params": {"color": "0000ff"}, "keys": ["Esc"]}]}}}
        self.call("/settings", {"profiles": [p]})
        self.call("/run", {"effect": "rainbow"})
        time.sleep(0.2)
        self.kb.history.clear()
        code, _ = self.call("/profiles/apply", {"name": "Typing"})
        self.assertEqual(code, 200)
        time.sleep(0.4)
        self.assertEqual(self.kb.config[1], 0x14, "custom mode stored on the keyboard")
        self.assertEqual(bytes(self.kb.areas[0][18 * 3:18 * 3 + 3]), b"\xff\x00\x00", "W stored red")
        self.assertNotIn(OLD_GREEN, self.kb.history, "the previous lighting must never show")
        self.assertEqual([h for h in self.kb.history if h[0] != "direct"],
                         [("custom", bytes(frame_from(custom, "000000")))], "colours and mode stored in one write")
        self.assertEqual(self.kb.visible()[0], "direct", "the profile's live layers stream on top")
        self.assertEqual(self.call("/status")[1]["effect"], "layers")
        f = self.call("/frame")[1]["colors"]
        self.assertEqual((f["Esc"], f["W"]), ("0000ff", "ff0000"))
        self.assertEqual(self.call("/settings")[1]["last_live"]["effect"], "layers", "resumed after a restart")

    def test_apply_without_live_ends_live_mode_on_the_new_hardware_effect(self):
        p = {"name": "Blue", "hardware": {"mode": 6, "brightness": 4, "speed": 2, "color": "0000ff",
                                          "direction": 0, "random": False, "colorset": 0}}
        self.call("/settings", {"profiles": [p]})
        self.call("/run", {"effect": "rainbow"})
        time.sleep(0.2)
        self.kb.history.clear()
        self.assertEqual(self.call("/profiles/apply", {"name": "Blue"})[0], 200)
        final = ("effect", 0x06, b"\x00\x00\xff", 4)
        self.assertEqual(self.kb.visible(), final)
        self.assertEqual([h for h in self.kb.history if h[0] != "direct"], [final])
        self.assertFalse(self.call("/status")[1]["live"])
        self.assertIsNone(self.call("/settings")[1]["last_live"])

    def test_apply_unknown_profile_is_404_and_changes_nothing(self):
        self.call("/run", {"effect": "rainbow"})
        self.assertEqual(self.call("/profiles/apply", {"name": "Nope"})[0], 404)
        self.assertEqual(self.call("/status")[1]["effect"], "rainbow")


class AgentTools(unittest.TestCase):
    def test_mcp_can_list_and_apply_profiles(self):
        import asyncio
        from evision_rgb import mcp_server
        names = {t.name for t in asyncio.run(mcp_server.mcp.list_tools())}
        self.assertTrue({"list_profiles", "apply_profile"} <= names, names)


@unittest.skipUnless(shutil.which("node"), "node not installed")
class UndoRedo(unittest.TestCase):
    """The editors' undo/redo history (pure JS module, run with node)."""

    def run_js(self, body):
        mod = os.path.abspath(os.path.join(ROOT, "evision_rgb", "gui", "web", "history.js")).replace("\\", "/")
        script = f"const H = require('{mod}');\n{body}"
        out = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=20)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout)

    def test_undo_and_redo_walk_through_edits(self):
        r = self.run_js("""
            const h = H.create({A: '000000'});
            h.push({A: 'ff0000'}); h.push({A: '00ff00'});
            const a = h.undo(), b = h.undo(), c = h.undo(), d = h.redo();
            console.log(JSON.stringify([a, b, c, d]));""")
        self.assertEqual(r, [{"A": "ff0000"}, {"A": "000000"}, None, {"A": "ff0000"}])

    def test_a_new_edit_clears_the_redo_stack(self):
        r = self.run_js("""
            const h = H.create({A: '000000'});
            h.push({A: 'ff0000'}); h.undo(); h.push({A: '0000ff'});
            console.log(JSON.stringify([h.redo(), h.canUndo(), h.canRedo(), h.undo(), h.undo()]));""")
        self.assertEqual(r, [None, True, False, {"A": "000000"}, None], "the undone red state is gone for good")

    def test_history_is_bounded(self):
        r = self.run_js("""
            const h = H.create({}, 5);
            for (let i = 0; i < 20; i++) h.push({i});
            let n = 0; while (h.undo()) n++;
            console.log(JSON.stringify(n));""")
        self.assertEqual(r, 5)

    def test_snapshots_are_copies_not_references(self):
        r = self.run_js("""
            const s = {A: '000000', L: [{keys: ['W']}]}; const h = H.create(s);
            s.A = 'ffffff'; s.L[0].keys.push('A'); h.push(s); s.A = '123456';
            const u = h.undo(); u.A = 'mutated'; const back = h.redo(); const again = h.undo();
            console.log(JSON.stringify([again, back.A]));""")
        self.assertEqual(r, [{"A": "000000", "L": [{"keys": ["W"]}]}, "ffffff"])

    def test_identical_snapshots_are_not_recorded_twice(self):
        r = self.run_js("""
            const h = H.create({A: '000000'});
            h.push({A: '000000'}); h.push({A: 'ff0000'}); h.push({A: 'ff0000'});
            let n = 0; while (h.undo()) n++;
            console.log(JSON.stringify(n));""")
        self.assertEqual(r, 1)


if __name__ == "__main__":
    unittest.main()

"""Behaviour spec — feature 3: shortcut hints + typing heatmap. Written BEFORE the implementation."""
import datetime as dt
import json
import os
import tempfile
import unittest

import numpy as np

from evision_rgb.effects import INDEX, N

BASE = np.tile([0.0, 0.6, 0.0], (N, 1))  # whatever is showing: green everywhere


def k(c, name):
    return np.asarray(c)[INDEX[name.lower()]]


class FakeInput:
    def __init__(self):
        self.mods = set()
        self.app = "notepad.exe"

    def probes(self):
        return {"caps": lambda: False, "mic_muted": lambda: False, "idle": lambda: 0.0, "locked": lambda: False,
                "now": lambda: dt.datetime(2026, 1, 1, 12), "modifiers": lambda: frozenset(self.mods),
                "app": lambda: self.app}


class ShortcutHints(unittest.TestCase):
    def setUp(self):
        from evision_rgb.automation import Automation
        self.inp = FakeInput()
        self.a = Automation({"hints": {"enabled": True, "delay_ms": 250}}, probes=self.inp.probes())

    def hold(self, mods, at=1.0):
        """Hold modifiers from t=0 and render at t=`at`."""
        self.inp.mods = set(mods)
        self.a.apply(BASE, 0.0)
        return np.asarray(self.a.apply(BASE, at))

    def test_no_modifier_no_hints(self):
        self.assertTrue(np.allclose(self.a.apply(BASE, 1.0), BASE))
        self.assertFalse(self.a.active(1.0))

    def test_holding_ctrl_lights_common_shortcuts_and_dims_the_rest(self):
        out = self.hold({"ctrl"})
        for key in ("C", "V", "X", "Z", "S", "F", "A"):
            self.assertGreater(k(out, key).max(), 0.8, key)
            self.assertFalse(np.allclose(k(out, key), BASE[0]), key)
        self.assertLess(k(out, "Q").max(), 0.6 * 0.35, "keys without shortcuts are dimmed")
        self.assertTrue(np.allclose(k(out, "LCtrl"), [1, 1, 1]), "the held modifier is shown")

    def test_hints_wait_for_a_short_hold_so_fast_combos_do_not_flash(self):
        quick = self.hold({"ctrl"}, at=0.1)
        self.assertTrue(np.allclose(quick, BASE))
        held = self.hold({"ctrl"}, at=0.4)
        self.assertFalse(np.allclose(held, BASE))

    def test_releasing_the_modifier_removes_hints_immediately(self):
        self.hold({"ctrl"})
        self.inp.mods = set()
        self.assertTrue(np.allclose(self.a.apply(BASE, 1.05), BASE))

    def test_hints_depend_on_the_app_in_front(self):
        self.inp.app = "Code.exe"
        code = self.hold({"ctrl"})
        self.inp.app = "notepad.exe"
        self.a = type(self.a)({"hints": {"enabled": True, "delay_ms": 250}}, probes=self.inp.probes())
        note = self.hold({"ctrl"})
        self.assertGreater(k(code, "P").max(), 0.8, "VS Code: Ctrl+P quick open")
        self.assertGreater(k(code, "Grave").max(), 0.8, "VS Code: Ctrl+` terminal")
        self.assertLess(k(note, "Grave").max(), 0.3, "not a shortcut in Notepad")

    def test_modifier_combinations_show_only_their_own_shortcuts(self):
        self.inp.app = "chrome.exe"
        out = self.hold({"ctrl", "shift"})
        self.assertGreater(k(out, "T").max(), 0.8, "Ctrl+Shift+T reopens a tab")
        self.assertLess(k(out, "C").max(), 0.3, "Ctrl+C is not a Ctrl+Shift shortcut here")

    def test_windows_key_shows_windows_shortcuts(self):
        out = self.hold({"win"})
        for key in ("E", "D", "L", "V", "Tab"):
            self.assertGreater(k(out, key).max(), 0.8, key)

    def test_categories_have_distinct_colours(self):
        out = self.hold({"ctrl"})
        self.assertFalse(np.allclose(k(out, "C"), k(out, "S")), "edit vs file")

    def test_user_shortcuts_extend_the_built_in_ones(self):
        self.a.configure({"hints": {"custom": {"notepad.exe": {"ctrl": {"app": ["Q"]}}}}})
        out = self.hold({"ctrl"})
        self.assertGreater(k(out, "Q").max(), 0.8)

    def test_hints_are_disabled_by_config(self):
        self.a.configure({"hints": {"enabled": False}})
        self.assertTrue(np.allclose(self.hold({"ctrl"}), BASE))


class Heatmap(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def counter(self, enabled=True):
        from evision_rgb.heatmap import KeyCounter
        return KeyCounter(os.path.join(self.dir, "heatmap.json"), enabled=enabled)

    def test_counting_is_off_by_default(self):
        from evision_rgb.heatmap import KeyCounter
        c = KeyCounter(os.path.join(self.dir, "heatmap.json"))
        c.record("A")
        self.assertEqual(c.total(), 0)

    def test_counts_presses_per_key_and_persists_only_counts(self):
        c = self.counter()
        for key in ("A", "A", "S", "Space"):
            c.record(key)
        c.save()
        with open(os.path.join(self.dir, "heatmap.json"), encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["counts"], {"A": 2, "S": 1, "Space": 1})
        self.assertEqual(set(data), {"counts", "since"}, "no sequences, text or timestamps per press")
        self.assertEqual(self.counter().counts()["A"], 2, "reloaded from disk")

    def test_clear_forgets_everything(self):
        c = self.counter()
        c.record("A"); c.save(); c.clear()
        self.assertEqual(c.total(), 0)
        self.assertEqual(self.counter().total(), 0)

    def test_unknown_keys_are_ignored(self):
        c = self.counter()
        c.record("NumPad7")
        self.assertEqual(c.total(), 0)

    def test_heatmap_effect_goes_from_cold_to_hot_and_leaves_unused_keys_dark(self):
        from evision_rgb.effects import EFFECTS
        e = EFFECTS["heatmap"](counts={"E": 1000, "T": 30, "Q": 1})
        c = np.asarray(e.render(0.0))
        self.assertGreater(k(c, "E")[0], k(c, "E")[2], "most used = red")
        self.assertGreater(k(c, "Q")[2], k(c, "Q")[0], "rarely used = blue")
        self.assertLess(k(c, "Z").max(), 0.05, "never used = dark")


if __name__ == "__main__":
    unittest.main()


class HeatmapAPI(unittest.TestCase):
    def setUp(self):
        import threading, warnings
        warnings.simplefilter("ignore", ResourceWarning)
        from fake_device import install
        self.kb = install(self)
        import evision_rgb.settings as st
        tmp = tempfile.mkdtemp()
        for attr, val in (("DIR", tmp), ("PATH", os.path.join(tmp, "settings.json"))):
            self.addCleanup(setattr, st, attr, getattr(st, attr))
            setattr(st, attr, val)
        from evision_rgb.server import Controller, build_server
        self.ctl = Controller(probes=FakeInput().probes(), hook_keys=False)
        self.httpd = build_server(self.ctl, 0)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(lambda: (self.httpd.shutdown(), self.ctl.close()))

    def call(self, path, body=None):
        import urllib.request
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=data,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read())

    def test_off_by_default_and_reports_counts_once_enabled(self):
        st = self.call("/heatmap")
        self.assertEqual((st["enabled"], st["total"]), (False, 0))
        self.ctl.heatmap.record("A")
        self.assertEqual(self.call("/heatmap")["total"], 0, "nothing is counted while off")
        self.call("/heatmap", {"enabled": True})
        for _ in range(3):
            self.ctl.heatmap.record("A")
        st = self.call("/heatmap")
        self.assertEqual(st["total"], 3)
        self.assertEqual(st["top"][0], ["A", 3])
        import evision_rgb.settings as s
        self.assertTrue(s.load()["heatmap_enabled"], "the choice is remembered")

    def test_clear_wipes_the_counts(self):
        self.call("/heatmap", {"enabled": True})
        self.ctl.heatmap.record("S")
        self.call("/heatmap", {"clear": True})
        self.assertEqual(self.call("/heatmap")["total"], 0)

    def test_heatmap_effect_uses_the_recorded_counts(self):
        import time
        self.call("/heatmap", {"enabled": True})
        for _ in range(50):
            self.ctl.heatmap.record("E")
        self.call("/run", {"effect": "heatmap"})
        time.sleep(0.4)
        f = self.call("/frame")["colors"]
        self.assertNotEqual(f["E"], "000000")
        self.assertEqual(f["Z"], "000000")

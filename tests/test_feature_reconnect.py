"""Behaviour spec — the keyboard's USB connection comes and goes. Written BEFORE the implementation.

Replugging the keyboard, sleep / resume and USB power saving (on battery) re-enumerate it, and the handle the app
holds goes stale: every read then fails with "read error". Found on the user's laptop: the app kept drawing into the
dead handle and nothing it did reached the keyboard until it was restarted. The app must open the keyboard again by
itself, at most about once a second while it is gone, say so while it is, and carry on with what it was showing."""
import json
import os
import tempfile
import threading
import time
import unittest
import urllib.request
import warnings

from fake_device import install
from evision_rgb.layout import KEYS


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


class Transport(unittest.TestCase):
    def setUp(self):
        self.kb = install(self)
        from evision_rgb.effects import Engine
        self.eng = Engine()
        self.addCleanup(self.eng.close)

    def test_a_live_effect_comes_back_after_the_keyboard_is_replugged(self):
        self.eng.run("solid", color="00ff00")
        self.assertTrue(wait_for(lambda: key(self.kb, "Q") == ("direct", (0, 255, 0))))
        self.kb.unplug()
        time.sleep(0.6)
        self.kb.replug()
        self.assertEqual(self.kb.visible()[0], "effect", "a replugged keyboard shows its own effect at first")
        self.assertTrue(wait_for(lambda: key(self.kb, "Q") == ("direct", (0, 255, 0)), secs=4), "the app took over again")

    def test_a_usb_reset_with_the_keyboard_still_there_is_recovered(self):
        self.eng.run("solid", color="0000ff")
        self.assertTrue(wait_for(lambda: key(self.kb, "Q")[0] == "direct"))
        self.kb.reset_usb()  # sleep / resume or selective suspend: same device, stale handle
        self.assertTrue(wait_for(lambda: key(self.kb, "Q") == ("direct", (0, 0, 255)), secs=4))

    def test_once_recovered_the_stream_reports_no_error(self):
        self.eng.run("solid", color="00ff00")
        self.kb.unplug()
        self.assertTrue(wait_for(lambda: self.eng.status()["stream_error"] is not None, secs=2), "the outage is reported")
        self.kb.replug()
        self.assertTrue(wait_for(lambda: self.eng.status()["stream_error"] is None, secs=4), "and cleared once it works")

    def test_reads_and_stored_settings_work_after_a_reset(self):
        from evision_rgb.keyboard import Keyboard
        k = Keyboard()
        self.addCleanup(k.close)
        self.assertEqual(k.info()["mode"], "normal")
        self.kb.reset_usb()
        self.assertEqual(k.info()["mode"], "normal", "reads reopen the keyboard")
        k.set_effect(mode="breath", brightness=2)
        self.assertEqual(self.kb.visible()[:2], ("effect", 0x05), "writes too")

    def test_while_it_is_unplugged_the_app_does_not_hammer_usb(self):
        self.eng.run("solid", color="00ff00")
        time.sleep(0.3)
        tries = lambda: self.kb.scans + self.kb.opens  # noqa: E731  (looking for the keyboard, opening it)
        before = tries()
        self.kb.unplug()
        time.sleep(2.5)
        self.assertLessEqual(tries() - before, 5, "at most about one attempt a second")
        self.kb.replug()
        self.assertTrue(wait_for(lambda: key(self.kb, "Q")[0] == "direct", secs=3))


class AppStatus(unittest.TestCase):
    def setUp(self):
        warnings.simplefilter("ignore", ResourceWarning)
        self.kb = install(self)
        import evision_rgb.settings as st
        tmp = tempfile.mkdtemp()
        for attr, val in (("DIR", tmp), ("PATH", os.path.join(tmp, "settings.json"))):
            self.addCleanup(setattr, st, attr, getattr(st, attr))
            setattr(st, attr, val)
        import datetime as _dt
        from evision_rgb.server import Controller, build_server
        self.ctl = Controller(probes={"now": lambda: _dt.datetime(2026, 1, 1, 12)}, hook_keys=False)
        self.httpd = build_server(self.ctl, 0)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(lambda: (self.httpd.shutdown(), self.ctl.close()))

    def status(self):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/status", timeout=5) as r:
            return json.loads(r.read())

    def test_status_says_when_the_keyboard_is_disconnected(self):
        self.ctl.run_live("solid", {"color": "00ff00"})
        self.assertTrue(wait_for(lambda: self.status()["keyboard"] == "connected"))
        self.kb.unplug()
        self.assertTrue(wait_for(lambda: self.status()["keyboard"] == "disconnected", secs=3))
        self.kb.replug()
        self.assertTrue(wait_for(lambda: self.status()["keyboard"] == "connected", secs=4))

    def test_the_app_starts_without_the_keyboard_and_takes_over_when_it_is_plugged_in(self):
        # a laptop logs in with the keyboard unplugged: the autostarted app must still start
        import datetime as _dt
        import evision_rgb.settings as st
        from evision_rgb.server import Controller
        tmp = tempfile.mkdtemp()
        for attr, val in (("DIR", tmp), ("PATH", os.path.join(tmp, "settings.json"))):
            self.addCleanup(setattr, st, attr, getattr(st, attr))
            setattr(st, attr, val)
        self.kb.unplug()
        ctl = Controller(probes={"now": lambda: _dt.datetime(2026, 1, 1, 12)}, hook_keys=False, automation_hz=20)
        self.addCleanup(ctl.close)
        ctl.run_live("solid", {"color": "ff0000"})
        self.assertFalse(ctl.kb.dev.connected)
        self.kb.replug()
        self.assertTrue(wait_for(lambda: key(self.kb, "Q") == ("direct", (255, 0, 0)), secs=4), "connected and showing it")
        self.assertTrue(wait_for(lambda: os.path.isfile(os.path.join(tmp, "backup", "config.bin")), secs=3),
                        "the first-start lighting backup is made once the keyboard is there")

    def test_the_app_window_shows_it(self):
        with open(os.path.join(os.path.dirname(__file__), "..", "evision_rgb", "gui", "web", "app.js"), encoding="utf-8") as f:
            self.assertIn('keyboard === "disconnected"', f.read())

"""Behaviour spec — feature 2: smart indicators & automation. Written BEFORE the implementation.

Automation runs on top of whatever is showing. Inputs (Caps Lock state, mic mute, idle time, lock
state, clock) come from injectable probes so each behaviour can be specified exactly."""
import datetime as dt
import json
import os
import tempfile
import threading
import time
import unittest
import urllib.request
import warnings

import numpy as np

from fake_device import install
from evision_rgb.effects import INDEX, N, X, Y


def k(colors, name):
    return np.asarray(colors)[INDEX[name.lower()]]


class Probes:
    def __init__(self):
        self.caps = False
        self.mic = False
        self.idle_s = 0.0
        self.lock = False
        self.clock = dt.datetime(2026, 10, 10, 14, 0)

    def as_dict(self):
        return {"caps": lambda: self.caps, "mic_muted": lambda: self.mic, "idle": lambda: self.idle_s,
                "locked": lambda: self.lock, "now": lambda: self.clock}


BASE = np.tile([0.0, 0.0, 0.5], (N, 1))  # whatever effect is running: dim blue everywhere


class Indicators(unittest.TestCase):
    def setUp(self):
        from evision_rgb.automation import Automation
        self.p = Probes()
        self.a = Automation(probes=self.p.as_dict())

    def test_caps_lock_key_lights_while_caps_lock_is_on(self):
        out = self.a.apply(BASE, 1.0)
        self.assertTrue(np.allclose(k(out, "CapsLock"), BASE[0]))
        self.p.caps = True
        out = self.a.apply(BASE, 1.1)
        self.assertTrue(np.allclose(k(out, "CapsLock"), [1, 1, 1]))
        self.assertTrue(np.allclose(k(out, "Q"), BASE[0]), "other keys untouched")

    def test_mic_mute_indicator_shows_on_its_key_when_enabled(self):
        self.a.configure({"mic_mute": {"enabled": True, "key": "F4", "color": "ff0000"}})
        self.p.mic = True
        self.assertTrue(np.allclose(k(self.a.apply(BASE, 0), "F4"), [1, 0, 0]))
        self.p.mic = False
        self.assertTrue(np.allclose(k(self.a.apply(BASE, 0), "F4"), BASE[0]))

    def test_disabled_indicators_do_nothing(self):
        self.a.configure({"caps_lock": {"enabled": False}})
        self.p.caps = True
        self.assertTrue(np.allclose(self.a.apply(BASE, 0), BASE))

    def test_indicator_needs_output_only_while_it_is_showing(self):
        self.assertFalse(self.a.active(0))
        self.p.caps = True
        self.assertTrue(self.a.active(0))


class Notifications(unittest.TestCase):
    def setUp(self):
        from evision_rgb.automation import Automation
        self.a = Automation(probes=Probes().as_dict())

    def test_notification_flashes_the_board_then_stops(self):
        self.a.notify("ff0000", flashes=3, period=0.4, t=10.0)
        on = self.a.apply(BASE, 10.05)
        off = self.a.apply(BASE, 10.25)
        after = self.a.apply(BASE, 10.0 + 3 * 0.4 + 0.1)
        self.assertTrue(np.allclose(on, [1, 0, 0]))
        self.assertTrue(np.allclose(off, BASE))
        self.assertTrue(np.allclose(after, BASE))
        self.assertFalse(self.a.active(10.0 + 3 * 0.4 + 0.1))

    def test_notification_can_target_specific_keys(self):
        self.a.notify("00ff00", keys=["Esc", "F1"], flashes=1, period=1.0, t=0.0)
        out = self.a.apply(BASE, 0.1)
        self.assertTrue(np.allclose(k(out, "Esc"), [0, 1, 0]))
        self.assertTrue(np.allclose(k(out, "F2"), BASE[0]))

    def test_unknown_key_in_notification_is_rejected(self):
        with self.assertRaises(KeyError):
            self.a.notify("ff0000", keys=["NumPad7"], t=0.0)


class Timer(unittest.TestCase):
    def setUp(self):
        from evision_rgb.automation import Automation
        self.a = Automation(probes=Probes().as_dict())
        self.row = [i for i in range(N) if 0.1 < Y[i] < 0.35]  # number row

    def lit(self, out):
        return [i for i in self.row if not np.allclose(out[i], BASE[i])]

    def test_timer_fills_the_number_row_left_to_right(self):
        self.a.start_timer(minutes=10, t=0.0)
        quarter = self.lit(self.a.apply(BASE, 150.0))
        half = self.lit(self.a.apply(BASE, 300.0))
        self.assertAlmostEqual(len(quarter) / len(self.row), 0.25, delta=0.1)
        self.assertAlmostEqual(len(half) / len(self.row), 0.5, delta=0.1)
        self.assertLess(max(X[i] for i in half), min(X[i] for i in self.row if i not in half) + 1e-9)

    def test_timer_end_flashes_then_clears(self):
        self.a.start_timer(minutes=1, t=0.0)
        self.assertTrue(self.a.active(30.0))
        done = self.a.apply(BASE, 60.1)
        self.assertFalse(np.allclose(done, BASE), "visible signal when time is up")
        self.assertFalse(self.a.active(70.0))
        self.assertIsNone(self.a.timer_status(70.0))

    def test_timer_can_be_cancelled_and_reports_remaining_time(self):
        self.a.start_timer(minutes=25, label="focus", t=0.0)
        st = self.a.timer_status(60.0)
        self.assertEqual(st["label"], "focus")
        self.assertAlmostEqual(st["remaining_s"], 24 * 60, delta=1)
        self.a.cancel_timer()
        self.assertIsNone(self.a.timer_status(61.0))


class PowerAndSchedule(unittest.TestCase):
    def setUp(self):
        from evision_rgb.automation import Automation
        self.p = Probes()
        self.a = Automation(probes=self.p.as_dict())

    def test_dims_when_idle_and_restores_instantly_on_activity(self):
        self.a.configure({"idle": {"enabled": True, "after_min": 5, "brightness": 0.0}})
        self.p.idle_s = 4 * 60
        self.assertTrue(np.allclose(self.a.apply(BASE, 0), BASE))
        self.p.idle_s = 6 * 60
        self.assertLess(np.asarray(self.a.apply(BASE, 1)).max(), 0.01)
        self.assertTrue(self.a.active(1), "dimming needs control of the LEDs")
        self.p.idle_s = 0
        self.assertTrue(np.allclose(self.a.apply(BASE, 2), BASE))

    def test_turns_off_while_the_pc_is_locked(self):
        self.p.lock = True
        self.assertLess(np.asarray(self.a.apply(BASE, 0)).max(), 0.01)

    def test_night_schedule_dims_across_midnight(self):
        self.a.configure({"night": {"enabled": True, "start": "22:00", "end": "07:00", "brightness": 0.3}})
        for hour, factor in ((14, 1.0), (23, 0.3), (3, 0.3), (7, 1.0)):
            self.p.clock = dt.datetime(2026, 10, 10, hour, 0)
            out = np.asarray(self.a.apply(BASE, 0))
            self.assertAlmostEqual(out.max(), 0.5 * factor, delta=0.01, msg=f"{hour}:00")

    def test_indicators_stay_visible_even_at_night(self):
        self.a.configure({"night": {"enabled": True, "start": "00:00", "end": "23:59", "brightness": 0.2}})
        self.p.caps = True
        self.assertGreater(k(self.a.apply(BASE, 0), "CapsLock").max(), 0.9)


class ControllerIntegration(unittest.TestCase):
    """The app starts/stops a live 'mirror' of the stored effect only while automation needs the LEDs."""

    def setUp(self):
        warnings.simplefilter("ignore", ResourceWarning)
        self.kb = install(self)
        import evision_rgb.settings as st
        tmp = tempfile.mkdtemp()
        for attr, val in (("DIR", tmp), ("PATH", os.path.join(tmp, "settings.json"))):
            self.addCleanup(setattr, st, attr, getattr(st, attr))
            setattr(st, attr, val)
        from evision_rgb.server import Controller, build_server
        self.p = Probes()
        self.ctl = Controller(probes=self.p.as_dict(), automation_hz=50)
        self.httpd = build_server(self.ctl, 0)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self._shutdown)

    def _shutdown(self):
        self.httpd.shutdown()
        self.ctl.close()

    def call(self, path, body=None):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=data,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            with e:
                return e.code, json.loads(e.read() or b"{}")

    def wait_for(self, cond, secs=2.0):
        end = time.monotonic() + secs
        while time.monotonic() < end:
            if cond():
                return True
            time.sleep(0.02)
        return False

    def test_notification_on_a_hardware_effect_streams_then_hands_back(self):
        stored = self.kb.visible()
        code, _ = self.call("/notify", {"color": "ff0000", "flashes": 2, "period": 0.2})
        self.assertEqual(code, 200)
        self.assertTrue(self.wait_for(lambda: self.kb.visible()[0] == "direct"), "flash is shown live")
        self.assertTrue(self.wait_for(lambda: self.kb.visible() == stored, secs=3), "keyboard effect returns")
        self.assertFalse(self.call("/status")[1]["live"])

    def test_caps_lock_on_a_custom_layout_keeps_the_layout_and_adds_the_indicator(self):
        self.call("/custom", {"colors": {"W": "00ff00"}})
        self.p.caps = True
        self.assertTrue(self.wait_for(lambda: self.kb.visible()[0] == "direct"))
        time.sleep(0.15)
        frame = self.kb.visible()[1]
        from evision_rgb.layout import KEYS
        px = lambda name: frame[KEYS[name][0] * 3:KEYS[name][0] * 3 + 3]
        self.assertEqual(px("CapsLock"), b"\xff\xff\xff")
        self.assertEqual(px("W")[1] > 200, True, "the stored custom layout is mirrored underneath")
        self.p.caps = False
        self.assertTrue(self.wait_for(lambda: self.kb.visible()[0] == "custom", secs=3))

    def test_user_started_live_effects_are_not_stopped_by_automation(self):
        self.call("/run", {"effect": "rainbow"})
        self.call("/notify", {"color": "ff0000", "flashes": 1, "period": 0.1})
        time.sleep(0.6)
        self.assertEqual(self.call("/status")[1]["effect"], "rainbow")

    def test_effect_started_while_an_indicator_shows_keeps_running_after_it_ends(self):
        self.p.caps = True
        self.assertTrue(self.wait_for(lambda: self.call("/status")[1]["effect"] == "emulate"))
        self.call("/run", {"effect": "rainbow"})
        self.p.caps = False
        time.sleep(0.4)
        self.assertEqual(self.call("/status")[1]["effect"], "rainbow")

    def test_timer_api_reports_and_cancels(self):
        self.assertEqual(self.call("/timer", {"minutes": 25, "label": "focus"})[0], 200)
        st = self.call("/status")[1]["timer"]
        self.assertEqual(st["label"], "focus")
        self.call("/timer", {"cancel": True})
        self.assertIsNone(self.call("/status")[1]["timer"])

    def test_automation_settings_persist(self):
        self.call("/automation", {"night": {"enabled": True, "start": "21:00", "end": "06:30", "brightness": 0.4}})
        import evision_rgb.settings as st
        self.assertEqual(st.load()["automation"]["night"]["start"], "21:00")

    def test_invalid_automation_settings_are_rejected(self):
        code, body = self.call("/automation", {"night": {"enabled": True, "start": "25:99"}})
        self.assertEqual(code, 400)


class MCPTools(unittest.TestCase):
    def test_notify_and_timer_tools_exist(self):
        import asyncio
        from evision_rgb import mcp_server
        names = {t.name for t in asyncio.run(mcp_server.mcp.list_tools())}
        self.assertTrue({"notify", "start_timer", "cancel_timer"} <= names)


if __name__ == "__main__":
    unittest.main()

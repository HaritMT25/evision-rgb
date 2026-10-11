"""Behaviour spec — battery saver and CPU savings. Written BEFORE the implementation.

1. Nothing moving, nothing resent. A frame that did not change goes to the keyboard only as the keep-alive
   (the keyboard drops back to its own effect shortly after the last frame), drawing slows to ~10 fps while
   nothing changes, and a key press, a notification, a live key colour or stop() wakes it at once. The app
   window's preview sends only frames that changed, plus a heartbeat so a closed window ends its stream.
2. Battery saver. On battery the app stops drawing (live effects, layer stacks and app profiles pause) and
   the keyboard shows its own built-in effect, which costs the PC nothing. Plugging in brings back exactly
   what was running. Whatever the user chooses while on battery wins. Indicators, notifications, the timer
   and dimming keep working. A PC without a battery never pauses anything.
"""
import datetime as dt
import json
import os
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import warnings

import numpy as np

from fake_device import install
from evision_rgb.layout import KEYS

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
WEB = os.path.join(ROOT, "evision_rgb", "gui", "web")


def wait_for(cond, secs=2.0, step=0.01):
    end = time.monotonic() + secs
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(step)
    return bool(cond())


def direct_frames(kb) -> int:
    """Full frames streamed to the (simulated) keyboard so far: cmd 0x12 at offset 0."""
    with kb.lock:
        return sum(1 for p in kb.writes if len(p) > 6 and p[3] == 0x12 and p[5] == 0 and p[6] == 0)


class NullStream:
    """Stands in for the keyboard stream when only the engine's own pacing is measured."""
    frames_sent, error, paused = 0, None, False

    def set_frame(self, frame):
        pass

    def show(self):
        self.frames_sent += 1

    def close(self):
        pass


def key_rgb(kb, key) -> tuple:
    v = kb.visible()
    if v[0] != "direct":
        return None
    s = KEYS[key][0]
    return tuple(v[1][s * 3:s * 3 + 3])


# ============================================================================ 1. still frames
class StillFrames(unittest.TestCase):
    def setUp(self):
        self.kb = install(self)
        from evision_rgb import effects as E
        self.E = E
        self.eng = E.Engine()
        self.eng.idle_after = 0.25  # (default 0.5 s) keeps the tests short
        self.addCleanup(self.eng.close)
        self.rendered = threading.Event()
        self.renders = 0
        self.moving = False
        spec = self

        class Still(E.Effect):
            """Dim blue everywhere; starts moving when the test says so."""
            name = "stillprobe"

            def render(self, t):
                spec.renders += 1
                spec.rendered.set()
                if spec.moving:
                    return E.hsv(E.X + t * 0.7)
                return np.tile([0.0, 0.0, 0.4], (E.N, 1))

        class CountingReactive(E.Reactive):
            name = "reactiveprobe"

            def start(self):
                pass  # no real key hook: the test presses keys itself, and typing on this PC must not interfere

            def render(self, t):
                out = super().render(t)
                spec.rendered.set()
                return out

        for cls in (Still, CountingReactive):
            E.EFFECTS[cls.name] = cls
            self.addCleanup(E.EFFECTS.pop, cls.name)

    def rate(self, counter, secs=1.0) -> float:
        n0, t0 = counter(), time.monotonic()
        time.sleep(secs)
        return (counter() - n0) / (time.monotonic() - t0)

    def just_rendered(self):
        """Return right after a frame was drawn, i.e. at the start of a (slow, idle) wait."""
        self.rendered.clear()
        self.assertTrue(self.rendered.wait(2))

    def test_a_still_frame_goes_to_the_keyboard_only_as_the_keep_alive(self):
        self.eng.run("rainbow", speed=0.0)  # a 60 fps effect whose picture does not move
        time.sleep(0.5)
        sent = self.rate(lambda: direct_frames(self.kb))
        self.assertLessEqual(sent, 12, f"{sent:.0f} identical frames/s sent to the keyboard")
        self.assertGreaterEqual(sent, 3, "the keep-alive must keep going")

    def test_the_keyboard_never_drops_back_to_its_own_effect_while_a_still_frame_shows(self):
        self.eng.run("rainbow", speed=0.0)
        time.sleep(0.3)
        start = len(self.kb.history)
        end = time.monotonic() + 1.5
        while time.monotonic() < end:
            self.assertEqual(self.kb.visible()[0], "direct")
            time.sleep(0.02)
        self.assertEqual([v[0] for v in self.kb.history[start:]], [], "no flicker back to the stored effect")

    def test_a_moving_effect_still_reaches_the_keyboard_every_frame(self):
        self.eng.run("rainbow", speed=0.5)
        time.sleep(0.5)
        sent = self.rate(lambda: direct_frames(self.kb))
        self.assertGreaterEqual(sent, 30, f"only {sent:.0f} frames/s")

    def test_drawing_slows_down_while_nothing_changes(self):
        self.eng.run("stillprobe")
        time.sleep(0.6)
        drawn = self.rate(lambda: self.renders, 0.8)
        self.assertLessEqual(drawn, 15, f"{drawn:.0f} renders/s of an unchanged picture")
        self.assertGreaterEqual(drawn, 5, "still checks for changes (overlays, Caps Lock...)")

    def test_drawing_is_back_to_full_speed_once_something_moves(self):
        # engine pacing only (no simulated USB traffic): ~60 frames/s. A wait that rounds up to the
        # Windows timer tick (15.6 ms) gives ~31.
        self.eng.stream = NullStream()
        self.eng.run("stillprobe")
        time.sleep(0.6)
        self.moving = True
        time.sleep(0.3)
        drawn = self.rate(lambda: self.renders, 0.8)
        self.assertGreaterEqual(drawn, 45, f"only {drawn:.0f} renders/s")

    def test_a_key_press_shows_at_once_on_a_still_typing_effect(self):
        self.eng.idle_period = 0.5  # a slow idle rate makes a missed wake-up obvious
        self.eng.run("reactiveprobe", base="000000", ripple=False)
        time.sleep(0.6)
        self.just_rendered()
        t0 = time.monotonic()
        self.eng.effect.events.key_down(0x41)  # what the key hook calls for 'A'
        self.eng.effect.events.key_up(0x41)
        self.assertTrue(wait_for(lambda: (key_rgb(self.kb, "A") or (0,))[0] > 100, secs=1.0))
        self.assertLess(time.monotonic() - t0, 0.2, "key press waited for the idle frame")

    def test_a_notification_shows_at_once_on_a_still_effect(self):
        from evision_rgb.automation import Automation
        auto = Automation(probes={})
        self.eng.post = auto.apply
        self.eng.idle_period = 0.5
        self.eng.run("stillprobe")
        time.sleep(0.6)
        self.just_rendered()
        t0 = time.monotonic()
        auto.notify("ff0000", flashes=2, period=1.0)
        self.assertTrue(wait_for(lambda: key_rgb(self.kb, "Q") == (255, 0, 0), secs=1.0))
        self.assertLess(time.monotonic() - t0, 0.2, "notification waited for the idle frame")

    def test_a_live_key_colour_shows_at_once_on_a_still_effect(self):
        self.eng.idle_period = 0.5
        self.eng.run("stillprobe")
        time.sleep(0.6)
        self.just_rendered()
        t0 = time.monotonic()
        self.eng.set_keys({"Esc": "ff0000"})
        self.assertTrue(wait_for(lambda: key_rgb(self.kb, "Esc") == (255, 0, 0), secs=1.0))
        self.assertLess(time.monotonic() - t0, 0.2)

    def test_stopping_a_still_effect_is_instant(self):
        self.eng.idle_period = 0.5
        self.eng.run("stillprobe")
        time.sleep(0.6)
        self.just_rendered()
        t0 = time.monotonic()
        self.eng.stop()
        self.assertLess(time.monotonic() - t0, 0.25)

    def test_the_engine_reports_when_it_is_idle(self):
        self.eng.run("stillprobe")
        self.assertTrue(wait_for(lambda: self.eng.status().get("idle") is True, secs=2))
        self.moving = True
        self.assertTrue(wait_for(lambda: self.eng.status().get("idle") is False, secs=1))


# ============================================================================ server fixtures
class _ServerCase(unittest.TestCase):
    battery = False  # what the power probe says: True = on battery, False = plugged in, None = no battery

    def setUp(self):
        warnings.simplefilter("ignore", ResourceWarning)
        self.kb = install(self)
        import evision_rgb.settings as st
        tmp = tempfile.mkdtemp()
        for attr, val in (("DIR", tmp), ("PATH", os.path.join(tmp, "settings.json"))):
            self.addCleanup(setattr, st, attr, getattr(st, attr))
            setattr(st, attr, val)
        self.caps = False
        probes = {"caps": lambda: self.caps, "mic_muted": lambda: False, "idle": lambda: 0.0,
                  "locked": lambda: False, "now": lambda: dt.datetime(2026, 1, 1, 12),
                  "on_battery": lambda: self.battery}
        self.make(probes)

    def make(self, probes, settings=None):
        from evision_rgb import settings as st
        from evision_rgb.server import Controller, build_server
        if settings:
            s = st.load()
            s.update(settings)
            st.save(s)
        self.ctl = Controller(probes=probes, hook_keys=False, automation_hz=20)
        self.httpd = build_server(self.ctl, 0)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self._shutdown, self.ctl, self.httpd)

    @staticmethod
    def _shutdown(ctl, httpd):
        httpd.shutdown()
        ctl.close()

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

    def effect(self):
        e = self.ctl.engine.effect
        return e.name if e is not None else None


# ============================================================================ preview stream
class PreviewStream(_ServerCase):
    def read_stream(self, secs):
        req = urllib.request.urlopen(f"http://127.0.0.1:{self.port}/stream", timeout=5)
        data = beats = 0
        t0 = time.monotonic()
        try:
            while time.monotonic() - t0 < secs:
                line = req.readline()
                if line.startswith(b"data: "):
                    data += 1
                    self.assertIn("colors", json.loads(line[6:]))
                elif line.startswith(b":"):
                    beats += 1
        finally:
            req.close()
        return data, beats

    def test_a_still_picture_is_not_resent_to_the_window(self):
        data, beats = self.read_stream(1.3)  # the simulated keyboard shows a static colour
        self.assertGreaterEqual(data, 1, "the window gets the picture once")
        self.assertLessEqual(data, 3, f"{data} identical frames sent to the window")
        self.assertGreaterEqual(beats, 1, "a heartbeat keeps checking the window is still there")

    def test_a_moving_picture_streams_smoothly(self):
        self.call("/run", {"effect": "rainbow", "params": {"speed": 0.5}})
        data, _ = self.read_stream(1.0)
        self.assertGreaterEqual(data, 30, f"only {data} frames/s")

    def test_closing_the_window_ends_its_stream(self):
        from evision_rgb import server
        self.assertTrue(wait_for(lambda: server.open_streams() == 0, secs=5), "earlier tests' streams end")
        req = urllib.request.urlopen(f"http://127.0.0.1:{self.port}/stream", timeout=5)
        req.readline()
        self.assertEqual(server.open_streams(), 1)
        req.close()
        self.assertTrue(wait_for(lambda: server.open_streams() == 0, secs=5),
                        "a closed window's stream must not run forever")


# ============================================================================ 2. battery saver
class BatterySaver(_ServerCase):
    def unplug(self):
        self.battery = True

    def plug_in(self):
        self.battery = False

    def test_unplugging_pauses_a_live_effect_and_the_keyboard_shows_its_own(self):
        self.call("/run", {"effect": "rainbow", "params": {"speed": 0.3}})
        self.assertTrue(wait_for(lambda: self.kb.visible()[0] == "direct"))
        self.unplug()
        self.assertTrue(wait_for(lambda: self.effect() is None), "live effect paused")
        self.assertTrue(wait_for(lambda: self.kb.visible()[0] == "effect"), "the keyboard's own effect shows")
        b = self.call("/status")[1]["battery"]
        self.assertEqual((b["on_battery"], b["saver"], b["paused"]), (True, True, "rainbow"))

    def test_plugging_back_in_resumes_the_same_effect_with_its_settings(self):
        self.call("/run", {"effect": "rainbow", "params": {"speed": 0.3, "scale": 2.0}})
        self.unplug()
        self.assertTrue(wait_for(lambda: self.effect() is None))
        self.plug_in()
        self.assertTrue(wait_for(lambda: self.effect() == "rainbow"))
        self.assertEqual(self.ctl.engine.params, {"speed": 0.3, "scale": 2.0})
        self.assertIsNone(self.call("/status")[1]["battery"]["paused"])

    def test_a_layer_stack_pauses_and_comes_back_too(self):
        layers = [{"effect": "solid", "params": {"color": "000040"}},
                  {"effect": "solid", "params": {"color": "ff0000"}, "keys": ["W", "A", "S", "D"]}]
        self.call("/run", {"effect": "layers", "params": {"layers": layers}})
        self.unplug()
        self.assertTrue(wait_for(lambda: self.effect() is None))
        self.plug_in()
        self.assertTrue(wait_for(lambda: self.effect() == "layers"))
        self.assertEqual(self.ctl.engine.params, {"layers": layers})

    def test_live_key_colours_come_back_with_the_effect(self):
        self.call("/run", {"effect": "solid", "params": {"color": "000020"}})
        self.call("/keys", {"colors": {"Esc": "ff0000"}})
        self.unplug()
        self.assertTrue(wait_for(lambda: self.effect() is None))
        self.plug_in()
        self.assertTrue(wait_for(lambda: key_rgb(self.kb, "Esc") == (255, 0, 0)))

    def test_app_profiles_pause_and_come_back(self):
        self.call("/apps", {"enabled": True, "default": {"effect": "rainbow", "params": {}}, "rules": []})
        self.assertTrue(wait_for(lambda: self.effect() == "rainbow"))
        self.unplug()
        self.assertTrue(wait_for(lambda: self.ctl.apps is None and self.effect() is None))
        self.assertEqual(self.call("/status")[1]["battery"]["paused"], "app profiles")
        self.plug_in()
        self.assertTrue(wait_for(lambda: self.ctl.apps is not None and self.effect() == "rainbow"))

    def test_with_the_saver_off_unplugging_changes_nothing(self):
        self.call("/automation", {"battery": {"enabled": False}})
        self.call("/run", {"effect": "rainbow"})
        self.unplug()
        time.sleep(0.6)
        self.assertEqual(self.effect(), "rainbow")
        self.assertIsNone(self.call("/status")[1]["battery"]["paused"])

    def test_an_effect_you_start_on_battery_runs_and_stays(self):
        self.call("/run", {"effect": "rainbow"})
        self.unplug()
        self.assertTrue(wait_for(lambda: self.effect() is None))
        self.call("/run", {"effect": "breathing"})  # your choice wins
        time.sleep(0.5)
        self.assertEqual(self.effect(), "breathing", "not paused again while on battery")
        self.plug_in()
        time.sleep(0.5)
        self.assertEqual(self.effect(), "breathing", "the old effect does not come back over it")

    def test_a_built_in_effect_chosen_on_battery_is_not_replaced_when_plugging_in(self):
        self.call("/run", {"effect": "rainbow"})
        self.unplug()
        self.assertTrue(wait_for(lambda: self.effect() is None))
        self.assertEqual(self.call("/effect", {"mode": "breath"})[0], 200)
        self.plug_in()
        time.sleep(0.5)
        self.assertIsNone(self.effect())
        self.assertEqual(self.kb.visible()[:2], ("effect", 0x05))

    def test_stopping_on_battery_means_nothing_comes_back(self):
        self.call("/run", {"effect": "rainbow"})
        self.unplug()
        self.assertTrue(wait_for(lambda: self.effect() is None))
        self.call("/stop", {})
        self.plug_in()
        time.sleep(0.5)
        self.assertIsNone(self.effect())

    def test_the_remembered_effect_survives_a_pause(self):
        self.call("/run", {"effect": "rainbow", "params": {"speed": 0.2}})
        self.unplug()
        self.assertTrue(wait_for(lambda: self.effect() is None))
        self.assertEqual(self.call("/settings")[1]["last_live"], {"effect": "rainbow", "params": {"speed": 0.2}})

    def test_switching_the_saver_off_on_battery_brings_the_effect_back(self):
        self.call("/run", {"effect": "rainbow"})
        self.unplug()
        self.assertTrue(wait_for(lambda: self.effect() is None))
        self.call("/automation", {"battery": {"enabled": False}})
        self.assertTrue(wait_for(lambda: self.effect() == "rainbow"))

    def test_switching_the_saver_on_while_on_battery_pauses_at_once(self):
        self.call("/automation", {"battery": {"enabled": False}})
        self.call("/run", {"effect": "rainbow"})
        self.unplug()
        time.sleep(0.3)
        self.assertEqual(self.effect(), "rainbow")
        self.call("/automation", {"battery": {"enabled": True}})
        self.assertTrue(wait_for(lambda: self.effect() is None))

    def test_unplugging_while_only_an_indicator_shows_pauses_nothing(self):
        self.caps = True  # the app shows the stored effect with Caps Lock on top (not a live effect of yours)
        self.assertTrue(wait_for(lambda: key_rgb(self.kb, "CapsLock") == (255, 255, 255)))
        self.unplug()
        time.sleep(0.3)
        self.assertIsNone(self.call("/status")[1]["battery"]["paused"])
        self.caps = False
        self.assertTrue(wait_for(lambda: self.kb.visible()[0] == "effect", secs=3))
        self.plug_in()
        time.sleep(0.4)
        self.assertIsNone(self.effect(), "nothing to bring back")
        self.assertEqual(self.kb.visible()[0], "effect")

    def test_indicators_still_work_on_battery(self):
        self.call("/run", {"effect": "rainbow"})
        self.unplug()
        self.assertTrue(wait_for(lambda: self.effect() is None))
        self.caps = True
        self.assertTrue(wait_for(lambda: key_rgb(self.kb, "CapsLock") == (255, 255, 255)), "Caps Lock still lights")
        self.caps = False
        self.assertTrue(wait_for(lambda: self.kb.visible()[0] == "effect", secs=3), "and hands the keyboard back")

    def test_the_battery_setting_must_be_true_or_false(self):
        code, body = self.call("/automation", {"battery": {"enabled": "yes"}})
        self.assertEqual(code, 400)
        self.assertIn("battery", body["error"])


class BatteryAtStartUp(_ServerCase):
    battery = True

    def setUp(self):
        warnings.simplefilter("ignore", ResourceWarning)
        self.kb = install(self)
        import evision_rgb.settings as st
        tmp = tempfile.mkdtemp()
        for attr, val in (("DIR", tmp), ("PATH", os.path.join(tmp, "settings.json"))):
            self.addCleanup(setattr, st, attr, getattr(st, attr))
            setattr(st, attr, val)
        probes = {"caps": lambda: False, "now": lambda: dt.datetime(2026, 1, 1, 12), "on_battery": lambda: self.battery}
        self.make(probes, settings={"startup": "restore", "last_live": {"effect": "rainbow", "params": {"speed": 0.4}}})

    def test_starting_the_app_on_battery_waits_for_power(self):
        start = len(self.kb.history)
        self.ctl.startup()
        time.sleep(0.4)
        self.assertIsNone(self.effect())
        self.assertNotIn("direct", [v[0] for v in self.kb.history[start:]], "the effect never showed, not even briefly")
        self.assertEqual(self.call("/status")[1]["battery"]["paused"], "rainbow")
        self.battery = False
        self.assertTrue(wait_for(lambda: self.effect() == "rainbow"))
        self.assertEqual(self.ctl.engine.params, {"speed": 0.4})


class NoBattery(_ServerCase):
    battery = None  # a desktop PC

    def test_a_pc_without_a_battery_never_pauses(self):
        self.call("/run", {"effect": "rainbow"})
        time.sleep(0.5)
        self.assertEqual(self.effect(), "rainbow")
        b = self.call("/status")[1]["battery"]
        self.assertEqual((b["on_battery"], b["paused"]), (None, None))


class PowerProbe(unittest.TestCase):
    def test_the_real_power_probe_answers_without_raising(self):
        from evision_rgb.automation import default_probes
        self.assertIn(default_probes()["on_battery"](), (True, False, None))

    def test_tests_and_neutral_probes_are_plugged_in(self):
        from evision_rgb.automation import Automation
        self.assertFalse(Automation(probes={}).probes["on_battery"]())

    def test_the_saver_is_on_by_default(self):
        from evision_rgb.automation import Automation
        self.assertTrue(Automation(probes={}).cfg["battery"]["enabled"])


class BatteryInTheApp(unittest.TestCase):
    def read(self, name):
        with open(os.path.join(WEB, name), encoding="utf-8") as f:
            return f.read()

    def test_the_automation_tab_has_the_battery_saver_switch_and_its_state(self):
        html = self.read("index.html")
        self.assertIn('data-a="battery.enabled"', html)
        self.assertIn('id="batStatus"', html)
        self.assertIn("status.battery", self.read("app.js"))

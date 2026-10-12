"""Behaviour spec — dim the keyboard on battery. Written BEFORE the implementation.

The battery saver can also turn the keyboard's own (stored) brightness down while the laptop runs on battery,
because the LEDs draw their power from the laptop's USB port (on the user's laptop that load is the prime suspect
for USB-C earbuds cutting out on battery). Off by default: battery.brightness is null ("leave as it is").

1. With a brightness picked (0..4), unplugging the charger stores that brightness on the keyboard (only the
   brightness: mode, colour, speed stay); plugging in brings back the brightness from before. One write per power
   change, never a write per tick.
2. A keyboard already at or below that brightness is left alone (nothing to restore either).
3. Your choice wins: a brightness you set while on battery is not undone when plugging in.
4. Switching the saver or the option off on battery restores at once; picking another level applies at once.
5. The app remembers a dim it made (settings), so quitting or crashing on battery and starting again plugged in
   still brings the brightness back. A keyboard that is unplugged at the moment is dimmed / restored when it
   comes back.
6. A live effect you started on battery keeps running through the change. A PC without a battery never dims.
"""
import time
import unittest

from test_feature_battery import WEB, _ServerCase, wait_for

ORIGINAL = 3  # the simulated keyboard starts at "normal", green, brightness 3


def brightness(kb) -> int:
    with kb.lock:
        return kb.config[2]


def config_writes(kb) -> int:
    """Write transactions (BEGIN packets) sent to the simulated keyboard so far."""
    with kb.lock:
        return sum(1 for p in kb.writes if len(p) > 3 and p[3] == 0x01)


class DimOnBattery(_ServerCase):
    def dim(self, level):
        code, body = self.call("/automation", {"battery": {"brightness": level}})
        self.assertEqual(code, 200, body)
        return body

    def test_off_by_default_nothing_is_written_on_battery(self):
        self.assertIsNone(self.call("/automation")[1]["battery"]["brightness"])
        before = config_writes(self.kb)
        self.battery = True
        time.sleep(0.4)
        self.assertEqual(config_writes(self.kb), before)
        self.assertEqual(brightness(self.kb), ORIGINAL)

    def test_unplugging_stores_the_picked_brightness_and_nothing_else(self):
        self.dim(1)
        with self.kb.lock:
            before = bytes(self.kb.config)
        self.battery = True
        self.assertTrue(wait_for(lambda: brightness(self.kb) == 1))
        with self.kb.lock:
            after = bytes(self.kb.config)
        self.assertEqual(before[:2] + before[3:], after[:2] + after[3:], "only the brightness byte changes")
        self.assertEqual(self.kb.visible()[0], "effect", "the keyboard shows its own effect")
        self.assertEqual(self.kb.applied[0][2], 1, "the dimmer brightness is committed (END)")

    def test_plugging_in_brings_the_old_brightness_back(self):
        self.dim(1)
        self.battery = True
        self.assertTrue(wait_for(lambda: brightness(self.kb) == 1))
        self.battery = False
        self.assertTrue(wait_for(lambda: brightness(self.kb) == ORIGINAL))
        self.assertTrue(wait_for(lambda: self.ctl.settings.get("battery_dimmed") is None))

    def test_one_write_per_power_change(self):
        self.dim(0)
        start = config_writes(self.kb)
        self.battery = True
        self.assertTrue(wait_for(lambda: brightness(self.kb) == 0))
        time.sleep(0.4)  # 8 automation ticks
        self.assertEqual(config_writes(self.kb) - start, 1)
        self.battery = False
        self.assertTrue(wait_for(lambda: brightness(self.kb) == ORIGINAL))
        time.sleep(0.4)
        self.assertEqual(config_writes(self.kb) - start, 2)

    def test_a_keyboard_already_that_dim_is_left_alone(self):
        for level in (ORIGINAL, 4):  # the same level, or a brighter one: never made brighter on battery
            self.dim(level)
            start = config_writes(self.kb)
            self.battery = True
            time.sleep(0.4)
            self.assertEqual(brightness(self.kb), ORIGINAL, level)
            self.battery = False
            time.sleep(0.4)
            self.assertEqual(config_writes(self.kb), start, level)
            self.assertEqual(brightness(self.kb), ORIGINAL, level)

    def test_a_brightness_you_set_on_battery_is_kept_when_plugging_in(self):
        self.dim(1)
        self.battery = True
        self.assertTrue(wait_for(lambda: brightness(self.kb) == 1))
        self.assertEqual(self.call("/effect", {"brightness": 4})[0], 200)
        self.battery = False
        time.sleep(0.4)
        self.assertEqual(brightness(self.kb), 4)
        self.assertTrue(wait_for(lambda: self.ctl.settings.get("battery_dimmed") is None))

    def test_another_effect_you_store_on_battery_keeps_the_dim_until_power_comes_back(self):
        self.dim(1)
        self.battery = True
        self.assertTrue(wait_for(lambda: brightness(self.kb) == 1))
        self.assertEqual(self.call("/effect", {"mode": "breathing"})[0], 200)
        self.battery = False
        self.assertTrue(wait_for(lambda: brightness(self.kb) == ORIGINAL))
        self.assertEqual(self.ctl.info(0)["mode"], "breath", "the effect you picked stays")

    def test_switching_the_saver_off_on_battery_restores_at_once(self):
        self.dim(1)
        self.battery = True
        self.assertTrue(wait_for(lambda: brightness(self.kb) == 1))
        self.call("/automation", {"battery": {"enabled": False}})
        self.assertTrue(wait_for(lambda: brightness(self.kb) == ORIGINAL))

    def test_leave_as_it_is_on_battery_restores_at_once(self):
        self.dim(1)
        self.battery = True
        self.assertTrue(wait_for(lambda: brightness(self.kb) == 1))
        self.dim(None)
        self.assertTrue(wait_for(lambda: brightness(self.kb) == ORIGINAL))

    def test_picking_another_level_on_battery_applies_at_once_and_still_restores(self):
        self.dim(0)
        self.battery = True
        self.assertTrue(wait_for(lambda: brightness(self.kb) == 0))
        self.dim(2)
        self.assertTrue(wait_for(lambda: brightness(self.kb) == 2))
        self.battery = False
        self.assertTrue(wait_for(lambda: brightness(self.kb) == ORIGINAL))

    def test_a_live_effect_you_start_on_battery_keeps_running_through_the_change(self):
        self.dim(1)
        self.battery = True
        self.assertTrue(wait_for(lambda: brightness(self.kb) == 1))
        self.call("/run", {"effect": "rainbow", "params": {"speed": 0.3}})
        self.battery = False
        self.assertTrue(wait_for(lambda: brightness(self.kb) == ORIGINAL))
        time.sleep(0.2)
        self.assertEqual(self.effect(), "rainbow")
        self.assertEqual(self.ctl.engine.params, {"speed": 0.3})
        self.assertTrue(wait_for(lambda: self.kb.visible()[0] == "direct"), "your effect is on the keys again")

    def test_a_paused_effect_comes_back_after_the_brightness(self):
        self.dim(1)
        self.call("/run", {"effect": "rainbow"})
        self.battery = True
        self.assertTrue(wait_for(lambda: brightness(self.kb) == 1 and self.effect() is None))
        mark = len(self.kb.history)
        runs, run = [], self.ctl.engine.run
        self.ctl.engine.run = lambda name, **p: (runs.append(name), run(name, **p))[1]
        self.battery = False
        self.assertTrue(wait_for(lambda: self.effect() == "rainbow" and self.kb.visible()[0] == "direct"))
        time.sleep(0.2)
        self.assertEqual(runs, ["rainbow"], "it resumes once, not restarted by the brightness change")
        seen = [v[0] for v in self.kb.history[mark:]]
        last_direct = max(i for i, v in enumerate(seen) if v == "direct")
        self.assertNotIn("effect", seen[seen.index("direct"):last_direct + 1],
                         "the brightness is restored before the effect resumes (no flash in between)")

    def test_status_reports_the_dim(self):
        self.dim(1)
        self.battery = True
        self.assertTrue(wait_for(lambda: brightness(self.kb) == 1))
        b = self.call("/status")[1]["battery"]
        self.assertEqual((b["brightness"], b["dimmed"]), (1, {"from": ORIGINAL, "to": 1}))
        self.battery = False
        self.assertTrue(wait_for(lambda: self.call("/status")[1]["battery"]["dimmed"] is None))

    def test_the_brightness_must_be_a_level_from_0_to_4_or_null(self):
        for bad in ("2", 5, -1, True, 2.5):
            code, body = self.call("/automation", {"battery": {"brightness": bad}})
            self.assertEqual(code, 400, bad)
            self.assertIn("battery", body["error"])
        self.assertIsNone(self.call("/automation")[1]["battery"]["brightness"])

    def test_an_unplugged_keyboard_is_dimmed_when_it_comes_back(self):
        self.dim(1)
        self.kb.unplug()
        self.battery = True
        time.sleep(0.4)
        self.kb.replug()
        self.assertTrue(wait_for(lambda: brightness(self.kb) == 1, secs=4))

    def test_a_write_that_fails_after_storing_still_brings_the_brightness_back(self):
        from evision_rgb import server
        self.addCleanup(setattr, server, "DIM_RETRY_S", server.DIM_RETRY_S)
        server.DIM_RETRY_S = 0.2
        real = self.ctl._store_brightness

        def glitch(level):  # the brightness reaches the keyboard, then the readback fails (cable glitch)
            real(level)
            raise OSError("readback failed")
        self.ctl._store_brightness = glitch
        self.dim(1)
        self.battery = True
        self.assertTrue(wait_for(lambda: brightness(self.kb) == 1))
        self.ctl._store_brightness = real
        time.sleep(0.4)
        self.battery = False
        self.assertTrue(wait_for(lambda: brightness(self.kb) == ORIGINAL), "the brightness from before is not lost")

    def test_a_failing_write_is_not_retried_on_every_tick_and_is_logged(self):
        calls = []

        def broken(level):
            calls.append(level)
            raise OSError("write rejected")
        self.ctl._store_brightness = broken
        self.dim(1)
        self.battery = True
        time.sleep(0.6)  # 12 automation ticks
        self.assertEqual(len(calls), 1)
        self.assertTrue(any("battery dim" in e["text"] and "write rejected" in e["text"]
                            for e in self.ctl.events.recent()))


class DimRememberedAcrossRestarts(_ServerCase):
    def setUp(self):
        super().setUp()
        # the app quit while on battery: the keyboard is still dimmed and the record says so
        self.ctl.close()
        with self.kb.lock:
            self.kb.config[2] = 1
        self.make(self.ctl.automation.probes, settings={"battery_dimmed": {"from": 4, "to": 1},
                                                        "automation": {"battery": {"brightness": 1}}})

    def test_starting_plugged_in_restores_the_brightness(self):
        self.assertTrue(wait_for(lambda: brightness(self.kb) == 4))
        self.assertTrue(wait_for(lambda: self.ctl.settings.get("battery_dimmed") is None))


class DimRememberedOnBattery(_ServerCase):
    battery = True

    def setUp(self):
        super().setUp()
        self.ctl.close()
        with self.kb.lock:
            self.kb.config[2] = 1
        self.make(self.ctl.automation.probes, settings={"battery_dimmed": {"from": 4, "to": 1},
                                                        "automation": {"battery": {"brightness": 1}}})

    def test_starting_on_battery_keeps_the_dim_and_still_restores_later(self):
        start = config_writes(self.kb)
        time.sleep(0.4)
        self.assertEqual((brightness(self.kb), config_writes(self.kb)), (1, start))
        self.battery = False
        self.assertTrue(wait_for(lambda: brightness(self.kb) == 4))


class NoBatteryNoDim(_ServerCase):
    battery = None

    def test_a_pc_without_a_battery_never_dims(self):
        self.call("/automation", {"battery": {"brightness": 0}})
        time.sleep(0.4)
        self.assertEqual(brightness(self.kb), ORIGINAL)


class DimInTheApp(unittest.TestCase):
    def read(self, name):
        import os
        with open(os.path.join(WEB, name), encoding="utf-8") as f:
            return f.read()

    def test_the_automation_tab_offers_the_levels_and_leave_as_it_is(self):
        html = self.read("index.html")
        at = html.find('data-a="battery.brightness"')
        self.assertGreater(at, 0, "the option is in the automation tab")
        for v in ('value=""', 'value="0"', 'value="1"', 'value="2"', 'value="3"'):
            self.assertIn(v, html[at:at + 900])
        js = self.read("app.js")
        self.assertIn("dimmed", js)


if __name__ == "__main__":
    unittest.main()

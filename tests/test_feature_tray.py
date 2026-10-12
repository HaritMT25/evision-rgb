"""Behaviour spec — the tray icon's tooltip says what is going on. Written BEFORE the implementation.

Hovering the tray icon shows the lighting (the live effect by its name in the app, app profiles, or the keyboard's
own effect) and anything unusual: the keyboard disconnected, the battery saver's pause or dim, game lighting or an
OpenRGB program in charge. Windows cuts tooltips at 127 characters, so it always fits.
"""
import os
import time
import unittest

from test_feature_battery import ROOT, _ServerCase, wait_for


class TrayTooltip(_ServerCase):
    def tip(self):
        return self.ctl.tray_summary()

    def test_nothing_running_is_the_keyboards_own_effect(self):
        self.assertEqual(self.tip(), "EVision RGB\nKeyboard's own effect")

    def test_a_live_effect_by_its_name_in_the_app(self):
        self.call("/run", {"effect": "rainbow"})
        self.assertEqual(self.tip(), "EVision RGB\nRainbow wave")

    def test_app_profiles_show_the_effect_they_picked(self):
        self.call("/apps", {"enabled": True, "default": {"effect": "solid", "params": {"color": "ff0000"}}, "rules": []})
        self.assertTrue(wait_for(lambda: "Solid colour (app profiles)" in self.tip()), self.tip())

    def test_a_disconnected_keyboard_is_mentioned(self):
        self.call("/run", {"effect": "rainbow"})
        self.kb.unplug()
        self.assertTrue(wait_for(lambda: "keyboard disconnected" in self.tip(), secs=3), self.tip())

    def test_the_battery_saver_is_mentioned(self):
        self.call("/automation", {"battery": {"brightness": 1}})
        self.call("/run", {"effect": "rainbow"})
        self.battery = True
        self.assertTrue(wait_for(lambda: "dimmed to 1/4" in self.tip()), self.tip())
        self.assertIn("on battery: Rainbow wave paused", self.tip())

    def test_game_lighting_by_name(self):
        self.call("/run", {"effect": "cs2", "params": {"demo": True}})
        self.assertEqual(self.tip(), "EVision RGB\nCS2 game lighting")

    def test_it_always_fits_a_windows_tooltip(self):
        self.call("/automation", {"battery": {"brightness": 0}})
        self.call("/run", {"effect": "rainbow"})
        self.battery = True
        self.kb.unplug()
        time.sleep(0.5)
        self.assertLessEqual(len(self.tip()), 127)


class TrayNames(unittest.TestCase):
    def test_effect_names_are_the_ones_the_app_shows(self):
        import re
        from evision_rgb.server import EFFECT_NAMES
        with open(os.path.join(ROOT, "evision_rgb", "gui", "web", "app.js"), encoding="utf-8") as f:
            cards = dict(re.findall(r'id: "([a-z0-9]+)", name: "([^"]+)"', f.read()))
        self.assertGreater(len(cards), 10)
        for eid, label in cards.items():
            self.assertEqual(EFFECT_NAMES.get(eid), label, eid)


class TrayInTheApp(unittest.TestCase):
    def test_the_tray_icon_updates_its_tooltip(self):
        with open(os.path.join(ROOT, "evision_rgb", "gui", "app.py"), encoding="utf-8") as f:
            src = f.read()
        self.assertIn("tray_summary()", src)
        self.assertIn("icon.title", src)


if __name__ == "__main__":
    unittest.main()

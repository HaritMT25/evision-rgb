"""Behaviour spec — handing the lighting between its owners. Written BEFORE the fixes (bugs found by a review).

Your own lighting is what always comes back. Game lighting and OpenRGB programs only borrow the keys, and the
battery saver pauses what YOU had, not what borrowed it:

1. On battery while a game has the lighting, the game steps aside and your lighting is what waits for the charger;
   a game that ended on battery leaves nothing of itself behind. Still playing when you plug in: it takes over again.
2. OpenRGB programs (a program you run, so it may light the keys on battery too): when one leaves while on battery,
   your lighting is what comes back on the charger (not its last frame, and nothing live runs on battery meanwhile).
   Plugging in while a program streams leaves the keys to it and keeps your lighting for when it leaves. A program
   that set its colours once gets them back after a battery round trip.
3. Live key colours (set_live_keys) stay when an indicator / notification that was showing ends.
4. What you choose is what the next start brings back: picking an effect, stopping, a profile, custom colours or
   restoring the backup switches app profiles off (the battery saver, a game or an OpenRGB program pausing them
   doesn't, and neither does a preview); restoring the backup or switching app profiles off forgets the live effect.
"""
import struct
import time
import unittest

from test_feature_battery import _ServerCase, key_rgb, wait_for
from test_feature_games import TOKEN, Clock, cs2
from test_feature_openrgb import NAME, UPDATE_LEDS, VERSION, Client, color, leds_packet
from evision_rgb.layout import KEYS

APPS = {"enabled": True, "default": {"effect": "breathing", "params": {}}, "rules": []}


class Base(_ServerCase):
    def setUp(self):
        super().setUp()
        from evision_rgb import games
        self.games = games
        self.clock = Clock()
        for g in ("cs2", "dota2"):
            self.addCleanup(games.set_feed, g, games.set_feed(g, games.GameFeed(g, clock=self.clock)))
        self.ctl.update_settings({"games": {"token": TOKEN, "auto": True}})
        self.clients = []
        self.addCleanup(self._close_clients)

    def _close_clients(self):
        for c in self.clients:
            try:
                c.close()
            except OSError:
                pass

    def client(self):
        port = self.call("/openrgb", {"enabled": True, "port": 0})[1]["port"]
        c = Client(port)
        self.clients.append(c)
        c.send(NAME, b"spec client\0")
        c.request(VERSION, struct.pack("<I", 4))
        return c

    def frame(self, c, rgb=(255, 0, 0)):
        c.send(UPDATE_LEDS, leds_packet([color(*rgb)] * len(KEYS)))

    def game_ends(self):
        self.clock.t += self.games.ACTIVE_S + 1
        self.assertTrue(wait_for(lambda: self.ctl.game_on is None))

    def restart(self):
        probes = self.ctl.automation.probes
        self.httpd.shutdown()
        self.ctl.close()
        self.make(probes)
        self.ctl.startup()
        time.sleep(0.4)


# ============================================================================ 1. games and the battery saver
class GamesAndBattery(Base):
    def test_a_game_that_ends_on_battery_leaves_your_lighting_to_come_back(self):
        self.ctl.run_live("rainbow", {"speed": 0.2})
        self.games.feed("cs2").update(cs2())
        self.assertTrue(wait_for(lambda: self.effect() == "cs2"))
        self.battery = True
        self.assertTrue(wait_for(lambda: self.effect() is None), "the game's lighting pauses on battery too")
        self.assertEqual(self.call("/status")[1]["battery"]["paused"], "rainbow", "your lighting is what waits")
        self.game_ends()
        self.battery = False
        self.assertTrue(wait_for(lambda: self.effect() == "rainbow"))
        self.assertEqual(self.ctl.engine.params, {"speed": 0.2})
        time.sleep(0.3)
        self.assertEqual(self.effect(), "rainbow")

    def test_still_playing_when_plugged_in_the_game_takes_over_and_gives_your_lighting_back(self):
        self.ctl.run_live("rainbow", {"speed": 0.2})
        self.games.feed("cs2").update(cs2())
        self.assertTrue(wait_for(lambda: self.effect() == "cs2"))
        self.battery = True
        self.assertTrue(wait_for(lambda: self.effect() is None))
        self.games.feed("cs2").update(cs2())
        self.battery = False
        self.assertTrue(wait_for(lambda: self.effect() == "cs2"), "the game lights the keys again")
        self.game_ends()
        self.assertTrue(wait_for(lambda: self.effect() == "rainbow"))


# ============================================================================ 2. OpenRGB programs and the battery saver
class OpenRGBAndBattery(Base):
    def test_a_program_that_leaves_on_battery_gives_your_lighting_back_on_the_charger(self):
        self.ctl.run_live("rainbow", {"speed": 0.2})
        c = self.client()
        self.frame(c)  # sets a colour once (e.g. Home Assistant)
        self.assertTrue(wait_for(lambda: self.effect() == "openrgb"))
        self.battery = True
        self.assertTrue(wait_for(lambda: self.effect() is None))
        c.close()
        self.assertTrue(wait_for(lambda: not self.ctl.ext_on))
        time.sleep(0.3)
        self.assertIsNone(self.effect(), "nothing live on battery")
        self.battery = False
        self.assertTrue(wait_for(lambda: self.effect() == "rainbow"), "your effect, not the program's last frame")

    def test_a_program_streaming_on_battery_that_leaves_runs_nothing_until_the_charger(self):
        self.ctl.run_live("rainbow", {"speed": 0.2})
        c = self.client()
        self.frame(c)
        self.assertTrue(wait_for(lambda: self.effect() == "openrgb"))
        self.battery = True
        self.assertTrue(wait_for(lambda: self.ctl.battery_paused is not None))
        self.frame(c, (0, 0, 255))  # Artemis keeps streaming: a program you run may light the keys on battery
        self.assertTrue(wait_for(lambda: self.effect() == "openrgb"))
        c.close()
        self.assertTrue(wait_for(lambda: not self.ctl.ext_on))
        time.sleep(0.3)
        self.assertIsNone(self.effect(), "your effect waits for the charger")
        self.battery = False
        self.assertTrue(wait_for(lambda: self.effect() == "rainbow"))

    def test_plugging_in_while_a_program_streams_keeps_your_lighting_for_when_it_leaves(self):
        self.ctl.run_live("rainbow", {"speed": 0.2})
        self.battery = True
        self.assertTrue(wait_for(lambda: self.effect() is None))
        c = self.client()
        self.frame(c)  # the program starts while on battery
        self.assertTrue(wait_for(lambda: self.effect() == "openrgb"))
        runs, run = [], self.ctl.engine.run
        self.ctl.engine.run = lambda name, **p: (runs.append(name), run(name, **p))[1]
        self.battery = False
        self.assertTrue(wait_for(lambda: self.ctl.battery_paused is None))
        time.sleep(0.2)
        self.assertEqual(self.effect(), "openrgb", "the program keeps the keys")
        self.assertNotIn("rainbow", runs, "your effect isn't drawn over the program")
        self.frame(c, (0, 255, 0))
        c.close()
        self.assertTrue(wait_for(lambda: self.effect() == "rainbow"), "and comes back when the program leaves")

    def test_a_program_that_set_its_colours_once_gets_them_back_after_battery(self):
        self.ctl.run_live("rainbow", {"speed": 0.2})
        c = self.client()
        self.frame(c, (255, 0, 0))
        self.assertTrue(wait_for(lambda: key_rgb(self.kb, "Q") == (255, 0, 0)))
        self.battery = True
        self.assertTrue(wait_for(lambda: self.effect() is None))
        self.battery = False
        self.assertTrue(wait_for(lambda: self.effect() == "openrgb" and key_rgb(self.kb, "Q") == (255, 0, 0)))
        c.close()
        self.assertTrue(wait_for(lambda: self.effect() == "rainbow"))


# ============================================================================ 3. live key colours over the indicator mirror
class LiveKeysOverTheMirror(_ServerCase):
    def test_keys_set_while_caps_lock_shows_stay_when_it_goes_off(self):
        self.caps = True
        self.assertTrue(wait_for(lambda: self.effect() == "emulate"))
        self.call("/keys", {"colors": {"Esc": "ff0000"}})
        self.assertTrue(wait_for(lambda: key_rgb(self.kb, "Esc") == (255, 0, 0)))
        self.caps = False
        time.sleep(0.5)
        self.assertEqual(key_rgb(self.kb, "Esc"), (255, 0, 0), "nobody cleared them")
        self.assertEqual(len(self.ctl.engine.overrides), 1)

    def test_keys_set_during_a_notification_stay_when_the_flash_ends(self):
        self.call("/notify", {"color": "00ff00", "flashes": 2, "period": 0.2})
        self.assertTrue(wait_for(lambda: self.effect() == "emulate"))
        self.call("/keys", {"colors": {"Esc": "ff0000"}})
        time.sleep(1.4)
        self.assertEqual(key_rgb(self.kb, "Esc"), (255, 0, 0))

    def test_without_live_keys_the_mirror_still_hands_the_keyboard_back(self):
        self.caps = True
        self.assertTrue(wait_for(lambda: self.effect() == "emulate"))
        self.caps = False
        self.assertTrue(wait_for(lambda: self.effect() is None))
        self.assertTrue(wait_for(lambda: self.kb.visible()[0] == "effect"))


# ============================================================================ 4. what the next start brings back
class NextStart(Base):
    def apps_on(self):
        self.call("/apps", APPS)
        self.assertTrue(wait_for(lambda: self.effect() == "breathing"))

    def test_an_effect_you_pick_over_app_profiles_is_what_comes_back(self):
        self.apps_on()
        self.call("/run", {"effect": "rainbow"})
        self.assertFalse(self.call("/settings")[1]["apps"]["enabled"], "the App Profiles switch shows off")
        self.restart()
        self.assertEqual(self.effect(), "rainbow")
        self.assertIsNone(self.ctl.apps)

    def test_stopping_app_profiles_lighting_means_nothing_comes_back(self):
        self.apps_on()
        self.call("/stop", {})
        self.restart()
        self.assertIsNone(self.effect())

    def test_a_built_in_effect_a_profile_or_custom_colours_switch_app_profiles_off(self):
        for choice in (("/effect", {"mode": "breath"}), ("/custom", {"colors": {"W": "ff0000"}}),
                       ("/restore", {})):
            self.apps_on()
            self.assertEqual(self.call(*choice)[0], 200, choice)
            self.assertFalse(self.ctl.settings["apps"]["enabled"], choice)
        self.ctl.update_settings({"profiles": [{"name": "P", "hardware": {"mode": "normal"}}]})
        self.apps_on()
        self.assertEqual(self.call("/profiles/apply", {"name": "P"})[0], 200)
        self.assertFalse(self.ctl.settings["apps"]["enabled"])

    def test_the_battery_saver_a_game_or_a_program_pausing_app_profiles_keeps_them_on(self):
        self.apps_on()
        self.battery = True
        self.assertTrue(wait_for(lambda: self.ctl.apps is None))
        self.battery = False
        self.assertTrue(wait_for(lambda: self.ctl.apps is not None))
        self.games.feed("cs2").update(cs2())
        self.assertTrue(wait_for(lambda: self.effect() == "cs2"))
        self.game_ends()
        self.assertTrue(wait_for(lambda: self.ctl.apps is not None))
        self.assertTrue(self.ctl.settings["apps"]["enabled"])

    def test_a_preview_does_not_switch_app_profiles_off_for_good(self):
        self.apps_on()
        self.call("/run", {"effect": "solid", "params": {"color": "000000"}, "remember": False})
        self.assertTrue(self.ctl.settings["apps"]["enabled"])

    def test_restoring_the_backup_forgets_the_live_effect(self):
        self.call("/run", {"effect": "rainbow"})
        self.assertEqual(self.call("/restore", {})[0], 200)
        self.assertIsNone(self.ctl.settings.get("last_live"))
        self.restart()
        self.assertIsNone(self.effect(), "the restored lighting stays")

    def test_switching_app_profiles_off_forgets_the_live_effect(self):
        self.call("/run", {"effect": "rainbow"})
        self.apps_on()
        self.call("/apps", {"enabled": False})
        self.restart()
        self.assertIsNone(self.effect())

    def test_restoring_the_backup_ends_the_applied_profile(self):
        self.ctl.update_settings({"profiles": [{"name": "P", "live": {"effect": "rainbow"}}]})
        self.call("/profiles/apply", {"name": "P"})
        self.assertEqual(self.ctl.active_profile, "P")
        self.call("/restore", {})
        self.assertIsNone(self.ctl.active_profile)


# ============================================================================ test isolation
class GameFeedsInTests(unittest.TestCase):
    def test_putting_back_no_feed_removes_it(self):
        from evision_rgb import games
        prev = games.set_feed("cs2", None)
        self.addCleanup(games.set_feed, "cs2", prev)
        self.assertIsNotNone(games.feed("cs2"), "a fresh feed, not None")
        self.assertFalse(games.feed("cs2").active())


if __name__ == "__main__":
    unittest.main()

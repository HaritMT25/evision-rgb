"""Behaviour spec — the server side of fixes for bugs a review of the app window found (2026-10-12). Written BEFORE
the fixes. (The window's side is checked by scripts/ui_smoke.mjs against the simulated keyboard.)

1. Discarding a custom colours preview, or switching groups, ended your lighting: the window sent /stop, which
   means "the keyboard's own effect" (the live effect forgotten, app profiles switched off for good), even when no
   preview was running. POST /preview/end brings back what you chose, as a restart would.
2. Renaming a profile could give two profiles the same name (the second one could then never be applied), and the
   pad keys and knobs that applied it kept the old name and failed. POST /profiles/rename refuses duplicates and
   renames them too.
"""
import unittest

from test_feature_battery import _ServerCase, wait_for

PREVIEW = {"effect": "solid", "params": {"color": "000000", "keys": {"W": "ff0000"}, "raw": True}, "remember": False}


class EndingAPreview(_ServerCase):
    def preview(self):
        self.assertEqual(self.call("/run", PREVIEW)[0], 200)
        self.assertEqual(self.effect(), "solid")

    def test_your_live_effect_comes_back(self):
        self.call("/run", {"effect": "rainbow", "params": {"speed": 0.3}})
        self.preview()
        code, _ = self.call("/preview/end", {})
        self.assertEqual(code, 200)
        self.assertEqual((self.effect(), self.ctl.engine.params), ("rainbow", {"speed": 0.3}))
        self.assertEqual(self.ctl.settings["last_live"], {"effect": "rainbow", "params": {"speed": 0.3}})

    def test_app_profiles_come_back(self):
        self.call("/apps", {"enabled": True, "default": {"effect": "breathing", "params": {}}, "rules": []})
        self.assertTrue(wait_for(lambda: self.effect() == "breathing"))
        self.preview()
        self.call("/preview/end", {})
        self.assertTrue(wait_for(lambda: self.ctl.apps is not None and self.effect() == "breathing"))
        self.assertTrue(self.ctl.settings["apps"]["enabled"])

    def test_with_nothing_chosen_the_keyboard_shows_its_own_effect(self):
        self.preview()
        self.call("/preview/end", {})
        self.assertIsNone(self.effect())
        self.assertTrue(wait_for(lambda: self.kb.visible()[0] == "effect"))

    def test_on_battery_it_waits_for_the_charger(self):
        self.call("/run", {"effect": "rainbow", "params": {}})
        self.battery = True
        self.assertTrue(wait_for(lambda: self.effect() is None))
        self.preview()  # (a preview is shown on battery: you are looking at it)
        self.call("/preview/end", {})
        self.assertIsNone(self.effect(), "nothing live on battery")
        self.battery = False
        self.assertTrue(wait_for(lambda: self.effect() == "rainbow"), "it comes back with the charger")


class RenamingAProfile(_ServerCase):
    def setUp(self):
        super().setUp()
        self.ctl.update_settings({"profiles": [{"name": "Work", "hardware": {"mode": 6, "color": "00ff00"}},
                                               {"name": "Gaming", "hardware": {"mode": 5, "color": "0000ff"}}]})
        self.ctl.pads.set_key("akp03", 0, {"label": "Game", "icon": "", "color": "000000",
                                           "action": {"type": "profile", "name": "Gaming"}})
        self.ctl.pads.dispatch = lambda fn: fn()

    def names(self):
        return [p["name"] for p in self.call("/settings")[1]["profiles"]]

    def test_two_profiles_never_get_the_same_name(self):
        code, res = self.call("/profiles/rename", {"name": "Gaming", "to": "Work"})
        self.assertEqual(code, 400, res)
        self.assertEqual(self.names(), ["Work", "Gaming"])
        self.assertEqual(self.call("/profiles/rename", {"name": "Gaming", "to": "   "})[0], 400, "nor no name")
        self.assertEqual(self.call("/profiles/rename", {"name": "Nope", "to": "Other"})[0], 404)

    def test_the_pad_keys_that_apply_it_follow_the_new_name(self):
        code, res = self.call("/profiles/rename", {"name": "Gaming", "to": "Gaming night"})
        self.assertEqual(code, 200, res)
        self.assertEqual(self.names(), ["Work", "Gaming night"])
        self.assertEqual(self.ctl.pads.config("akp03")["keys"][0]["action"], {"type": "profile", "name": "Gaming night"})
        self.ctl.pads.press("akp03", key=0)
        self.assertEqual(self.kb.visible()[:2], ("effect", 0x05), "the pad key applies it")
        self.assertIsNone(self.ctl.pads.error)

    def test_the_current_profile_keeps_being_the_current_one(self):
        self.call("/profiles/apply", {"name": "Gaming"})
        self.call("/profiles/rename", {"name": "Gaming", "to": "Gaming night"})
        self.assertEqual(self.ctl.active_profile, "Gaming night")


if __name__ == "__main__":
    unittest.main()

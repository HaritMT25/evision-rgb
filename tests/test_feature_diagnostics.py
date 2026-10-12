"""Behaviour spec — diagnostics for bug reports. Written BEFORE the implementation.

GET /diagnostics gathers what a bug report needs in one JSON document: app and Python versions, the OS, whether
this is the packaged app, the keyboard (connected, stored effect, capabilities), what is running (live effect,
battery saver, games, OpenRGB, the exact-emulation engine), the settings and the recent events. It is safe to
paste into a public issue: secrets (the game token, anything called a token) are hidden and the user's home folder
is shown as "~". Asking for it never touches the keyboard beyond reading its state. Other Settings has a button
that copies it.
"""
import json
import os
import unittest

from test_feature_battery import WEB, _ServerCase


class Diagnostics(_ServerCase):
    def diag(self):
        code, body = self.call("/diagnostics")
        self.assertEqual(code, 200, body)
        return body

    def test_versions_os_and_build(self):
        from evision_rgb import __version__
        d = self.diag()
        self.assertEqual(d["app"]["version"], __version__)
        for k in ("python", "platform", "frozen"):
            self.assertIn(k, d["app"])
        self.assertIs(d["app"]["frozen"], False)

    def test_the_keyboard_and_what_runs(self):
        self.call("/run", {"effect": "rainbow"})
        d = self.diag()
        self.assertEqual(d["keyboard"]["connected"], True)
        self.assertEqual(d["keyboard"]["info"]["mode"], "normal")
        self.assertIn("caps", d["keyboard"]["info"])
        self.assertEqual(d["status"]["effect"], "rainbow")
        for k in ("battery", "openrgb", "firmware"):
            self.assertIn(k, d)

    def test_recent_events_and_settings_are_included(self):
        d = self.diag()
        self.assertIn("app started", d["events"][0]["text"])
        self.assertIn("startup", d["settings"])
        self.assertIn("battery", d["automation"], "the automation in effect, saved or default")

    def test_secrets_are_hidden(self):
        self.ctl.games_settings()  # makes the game token
        token = self.ctl.settings["games"]["token"]
        self.ctl.update_settings({"pads": {"x": {"nested": [{"token": "abc123secret"}]}}})
        text = json.dumps(self.diag())
        self.assertNotIn(token, text)
        self.assertNotIn("abc123secret", text)
        self.assertIn("(hidden)", text)

    def test_the_home_folder_is_shown_as_a_tilde(self):
        home = os.path.expanduser("~")
        self.ctl.update_settings({"backup_dir": os.path.join(home, "somewhere", "backup")})
        d = self.diag()
        self.assertNotIn(home, json.dumps(d))
        self.assertEqual(d["settings"]["backup_dir"], os.path.join("~", "somewhere", "backup"))

    def test_the_home_folder_is_hidden_in_any_spelling(self):
        home = os.path.expanduser("~")
        path = os.path.join(home, "AppData", "x.log")
        self.ctl.update_settings({"pads": {path.upper(): repr(OSError(path)), "fwd": path.replace(os.sep, "/")}})
        self.ctl.events.add(f"error in automation: {OSError(path)!r}")
        text = json.dumps(self.diag())
        user = os.path.basename(home)
        self.assertNotIn(user.lower(), text.lower(), "repr()'s doubled backslashes, a key, any case, forward slashes")

    def test_asking_never_writes_to_the_keyboard(self):
        with self.kb.lock:
            before = len([p for p in self.kb.writes if len(p) > 3 and p[3] in (0x01, 0x06, 0x0B)])
        self.diag()
        with self.kb.lock:
            after = len([p for p in self.kb.writes if len(p) > 3 and p[3] in (0x01, 0x06, 0x0B)])
        self.assertEqual(before, after)

    def test_works_while_the_keyboard_is_unplugged(self):
        self.kb.unplug()
        d = self.diag()
        self.assertEqual(d["keyboard"]["connected"], False)
        self.assertIn("error", d["keyboard"])


class DiagnosticsInTheApp(unittest.TestCase):
    def test_other_settings_has_a_copy_button(self):
        with open(os.path.join(WEB, "index.html"), encoding="utf-8") as f:
            self.assertIn('id="sDiag"', f.read())
        with open(os.path.join(WEB, "app.js"), encoding="utf-8") as f:
            self.assertIn('"/diagnostics"', f.read())


if __name__ == "__main__":
    unittest.main()

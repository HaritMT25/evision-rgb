"""Behaviour spec — bugs a second review found (2026-10-12), each confirmed with a script. Written BEFORE the fixes.

1. The keyboard is only asked for memory the official app uses. `evrgb custom --colorset 3` (the window numbers the
   groups 1-3), POST /custom {"colorset": 3} or GET /custom?colorset=3 wrote or read past the three custom colour
   areas, into the keyboard's program memory. Now they are refused before anything is sent, and the protocol layer
   refuses any command outside the ranges the official app uses.
2. The app started before the keyboard was plugged in, restoring a layer stack with the keyboard's own effect at the
   bottom: once plugged in the keyboard stayed black (the effect was published before it could start, and the
   stored-effect layer needed the keyboard at that very moment). Now the layer waits for the keyboard, and an effect
   that cannot start is not left half running.
3. The macro pad's brightness knob ended the live effect, forgot it for the next start and switched app profiles
   off for good (it went through "store this hardware effect"). Now it only stores the brightness.
4. App profiles with "Keyboard's own effect" as the default did nothing for programs no rule matches, until a
   program that one matches had been in front.
5. /apps accepted rules whose effect options are wrong; they then failed with a print nobody sees. Now 400 with
   the reason, and a rule that fails anyway (settings from before) is in the event log.
6. A settings file the app could not read (saved by Notepad as "UTF-8 with BOM", or cut short) was replaced at the
   next save: profiles, pad set-up and app rules gone. Now a BOM is fine, and an unreadable file is kept next to
   it (settings.json.bad-...) and noted in the event log.
7. Live key colours set with nothing else running (MCP set_live_keys) left the keyboard black after they were
   cleared: the black base under them kept running.
8. `evrgb live solid color=990000`: colours written only with digits became numbers (a crash).
9. `evrgb apps`: rules using fire, ocean, twinkle, digital rain, album colours or game lighting failed.
10. With the mic-mute indicator on, the microphone's Core Audio objects were made again about 18 times a second
    (the automation loop and the render thread took turns).
11. Shift+Alt shortcut hints (VS Code, Windows Terminal) never showed: their tables said "shift+alt".
"""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import warnings

import numpy as np

from fake_device import install
from test_feature_battery import _ServerCase, key_rgb, wait_for

TESTS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TESTS)


def custom_packets(kb):
    return [p for p in kb.writes if len(p) == 64 and p[3] in (0x0A, 0x0B)]


# ============================================================================ 1. memory the official app uses
class DeviceRanges(unittest.TestCase):
    def setUp(self):
        warnings.simplefilter("ignore", ResourceWarning)
        self.kb = install(self)

    def test_the_protocol_refuses_ranges_the_official_app_never_uses(self):
        from evision_rgb.protocol import (AK820, CMD_DIRECT, CMD_READ_CONFIG, CMD_READ_CUSTOM, CMD_WRITE_CONFIG,
                                          CMD_WRITE_CUSTOM, ProtocolError)
        dev = AK820(allow_writes=True)
        self.addCleanup(dev.close)
        n = len(self.kb.writes)
        writes = (CMD_WRITE_CUSTOM, CMD_WRITE_CONFIG, CMD_DIRECT)
        for cmd, length, offset in ((CMD_WRITE_CUSTOM, 0x38, 0x600), (CMD_WRITE_CUSTOM, 0x38, 0xFE00),
                                    (CMD_READ_CUSTOM, 0x38, 0x180), (CMD_WRITE_CUSTOM, 0x10, 0x178),
                                    (CMD_WRITE_CONFIG, 1, 0x1D), (CMD_READ_CONFIG, 0x38, 0x80), (CMD_DIRECT, 8, 0x180)):
            with self.assertRaises(ProtocolError, msg=f"{cmd:#04x} at {offset:#x}+{length}"):
                dev.query(cmd, length, offset, bytes(length) if cmd in writes else b"")
        self.assertEqual(len(self.kb.writes), n, "nothing was sent")
        for cs in range(3):  # what the app does use still works
            self.assertEqual(len(dev.read_custom(cs)), 384)
        self.assertEqual(len(dev.read_config(0x80)), 0x80)
        self.assertEqual(len(dev.read_keymap()), 384)

    def test_custom_colours_go_only_to_the_three_groups(self):
        from evision_rgb.keyboard import Keyboard
        k = Keyboard()
        self.addCleanup(k.close)
        n = len(self.kb.writes)
        for cs in (3, -1, 7):
            with self.assertRaises(ValueError):
                k.set_custom({"W": "ff0000"}, colorset=cs)
            with self.assertRaises(ValueError):
                k.set_custom({"W": "ff0000"}, colorset=cs, activate=False)
            with self.assertRaises(ValueError):
                k.get_custom(cs)
        self.assertEqual(len(self.kb.writes), n, "refused before anything was sent")
        k.set_custom({"W": "ff0000"}, colorset=2)  # the third group still works
        self.assertEqual(k.get_custom(2)["W"], "ff0000")

    def test_the_command_line_offers_only_the_three_groups(self):
        from evision_rgb import cli
        n = len(self.kb.writes)
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            cli.main(["custom", "--colorset", "3", "W=ff0000"])
        self.assertEqual(len(self.kb.writes), n)


class CustomColoursOverTheAPI(_ServerCase):
    def test_a_wrong_group_is_refused_and_changes_nothing(self):
        self.call("/run", {"effect": "rainbow", "params": {}})
        before = len(custom_packets(self.kb))
        code, res = self.call("/custom", {"colors": {"W": "ff0000"}, "colorset": 3})
        self.assertEqual(code, 400, res)
        self.assertEqual(self.effect(), "rainbow", "a refused request leaves the lighting as it was")
        self.assertEqual(self.call("/custom?colorset=3")[0], 400)
        self.assertEqual(len(custom_packets(self.kb)), before, "nothing read or written")


# ============================================================================ 2. started before the keyboard
class StartedBeforeTheKeyboard(_ServerCase):
    LAYERS = [{"effect": "keyboard"}, {"effect": "solid", "params": {"color": "ff0000"}, "keys": ["W"]}]

    def test_a_restored_layer_stack_shows_once_the_keyboard_is_plugged_in(self):
        probes = self.ctl.automation.probes
        self.httpd.shutdown()
        self.ctl.close()
        self.kb.unplug()  # the PC starts the app before the keyboard is there
        self.make(probes, {"startup": "restore", "last_live": {"effect": "layers", "params": {"layers": self.LAYERS}}})
        self.ctl.startup()
        self.assertEqual(self.effect(), "layers")
        self.kb.replug()

        def shows():  # W red on the stored green effect, not on black
            w, q = key_rgb(self.kb, "W"), key_rgb(self.kb, "Q")
            return w == (255, 0, 0) and q is not None and q[0] == 0 and q[1] > 0
        self.assertTrue(wait_for(shows, secs=6), (key_rgb(self.kb, "W"), key_rgb(self.kb, "Q")))


class EffectsThatCannotStart(unittest.TestCase):
    def test_an_effect_that_cannot_start_is_not_left_half_running(self):
        warnings.simplefilter("ignore", ResourceWarning)
        kb = install(self)
        from evision_rgb.effects import EFFECTS, Effect, Engine, N

        class CannotStart(Effect):
            name = "cannot-start"

            def start(self):
                raise RuntimeError("no capture device")

            def render(self, t):
                return np.zeros((N, 3))

        EFFECTS["cannot-start"] = CannotStart
        self.addCleanup(EFFECTS.pop, "cannot-start", None)
        eng = Engine()
        self.addCleanup(eng.close)
        eng.run("rainbow")
        self.assertTrue(wait_for(lambda: kb.visible()[0] == "direct"))
        with self.assertRaises(RuntimeError):
            eng.run("cannot-start")
        st = eng.status()
        self.assertEqual((st["effect"], st["live"]), (None, False), "not reported as running")
        self.assertTrue(wait_for(lambda: kb.visible()[0] == "effect", secs=3), "the keyboard shows its own effect")


# ============================================================================ 3. the pad's brightness knob
class PadBrightnessKnob(_ServerCase):
    def setUp(self):
        super().setUp()
        self.pads = self.ctl.pads
        self.pads.dispatch = lambda fn: fn()  # run pad actions inline

    def knob(self, move):  # the AKP03's second knob turns the brightness by default
        self.assertEqual(self.pads.config("akp03")["knobs"][1][move]["type"], "brightness")
        self.pads.press("akp03", knob=1, move=move)

    def test_the_knob_keeps_the_live_effect(self):
        self.ctl.run_live("rainbow", {"speed": 0.3})
        b = self.kb.config[2]
        self.knob("left")
        self.assertEqual(self.kb.config[2], b - 1, "one step dimmer, stored on the keyboard")
        self.assertTrue(wait_for(lambda: self.effect() == "rainbow"))
        time.sleep(0.3)
        self.assertEqual(self.effect(), "rainbow")
        self.assertEqual(self.ctl.engine.params, {"speed": 0.3})
        self.assertEqual(self.ctl.settings["last_live"], {"effect": "rainbow", "params": {"speed": 0.3}},
                         "the next start still brings it back")

    def test_the_knob_leaves_app_profiles_on(self):
        self.call("/apps", {"enabled": True, "default": {"effect": "breathing", "params": {}}, "rules": []})
        self.assertTrue(wait_for(lambda: self.effect() == "breathing"))
        self.knob("left")
        time.sleep(0.8)
        self.assertIsNotNone(self.ctl.apps, "app profiles still run")
        self.assertTrue(self.ctl.settings["apps"]["enabled"], "and are still switched on")
        self.assertEqual(self.effect(), "breathing")


# ============================================================================ 4.-5. app profiles
class AppProfilesRules(_ServerCase):
    def front(self, name):
        from evision_rgb import apps
        self.addCleanup(setattr, apps, "foreground", apps.foreground)
        apps.foreground = lambda: (name, "")

    def test_the_keyboards_own_effect_as_the_default_applies_at_once(self):
        self.front("explorer.exe")
        self.ctl.run_live("rainbow", {})
        code, _ = self.call("/apps", {"enabled": True, "default": None,
                                      "rules": [{"match": "spotify", "effect": "breathing", "params": {}}]})
        self.assertEqual(code, 200)
        self.assertTrue(wait_for(lambda: self.effect() is None), "no rule matches: the keyboard's own effect")

    def test_rules_with_wrong_options_are_refused(self):
        self.ctl.run_live("rainbow", {})
        bad = {"enabled": True, "default": None,
               "rules": [{"match": "explorer", "effect": "solid", "params": {"colour": "ff0000"}}]}
        code, res = self.call("/apps", bad)
        self.assertEqual(code, 400, res)
        self.assertIn("colour", res["error"])
        self.assertIn("explorer", res["error"], "which rule")
        self.assertEqual(self.effect(), "rainbow", "nothing changed")
        self.assertFalse(self.ctl.settings["apps"]["enabled"], "nor was it saved")
        for cfg in ({"default": {"effect": "nope", "params": {}}, "rules": []},
                    {"default": None, "rules": [{"effect": "rainbow"}]},  # no match
                    {"default": None, "rules": "spotify"}):
            self.assertEqual(self.call("/apps", {"enabled": False, **cfg})[0], 400, cfg)
        window = {"enabled": False, "default": {"effect": "layers", "params": {"layers": [{"effect": "rainbow"}]}},
                  "rules": [{"match": "game", "effect": None, "params": {}},
                            {"match": "code.exe", "effect": "reactive", "params": {"base": "000818"}}]}
        self.assertEqual(self.call("/apps", window)[0], 200, "what the window sends is fine")

    def test_a_rule_that_fails_anyway_is_in_the_event_log(self):
        self.front("explorer.exe")  # settings from before this check: the app starts them as they are
        self.ctl.start_apps({"enabled": True, "default": {"effect": "solid", "params": {"colour": "ff0000"}}, "rules": []})
        self.assertTrue(wait_for(lambda: any("colour" in e["text"] for e in self.ctl.events.recent()), secs=3),
                        [e["text"] for e in self.ctl.events.recent()])


class OldAppRules(unittest.TestCase):
    def test_a_rule_without_a_match_matches_nothing(self):  # (settings saved before rules were checked)
        from evision_rgb.apps import AppProfiles
        p = AppProfiles(None, {"default": {"effect": "rainbow"},
                               "rules": [{"effect": "fire"}, {"match": "", "effect": "ocean"},
                                         {"match": "code", "effect": "reactive"}]})
        self.assertEqual(p.pick("code.exe", "")["effect"], "reactive")
        self.assertEqual(p.pick("explorer.exe", "Desktop")["effect"], "rainbow")


# ============================================================================ 6. the settings file
class SettingsFile(unittest.TestCase):
    def setUp(self):
        import evision_rgb.settings as st
        self.st = st
        self.tmp = tempfile.mkdtemp()
        for attr, val in (("DIR", self.tmp), ("PATH", os.path.join(self.tmp, "settings.json")),
                          ("OLD_DIR", os.path.join(self.tmp, "old"))):
            self.addCleanup(setattr, st, attr, getattr(st, attr))
            setattr(st, attr, val)

    def write(self, text, encoding="utf-8"):
        with open(self.st.PATH, "w", encoding=encoding) as f:
            f.write(text)

    def bad_copies(self):
        return sorted(f for f in os.listdir(self.tmp) if f.startswith("settings.json.bad"))

    def test_a_file_saved_with_a_bom_is_read(self):
        self.write(json.dumps({"profiles": [{"name": "Mine"}]}), "utf-8-sig")
        self.assertEqual(self.st.load()["profiles"], [{"name": "Mine"}])

    def test_an_unreadable_file_is_kept_not_overwritten(self):
        for damaged in ('{"profiles": [{"name": "Mine"}], "pads": {"ak', '[1, 2, 3]'):
            self.write(damaged)
            s = self.st.load()
            self.assertEqual(s["profiles"], [], "starts from the defaults")
            self.st.save(s)
            with open(self.st.PATH, encoding="utf-8") as f:
                json.load(f)  # a good file again
            kept = self.bad_copies()
            self.assertTrue(kept, "the old file is kept")
            with open(os.path.join(self.tmp, kept[-1]), encoding="utf-8") as f:
                self.assertEqual(f.read(), damaged)
            for f in kept:
                os.remove(os.path.join(self.tmp, f))

    def test_the_app_says_so_in_its_event_log(self):
        self.write('{"profiles": [')
        install(self)
        import datetime as dt
        from evision_rgb.server import Controller
        ctl = Controller(probes={"now": lambda: dt.datetime(2026, 1, 1, 12)}, hook_keys=False)
        self.addCleanup(ctl.close)
        texts = [e["text"] for e in ctl.events.recent()]
        self.assertTrue(any("settings" in t and self.bad_copies()[0] in t for t in texts), texts)


# ============================================================================ 7. live key colours with nothing else
class LiveKeysAlone(_ServerCase):
    def test_clearing_them_gives_the_keyboard_back(self):
        self.call("/keys", {"colors": {"Esc": "ff0000"}})
        self.assertTrue(wait_for(lambda: key_rgb(self.kb, "Esc") == (255, 0, 0)))
        self.call("/clear", {})
        self.assertTrue(wait_for(lambda: self.kb.visible()[0] == "effect", secs=3), "its own effect, not black")
        self.assertIsNone(self.effect())

    def test_taking_the_last_one_away_too(self):
        self.call("/keys", {"colors": {"Esc": "ff0000"}})
        self.assertTrue(wait_for(lambda: key_rgb(self.kb, "Esc") == (255, 0, 0)))
        self.call("/keys", {"colors": {"Esc": None}})
        self.assertTrue(wait_for(lambda: self.kb.visible()[0] == "effect", secs=3))

    def test_your_effect_keeps_running_when_they_are_cleared(self):
        self.call("/run", {"effect": "rainbow", "params": {}})
        self.call("/keys", {"colors": {"Esc": "ff0000"}})
        self.call("/clear", {})
        time.sleep(0.3)
        self.assertEqual(self.effect(), "rainbow")

    def test_after_an_indicator_too(self):  # the black base the indicator mirror leaves for them
        self.caps = True
        self.assertTrue(wait_for(lambda: self.effect() == "emulate"))
        self.call("/keys", {"colors": {"Esc": "ff0000"}})
        self.caps = False
        self.assertTrue(wait_for(lambda: self.effect() == "solid"))
        self.call("/clear", {})
        self.assertTrue(wait_for(lambda: self.kb.visible()[0] == "effect", secs=3))


# ============================================================================ 8.-9. the command line
class CommandLine(unittest.TestCase):
    def test_colours_written_with_digits_only_stay_colours(self):
        warnings.simplefilter("ignore", ResourceWarning)
        kb = install(self)
        from evision_rgb import cli
        from evision_rgb.layout import KEYS
        w = KEYS["W"][0]
        for args, check in ((["solid", "color=990000"], lambda f: f[3 * w] > 0 and f[3 * w + 1] == f[3 * w + 2] == 0),
                            (["sysmon", "base=000010"], lambda f: True),
                            (["rainbow", "speed=0.5"], lambda f: True)):  # numbers stay numbers
            kb.history.clear()
            with contextlib.redirect_stdout(io.StringIO()):
                cli.main(["live"] + args + ["--seconds", "0.2"])
            frames = [h[1] for h in kb.history if h[0] == "direct"]
            self.assertTrue(frames and any(check(f) for f in frames), args)

    def test_app_rules_may_use_every_effect(self):
        tmp = tempfile.mkdtemp()
        profile = os.path.join(tmp, "profile.json")
        with open(profile, "w", encoding="utf-8") as f:
            json.dump({"default": {"effect": "rainbow"}, "rules": [{"match": "game.exe", "effect": "fire"}]}, f)
        runner = os.path.join(tmp, "runner.py")
        with open(runner, "w", encoding="utf-8") as f:  # a fresh process: only what `evrgb apps` itself loads
            f.write(CLI_APPS)
        p = subprocess.run([sys.executable, runner, TESTS, ROOT, profile], capture_output=True, text=True, timeout=120)
        self.assertIn("ran: fire", p.stdout, p.stdout + p.stderr)
        self.assertNotIn("profile error", p.stdout)


CLI_APPS = r'''
import sys, time as _time
tests, root, profile = sys.argv[1:4]
sys.path[:0] = [tests, root]
from fake_device import install
install(type("T", (), {"addCleanup": lambda self, *a, **k: None})())
from evision_rgb import apps, cli
apps.foreground = lambda: ("game.exe", "My game")
seen = []
stop = apps.AppProfiles.stop
def spy(self):
    seen.append(self.engine.status()["effect"])
    stop(self)
apps.AppProfiles.stop = spy
class Clock:  # Ctrl+C after a few rounds of the command's loop
    monotonic = staticmethod(_time.monotonic)
    n = 0
    def sleep(self, s):
        self.n += 1
        if self.n > 3:
            raise KeyboardInterrupt
        _time.sleep(s)
cli.time = Clock()
cli.main(["apps", profile])
print("ran:", seen[0] if seen else None)
'''


# ============================================================================ 10. the mic-mute indicator
class MicMuteProbe(unittest.TestCase):
    def test_the_microphone_is_opened_once_while_threads_take_turns(self):
        from evision_rgb.automation import _MicProbe
        opened, muted = [], [True]

        class Volume:
            def GetMute(self):
                return muted[0]

        class Probe(_MicProbe):
            def _open(self):
                opened.append(threading.current_thread().name)
                return Volume()

        probe, seen = Probe(), {"a": [], "b": []}

        def reader(name):  # the automation loop and the render thread
            for _ in range(40):
                seen[name].append(probe())
                time.sleep(0.003)

        threads = [threading.Thread(target=reader, args=(n,), name=n) for n in seen]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(opened), 1, opened)
        self.assertTrue(all(seen["a"][5:]) and all(seen["b"][5:]), seen)
        muted[0] = False
        self.assertFalse(probe(), "a thread that outlives the one that opened it reads it itself")
        self.assertEqual(len(opened), 2)


# ============================================================================ 11. shortcut hints
class ShortcutHints(unittest.TestCase):
    def test_shift_alt_hints_show(self):
        from evision_rgb.shortcuts import hints
        self.assertEqual(hints("code.exe", {"shift", "alt"}).get("Up"), "edit")
        self.assertEqual(hints("windowsterminal.exe", {"alt", "shift"}).get("Equal"), "window")
        mine = {"*": {"shift+ctrl": {"edit": ["Q"]}}}
        self.assertEqual(hints("notepad.exe", {"ctrl", "shift"}, mine).get("Q"), "edit", "your own, in any order")


if __name__ == "__main__":
    unittest.main()

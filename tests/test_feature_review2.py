"""Behaviour spec — bugs a third review found (2026-10-12), each confirmed with a script. Written BEFORE the fixes.

1. Custom colours changed shade when saved: while you painted, the keyboard previewed them as a live effect (with
   the gamma live effects use), but they are stored, and shown, as the LED values themselves. Now the preview sends
   them as they will be stored.
2. The desktop app never saved the typing heatmap: Quit didn't close the controller, so every quit, shutdown or crash
   lost the counts. Now Quit saves them, and they are saved every minute while counting.
3. A heatmap brought back at start (or by an app rule) was all black: only the run you started got the counts. Now the
   heatmap reads the counts itself, and follows your typing.
4. With "Close to tray" off, closing the window didn't quit: the tray icon, the live effect and the API went on, and
   nothing could bring the window back.
5. Indicators, notifications and the "keyboard" layer drew the stored colours darker (the approximate emulation put
   the live-effect gamma on LED values; the exact engine didn't).
6. The black base under live key colours stayed after they were cleared again, once the pad's brightness knob or the
   battery saver had restarted it.
7. On a custom layout the window's preview read the custom colours from the keyboard about 60 times a second (some
   400 USB transactions a second, under the app's lock). Now they are read once a second at most.
8. A second app started while the first was still starting (autostart and a click at login) ran beside it: Windows
   let both listen on the same port, and both drew on the keyboard. Now the port is taken exclusively and first,
   before anything touches the keyboard, and the second app brings the first one's window forward.
"""
import json
import os
import socket
import threading
import time
import types
import sys
import unittest
import warnings

from fake_device import install
from test_feature_battery import ROOT, _ServerCase, key_rgb, wait_for
from evision_rgb.layout import KEYS


def gui_module(test):
    """evision_rgb.gui.app; the numba settings its import makes for the real app are undone after the test, and its
    log goes to a temp file (never the real app's)."""
    import tempfile
    saved = {k: os.environ.get(k) for k in ("NUMBA_CACHE_DIR", "NUMBA_CACHE_LOCATOR_CLASSES")}

    def restore():
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    test.addCleanup(restore)
    from evision_rgb.gui import app
    test.addCleanup(setattr, app, "LOG", app.LOG)
    app.LOG = os.path.join(tempfile.mkdtemp(), "evisionrgb.log")
    return app


# ============================================================================ 1. custom colours as they are stored
class CustomPreview(_ServerCase):
    COLORS = {"W": "ff8000", "A": "0099ff", "S": "8dff8d"}

    def test_the_preview_shows_the_colours_as_they_will_be_stored(self):
        code, res = self.call("/run", {"effect": "solid", "params": {"color": "000000", "keys": self.COLORS, "raw": True},
                                       "remember": False})
        self.assertEqual(code, 200, res)
        self.assertTrue(wait_for(lambda: key_rgb(self.kb, "W") == (0xff, 0x80, 0x00)), key_rgb(self.kb, "W"))
        self.assertEqual(key_rgb(self.kb, "A"), (0x00, 0x99, 0xff))
        self.assertEqual(self.call("/frame")[1]["colors"]["W"], "ff8000", "and the window shows them the same")
        self.call("/custom", {"colors": self.COLORS, "colorset": 0})
        shown = self.kb.visible()
        w = KEYS["W"][0]
        self.assertEqual((shown[0], tuple(shown[1][3 * w:3 * w + 3])), ("custom", (0xff, 0x80, 0x00)), "same once saved")

    def test_the_window_previews_them_that_way(self):
        with open(os.path.join(ROOT, "evision_rgb", "gui", "web", "app.js"), encoding="utf-8") as f:
            js = f.read()
        at = js.index("const pushPreview")
        self.assertIn("raw: true", js[at:at + 300])


# ============================================================================ 2.-4. the desktop app
class _Event(list):
    def __iadd__(self, fn):
        self.append(fn)
        return self


class FakeWindow:
    def __init__(self):
        self.events = types.SimpleNamespace(closing=_Event())
        self.open, self.shown = True, 0

    def close_button(self):  # the window's X: it closes unless a handler cancels
        if self.open and all(h() is not False for h in self.events.closing):
            self.open = False

    destroy = close_button

    def show(self):
        self.shown += 1

    def restore(self):
        pass

    def hide(self):
        pass


class FakeIcon:
    """pystray's icon: its loop runs in a NON-daemon thread until stop() (it keeps the app alive)."""

    def __init__(self, name, image, title, menu):
        self.title, self.menu, self.visible = title, menu, False
        self.stopped = threading.Event()

    def run_detached(self, setup):
        threading.Thread(target=self.stopped.wait, name="fake tray loop").start()
        setup(self)

    def stop(self):
        self.stopped.set()

    def notify(self, *a):
        pass

    def update_menu(self):
        pass

    def click(self, label):
        item = next(i for i in self.menu.items if i[0] and i[0][0] == label)
        item[0][1](self, None)


class FakeGui:
    """pywebview and pystray as App.run() uses them; `user(window, icon)` is what happens while the window is open."""

    def __init__(self, test, user):
        self.test, self.user, self.window, self.icon = test, user, None, None
        webview = types.ModuleType("webview")
        webview.create_window = self.create_window
        webview.start = self.start
        pystray = types.ModuleType("pystray")
        pystray.Icon = self.make_icon
        pystray.MenuItem = lambda *a, **k: (a, k)

        class Menu:
            SEPARATOR = ((None,), {})

            def __init__(self, *items):
                self.items = items
        pystray.Menu = Menu
        for name, mod in (("webview", webview), ("pystray", pystray)):
            test.addCleanup(lambda n=name, old=sys.modules.get(name): sys.modules.__setitem__(n, old) if old
                            else sys.modules.pop(n, None))
            sys.modules[name] = mod

    def create_window(self, *a, **k):
        self.window = FakeWindow()
        return self.window

    def make_icon(self, *a):
        self.icon = FakeIcon(*a)
        self.test.addCleanup(self.icon.stop)  # (its thread is not a daemon: never left running after the test)
        return self.icon

    def start(self, **k):  # returns once the window is closed
        self.user(self.window, self.icon)
        assert not self.window.open, "the window is still open: the real webview.start() would never return"


class DesktopApp(unittest.TestCase):
    def setUp(self):
        warnings.simplefilter("ignore", ResourceWarning)
        self.kb = install(self)
        from fake_pad import install_pads
        install_pads(self)  # no pads (the app scans for them)
        import tempfile
        import evision_rgb.settings as st
        self.st = st
        tmp = tempfile.mkdtemp()
        for attr, val in (("DIR", tmp), ("PATH", os.path.join(tmp, "settings.json"))):
            self.addCleanup(setattr, st, attr, getattr(st, attr))
            setattr(st, attr, val)
        self.gui = gui_module(self)
        from evision_rgb import system
        self.addCleanup(setattr, system, "promote_tray_icon", system.promote_tray_icon)
        system.promote_tray_icon = lambda *a, **k: False  # (it writes the registry)
        import datetime as dt
        from evision_rgb.server import Controller
        self.made = []

        def controller():
            c = Controller(probes={"now": lambda: dt.datetime(2026, 1, 1, 12)}, hook_keys=False)
            self.made.append(c)
            self.addCleanup(c.close)
            return c
        self.addCleanup(setattr, self.gui, "Controller", self.gui.Controller)
        self.gui.Controller = controller

    def run_app(self, user, **settings):
        self.st.save({**self.st.load(), "tray_hint_shown": True, **settings})
        fake = FakeGui(self, user)
        app = self.gui.App(0, background=False)
        self.addCleanup(lambda: app.httpd and app.httpd.server_close())
        app.run()
        return app, fake

    def test_quit_from_the_tray_saves_the_heatmap(self):
        def user(window, icon):
            for _ in range(40):
                self.made[0].heatmap.record("W")  # (what the key hook does for each press)
            window.close_button()  # to the tray
            icon.click("Quit")
        self.run_app(user, heatmap_enabled=True)
        from evision_rgb.heatmap import KeyCounter
        self.assertEqual(KeyCounter(os.path.join(self.st.DIR, "heatmap.json"), enabled=True).counts(), {"W": 40})

    def test_closing_the_window_quits_when_close_to_tray_is_off(self):
        def user(window, icon):
            self.made[0].run_live("rainbow", {})
            window.close_button()
        app, fake = self.run_app(user, close_to_tray=False)
        self.assertTrue(fake.icon.stopped.is_set(), "the tray icon is gone (it would keep the app running)")
        self.assertFalse(self.made[0].engine.status()["live"], "and so is the live effect")

    def test_closing_the_window_keeps_the_app_in_the_tray_by_default(self):
        def user(window, icon):
            window.close_button()
            self.assertTrue(window.open, "hidden, not closed")
            self.assertFalse(icon.stopped.is_set())
            icon.click("Quit")
        self.run_app(user)


class HeatmapSaving(_ServerCase):
    def path(self):
        import evision_rgb.settings as st
        return os.path.join(st.DIR, "heatmap.json")

    def test_counts_are_saved_while_the_app_runs(self):  # (a crash or a shutdown keeps them)
        from evision_rgb import server
        from evision_rgb.heatmap import KeyCounter
        self.addCleanup(setattr, server, "HEATMAP_SAVE_S", server.HEATMAP_SAVE_S)
        server.HEATMAP_SAVE_S = 0.2
        self.call("/heatmap", {"enabled": True})
        for _ in range(3):
            self.ctl.heatmap.record("A")
        self.assertTrue(wait_for(lambda: KeyCounter(self.path(), True).counts().get("A") == 3, secs=3))

    def test_a_heatmap_brought_back_at_start_shows_the_counts_and_follows_the_typing(self):
        from evision_rgb.heatmap import KeyCounter
        kc = KeyCounter(self.path(), enabled=True)
        for k, n in (("W", 50), ("A", 5)):
            for _ in range(n):
                kc.record(k)
        kc.save()
        probes = self.ctl.automation.probes
        self.httpd.shutdown()
        self.ctl.close()
        self.make(probes, {"startup": "restore", "heatmap_enabled": True,
                           "last_live": {"effect": "heatmap", "params": {}}})
        self.ctl.startup()
        red = lambda k: (key_rgb(self.kb, k) or (0, 0, 0))[0] > 0xC0
        self.assertTrue(wait_for(lambda: red("W"), secs=3), key_rgb(self.kb, "W"))
        self.assertEqual(key_rgb(self.kb, "Q"), (0, 0, 0), "never pressed")
        for _ in range(500):
            self.ctl.heatmap.record("Q")
        self.assertTrue(wait_for(lambda: red("Q"), secs=4), "it follows your typing")


# ============================================================================ 5. stored colours under indicators
class StoredColoursUnderIndicators(_ServerCase):
    COLORS = {"W": "ff8000", "Q": "808080", "E": "40c0ff"}

    def stored(self):
        self.call("/custom", {"colors": self.COLORS, "colorset": 0})

    def test_caps_lock_keeps_the_layout_as_it_is(self):
        self.stored()
        self.caps = True
        self.assertTrue(wait_for(lambda: key_rgb(self.kb, "CapsLock") == (255, 255, 255)))
        for k, h in self.COLORS.items():
            self.assertEqual(key_rgb(self.kb, k), tuple(bytes.fromhex(h)), k)

    def test_the_keyboard_layer_too(self):
        self.stored()
        self.call("/run", {"effect": "layers", "params": {"layers": [{"effect": "keyboard"}]}})
        self.assertTrue(wait_for(lambda: key_rgb(self.kb, "W") == (0xff, 0x80, 0x00)), key_rgb(self.kb, "W"))
        self.assertEqual(key_rgb(self.kb, "Q"), (0x80, 0x80, 0x80))


# ============================================================================ 6. the black base under live keys
class LiveKeysAfterARestart(_ServerCase):
    def keys_then(self, between):
        self.call("/keys", {"colors": {"Esc": "ff0000"}})
        self.assertTrue(wait_for(lambda: key_rgb(self.kb, "Esc") == (255, 0, 0)))
        between()
        self.assertTrue(wait_for(lambda: key_rgb(self.kb, "Esc") == (255, 0, 0)), "the key colour is back")
        self.call("/clear", {})
        self.assertTrue(wait_for(lambda: self.kb.visible()[0] == "effect", secs=3), "its own effect, not black")

    def test_after_the_pads_brightness_knob(self):
        pads = self.ctl.pads
        pads.dispatch = lambda fn: fn()
        self.keys_then(lambda: pads.press("akp03", knob=1, move="left"))

    def test_after_a_battery_round_trip(self):
        def round_trip():
            self.battery = True
            self.assertTrue(wait_for(lambda: self.effect() is None))
            self.battery = False
        self.keys_then(round_trip)


# ============================================================================ 7. the preview of a custom layout
class CustomLayoutPreview(_ServerCase):
    def custom_reads(self):
        return sum(1 for p in self.kb.writes if len(p) == 64 and p[3] == 0x0A)

    def test_the_colours_are_read_once_a_second_at_most(self):
        self.call("/custom", {"colors": {"W": "ff0000"}, "colorset": 0})
        self.call("/frame")
        before = self.custom_reads()
        for _ in range(30):
            self.assertEqual(self.call("/frame")[1]["colors"]["W"], "ff0000")
        self.assertLessEqual(self.custom_reads() - before, 2 * 7, "a frame doesn't read 7 packets from the keyboard")

    def test_new_colours_show_at_once(self):
        self.call("/custom", {"colors": {"W": "ff0000"}, "colorset": 0})
        self.call("/frame")
        self.call("/custom", {"colors": {"W": "00ff00"}, "colorset": 0})
        self.assertEqual(self.call("/frame")[1]["colors"]["W"], "00ff00")


# ============================================================================ 8. one app at a time
class OneAppAtATime(unittest.TestCase):
    def test_a_second_server_cannot_take_the_port(self):
        from evision_rgb.server import listen
        first = listen(0)
        self.addCleanup(first.server_close)
        with self.assertRaises(OSError):
            listen(first.server_address[1]).server_close()

    def test_the_app_takes_its_port_before_it_touches_the_keyboard(self):
        gui = gui_module(self)
        busy = socket.socket()
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        self.addCleanup(busy.close)
        made = []
        self.addCleanup(setattr, gui, "Controller", gui.Controller)
        gui.Controller = lambda: made.append(1)
        with self.assertRaises(gui.PortTaken):
            gui.App(busy.getsockname()[1], background=True).start_backend()
        self.assertEqual(made, [], "no keyboard, no start-up lighting")

    def test_a_second_launch_brings_the_first_window_forward(self):
        gui = gui_module(self)
        answers, posted = iter([False, True]), []  # the first app was still starting when the second one asked
        for name, fake in (("_running", lambda port: next(answers)), ("_post", lambda port, path, *a, **k: posted.append(path) or {"shown": True})):
            self.addCleanup(setattr, gui, name, getattr(gui, name))
            setattr(gui, name, fake)

        class Taken:
            def __init__(self, *a):
                pass

            def run(self):
                raise gui.PortTaken("in use")
        self.addCleanup(setattr, gui, "App", gui.App)
        gui.App = Taken
        self.assertEqual(gui.main(["--port", "8899"]), 0)
        self.assertEqual(posted, ["/show"])


if __name__ == "__main__":
    unittest.main()

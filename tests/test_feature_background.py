"""Behaviour spec — what the app does in the background (2026-10-12), after the user asked whether the app takes power
with the keyboard unplugged (their earbuds also cut out on battery with the keyboard away). Written BEFORE the fixes.

1. Once the window had been opened and closed to the tray (or minimised), it went on drawing the live preview 60 times
   a second for nobody, and asked for the status every second: WebView2 tells the page it is hidden when the app
   starts hidden, but not when the window is hidden later (checked with pywebview 6.2.1 and a test window). Over the
   keyboard's own effect that is its firmware emulated 60 times a second: ~30% of a CPU core on the user's laptop, on
   battery too. Now the app tells the page when it hides or shows the window, and when it is minimised or restored.
2. While no macro pad is plugged in, the app looks for one every 2 s. It listed EVERY USB input device to do that,
   which reads each one's name strings: the user's earbuds (their buttons are such a device) were asked for theirs
   every 2 s, all day. Now only the pads' own vendors are listed.
"""
import unittest

from fake_pad import FakePad, install_pads
from test_feature_battery import wait_for
from test_feature_review2 import _DesktopCase


def sd():
    from evision_rgb import streamdock
    return streamdock


EARBUDS = {"vendor_id": 0x1620, "product_id": 0x0B21, "usage_page": 0x000C, "usage": 1, "path": b"earbud-buttons",
           "interface_number": 3}  # (the user's USB-C earbuds: audio + a consumer-control HID interface)
AK820 = {"vendor_id": 0x320F, "product_id": 0x505B, "usage_page": 0xFF1C, "usage": 0x92, "path": b"ak820",
         "interface_number": 1}


# ============================================================================ 2. looking for a pad
class LookingForAPad(unittest.TestCase):
    def test_only_the_pads_vendors_are_listed(self):
        fake = install_pads(self, extra=[EARBUDS, AK820])
        self.assertEqual(sd().scan(), [])
        vendors = {v for m in sd().MODELS.values() for v, _ in m.usb}
        self.assertTrue(fake.calls, "it looked")
        self.assertTrue(all(vid in vendors for vid, _pid in fake.calls),
                        f"devices of other vendors were listed (vendor 0 = every device): {fake.calls}")

    def test_every_supported_pad_is_still_found(self):
        for model in sd().MODELS.values():
            for usb in model.usb:
                with self.subTest(model=model.id, usb=usb):
                    pad = FakePad(model)
                    pad.vid, pad.pid = usb
                    install_pads(self, pad, extra=[EARBUDS, AK820])
                    self.assertEqual([(m.id, p) for m, p in sd().scan()], [(model.id, pad.path)])

    def test_the_pad_managers_search_too(self):
        fake = install_pads(self, extra=[EARBUDS])
        from evision_rgb.macropad import PadManager

        class Ctl:  # (the manager only looks for a pad while none is connected: no lighting needed)
            settings = {}
        mgr = PadManager(Ctl())
        now = [0.0]
        mgr.clock = lambda: now[0]
        for _ in range(3):
            mgr.tick()
            now[0] += mgr.SCAN_S
        self.assertGreaterEqual(len(fake.calls), 3)
        self.assertNotIn(0, {vid for vid, _ in fake.calls}, fake.calls)


# ============================================================================ 1. the window's page is told
class HiddenWindow(_DesktopCase):
    def test_closing_the_window_to_the_tray_tells_the_page(self):
        def user(window, icon):
            window.close_button()  # (to the tray)
            self.assertTrue(wait_for(lambda: window.told == [False], secs=5), window.told)
            icon.click("Open EVision RGB")
            self.assertTrue(wait_for(lambda: window.told == [False, True], secs=5), window.told)
            icon.click("Quit")
        self.run_app(user)

    def test_minimising_tells_the_page_too(self):
        def user(window, icon):
            window.minimize_button()
            self.assertTrue(wait_for(lambda: window.told == [False], secs=5), window.told)
            window.restore_button()
            self.assertTrue(wait_for(lambda: window.told == [False, True], secs=5), window.told)
            window.restore_button()  # (restored from maximised: nothing changed for the page)
            window.close_button()
            self.assertTrue(wait_for(lambda: window.told == [False, True, False], secs=5), window.told)
            icon.click("Quit")
        self.run_app(user)

    def test_a_page_that_cannot_be_told_changes_nothing_else(self):
        def user(window, icon):
            window.js_error = RuntimeError("the page is still loading")
            window.close_button()
            icon.click("Open EVision RGB")  # (a tray click must not fail: the second launch's /show uses it too)
            self.assertEqual(window.shown, 1)
            window.js_error = None
            window.close_button()
            self.assertTrue(wait_for(lambda: window.told == [False], secs=5), window.told)
            icon.click("Quit")
        self.run_app(user)


if __name__ == "__main__":
    unittest.main()

"""Hardware-in-the-loop checks against the REAL keyboard (opt-in; restores state after each test):

    set AK820_HW=1 && .venv\\Scripts\\python -m unittest discover -s tests -p "test_hardware.py" -v

These confirm the assumptions the simulator (fake_device.py) is built on. Visual behaviour
(flicker, what the LEDs show) still needs human eyes; docs/PROTOCOL.md lists what was confirmed on hardware.
"""
import os
import time
import unittest

from evision_rgb import protocol as P
from evision_rgb.keyboard import CONFIG_LEN, Keyboard, frame_from
from evision_rgb.layout import KEYS

HW = os.environ.get("AK820_HW") == "1"


@unittest.skipUnless(HW, "set AK820_HW=1 to run against the real keyboard")
class RealKeyboard(unittest.TestCase):
    def setUp(self):
        self.k = Keyboard()
        self.addCleanup(self.k.close)
        self.saved_cfg = self.k.dev.read_config(CONFIG_LEN)
        self.saved_area0 = self.k.dev.read_custom(0)
        self.addCleanup(self._restore)

    def _restore(self):
        self.k.dev.begin()
        self.k.dev.write_block(P.CMD_WRITE_CONFIG, self.saved_cfg, 0)
        self.k.dev.write_block(P.CMD_WRITE_CUSTOM, self.saved_area0, 0)
        self.k.dev.end()
        self.assertEqual(self.k.dev.read_config(CONFIG_LEN), self.saved_cfg)

    def test_capabilities_magic_and_map_size(self):
        caps = self.k.dev.capabilities()
        self.assertEqual(caps[:2], b"\xaa\x55")
        self.assertEqual(caps[5], 0x80)

    def test_keymap_matches_generated_layout(self):
        km = self.k.dev.read_keymap()
        self.assertEqual(km[KEYS["W"][0] * 3:KEYS["W"][0] * 3 + 3], bytes([0x20, 0, 0x1A]))  # HID usage W
        self.assertEqual(km[KEYS["Fn"][0] * 3:KEYS["Fn"][0] * 3 + 3], bytes([0xA0, 0x01, 0x00]))

    def test_config_write_reads_back_and_preserves_tail_bytes(self):
        tail = self.saved_cfg[19:]
        info = self.k.set_effect("breath", brightness=1, speed=2, color="102030")
        self.assertEqual((info["mode"], info["brightness"], info["speed"], info["color"]), ("breath", 1, 2, "102030"))
        self.assertEqual(self.k.dev.read_config(CONFIG_LEN)[19:], tail)

    def test_custom_single_transaction_stores_mode_and_colours(self):
        pat = {k: ("00ff00" if x < 0.5 else "ff00ff") for k, (s, x, y) in KEYS.items()}
        self.k.set_effect("normal")
        self.k.set_custom(pat)
        self.assertEqual(self.k.info()["mode"], "custom")
        self.assertEqual(self.k.dev.read_custom(0), bytes(frame_from(pat)))

    def test_direct_frames_are_acknowledged_at_a_useful_rate(self):
        n, t = 0, time.monotonic()
        while time.monotonic() - t < 1.0:
            self.k.dev.direct(b"\x00\x00\x20" * 128)
            n += 1
        self.k.dev.direct_end()
        self.assertGreater(n, 40, f"only {n} fps")

    def test_unsupported_app_poll_command_is_not_on_our_allow_list(self):
        with self.assertRaises(P.ProtocolError):
            self.k.dev.query(0x1A, 6)


if __name__ == "__main__":
    unittest.main()

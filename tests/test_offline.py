"""Offline tests (no keyboard needed): python -m unittest discover -s tests"""
import unittest

from evision_rgb.effects import EFFECTS, N, to_frame
from evision_rgb.keyboard import Keyboard, frame_from, parse_color
from evision_rgb.layout import KEYS
from evision_rgb.protocol import CMD_READ_CAPS, CMD_WRITE_CONFIG, build, checksum


class Protocol(unittest.TestCase):
    def test_caps_packet_matches_device_capture(self):
        # sent to the real AK820 and echoed back verbatim (analysis/probe_caps.txt)
        self.assertEqual(bytes(build(CMD_READ_CAPS, 0x22))[:8].hex(" "), "04 25 00 03 22 00 00 00")

    def test_static_red_matches_vendor_capture(self):
        # Marvo KG980A vendor-app capture (same PID): 04 2a 01 06 1a 00 00 00 | 00 06 05 00 00 00 ff ...
        data = bytes([0, 6, 5, 0, 0, 0, 0xFF]) + bytes(0x1A - 7)
        pkt = build(CMD_WRITE_CONFIG, 0x1A, 0, data)
        self.assertEqual(bytes(pkt[:16]).hex(" "), "04 2a 01 06 1a 00 00 00 00 06 05 00 00 00 ff 00")
        self.assertEqual(checksum(pkt), 0x012A)

    def test_checksum_unsigned(self):
        pkt = build(0x12, 0x38, 0, b"\xff" * 0x38)
        self.assertEqual(checksum(pkt), (0x12 + 0x38 + 0xFF * 0x38) & 0xFFFF)


class Layout(unittest.TestCase):
    def test_82_unique_slots(self):
        slots = [s for s, _, _ in KEYS.values()]
        self.assertEqual(len(KEYS), 82)
        self.assertEqual(len(set(slots)), 82)
        self.assertTrue(all(0 <= s < 128 for s in slots))

    def test_known_slots(self):  # verified on hardware (WASD preset + layout test)
        for k, s in {"A": 11, "W": 18, "S": 19, "D": 27, "Left": 109, "Up": 116, "Down": 117, "Right": 125, "Fn": 85}.items():
            self.assertEqual(KEYS[k][0], s, k)


class Fields(unittest.TestCase):
    def test_brightness_limit(self):
        with self.assertRaises(ValueError):
            Keyboard.apply_fields(bytearray(0x1D), brightness=5)  # 5 = LEDs off on AK820
        cfg = Keyboard.apply_fields(bytearray(0x1D), mode="static", brightness=4, color="00ff00", colorset=2)
        self.assertEqual(cfg[:9].hex(), "000604000000" + "00ff00")
        self.assertEqual(cfg[18], 2)

    def test_colors_and_frames(self):
        self.assertEqual(parse_color("#FF8000"), (255, 128, 0))
        self.assertEqual(parse_color("red"), (255, 0, 0))
        f = frame_from({"W": "ff0000"}, default="000010")
        self.assertEqual(f[18 * 3:18 * 3 + 3], b"\xff\x00\x00")
        self.assertEqual(f[0:3], b"\x00\x00\x10")


class Effects(unittest.TestCase):
    def test_all_render(self):
        for name, cls in EFFECTS.items():
            if name in ("reactive", "music", "keyboard"):  # need key hook / audio device / the app's stored effect
                continue
            e = cls()
            c = e.render(0.5)
            self.assertEqual(len(c), N, name)
            self.assertEqual(len(to_frame(c)), 384, name)


if __name__ == "__main__":
    unittest.main()

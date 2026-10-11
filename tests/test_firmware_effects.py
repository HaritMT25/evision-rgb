"""Specs checked against the keyboard's REAL firmware code running in the 8051 emulator.

Needs the official AJAZZ updater installed locally (the firmware is not part of this repo), so these
tests are skipped elsewhere. They encode behaviour the user observed on the physical keyboard; passing
here means the emulation reproduces the hardware because it IS the hardware's code."""
import os
import unittest

import numpy as np

from evision_rgb.layout import KEYS

try:
    from evision_rgb.firmware import FirmwareLighting, find_firmware
    IMAGE = find_firmware()
except Exception:  # missing pefile / numba / updater
    IMAGE = None

CFG = bytes.fromhex("00" "06" "04" "02" "00" "00" "ff2000") + bytes(0x1D - 9)


def slot(name):
    return KEYS[name][0]


@unittest.skipUnless(IMAGE, "AK820 firmware (official AJAZZ updater) not found on this PC")
class RealFirmware(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = FirmwareLighting(IMAGE)

    def engine(self, mode, **fields):
        import copy
        fl = copy.deepcopy(self.base)
        cfg = bytearray(CFG)
        cfg[1] = mode
        for k, v in fields.items():
            i = {"brightness": 2, "speed": 3, "direction": 4, "random": 5, "variant": 18, "area": 25}[k]
            cfg[i] = v
        if "rgb" in fields:
            pass
        fl.configure(bytes(cfg))
        return fl

    def run_frames(self, fl, secs, fps=30, presses=()):
        out = []
        presses = dict(presses)
        for i in range(int(secs * fps)):
            if i in presses:
                fl.press(slot(presses[i]))
            fl.advance(1 / fps)
            out.append(fl.frame().astype(float) / 255)
        return np.array(out)

    # --- static modes ------------------------------------------------------------------------
    def test_normal_is_the_colour_scaled_by_brightness(self):
        f = self.run_frames(self.engine(6, brightness=4), 0.5)[-1]
        w = f[slot("W")]
        self.assertGreater(w[0], 0.6)
        self.assertLess(w[2], 0.05)
        dim = self.run_frames(self.engine(6, brightness=1), 0.5)[-1][slot("W")]
        self.assertLess(dim[0], w[0])

    def test_custom_mode_shows_the_area_selected_by_config_byte_25(self):
        fl = self.engine(0x14, area=1)
        a0 = bytes(384); a1 = bytearray(384)
        a1[slot("W") * 3:slot("W") * 3 + 3] = b"\x00\xff\x00"
        fl.set_custom(a0, 0); fl.set_custom(bytes(a1), 1)
        f = self.run_frames(fl, 0.2)[-1]
        self.assertGreater(f[slot("W")][1], 0.5, "area 1 is displayed when byte 25 = 1")

    # --- observations from the physical keyboard ----------------------------------------------
    def test_cartoon_is_a_cluster_that_grows_fades_and_returns(self):
        f = self.run_frames(self.engine(14), 8)
        lit = f.max(axis=2) > 0.15
        counts = lit.sum(axis=1)
        self.assertGreater(counts.max(), 3)
        self.assertEqual(counts.min(), 0, "goes away completely at times")
        self.assertLess(counts.mean(), len(KEYS) * 0.6, "a cluster, not the whole board")

    def test_rain_drops_fall_down(self):
        from evision_rgb.effects import Y, X, INDEX
        f = self.run_frames(self.engine(15), 8)
        lit = f.max(axis=2) > 0.15
        self.assertTrue(lit[:, [KEYS[n][0] for n in KEYS if KEYS[n][2] > 0.7]].any(), "reaches lower rows")

    def test_speed_direction_picks_the_entry_edge(self):
        def first(fl, key):
            f = self.run_frames(fl, 4)
            return int(np.argmax(f[:, slot(key)].max(axis=1) > 0.3))
        self.assertLessEqual(first(self.engine(18, direction=0), "Tab"), first(self.engine(18, direction=0), "Backslash"))

    def test_traverse_is_dark_until_a_key_is_pressed_then_lights_its_row(self):
        fl = self.engine(9)
        idle = self.run_frames(fl, 1.0)
        self.assertLess(idle.max(), 0.05, "dark while idle")
        f = self.run_frames(fl, 1.0, presses={0: "H"})
        row = [n for n in KEYS if abs(KEYS[n][2] - KEYS["H"][2]) < 0.02]
        other = [n for n in KEYS if abs(KEYS[n][2] - KEYS["H"][2]) > 0.3]
        self.assertGreater(f[:, [slot(n) for n in row]].max(), 0.3)
        self.assertLess(f[:, [slot(n) for n in other]].max(), 0.05)

    def test_surmount_styles_are_fixed_presets_independent_of_the_chosen_colour(self):
        # firmware truth (the earlier "style 4 = chosen colour" guess was wrong)
        def colour(variant, rgb):
            fl = self.engine(17, variant=variant)
            cfg = bytearray(CFG); cfg[1] = 17; cfg[18] = variant; cfg[6:9] = bytes.fromhex(rgb)
            fl.configure(bytes(cfg))
            return self.run_frames(fl, 3.5)[-1][slot("W")]  # fades in over ~3 s
        for v in range(4):
            self.assertTrue(np.allclose(colour(v, "ff2000"), colour(v, "00ffff")), f"style {v}")
        # settles to preset colours (user saw style 4 = a cyan block even with pink selected)
        r, g, b = colour(3, "ff2000")
        self.assertTrue(g > 0.6 and b > 0.6 and r < 0.1, "style 4 is cyan")
        r, g, b = colour(1, "ff2000")
        self.assertTrue(r > 0.6 and g > 0.6 and b < 0.3, "style 2 is yellow")
        r, g, b = colour(2, "ff2000")
        self.assertTrue(g > 0.6 and r < 0.4, "style 3 is green")

if __name__ == "__main__":
    unittest.main()

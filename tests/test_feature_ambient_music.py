"""Behaviour spec — feature 1: screen ambient + more music visualiser styles.
Written BEFORE the implementation; every test states a user-visible behaviour."""
import unittest

import numpy as np

from evision_rgb.effects import INDEX, N, X, Y


def key(c, name):
    return np.asarray(c)[INDEX[name.lower()]]


def screen(left, right, h=90, w=160):
    img = np.zeros((h, w, 3), dtype=np.uint8)
    img[:, : w // 2] = left
    img[:, w // 2:] = right
    return img


class Ambient(unittest.TestCase):
    def make(self, img, **kw):
        from evision_rgb.effects import Ambient
        self.calls = 0

        def source():
            self.calls += 1
            return self.img
        self.img = img
        return Ambient(source=source, **kw)

    def settle(self, e, t0=0.0, secs=2.0, fps=30):
        c = None
        for i in range(int(secs * fps)):
            c = e.render(t0 + i / fps)
        return np.asarray(c)

    def test_keys_mirror_the_screen_region_under_them(self):
        e = self.make(screen((255, 0, 0), (0, 0, 255)), saturation=1.0)
        c = self.settle(e)
        self.assertGreater(key(c, "Q")[0], 0.8); self.assertLess(key(c, "Q")[2], 0.2)      # left → red
        self.assertGreater(key(c, "P")[2], 0.8); self.assertLess(key(c, "P")[0], 0.2)      # right → blue

    def test_vertical_position_is_mirrored_too(self):
        img = np.zeros((90, 160, 3), np.uint8); img[:45] = (0, 255, 0); img[45:] = (255, 255, 0)
        c = self.settle(self.make(img, saturation=1.0))
        self.assertLess(key(c, "F5")[0], 0.2)       # top row → green
        self.assertGreater(key(c, "Space")[0], 0.8)  # bottom row → yellow

    def test_average_mode_paints_every_key_with_the_screen_average(self):
        c = self.settle(self.make(screen((255, 0, 0), (0, 0, 255)), mode="average", saturation=1.0))
        self.assertTrue(np.allclose(c, c[0], atol=0.02))
        self.assertAlmostEqual(c[0][0], c[0][2], delta=0.05)  # purple-ish average of red + blue

    def test_saturation_boost_makes_dull_colours_more_vivid(self):
        dull = screen((150, 110, 110), (150, 110, 110))
        plain = self.settle(self.make(dull, saturation=1.0))
        boosted = self.settle(self.make(dull, saturation=2.0))
        spread = lambda c: c[:, 0].mean() - c[:, 1].mean()
        self.assertGreater(spread(boosted), spread(plain) * 1.5)

    def test_changes_are_smoothed_not_instant(self):
        e = self.make(screen((0, 0, 0), (0, 0, 0)), saturation=1.0, smooth=0.3)
        self.settle(e, secs=1)
        self.img = screen((255, 255, 255), (255, 255, 255))
        soon = np.asarray(e.render(1.05)).mean()
        later = self.settle(e, t0=1.1, secs=2).mean()
        self.assertGreater(soon, 0.02, "starts moving immediately")
        self.assertLess(soon, 0.7, "but does not jump")
        self.assertGreater(later, 0.9, "and converges")

    def test_screen_capture_rate_is_capped(self):
        e = self.make(screen((10, 10, 10), (10, 10, 10)), capture_fps=10)
        for i in range(60):           # 60 renders over 1 s
            e.render(i / 60)
        self.assertLessEqual(self.calls, 12)

    def test_black_screen_gives_dark_keys(self):
        c = self.settle(self.make(screen((0, 0, 0), (0, 0, 0))))
        self.assertLess(c.max(), 0.02)

    def test_registered_as_live_effect(self):
        from evision_rgb.effects import EFFECTS
        self.assertIn("ambient", EFFECTS)


class MusicStyles(unittest.TestCase):
    def music(self, style):
        from evision_rgb.effects import Music
        m = Music(style=style)
        return m

    def lit(self, c):
        return np.asarray(c).max(axis=1) > 0.3

    def test_vu_meter_fills_left_to_right_with_overall_level(self):
        m = self.music("vu")
        m.audio.levels = np.zeros(16); dark = self.lit(m.render(0.0))
        m.audio.levels = np.full(16, 1.0); full = self.lit(m.render(0.5))
        m.disp = np.zeros(16); m.audio.levels = np.full(16, 0.5); half = self.lit(m.render(1.0))
        self.assertEqual(dark.sum(), 0)
        self.assertEqual(full.sum(), N)
        self.assertTrue(half[X < 0.3].all() and not half[X > 0.7].any())

    def test_beat_flashes_on_a_bass_hit_and_fades_on_steady_bass(self):
        m = self.music("beat")
        lv = np.full(16, 0.1)
        t = 0.0
        for _ in range(30):                       # quiet
            m.audio.levels = lv; m.render(t); t += 1 / 30
        quiet = np.asarray(m.render(t)).max()
        lv = lv.copy(); lv[:3] = 1.0              # bass hit
        m.audio.levels = lv; t += 1 / 30
        hit = np.asarray(m.render(t)).max()
        trail = []
        for _ in range(45):                       # bass stays up (no new onset)
            t += 1 / 30; m.audio.levels = lv; trail.append(np.asarray(m.render(t)).max())
        self.assertGreater(hit, 0.8)
        self.assertLess(quiet, 0.3)
        self.assertLess(trail[-1], hit * 0.5)
        self.assertTrue(all(b <= a + 1e-9 for a, b in zip([hit] + trail, trail)),
                        "a held bass note must not re-trigger the flash")

    def test_ripple_spreads_from_the_centre_on_a_bass_hit(self):
        m = self.music("ripple")
        m.audio.levels = np.full(16, 0.05)
        for i in range(20):
            m.render(i / 30)
        lv = np.full(16, 0.05); lv[:3] = 1.0
        m.audio.levels = lv
        early = np.asarray(m.render(20 / 30 + 0.03)).max(axis=1)
        late = np.asarray(m.render(20 / 30 + 0.35)).max(axis=1)
        centre = int(np.argmin((X - 0.5) ** 2 + ((Y - 0.5) * 0.35) ** 2))
        edge = INDEX["esc"]
        self.assertGreater(early[centre], early[edge])
        self.assertGreater(late[edge], early[edge])

    def test_unknown_style_is_rejected(self):
        from evision_rgb.effects import Music
        with self.assertRaises(ValueError):
            Music(style="disco")


if __name__ == "__main__":
    unittest.main()

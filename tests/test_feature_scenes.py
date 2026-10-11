"""Behaviour spec — classic LED effects for the keyboard's grid. Written BEFORE the implementation.

Fire (flames rising from the bottom row, after FastLED's Fire2012), Ocean (layered blue-green waves with whitecaps,
after FastLED's Pacifica), Twinkle (keys twinkling like a starry sky, after FastLED's TwinkleFox) and Digital rain
(falling green trails). FastLED's examples are MIT licensed; these are written for numpy and this keyboard's layout.
Every effect takes a seed, so its behaviour can be checked frame by frame."""
import unittest

import numpy as np

from evision_rgb.effects import NAMES, N, X, Y


def frames(e, secs=6.0, fps=30, start=0.0):
    return np.array([np.asarray(e.render(start + i / fps), dtype=float) for i in range(int(secs * fps))])


def make(name, **params):
    from evision_rgb import scenes  # noqa: F401  (registers the effects)
    from evision_rgb.effects import EFFECTS
    return EFFECTS[name](**params)


BOTTOM, TOP = Y > 0.7, Y < 0.3


class Fire(unittest.TestCase):
    def test_flames_are_hottest_at_the_bottom(self):
        f = frames(make("fire", seed=1))
        heat = f.max(axis=2).mean(axis=0)
        self.assertGreater(heat[BOTTOM].mean(), heat[TOP].mean() * 1.5)

    def test_flames_rise_into_the_middle_rows(self):
        f = frames(make("fire", seed=5))
        middle = (Y > 0.35) & (Y < 0.65)
        self.assertGreater(f.max(axis=2).mean(axis=0)[middle].mean(), 0.15, "not just a glowing bottom row")

    def test_fire_colours_have_no_blue_tint(self):
        f = frames(make("fire", seed=2))
        r, g, b = f[..., 0], f[..., 1], f[..., 2]
        self.assertTrue((g <= r + 1e-6).all(), "green never above red")
        self.assertTrue((b <= g + 1e-6).all(), "blue only appears in white-hot cores")

    def test_it_flickers(self):
        f = frames(make("fire", seed=3), secs=2)
        self.assertGreater(np.abs(np.diff(f, axis=0)).mean(), 0.01)

    def test_more_cooling_means_smaller_flames(self):
        hot = frames(make("fire", cooling=0.2, seed=4)).mean()
        cool = frames(make("fire", cooling=0.9, seed=4)).mean()
        self.assertLess(cool, hot * 0.75)


class Ocean(unittest.TestCase):
    def test_the_sea_is_blue_green_and_never_black(self):
        f = frames(make("ocean", seed=1))
        r, g, b = f[..., 0], f[..., 1], f[..., 2]
        self.assertLess(r.mean(), 0.5 * b.mean())
        self.assertGreater(f.max(axis=2).min(), 0.02, "always some water light")

    def test_waves_roll_and_crest(self):
        f = frames(make("ocean", seed=2))
        self.assertGreater(np.abs(np.diff(f, axis=0)).mean(), 0.002, "it moves")
        self.assertGreater(f.max(), 0.6, "whitecaps where waves meet")

    def test_it_moves_slowly_not_flickering(self):
        f = frames(make("ocean", seed=3), fps=60, secs=2)
        self.assertLess(np.abs(np.diff(f, axis=0)).max(), 0.15, "no jumps between frames")


class Twinkle(unittest.TestCase):
    def test_a_night_sky_most_keys_dim_some_bright(self):
        f = frames(make("twinkle", seed=1))
        bright = (f.max(axis=2) > 0.5).mean(axis=1)  # share of keys lit at each moment
        self.assertTrue((bright < 0.5).all(), "most keys are dim at any moment")
        self.assertGreater(bright.mean(), 0.03, "but some are always twinkling")

    def test_every_key_twinkles_sometime(self):
        f = frames(make("twinkle", seed=2), secs=12)
        self.assertGreater((f.max(axis=(0, 2)) > 0.5).mean(), 0.9)

    def test_twinkles_fade_smoothly(self):
        f = frames(make("twinkle", seed=3), fps=60, secs=3)
        self.assertLess(np.abs(np.diff(f, axis=0)).max(), 0.25)

    def test_colours_come_from_the_given_palette(self):
        f = frames(make("twinkle", colors=["ff0000", "0000ff"], seed=4))
        lit = f[f.max(axis=2) > 0.3]
        self.assertLess(lit[:, 1].max(), 0.05, "no green in a red/blue palette")

    def test_a_bad_palette_is_refused(self):
        with self.assertRaises(ValueError):
            make("twinkle", colors=[])


class DigitalRain(unittest.TestCase):
    def test_the_rain_is_green(self):
        f = frames(make("matrix", seed=1))
        lit = f[f.max(axis=2) > 0.2]
        self.assertGreater(len(lit), 100)
        self.assertTrue((lit[:, 1] >= lit[:, 0] - 1e-6).all() and (lit[:, 1] >= lit[:, 2] - 1e-6).all())

    def test_drops_fall_down(self):
        e = make("matrix", seed=2, density=0.3)
        f = frames(e, secs=8, fps=30)
        # where the brightest key of each column is, frame by frame: it moves to larger Y (down) between resets
        moved_down = 0
        cols = np.round(X * 16).astype(int)
        for c in set(cols):
            idx = np.where(cols == c)[0]
            if len(idx) < 4:
                continue
            ys = Y[idx][f[:, idx].max(axis=2).argmax(axis=1)]
            moved_down += int(((np.diff(ys) > 0).sum()) > ((np.diff(ys) < 0).sum()))
        self.assertGreater(moved_down, 5, "in most columns the heads travel downwards")

    def test_heads_are_brighter_than_tails(self):
        f = frames(make("matrix", seed=3))
        g = f[..., 1]
        self.assertGreater((f.max(axis=2) > 0.9).sum(), 10, "bright heads")
        self.assertGreater(((g > 0.05) & (g < 0.5)).sum(), (f.max(axis=2) > 0.9).sum(), "longer fading tails")


class EverywhereElse(unittest.TestCase):
    def test_they_work_as_layers_and_with_defaults(self):
        from evision_rgb.effects import EFFECTS
        for name in ("fire", "ocean", "twinkle", "matrix"):
            out = np.asarray(make(name).render(1.0))
            self.assertEqual(out.shape, (N, 3))
            stack = EFFECTS["layers"](layers=[{"effect": name}, {"effect": "solid", "params": {"color": "ffffff"}, "keys": ["Esc"]}])
            self.assertEqual(np.asarray(stack.render(1.0)).shape, (N, 3))

    def test_same_seed_same_show(self):
        a = frames(make("fire", seed=9), secs=1)
        b = frames(make("fire", seed=9), secs=1)
        self.assertTrue(np.allclose(a, b))

    def test_the_app_lists_them(self):
        import os
        with open(os.path.join(os.path.dirname(__file__), "..", "evision_rgb", "gui", "web", "app.js"), encoding="utf-8") as f:
            js = f.read()
        for name in ("fire", "ocean", "twinkle", "matrix"):
            self.assertIn(f'id: "{name}"', js)

    def test_the_server_and_cli_know_them(self):
        from evision_rgb import cli, server  # noqa: F401
        effects = cli.load_effects()
        for name in ("fire", "ocean", "twinkle", "matrix"):
            self.assertIn(name, effects)

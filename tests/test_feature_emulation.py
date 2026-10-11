"""Behaviour spec — hardware-effect emulation (fixes the 'Caps Lock turns the rainbow solid red' bug).
The firmware renders its animations internally, so the app emulates them for (a) the on-screen preview
and (b) the base under automation overlays. Written BEFORE the implementation."""
import json
import os
import tempfile
import threading
import time
import unittest
import urllib.request
import warnings

import numpy as np

from fake_device import install
from evision_rgb.effects import INDEX, N, X
from evision_rgb.protocol import MODES


def emu(mode, **kw):
    from evision_rgb.emulate import emulator
    cfg = {"mode": MODES[mode], "brightness": 4, "speed": 2, "direction": 0, "random": 0, "color": "ff0000", "variant": 0}
    cfg.update(kw)
    return emulator(cfg)


def frames(e, secs=2.0, fps=20):
    return np.array([np.asarray(e.render(i / fps)) for i in range(int(secs * fps))])


class Emulation(unittest.TestCase):
    def test_every_hardware_mode_has_an_emulation(self):
        for name in MODES:
            f = frames(emu(name), secs=0.5)
            self.assertEqual(f.shape[1:], (N, 3), name)
            self.assertTrue(((f >= 0) & (f <= 1)).all(), name)

    def test_rainbow_type_modes_animate_with_many_colours_regardless_of_colour_setting(self):
        for name in ("corrugated", "spectrum", "wave"):
            f = frames(emu(name, color="ff0000"))
            hues = np.unique(np.round(f[-1] * 4), axis=0)
            self.assertGreater(len(hues), 4, f"{name} should be multi-coloured")
            self.assertGreater(np.abs(f[0] - f[-1]).max(), 0.2, f"{name} should move")

    def test_normal_is_a_steady_single_colour(self):
        f = frames(emu("normal", color="00ff00"))
        self.assertTrue(np.allclose(f, f[0]))
        self.assertTrue(np.allclose(f[0][0], [0, 1, 0]))

    def test_breath_pulses_in_the_chosen_colour(self):
        f = frames(emu("breath", color="0000ff"), secs=6)
        level = f[:, 0, 2]
        self.assertGreater(level.max() - level.min(), 0.5)
        self.assertLess(f[:, :, :2].max(), 0.05, "only blue")

    def test_random_colour_flag_makes_single_colour_modes_multicoloured(self):
        f = frames(emu("breath", color="0000ff", random=1), secs=8)
        self.assertGreater(f[:, :, 0].max(), 0.3, "not just blue any more")

    def test_brightness_scales_output_and_zero_is_dim_not_off(self):
        hi = frames(emu("normal", color="ffffff", brightness=4), secs=0.2).max()
        lo = frames(emu("normal", color="ffffff", brightness=1), secs=0.2).max()
        self.assertGreater(hi, lo)
        self.assertGreater(lo, 0)

    def test_higher_speed_animates_faster(self):
        def motion(speed):  # device speed 0 = fastest
            f = frames(emu("corrugated", speed=speed), secs=1.0)
            return np.abs(np.diff(f, axis=0)).mean()
        self.assertGreater(motion(0), motion(4))

    def test_direction_flips_the_wave(self):
        def drift(direction):
            e = emu("corrugated", direction=direction)
            a, b = np.asarray(e.render(0.0)), np.asarray(e.render(0.05))
            left, right = INDEX["q"], INDEX["p"]
            return a, b
        a0, b0 = drift(0)
        a1, b1 = drift(1)
        self.assertFalse(np.allclose(b0 - a0, b1 - a1))

    def test_reactive_modes_light_pressed_keys(self):
        e = emu("reaction", color="ff8000")
        before = np.asarray(e.render(0.0))[INDEX["w"]].max()
        e.press("W")
        after = np.asarray(e.render(0.05))[INDEX["w"]].max()
        self.assertGreater(after, before + 0.3)

    def test_cartoon_is_a_cluster_around_h_j_k_that_grows_fades_and_returns(self):
        # user observation on the real AK820 (fw V0103)
        f = frames(emu("cartoon"), secs=8, fps=20)
        centre = f[:, [INDEX["h"], INDEX["j"], INDEX["k"]]].max(axis=2).mean(axis=1)
        edge = f[:, [INDEX["esc"], INDEX["right"], INDEX["lctrl"]]].max(axis=2).mean(axis=1)
        self.assertGreater(centre.max(), 0.6, "cluster lights the centre")
        self.assertLess(centre.min(), 0.15, "then goes away")
        self.assertGreater(((centre[1:] > 0.4) & (centre[:-1] <= 0.4)).sum(), 1, "and comes back repeatedly")
        self.assertGreater(centre.mean(), edge.mean() * 2, "it is a cluster, not the whole board")

    def test_traverse_is_dark_until_a_key_sends_light_along_its_row(self):
        # observed: pressing a key sends light both ways along that key's row
        e = emu("traverse", color="ff0000")
        idle = np.asarray(e.render(0.0))
        self.assertLess(idle.max(), 0.05, "dark while idle")
        e.press("H")
        time.sleep(0.15)
        out = np.asarray(e.render(0.15))
        self.assertGreater(out[INDEX["h"]].max() + out[INDEX["g"]].max() + out[INDEX["j"]].max(), 0.5)
        self.assertLess(out[INDEX["y"]].max() + out[INDEX["n"]].max(), 0.05, "rows above/below stay dark")
        time.sleep(0.35)
        out2 = np.asarray(e.render(0.5))
        self.assertGreater(out2[INDEX["a"]].max() + out2[INDEX["l"]].max(), 0.3, "travels both ways along the row")
        self.assertLess(out2[INDEX["y"]].max() + out2[INDEX["n"]].max(), 0.1, "other rows stay dark")
        self.assertGreater(out2[:, 0].max(), out2[:, 2].max(), "uses the chosen colour when random is off")

    def test_surmount_is_static_and_variant_4_uses_the_chosen_colour(self):
        f = frames(emu("surmount", color="00ffff", variant=3), secs=2)
        self.assertTrue(np.allclose(f, f[0]), "static")
        self.assertTrue(np.allclose(f[0][0], [0, 1, 1]))
        pink = frames(emu("surmount", color="00ffff", variant=0), secs=0.2)[0][0]
        self.assertGreater(pink[0], pink[1], "variant 1 is the light-pink preset")

    def test_rain_drops_fall_down_the_columns(self):
        # user: on the real keyboard drops fall; the old emulation only lit the top row
        from evision_rgb.effects import Y
        f = frames(emu("rain", color="00aaff"), secs=8, fps=30)
        lit = f.max(axis=2) > 0.3
        self.assertGreater(lit[:, Y > 0.7].any(axis=1).mean(), 0.3, "drops reach the bottom rows")
        self.assertLess(lit.mean(), 0.35, "sparse drops, not the whole board")
        below = {i: [j for j in range(N) if abs(X[j] - X[i]) < 0.035 and 0.1 < Y[j] - Y[i] < 0.3] for i in range(N)}
        down = up = 0
        for t in range(len(f) - 8):
            for i in np.where(lit[t])[0]:
                for j in below[i]:
                    if not lit[t, j] and lit[t + 1:t + 8, j].any():
                        down += 1
            for j in range(N):
                for i in [i for i in range(N) if j in below[i]]:
                    if lit[t, j] and not lit[t, i] and lit[t + 1:t + 8, i].any():
                        up += 1
        self.assertGreater(down, 3 * max(up, 1), f"motion is downward (down={down}, up={up})")

    def test_speed_sends_coloured_pulses_in_from_the_edge_with_gaps(self):
        # user: colours come in from one edge and ripple across (direction picks the edge), with gaps
        f = frames(emu("speed", direction=0), secs=4, fps=30)
        g = f[:, INDEX["g"]]
        self.assertLess(g.max(axis=1).min(), 0.1, "dark gaps between pulses")
        self.assertGreater(g.max(axis=1).max(), 0.6)
        lit_colours = {tuple(np.round(c * 3)) for c in g if c.max() > 0.6}
        self.assertGreater(len(lit_colours), 2, "each pulse has its own colour")
        def first_lit(frames_, key):
            return int(np.argmax(frames_[:, INDEX[key]].max(axis=1) > 0.5))
        self.assertLess(first_lit(f, "tab"), first_lit(f, "backslash"), "left-to-right enters on the left")
        f1 = frames(emu("speed", direction=1), secs=4, fps=30)
        self.assertLess(first_lit(f1, "backslash"), first_lit(f1, "tab"), "right-to-left enters on the right")

    def test_custom_mode_shows_the_stored_layout(self):
        from evision_rgb.emulate import emulator
        e = emulator({"mode": MODES["custom"], "brightness": 4, "speed": 0, "direction": 0, "random": 0,
                      "color": "000000"}, custom={"W": "00ff00"})
        c = np.asarray(e.render(0.0))
        self.assertTrue(np.allclose(c[INDEX["w"]], [0, 1, 0]))
        self.assertTrue(np.allclose(c[INDEX["q"]], [0, 0, 0]))


class VariantField(unittest.TestCase):
    def test_variant_is_written_to_byte_18_for_built_in_effects(self):
        from fake_device import install
        kb = install(self)
        from evision_rgb.keyboard import Keyboard
        with Keyboard() as k:
            k.set_effect("surmount", variant=3)
            self.assertEqual(kb.config[18], 3)
            with self.assertRaises(ValueError):
                k.set_effect("surmount", variant=4)
            k.set_custom({"W": "ff0000"})
            self.assertEqual(kb.config[18], 0, "custom mode still forces area 0")


class AppUsesEmulation(unittest.TestCase):
    def setUp(self):
        warnings.simplefilter("ignore", ResourceWarning)
        self.kb = install(self)
        import evision_rgb.settings as st
        tmp = tempfile.mkdtemp()
        for attr, val in (("DIR", tmp), ("PATH", os.path.join(tmp, "settings.json"))):
            self.addCleanup(setattr, st, attr, getattr(st, attr))
            setattr(st, attr, val)
        import datetime as _dt
        self.caps = False
        probes = {"caps": lambda: self.caps, "mic_muted": lambda: False, "idle": lambda: 0.0,
                  "locked": lambda: False, "now": lambda: _dt.datetime(2026, 1, 1, 12)}
        from evision_rgb.server import Controller, build_server
        self.ctl = Controller(probes=probes, automation_hz=50)
        self.httpd = build_server(self.ctl, 0)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(lambda: (self.httpd.shutdown(), self.ctl.close()))

    def call(self, path, body=None):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=data,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read())

    def test_preview_of_an_animated_hardware_effect_is_animated(self):
        self.call("/effect", {"mode": "spectrum", "color": "ff0000"})
        a = self.call("/frame")
        time.sleep(0.3)
        b = self.call("/frame")
        self.assertEqual(a["source"], "hardware")
        self.assertGreater(len(set(a["colors"].values())), 3, "multi-coloured, not flat red")
        self.assertNotEqual(a["colors"], b["colors"], "moves over time")

    def test_caps_lock_on_a_rainbow_effect_keeps_the_rainbow_under_the_indicator(self):
        self.call("/effect", {"mode": "corrugated", "color": "ff0000"})
        self.caps = True
        end = time.monotonic() + 2
        while time.monotonic() < end and self.kb.visible()[0] != "direct":
            time.sleep(0.02)
        time.sleep(0.2)
        frame = np.frombuffer(self.kb.visible()[1], dtype=np.uint8).reshape(128, 3)
        from evision_rgb.layout import KEYS
        slots = [s for n, (s, _, _) in KEYS.items() if n != "CapsLock"]
        colours = {tuple(frame[s] // 32) for s in slots}
        self.assertGreater(len(colours), 3, "rainbow continues underneath, not a solid colour")
        self.assertEqual(tuple(frame[KEYS["CapsLock"][0]]), (255, 255, 255))


if __name__ == "__main__":
    unittest.main()

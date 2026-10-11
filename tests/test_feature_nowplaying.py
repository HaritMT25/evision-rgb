"""Behaviour spec — now-playing colours. Written BEFORE the implementation.

The keyboard takes its colours from the cover art of whatever is playing (Spotify, a browser, any player in
Windows' media controls), with no account or API key: Windows' media session gives the title, artist, play
state and cover. A macro pad's play/pause key shows the cover and whether it is playing.
The player query is injectable, so every behaviour is specified without Windows or music."""
import colorsys
import io
import json
import os
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import warnings

import numpy as np

from fake_device import install

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
WEB = os.path.join(ROOT, "evision_rgb", "gui", "web")


def image(*bands, size=(120, 120), vertical=False):
    """A test cover: (fraction, 'rrggbb') bands left to right (or top to bottom)."""
    from PIL import Image, ImageDraw
    im = Image.new("RGB", size)
    d = ImageDraw.Draw(im)
    pos = 0.0
    for frac, hexc in bands:
        a, b = int(pos * size[0 if not vertical else 1]), int((pos + frac) * size[0 if not vertical else 1])
        box = [a, 0, b - 1, size[1] - 1] if not vertical else [0, a, size[0] - 1, b - 1]
        d.rectangle(box, fill="#" + hexc)
        pos += frac
    return im


def png(im) -> bytes:
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def hue(hexc):
    return colorsys.rgb_to_hsv(*(int(hexc[i:i + 2], 16) / 255 for i in (0, 2, 4)))


def close(a, b, tol=40):
    return sum((int(a[i:i + 2], 16) - int(b[i:i + 2], 16)) ** 2 for i in (0, 2, 4)) ** 0.5 <= tol


class Player:
    """What Windows' media session would report; the cover is read lazily (it is the slow part)."""

    def __init__(self):
        self.track = None  # None = nothing playing anywhere
        self.cover_reads = 0
        self.calls = 0
        self.fail = False

    def play(self, title="Song", artist="Band", cover=None, playing=True, app="Spotify.exe", album="Album"):
        self.track = {"app": app, "title": title, "artist": artist, "album": album, "playing": playing,
                      "cover_bytes": cover}

    def fetch(self):
        self.calls += 1
        if self.fail:
            raise OSError("media session went away")
        if self.track is None:
            return None
        t = dict(self.track)
        data = t.pop("cover_bytes")

        def cover():
            self.cover_reads += 1
            return data
        t["cover"] = cover
        return t


RED, BLUE, YELLOW = "ff0000", "0000ff", "ffd000"


# ============================================================================ palette
class Palette(unittest.TestCase):
    def pal(self, im, n=5):
        from evision_rgb.media import palette
        return palette(im, n)

    def test_the_main_colours_of_a_cover_come_first_by_area(self):
        p = self.pal(image((0.6, RED), (0.3, BLUE), (0.1, YELLOW)))
        self.assertTrue(close(p[0], RED), p)
        self.assertTrue(close(p[1], BLUE), p)
        self.assertTrue(any(close(c, YELLOW) for c in p), p)

    def test_a_small_vivid_accent_beats_a_large_dull_area(self):
        p = self.pal(image((0.85, "808080"), (0.15, "ff7000")))
        self.assertTrue(close(p[0], "ff7000"), p)

    def test_a_black_and_white_cover_gives_neutral_colours(self):
        p = self.pal(image((0.3, "101010"), (0.3, "777777"), (0.4, "eeeeee")))
        self.assertTrue(p)
        self.assertTrue(all(hue(c)[1] < 0.15 for c in p), p)

    def test_near_identical_shades_count_once(self):
        p = self.pal(image((0.5, "ff0000"), (0.2, "f40808"), (0.3, BLUE)))
        self.assertEqual(sum(close(c, RED) for c in p), 1, p)

    def test_black_borders_do_not_become_a_colour(self):
        p = self.pal(image((0.3, "000000"), (0.4, "00c040"), (0.3, "000000")))
        self.assertTrue(close(p[0], "00c040"), p)
        self.assertFalse(any(close(c, "000000") for c in p), p)

    def test_colours_are_hex_and_the_result_is_stable(self):
        im = image((0.5, RED), (0.5, BLUE))
        p = self.pal(im)
        self.assertEqual(p, self.pal(im))
        self.assertTrue(all(len(c) == 6 and int(c, 16) >= 0 for c in p))
        self.assertLessEqual(len(self.pal(im, 2)), 2)


# ============================================================================ the player query
class Source(unittest.TestCase):
    def setUp(self):
        from evision_rgb.media import NowPlaying
        self.player = Player()
        self.src = NowPlaying(fetch=self.player.fetch)

    def test_the_snapshot_follows_the_player(self):
        self.player.play("Midnight City", "M83", png(image((0.7, RED), (0.3, BLUE))))
        self.src.poll()
        s = self.src.current()
        self.assertEqual((s["title"], s["artist"], s["app"], s["playing"]), ("Midnight City", "M83", "Spotify.exe", True))
        self.assertTrue(close(s["palette"][0], RED), s["palette"])
        self.assertTrue(s["available"])

    def test_the_cover_is_read_once_per_track(self):
        self.player.play("A", cover=png(image((1.0, RED))))
        for _ in range(5):
            self.src.poll()
        self.assertEqual(self.player.cover_reads, 1)
        self.player.play("B", cover=png(image((1.0, BLUE))))
        self.src.poll()
        self.assertEqual(self.player.cover_reads, 2)
        self.assertTrue(close(self.src.current()["palette"][0], BLUE))

    def test_every_visible_change_gets_a_new_id(self):
        self.player.play("A", cover=png(image((1.0, RED))))
        self.src.poll()
        a = self.src.current()["id"]
        self.src.poll()
        self.assertEqual(self.src.current()["id"], a, "nothing changed")
        self.player.track["playing"] = False
        self.src.poll()
        self.assertNotEqual(self.src.current()["id"], a, "paused")

    def test_pausing_keeps_the_track_and_its_colours(self):
        self.player.play("A", cover=png(image((1.0, RED))))
        self.src.poll()
        self.player.track["playing"] = False
        self.src.poll()
        s = self.src.current()
        self.assertEqual((s["title"], s["playing"]), ("A", False))
        self.assertTrue(close(s["palette"][0], RED))

    def test_nothing_playing(self):
        self.src.poll()
        s = self.src.current()
        self.assertEqual((s["playing"], s["title"], s["palette"]), (False, None, []))

    def test_when_the_player_closes_its_track_disappears(self):
        self.player.play("A", cover=png(image((1.0, RED))))
        self.src.poll()
        self.player.track = None
        self.src.poll()
        s = self.src.current()
        self.assertEqual((s["title"], s["palette"], s["image"], s["playing"]), (None, [], None, False))

    def test_a_failing_player_query_keeps_the_last_state(self):
        self.player.play("A", cover=png(image((1.0, RED))))
        self.src.poll()
        self.player.fail = True
        self.src.poll()
        s = self.src.current()
        self.assertEqual(s["title"], "A")
        self.assertIn("went away", s["error"])

    def test_a_cover_that_is_not_an_image_still_shows_the_track(self):
        self.player.play("A", cover=b"not an image")
        self.src.poll()
        s = self.src.current()
        self.assertEqual((s["title"], s["palette"]), ("A", []))

    def test_it_only_keeps_asking_the_player_while_something_listens(self):
        from evision_rgb.media import NowPlaying
        src = NowPlaying(fetch=self.player.fetch, poll_s=0.05, linger_s=0.0)
        src.subscribe()
        time.sleep(0.3)
        self.assertGreaterEqual(self.player.calls, 3)
        src.unsubscribe()
        time.sleep(0.2)
        n = self.player.calls
        time.sleep(0.3)
        self.assertEqual(self.player.calls, n, "stopped asking")

    def test_a_one_off_question_refreshes_an_old_answer(self):
        self.player.play("A", cover=png(image((1.0, RED))))
        s = self.src.snapshot(max_age=1.0)  # nothing running in the background: asks now
        self.assertEqual(s["title"], "A")

    def test_without_windows_media_support_it_says_why(self):
        from evision_rgb.media import MediaUnavailable, NowPlaying

        def fetch():
            raise MediaUnavailable("needs Windows 10 or newer")
        src = NowPlaying(fetch=fetch)
        src.poll()
        s = src.current()
        self.assertEqual((s["available"], s["reason"]), (False, "needs Windows 10 or newer"))

    def test_the_real_windows_query_answers_without_raising(self):
        from evision_rgb.media import MediaUnavailable, windows_fetch
        try:
            info = windows_fetch()
        except MediaUnavailable:
            self.skipTest("Windows media controls not available here")
        self.assertTrue(info is None or {"title", "artist", "playing", "cover"} <= set(info), info)


# ============================================================================ the effect
class FixturesAreQuiet(unittest.TestCase):
    def test_the_test_keyboard_hides_the_real_media_session(self):
        from evision_rgb import media
        install(self)
        media.source().poll()
        self.assertIsNone(media.source().current()["title"])


class _WithPlayer(unittest.TestCase):
    def setUp(self):
        from evision_rgb import media
        self.player = Player()
        self.src = media.NowPlaying(fetch=self.player.fetch)
        prev = media.set_source(self.src)
        self.addCleanup(media.set_source, prev)


class AlbumColours(_WithPlayer):
    def make(self, **params):
        from evision_rgb.effects import EFFECTS
        e = EFFECTS["nowplaying"](**params)
        return e

    def settle(self, e, t=5.0):
        return np.asarray(e.render(t))

    def test_flow_uses_only_the_cover_colours(self):
        self.player.play(cover=png(image((0.5, RED), (0.5, BLUE))))
        self.src.poll()
        e = self.make(style="flow")
        for t in (5.0, 6.3, 9.1):
            out = np.asarray(e.render(t))
            self.assertLess(out[:, 1].max(), 0.08, "no green in a red/blue cover")
        self.assertGreater((out[:, 0] > out[:, 2] + 0.3).sum(), 5, "some keys red")
        self.assertGreater((out[:, 2] > out[:, 0] + 0.3).sum(), 5, "some keys blue")

    def test_flow_moves_over_time(self):
        self.player.play(cover=png(image((0.5, RED), (0.5, BLUE))))
        self.src.poll()
        e = self.make(style="flow", speed=0.1)
        self.assertFalse(np.allclose(e.render(5.0), e.render(7.0)))

    def test_cover_style_paints_the_cover_onto_the_keys(self):
        from evision_rgb.effects import X
        self.player.play(cover=png(image((0.5, RED), (0.5, BLUE))))
        self.src.poll()
        out = self.settle(self.make(style="cover"))
        left, right = out[X < 0.35], out[X > 0.65]
        self.assertTrue((left[:, 0] > 0.6).all() and (left[:, 2] < 0.2).all())
        self.assertTrue((right[:, 2] > 0.6).all() and (right[:, 0] < 0.2).all())

    def test_solid_style_uses_the_main_colour(self):
        self.player.play(cover=png(image((0.7, "00c040"), (0.3, BLUE))))
        self.src.poll()
        out = self.settle(self.make(style="solid"))
        self.assertTrue(np.allclose(out, out[0]))
        self.assertGreater(out[0, 1], out[0, 0] + 0.3)

    def test_paused_music_dims_the_colours(self):
        self.player.play(cover=png(image((1.0, RED))))
        self.src.poll()
        e = self.make(style="solid", paused=0.3)
        bright = self.settle(e, 5.0)[0, 0]
        self.player.track["playing"] = False
        self.src.poll()
        e.render(9.0)  # noticed; fades over `fade` seconds
        dim = np.asarray(e.render(11.0))[0, 0]
        self.assertAlmostEqual(dim, bright * 0.3, delta=0.02)

    def test_nothing_playing_shows_the_fallback_colour(self):
        self.src.poll()
        out = self.settle(self.make(fallback="102030"))
        self.assertTrue(np.allclose(out, [0x10 / 255, 0x20 / 255, 0x30 / 255], atol=0.01))

    def test_a_new_track_fades_in(self):
        self.player.play("A", cover=png(image((1.0, RED))))
        self.src.poll()
        e = self.make(style="solid", fade=1.0)
        self.assertGreater(self.settle(e, 5.0)[0, 0], 0.9)
        self.player.play("B", cover=png(image((1.0, BLUE))))
        self.src.poll()
        e.render(5.4)  # noticed
        mid = np.asarray(e.render(5.9))[0]
        self.assertTrue(0.1 < mid[0] < 0.9 and 0.1 < mid[2] < 0.9, f"half way {mid}")
        end = np.asarray(e.render(7.0))[0]
        self.assertTrue(end[2] > 0.9 and end[0] < 0.05, end)

    def test_unknown_style_is_refused(self):
        with self.assertRaises(ValueError):
            self.make(style="disco")

    def test_it_listens_only_while_it_runs(self):
        e = self.make()
        e.start()
        self.assertEqual(self.src.users, 1)
        e.stop()
        self.assertEqual(self.src.users, 0)

    def test_it_works_as_a_layer(self):
        from evision_rgb.effects import EFFECTS
        self.player.play(cover=png(image((1.0, RED))))
        self.src.poll()
        stack = EFFECTS["layers"](layers=[{"effect": "nowplaying", "params": {"style": "solid"}},
                                          {"effect": "solid", "params": {"color": "00ff00"}, "keys": ["Esc"]}])
        out = np.asarray(stack.render(5.0))
        from evision_rgb.effects import INDEX
        self.assertGreater(out[INDEX["q"], 0], 0.9)
        self.assertGreater(out[INDEX["esc"], 1], 0.9)


# ============================================================================ API, agents, app
class NowPlayingAPI(_WithPlayer):
    def setUp(self):
        warnings.simplefilter("ignore", ResourceWarning)
        self.kb = install(self)  # (also makes the real media session invisible to tests)
        super().setUp()
        import evision_rgb.settings as st
        tmp = tempfile.mkdtemp()
        for attr, val in (("DIR", tmp), ("PATH", os.path.join(tmp, "settings.json"))):
            self.addCleanup(setattr, st, attr, getattr(st, attr))
            setattr(st, attr, val)
        import datetime as _dt
        from evision_rgb.server import Controller, build_server
        self.ctl = Controller(probes={"now": lambda: _dt.datetime(2026, 1, 1, 12)}, hook_keys=False)
        self.httpd = build_server(self.ctl, 0)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(lambda: (self.httpd.shutdown(), self.ctl.close()))

    def get(self, path):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}", timeout=5) as r:
                return r.status, r.headers.get_content_type(), r.read()
        except urllib.error.HTTPError as e:
            with e:
                return e.code, e.headers.get_content_type(), e.read()

    def test_what_is_playing_is_reported(self):
        self.player.play("Midnight City", "M83", png(image((1.0, RED))))
        code, _, body = self.get("/nowplaying")
        d = json.loads(body)
        self.assertEqual(code, 200)
        self.assertEqual((d["title"], d["artist"], d["playing"]), ("Midnight City", "M83", True))
        self.assertTrue(close(d["palette"][0], RED))
        self.assertNotIn("image", d)

    def test_the_cover_is_served_as_a_small_png(self):
        self.player.play(cover=png(image((1.0, BLUE), size=(600, 600))))
        code, ctype, body = self.get("/nowplaying/cover.png")
        self.assertEqual((code, ctype), (200, "image/png"))
        from PIL import Image
        im = Image.open(io.BytesIO(body))
        self.assertLessEqual(max(im.size), 128)

    def test_no_cover_is_a_404(self):
        self.assertEqual(self.get("/nowplaying/cover.png")[0], 404)

    def test_the_effect_is_listed(self):
        code, _, body = self.get("/effects")
        self.assertIn("nowplaying", json.loads(body)["live"])


class NowPlayingInTheApp(unittest.TestCase):
    def read(self, *parts):
        with open(os.path.join(*parts), encoding="utf-8") as f:
            return f.read()

    def test_the_live_tab_has_the_album_colours_card_and_shows_the_track(self):
        js = self.read(WEB, "app.js")
        self.assertIn('id: "nowplaying"', js)
        self.assertIn("/nowplaying", js)
        self.assertIn("npInfo", js)

    def test_agents_can_ask_what_is_playing(self):
        self.assertIn("def now_playing(", self.read(ROOT, "evision_rgb", "mcp_server.py"))


# ============================================================================ macro pad
class PadCover(_WithPlayer):
    """akp03: put play/pause on screen key 0 (its media keys are plain buttons by default)."""

    def setUp(self):
        from fake_pad import FakePad, install_pads
        warnings.simplefilter("ignore", ResourceWarning)
        self.kb = install(self)
        super().setUp()
        import evision_rgb.settings as st
        tmp = tempfile.mkdtemp()
        for attr, val in (("DIR", tmp), ("PATH", os.path.join(tmp, "settings.json"))):
            self.addCleanup(setattr, st, attr, getattr(st, attr))
            setattr(st, attr, val)
        import datetime as _dt
        self.pad = FakePad("akp03e-rev2")
        install_pads(self, self.pad)
        from evision_rgb.server import Controller
        self.ctl = Controller(probes={"now": lambda: _dt.datetime(2026, 1, 1, 12)}, hook_keys=False)
        self.addCleanup(self.ctl.close)
        self.m = self.ctl.pads
        self.m.dispatch = lambda fn: fn()
        self.m.send_keys = lambda keys: None
        self.now = 1000.0
        self.m.clock = lambda: self.now
        self.m.set_key("akp03", 0, {"label": "Play", "icon": "⏯️", "color": "202024",
                                    "action": {"type": "media", "key": "play_pause"}})

    def tick(self, dt=1.0):
        self.now += dt
        self.m.tick()

    def mean(self, im, box=None):
        px = np.asarray((im.crop(box) if box else im).convert("RGB"), dtype=float)
        return px.reshape(-1, 3).mean(axis=0)

    def test_the_play_key_shows_the_cover_while_something_plays(self):
        self.player.play(cover=png(image((1.0, RED))))
        self.tick()
        im = self.pad.image(0)
        r, g, b = self.mean(im)
        self.assertGreater(r, 150)
        self.assertLess(g, 90)

    def test_playing_and_paused_look_different(self):
        self.player.play(cover=png(image((1.0, RED))))
        self.tick()
        playing = self.pad.image(0).copy()
        self.player.track["playing"] = False
        self.src.poll()
        self.tick()
        paused = self.pad.image(0)
        diff = np.abs(np.asarray(playing, dtype=float) - np.asarray(paused, dtype=float)).mean()
        self.assertGreater(diff, 2.0, "the play/pause badge changes")

    def test_a_new_track_updates_the_key(self):
        self.player.play("A", cover=png(image((1.0, RED))))
        self.tick()
        self.player.play("B", cover=png(image((1.0, BLUE))))
        self.src.poll()
        self.tick()
        r, g, b = self.mean(self.pad.image(0))
        self.assertGreater(b, 150)
        self.assertLess(r, 90)

    def test_with_nothing_playing_the_key_keeps_its_icon(self):
        from evision_rgb import streamdock
        self.tick()
        plain = streamdock.render_key(self.pad.model.keys[0].size, "Play", "202024", "⏯️", False)
        diff = np.abs(np.asarray(self.pad.image(0), dtype=float) - np.asarray(plain, dtype=float)).mean()
        self.assertLess(diff, 8, "the normal key with its icon")

    def test_other_keys_are_not_redrawn_for_a_new_track(self):
        self.player.play("A", cover=png(image((1.0, RED))))
        self.tick()
        n = len(self.pad.uploads)
        self.player.play("B", cover=png(image((1.0, BLUE))))
        self.src.poll()
        self.tick()
        self.assertEqual(len(self.pad.uploads) - n, 1, "only the play key")

    def test_the_app_preview_of_the_key_shows_the_cover(self):
        from PIL import Image
        self.player.play(cover=png(image((1.0, BLUE))))
        self.src.poll()
        im = Image.open(io.BytesIO(self.m.preview_png("akp03", 0)))
        r, g, b = self.mean(im)
        self.assertGreater(b, 150)

"""Now playing: what this PC is playing, from Windows' media controls (Spotify, browsers, any player that shows
up there), with the main colours of its cover. No account or API key needed.

Used by the 'nowplaying' live effect (album colours), the macro pad's play/pause key and GET /nowplaying.
The player query (`fetch`) is injectable; `windows_fetch` asks Windows (needs the 'media' extra).
"""
from __future__ import annotations

import colorsys
import io
import threading
import time

import numpy as np

from .effects import EFFECTS, Effect, N, X, Y, rgbf

GRID = (6, 16)   # rows, columns the cover is averaged into for style=cover (like screen ambient)
THUMB = 96       # px: size of the cover kept for the app window and the pad
PUBLIC = ("id", "available", "reason", "playing", "app", "title", "artist", "album", "palette", "error")


class MediaUnavailable(Exception):
    """This PC cannot say what is playing (not Windows 10+, or the 'media' extra is not installed)."""


# ------------------------------------------------------------------------------ cover colours
def palette(img, n: int = 5) -> list[str]:
    """The cover's main colours as 'rrggbb', most important first. Vivid colours count for more than their
    area (a small orange logo beats a big grey background); black borders never count; a black-and-white
    cover gives neutral colours; near-identical shades count once."""
    from PIL import Image
    im = img.convert("RGB").resize((48, 48), Image.Resampling.BILINEAR)
    q = im.quantize(colors=12, method=Image.Quantize.MEDIANCUT)
    pal, total = q.getpalette(), 48 * 48
    cands = []
    for count, idx in q.getcolors():
        rgb = tuple(pal[idx * 3:idx * 3 + 3])
        _, s, v = colorsys.rgb_to_hsv(*(c / 255 for c in rgb))
        cands.append((count / total, rgb, s, v))
    vivid = [c for c in cands if c[2] >= 0.25 and c[3] >= 0.2]
    if sum(c[0] for c in vivid) >= 0.03:
        pool = vivid
    else:  # (nearly) black and white: its greys, but not the black
        pool = [c for c in cands if c[3] >= 0.12] or cands
    pool.sort(key=lambda c: (-c[0] * (0.3 + c[2]) * (0.4 + c[3]), c[1]))
    out: list[tuple] = []
    for _, rgb, _, _ in pool:
        if all(sum((a - b) ** 2 for a, b in zip(rgb, o)) > 40 ** 2 for o in out):
            out.append(rgb)
        if len(out) == n:
            break
    return ["%02x%02x%02x" % c for c in out]


def _art(data: bytes | None):
    """(thumbnail, palette, grid) of a cover image, or (None, [], None) when there is none."""
    if not data:
        return None, [], None
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(data)).convert("RGB")
        thumb = im.copy()
        thumb.thumbnail((THUMB, THUMB))
        grid = np.asarray(im.resize((GRID[1], GRID[0]), Image.Resampling.BOX), dtype=float) / 255.0
        return thumb, palette(im), grid
    except Exception:  # not an image
        return None, [], None


# ------------------------------------------------------------------------------ asking Windows
def windows_fetch() -> dict | None:
    """What Windows' media controls show: None when nothing is playing, else
    {app, title, artist, album, playing, cover: fn() -> bytes | None} (the cover is read only when asked)."""
    try:
        import asyncio
        from winrt.windows.media.control import (
            GlobalSystemMediaTransportControlsSessionManager as Manager,
            GlobalSystemMediaTransportControlsSessionPlaybackStatus as Status)
        from winrt.windows.storage.streams import Buffer, InputStreamOptions
    except Exception as e:  # not Windows, or the 'media' extra is not installed
        raise MediaUnavailable("needs Windows 10 or newer and the 'media' extra") from e

    async def ask():
        session = (await Manager.request_async()).get_current_session()
        if session is None:
            return None
        props = await session.try_get_media_properties_async()
        info = session.get_playback_info()
        return session, props, info

    got = asyncio.run(ask())
    if got is None:
        return None
    session, props, info = got
    thumb = props.thumbnail

    def cover():
        if thumb is None:
            return None

        async def read():
            stream = await thumb.open_read_async()
            size = int(stream.size)
            if not 0 < size <= 8 << 20:
                return None
            buf = Buffer(size)
            await stream.read_async(buf, size, InputStreamOptions.READ_AHEAD)
            return bytes(buf)
        return asyncio.run(read())

    return {"app": session.source_app_user_model_id, "title": props.title or None, "artist": props.artist or None,
            "album": props.album_title or None, "playing": info.playback_status == Status.PLAYING, "cover": cover}


# ------------------------------------------------------------------------------ the shared source
def _empty(**kw) -> dict:
    return {"id": 0, "available": True, "reason": None, "playing": False, "app": None, "title": None, "artist": None,
            "album": None, "palette": [], "image": None, "grid": None, "error": None, **kw}


class NowPlaying:
    """What is playing. A background thread asks the player every `poll_s` while something listens (the
    album-colours effect) and for `linger_s` after a one-off question (the API, a pad key). The cover is read
    and analysed once per track. Snapshots are replaced, never changed, and get a new id when anything
    visible changes."""

    def __init__(self, fetch=None, poll_s: float = 1.0, linger_s: float = 10.0):
        self.fetch = fetch or windows_fetch
        self.poll_s, self.linger_s = poll_s, linger_s
        self.users = 0
        self.lock = threading.Lock()
        self._snap = _empty()
        self._track = None
        self._art = (None, [], None)
        self._thread = None
        self._until = 0.0
        self._wake = threading.Event()
        self._fresh = threading.Event()
        self.polled_at = -1e9

    # -- listeners
    def subscribe(self):
        with self.lock:
            self.users += 1
            self._ensure()

    def unsubscribe(self):
        with self.lock:
            self.users = max(0, self.users - 1)

    def current(self) -> dict:
        return self._snap

    def snapshot(self, max_age: float = 2.0) -> dict:
        """The current state, asked again first if the last answer is older than max_age seconds."""
        if time.monotonic() - self.polled_at > max_age and threading.current_thread() is not self._thread:
            self._fresh.clear()
            with self.lock:
                self._until = max(self._until, time.monotonic() + self.linger_s)
                self._ensure()
            self._wake.set()
            self._fresh.wait(1.0)
        return self._snap

    def _ensure(self):
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="nowplaying", daemon=True)
            self._thread.start()

    def _run(self):
        while True:
            self.poll()
            self._fresh.set()
            with self.lock:
                if self.users == 0 and time.monotonic() >= self._until:
                    self._thread = None
                    return
            self._wake.wait(self.poll_s)
            self._wake.clear()

    # -- one question
    def poll(self):
        try:
            info = self.fetch()
        except MediaUnavailable as e:
            self._set(_empty(available=False, reason=str(e)))
            return
        except Exception as e:  # player went away mid-question: keep showing the last state
            self._set({**self._snap, "error": repr(e)})
            return
        finally:
            self.polled_at = time.monotonic()
        if info is None:
            self._track, self._art = None, (None, [], None)
            self._set(_empty())
            return
        track = tuple(info.get(k) for k in ("app", "title", "artist", "album"))
        if track != self._track:
            self._track = track
            cover = info.get("cover")
            try:
                self._art = _art(cover() if callable(cover) else cover)
            except Exception:
                self._art = (None, [], None)
        image, pal, grid = self._art
        self._set(_empty(playing=bool(info.get("playing")), app=info.get("app"), title=info.get("title"),
                         artist=info.get("artist"), album=info.get("album"), palette=pal, image=image, grid=grid))

    def _set(self, snap: dict):
        old = self._snap
        visible = [k for k in PUBLIC if k not in ("id", "error")]
        same = all(old.get(k) == snap.get(k) for k in visible) and old.get("image") is snap.get("image")
        snap["id"] = old["id"] if same else old["id"] + 1
        self._snap = snap


_SOURCE = {"src": None}
_SOURCE_LOCK = threading.Lock()


def source() -> NowPlaying:
    """The shared source (asks Windows)."""
    with _SOURCE_LOCK:
        if _SOURCE["src"] is None:
            _SOURCE["src"] = NowPlaying()
        return _SOURCE["src"]


def set_source(src):
    """Replace the shared source (tests); returns the previous one."""
    with _SOURCE_LOCK:
        prev, _SOURCE["src"] = _SOURCE["src"], src
        return prev


def public(snap: dict) -> dict:
    return {k: snap.get(k) for k in PUBLIC}


def cover_png(snap: dict) -> bytes | None:
    im = snap.get("image")
    if im is None:
        return None
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


# ------------------------------------------------------------------------------ the effect
def _smooth(a):
    return a * a * (3 - 2 * a)


class AlbumColours(Effect):
    """Album colours: the keys take the colours of the cover of whatever is playing (Spotify, a browser, any
    player in Windows' media controls). style=flow (the cover's colours drift across the keys), cover (the
    cover painted onto the keyboard) or solid (its main colour). paused = brightness while paused,
    fallback = colour when nothing plays, fade = seconds to blend into a new track."""
    name = "nowplaying"
    fps = 30
    STYLES = ("flow", "cover", "solid")

    def __init__(self, style: str = "flow", speed: float = 0.05, scale: float = 1.0, brightness: float = 1.0,
                 paused: float = 0.35, fallback="141428", fade: float = 1.2):
        if style not in self.STYLES:
            raise ValueError(f"unknown style {style!r}; choose from {self.STYLES}")
        for k, v, lo, hi in (("brightness", brightness, 0, 1), ("paused", paused, 0, 1), ("fade", fade, 0, 10),
                             ("speed", speed, -2, 2), ("scale", scale, 0.1, 5)):
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not lo <= v <= hi:
                raise ValueError(f"{k} must be a number from {lo} to {hi}")
        self.style, self.speed, self.scale = style, float(speed), float(scale)
        self.brightness, self.paused, self.fade = float(brightness), float(paused), float(fade)
        self.fallback = rgbf(fallback)
        self.src = source()
        gh, gw = GRID
        self.cell = np.minimum((Y * gh).astype(int), gh - 1) * gw + np.minimum((X * gw).astype(int), gw - 1)
        self.sid, self.prev, self.changed, self.out = None, None, 0.0, None

    def start(self):
        self.src.subscribe()

    def stop(self):
        self.src.unsubscribe()

    def _target(self, s: dict, t: float) -> np.ndarray:
        if s["title"] is None and not s["palette"]:
            return np.tile(self.fallback, (N, 1))
        pal = np.array([rgbf(c) for c in s["palette"]] or [self.fallback])
        if self.style == "cover" and s.get("grid") is not None:
            out = s["grid"].reshape(-1, 3)[self.cell]
        elif self.style == "solid" or len(pal) == 1:
            out = np.tile(pal[0], (N, 1))
        else:  # flow: the palette as a loop of soft bands drifting across the keys
            k = len(pal)
            u = ((X * self.scale - t * self.speed) % 1.0) * k
            i = np.floor(u).astype(int) % k
            f = _smooth(u - np.floor(u))[:, None]
            out = pal[i] * (1 - f) + pal[(i + 1) % k] * f
        return out if s["playing"] else out * self.paused

    def render(self, t):
        s = self.src.current()
        if s["id"] != self.sid:
            if self.out is not None and self.fade > 0:
                self.prev, self.changed = self.out, t
            self.sid = s["id"]
        out = self._target(s, t)
        if self.prev is not None:
            a = (t - self.changed) / self.fade
            if a >= 1:
                self.prev = None
            else:
                out = self.prev + (out - self.prev) * _smooth(max(0.0, a))
        self.out = out
        return out * self.brightness


EFFECTS["nowplaying"] = AlbumColours

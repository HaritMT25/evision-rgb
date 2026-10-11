"""Live effects rendered on the host and streamed with direct mode (cmd 0x12).

Every effect is a class with `render(t) -> np.ndarray[N_KEYS, 3]` (floats 0..1, one row per key
in `NAMES` order). `Engine` owns the keyboard stream and runs one effect at a time in a thread.
"""
from __future__ import annotations

import colorsys
import math
import threading
import time

import numpy as np

from .keyboard import Keyboard, parse_color
from .layout import KEYS
from .protocol import MAP_SIZE

NAMES = list(KEYS)
SLOTS = np.array([KEYS[n][0] for n in NAMES])
X = np.array([KEYS[n][1] for n in NAMES])
Y = np.array([KEYS[n][2] for n in NAMES])
N = len(NAMES)
INDEX = {n.lower(): i for i, n in enumerate(NAMES)}


def rgbf(c) -> np.ndarray:
    return np.array(parse_color(c), dtype=float) / 255.0


def hsv(h: np.ndarray, s=1.0, v=1.0) -> np.ndarray:
    """Vectorised HSV->RGB, h in 0..1 (array)."""
    h = np.asarray(h, dtype=float) % 1.0
    i = np.floor(h * 6).astype(int) % 6
    f = h * 6 - np.floor(h * 6)
    p, q, t_ = v * (1 - s), v * (1 - f * s), v * (1 - (1 - f) * s)
    v = np.full_like(h, v)
    p, q, t_ = np.broadcast_to(p, h.shape), np.broadcast_to(q, h.shape), np.broadcast_to(t_, h.shape)
    table = [(v, t_, p), (q, v, p), (p, v, t_), (p, q, v), (t_, p, v), (v, p, q)]
    out = np.zeros(h.shape + (3,))
    for k, (r, g, b) in enumerate(table):
        m = i == k
        out[m] = np.stack([r[m], g[m], b[m]], axis=-1)
    return out


def to_frame(colors: np.ndarray, gamma: float = 2.2) -> bytes:
    c = np.clip(colors, 0.0, 1.0) ** gamma
    frame = np.zeros((MAP_SIZE, 3), dtype=np.uint8)
    frame[SLOTS] = (c * 255 + 0.5).astype(np.uint8)
    return frame.tobytes()


# ---------------------------------------------------------------------------- effects
class Effect:
    name = "base"
    fps = 60

    def start(self):
        pass

    def stop(self):
        pass

    def render(self, t: float) -> np.ndarray:
        raise NotImplementedError


class Solid(Effect):
    """One colour everywhere; optional per-key colours: keys={"W": "ff0000"}."""
    name = "solid"
    fps = 5

    def __init__(self, color="ffffff", keys: dict | None = None):
        self.base = np.tile(rgbf(color), (N, 1))
        for k, c in (keys or {}).items():
            self.base[INDEX[k.lower()]] = rgbf(c)

    def render(self, t):
        return self.base


class Mirror(Effect):
    """Static copy of what the keyboard has stored (used under automation overlays)."""
    name = "mirror"
    fps = 30

    def __init__(self, colors: dict | None = None, color="000000"):
        self.base = np.tile(rgbf(color), (N, 1))
        for k, c in (colors or {}).items():
            if k.lower() in INDEX:
                self.base[INDEX[k.lower()]] = rgbf(c)

    def render(self, t):
        return self.base


class Rainbow(Effect):
    """Horizontal rainbow wave. speed = cycles/s, scale = rainbows across the board, angle in degrees."""
    name = "rainbow"

    def __init__(self, speed: float = 0.25, scale: float = 1.0, angle: float = 0.0, brightness: float = 1.0):
        self.speed, self.scale, self.brightness = speed, scale, brightness
        a = math.radians(angle)
        self.proj = X * math.cos(a) + Y * 0.35 * math.sin(a)

    def render(self, t):
        return hsv(self.proj * self.scale - t * self.speed, 1.0, self.brightness)


class Breathing(Effect):
    """Whole board fades in and out. color, period (seconds)."""
    name = "breathing"

    def __init__(self, color="00a0ff", period: float = 4.0):
        self.c, self.period = rgbf(color), period

    def render(self, t):
        v = 0.5 - 0.5 * math.cos(2 * math.pi * t / self.period)
        return np.tile(self.c * (0.03 + 0.97 * v), (N, 1))


class Gradient(Effect):
    """Static two-colour gradient. left, right colours; vertical=true for top->bottom."""
    name = "gradient"
    fps = 5

    def __init__(self, left="ff0080", right="00c0ff", vertical: bool = False):
        self.a, self.b = rgbf(left), rgbf(right)
        self.pos = Y if vertical else X

    def render(self, t):
        p = self.pos[:, None]
        return self.a * (1 - p) + self.b * p


# pynput Windows virtual-key codes -> layout names
_VK = {**{0x41 + i: c for i, c in enumerate("ABCDEFGHIJKLMNOPQRSTUVWXYZ")},
       **{0x30 + i: str(i) for i in range(10)}, **{0x70 + i: f"F{i + 1}" for i in range(12)},
       0x1B: "Esc", 0xC0: "Grave", 0xBD: "Minus", 0xBB: "Equal", 0x08: "Backspace", 0x09: "Tab",
       0xDB: "LBracket", 0xDD: "RBracket", 0xDC: "Backslash", 0x14: "CapsLock", 0xBA: "Semicolon",
       0xDE: "Quote", 0x0D: "Enter", 0xA0: "LShift", 0xA1: "RShift", 0x10: "LShift", 0xBC: "Comma",
       0xBE: "Period", 0xBF: "Slash", 0xA2: "LCtrl", 0xA3: "RCtrl", 0x11: "LCtrl", 0x5B: "LWin",
       0xA4: "LAlt", 0xA5: "RAlt", 0x12: "LAlt", 0x20: "Space", 0x2E: "Delete", 0x24: "Home",
       0x21: "PageUp", 0x22: "PageDown", 0x23: "End", 0x26: "Up", 0x28: "Down", 0x25: "Left",
       0x27: "Right"}


class KeyEvents:
    """Global key-press listener (pynput). Calls callback(name) for keys on the AK820 layout."""

    def __init__(self, callback):
        self.callback = callback
        self.listener = None

    def start(self):
        from pynput import keyboard as kbd

        def on_press(key):
            vk = getattr(key, "vk", None)
            if vk is None and hasattr(key, "value"):
                vk = getattr(key.value, "vk", None)
            name = _VK.get(vk)
            if name in KEYS:
                self.callback(name)

        self.listener = kbd.Listener(on_press=on_press)
        self.listener.daemon = True
        self.listener.start()

    def stop(self):
        if self.listener:
            self.listener.stop()


class Reactive(Effect):
    """Typing-reactive: pressed key flashes and a coloured ripple spreads from it."""
    name = "reactive"

    def __init__(self, base="000008", color: str | None = None, fade: float = 0.6, ripple: bool = True,
                 ripple_speed: float = 1.2, ripple_width: float = 0.06):
        self.base = rgbf(base)
        self.color = rgbf(color) if color else None
        self.fade, self.ripple, self.rs, self.rw = fade, ripple, ripple_speed, ripple_width
        self.last = np.full(N, -1e9)
        self.ripples: list[tuple[float, float, float, float]] = []  # t0, x, y, hue
        self.lock = threading.Lock()
        self.events = KeyEvents(self.press)
        self.hue = 0.0

    def press(self, name: str):
        i = INDEX[name.lower()]
        now = time.monotonic()
        with self.lock:
            self.last[i] = now
            self.hue = (self.hue + 0.13) % 1.0
            if self.ripple:
                self.ripples.append((now, X[i], Y[i] * 0.35, self.hue))

    def start(self):
        self.events.start()

    def stop(self):
        self.events.stop()

    def render(self, t):
        now = time.monotonic()
        out = np.tile(self.base, (N, 1))
        with self.lock:
            age = now - self.last
            self.ripples = [r for r in self.ripples if now - r[0] < 1.5]
            ripples = list(self.ripples)
        flash = np.exp(-np.maximum(age, 0) / (self.fade / 3))[:, None]
        key_col = self.color if self.color is not None else np.array([1.0, 1.0, 1.0])
        out = out * (1 - flash) + key_col * flash
        yy = Y * 0.35
        for t0, x0, y0, h in ripples:
            r = (now - t0) * self.rs
            d = np.hypot(X - x0, yy - y0)
            ring = np.exp(-((d - r) / self.rw) ** 2) * max(0.0, 1 - (now - t0) / 1.5)
            c = self.color if self.color is not None else hsv(np.array([h]))[0]
            out = np.maximum(out, ring[:, None] * c)
        return out


class AudioCapture:
    """WASAPI loopback of the default output device -> normalised band levels 0..1 (thread).

    4096-point FFT (hop 1024) for usable bass resolution; per-band dB with adaptive floor/ceiling
    trackers so every band uses its own dynamic range without amplifying noise."""

    def __init__(self, bands: int = 16, samplerate: int = 48000, hop: int = 1024, nfft: int = 4096,
                 fmin: float = 50, fmax: float = 12000, min_range_db: float = 15, smooth: float = 0.45):
        self.bands, self.sr, self.hop, self.nfft = bands, samplerate, hop, nfft
        self.levels = np.zeros(bands)
        self.min_range = min_range_db
        self.smooth = smooth  # EMA weight on the previous block (de-flicker)
        self._stop = threading.Event()
        freqs = np.fft.rfftfreq(nfft, 1 / samplerate)
        edges = np.geomspace(fmin, fmax, bands + 1)
        self.bins = []
        for i in range(bands):
            b = np.where((freqs >= edges[i]) & (freqs < edges[i + 1]))[0]
            self.bins.append(b if len(b) else np.array([np.argmin(abs(freqs - edges[i]))]))
        self.floor = np.full(bands, -20.0)
        self.ceil = np.full(bands, 0.0)
        self.error = None

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()

    def stop(self):
        self._stop.set()

    def _update(self, db: np.ndarray):
        up = db > self.floor
        self.floor += np.where(up, 0.01, 0.05) * (db - self.floor)  # sits near the 17th percentile
        up = db > self.ceil
        self.ceil += np.where(up, 0.3, 0.003) * (db - self.ceil)
        top = np.maximum(self.ceil, self.floor + self.min_range)
        lv = np.clip((db - self.floor) / (top - self.floor), 0, 1)
        lv[db < -70] = 0.0  # silence
        self.levels = self.smooth * self.levels + (1 - self.smooth) * lv

    def _run(self):
        import warnings
        import soundcard as sc
        warnings.filterwarnings("ignore", category=sc.SoundcardRuntimeWarning)
        try:
            mic = sc.get_microphone(id=str(sc.default_speaker().name), include_loopback=True)
            win = np.hanning(self.nfft)
            ring = np.zeros(self.nfft)
            with mic.recorder(samplerate=self.sr, blocksize=self.hop) as rec:
                while not self._stop.is_set():
                    data = rec.record(numframes=self.hop)
                    mono = data.mean(axis=1) if data.ndim > 1 else data
                    ring = np.concatenate([ring, mono])[-self.nfft:]
                    power = np.abs(np.fft.rfft(ring * win)) ** 2
                    db = np.array([10 * np.log10(power[b].mean() + 1e-12) for b in self.bins])
                    self._update(db)
        except Exception as ex:  # surfaced via Engine.status
            self.error = repr(ex)


class Music(Effect):
    """Music visualiser. style=bars: spectrum bars across the columns (bass left) with peak caps;
    style=pulse: whole board pulses with the bass."""
    name = "music"

    STYLES = ("bars", "pulse", "vu", "beat", "ripple")

    def __init__(self, style: str = "bars", hue_speed: float = 0.03, base="000000",
                 fall: float = 1.2, peak_hold: float = 0.5, scale: float = 0.8):
        if style not in self.STYLES:
            raise ValueError(f"unknown music style {style!r}; choose from {self.STYLES}")
        self.audio = AudioCapture(bands=16)
        self.style, self.hue_speed, self.base = style, hue_speed, rgbf(base)
        self.fall, self.hold = fall, peak_hold
        self.band = np.minimum((X * 16).astype(int), 15)
        self.disp = np.zeros(16)
        self.peak = np.zeros(16)
        self.peak_t = np.zeros(16)
        self.last_t = 0.0
        # 0 bottom .. `scale` top, quantised to the 6 rows; scale<1 lets loud-but-not-max music reach the F-row
        self.row_h = np.round((1 - Y) * 5) / 5 * scale
        self.top_h = scale
        # beat / ripple state
        self.bass_prev = 0.0
        self.bass_avg = 0.0
        self.last_onset = -1e9
        self.flash = 0.0
        self.beat_hue = 0.0
        self.rings: list[tuple[float, float]] = []  # (t0, hue)

    def _onset(self, t: float, dt: float) -> bool:
        """Bass hit = rising bass clearly above its recent average (with a short refractory period)."""
        bass = float(np.mean(self.audio.levels[:3]))
        rising = bass > self.bass_prev + 0.02
        hit = rising and bass > 0.35 and bass > self.bass_avg + 0.25 and t - self.last_onset > 0.12
        self.bass_avg += (bass - self.bass_avg) * (1 - math.exp(-dt / 0.3)) if dt > 0 else 0.0
        self.bass_prev = bass
        if hit:
            self.last_onset = t
            self.beat_hue = (self.beat_hue + 0.17) % 1.0
        return hit

    def start(self):
        self.audio.start()

    def stop(self):
        self.audio.stop()

    def _smooth(self, t):
        dt = max(0.0, t - self.last_t)
        self.last_t = t
        lv = self.audio.levels
        self.disp = np.maximum(lv, self.disp - self.fall * dt)
        newpk = self.disp >= self.peak
        self.peak = np.where(newpk, self.disp, self.peak)
        self.peak_t = np.where(newpk, t, self.peak_t)
        falling = t - self.peak_t > self.hold
        self.peak = np.where(falling, np.maximum(self.disp, self.peak - self.fall * 0.6 * dt), self.peak)

    def render(self, t):
        prev_t = self.last_t
        self._smooth(t)
        dt = max(0.0, t - prev_t)
        if self.style in ("beat", "ripple"):
            hit = self._onset(t, dt)
            if self.style == "beat":
                self.flash = 1.0 if hit else self.flash * math.exp(-dt / 0.25)
                return hsv(self.beat_hue + X * 0.25, 1.0, 0.05 + 0.95 * self.flash)
            if hit:
                self.rings.append((t, self.beat_hue))
            self.rings = [r for r in self.rings if t - r[0] < 1.2]
            yy = Y * 0.35
            d = np.hypot(X - 0.5, yy - 0.175)
            out = hsv(X * 0.3 + t * self.hue_speed, 1.0, 0.04 + 0.12 * float(np.mean(self.disp)))
            for t0, h in self.rings:
                ring = np.exp(-((d - (t - t0) * 1.6) / 0.07) ** 2) * (1 - (t - t0) / 1.2)
                out = np.maximum(out, ring[:, None] * hsv(np.array([h]))[0])
            return out
        if self.style == "vu":
            level = float(np.mean(self.disp)) * 1.05
            on = np.clip((level - X) * 10, 0, 1)
            return np.maximum(self.base, hsv(0.33 - 0.33 * X + t * self.hue_speed) * on[:, None])
        if self.style == "pulse":
            bass = float(self.disp[:3].max())
            return hsv(X * 0.6 + t * self.hue_speed, 1.0, 0.04 + 0.96 * bass ** 1.5)
        h = self.row_h
        lv = self.disp[self.band] * 1.0001
        on = np.clip((lv - h) * 6 + 1, 0, 1)  # rows below the level fully on, soft edge
        on[h == 0] = np.clip(lv[h == 0] * 8, 0, 1)  # bottom row fades in with any signal
        col = hsv(0.33 - 0.33 * h + t * self.hue_speed)  # green bottom -> red top
        out = np.maximum(self.base, col * on[:, None])
        pk = self.peak[self.band]
        step = self.top_h / 5
        cap = (np.abs(pk - h) < step / 2) & (pk > step) & (on < 0.5)
        out[cap] = np.maximum(out[cap], col[cap] * 0.7 + 0.15)  # brightened bar colour, not white
        return out


def grab_screen(size=(64, 36)) -> np.ndarray:
    """Primary screen, box-downscaled to `size`, as an (h, w, 3) uint8 array."""
    from PIL import Image, ImageGrab
    im = ImageGrab.grab().convert("RGB").resize(size, Image.BOX)
    return np.asarray(im)


class Ambient(Effect):
    """Screen ambient: keys take the colour of the screen region under them (mode=map) or the average
    screen colour (mode=average). saturation boosts vividness, smooth = transition time in seconds."""
    name = "ambient"
    fps = 30
    GRID = (6, 16)  # rows, cols the screen is averaged into

    def __init__(self, mode: str = "map", saturation: float = 1.4, smooth: float = 0.25,
                 capture_fps: float = 20, brightness: float = 1.0, source=None):
        if mode not in ("map", "average"):
            raise ValueError("mode must be 'map' or 'average'")
        self.mode, self.sat, self.smooth = mode, float(saturation), float(smooth)
        self.period, self.brightness = 1.0 / max(1.0, float(capture_fps)), float(brightness)
        self.threaded = source is None  # real screen grabs (~40 ms) run off the render thread
        self.source = source or grab_screen
        self._img = None
        self._stop = threading.Event()
        self.cur = None
        self.target = np.zeros((N, 3))
        self.last_cap = -1e9
        self.last_t = None
        gh, gw = self.GRID
        self.cell = np.minimum((Y * gh).astype(int), gh - 1) * gw + np.minimum((X * gw).astype(int), gw - 1)

    def start(self):
        if self.threaded:
            threading.Thread(target=self._grab_loop, daemon=True).start()

    def stop(self):
        self._stop.set()

    def _grab_loop(self):
        while not self._stop.is_set():
            t = time.monotonic()
            try:
                self._img = self.source()
            except Exception:  # e.g. secure desktop / lock screen: keep the last colours
                pass
            self._stop.wait(max(0.0, self.period - (time.monotonic() - t)))

    def _capture(self):
        raw = self._img if self.threaded else self.source()
        if raw is None:
            return
        img = np.asarray(raw, dtype=float)[..., :3] / 255.0
        if self.mode == "average":
            t = np.tile(img.reshape(-1, 3).mean(axis=0), (N, 1))
        else:
            gh, gw = self.GRID
            h, w = img.shape[0] // gh * gh, img.shape[1] // gw * gw
            if h == 0 or w == 0:  # tiny source image: sample instead of averaging
                img = np.repeat(np.repeat(img, gh, axis=0), gw, axis=1)
                h, w = img.shape[0] // gh * gh, img.shape[1] // gw * gw
            cells = img[:h, :w].reshape(gh, h // gh, gw, w // gw, 3).mean(axis=(1, 3)).reshape(-1, 3)
            t = cells[self.cell]
        m = t.mean(axis=1, keepdims=True)
        self.target = np.clip(m + (t - m) * self.sat, 0, 1)

    def render(self, t):
        if t - self.last_cap >= self.period - 1e-9:
            self.last_cap = t
            self._capture()
        if self.cur is None:
            self.cur = self.target.copy()
        else:
            dt = max(0.0, t - self.last_t)
            a = 1 - math.exp(-dt / self.smooth) if self.smooth > 0 else 1.0
            self.cur += (self.target - self.cur) * a
        self.last_t = t
        return self.cur * self.brightness


class SystemMonitor(Effect):
    """F-row = CPU load bar, number row = RAM bar, Esc pulses red above 90 % CPU, rest dim."""
    name = "sysmon"
    fps = 20

    def __init__(self, base="000010"):
        import psutil
        self.ps = psutil
        self.base = rgbf(base)
        self.cpu = self.ram = 0.0
        self.next = 0.0
        self.ps.cpu_percent(None)
        self.frow = np.array([i for i, n in enumerate(NAMES) if Y[i] < 0.1 and n != "Esc"])
        self.nrow = np.array([i for i, n in enumerate(NAMES) if 0.1 < Y[i] < 0.35])

    def _bar(self, out, idx, level):
        xs = X[idx]
        lo, hi = xs.min(), xs.max()
        pos = (xs - lo) / (hi - lo + 1e-9)
        lit = pos <= level + 1e-6
        col = hsv(0.33 * (1 - pos))  # green → red along the bar
        out[idx] = np.where(lit[:, None], col, out[idx] * 0.5 + col * 0.04)

    def render(self, t):
        if t >= self.next:
            self.cpu = self.ps.cpu_percent(None) / 100
            self.ram = self.ps.virtual_memory().percent / 100
            self.next = t + 0.5
        out = np.tile(self.base, (N, 1))
        self._bar(out, self.frow, self.cpu)
        self._bar(out, self.nrow, self.ram)
        if self.cpu > 0.9:
            out[INDEX["esc"]] = [0.5 + 0.5 * math.sin(t * 8), 0, 0]
        return out


EFFECTS = {cls.name: cls for cls in (Solid, Mirror, Rainbow, Breathing, Gradient, Reactive, Music, Ambient, SystemMonitor)}


# ---------------------------------------------------------------------------- engine
class Engine:
    """Owns the keyboard. One live effect at a time, plus manual per-key overrides."""

    def __init__(self, kb: Keyboard | None = None):
        self.kb = kb or Keyboard()
        self.stream = None
        self.effect: Effect | None = None
        self.overrides: dict[int, np.ndarray] = {}
        self._thread = None
        self._stop = threading.Event()
        self.lock = threading.RLock()
        self.fps_actual = 0.0
        self.last: np.ndarray | None = None  # last rendered colours (N,3) for UI preview
        self.params: dict = {}
        self.post = None  # optional fn(colors, monotonic_t) -> colors (automation overlays)

    def _ensure_stream(self):
        if self.stream is None:
            self.stream = self.kb.stream()

    def run(self, name: str, **params) -> dict:
        eff = EFFECTS[name](**params)
        with self.lock:
            self.stop_effect()
            self._ensure_stream()
            self.effect = eff
            self.params = dict(params)
            self._stop = threading.Event()
            eff.start()
            self._thread = threading.Thread(target=self._loop, args=(eff, self._stop), daemon=True)
            self._thread.start()
        return self.status()

    def _loop(self, eff: Effect, stop: threading.Event):
        t0 = time.monotonic()
        frames, tl = 0, t0
        period = 1.0 / eff.fps
        while not stop.is_set():
            t = time.monotonic() - t0
            colors = np.array(eff.render(t), dtype=float)
            with self.lock:
                for i, c in self.overrides.items():
                    colors[i] = c
            if self.post:
                try:
                    colors = self.post(colors, time.monotonic())
                except Exception:  # a broken overlay must never kill the render loop
                    pass
            self.last = colors
            self.stream.set_frame(to_frame(colors, 1.0 if getattr(eff, "raw", False) else 2.2))
            self.stream.show()
            frames += 1
            now = time.monotonic()
            if now - tl >= 1:
                self.fps_actual, frames, tl = frames / (now - tl), 0, now
            time.sleep(max(0.0, period - (time.monotonic() - now)))

    def set_keys(self, colors: dict) -> dict:
        """Per-key override on top of the running effect (or on black if none)."""
        with self.lock:
            for k, c in colors.items():
                i = INDEX[k.lower()]
                if c is None:
                    self.overrides.pop(i, None)
                else:
                    self.overrides[i] = rgbf(c)
            if self.effect is None:
                self.run("solid", color="000000")
        return self.status()

    def clear_keys(self):
        with self.lock:
            self.overrides.clear()

    def stop_effect(self):
        with self.lock:
            if self.effect:
                self._stop.set()
                if self._thread:
                    self._thread.join(2)
                self.effect.stop()
                self.effect = None
                self.last = None

    def pause(self):
        """Freeze live output before a persistent write: the keyboard keeps showing the last live
        frame while new settings are stored, so finishing with stop() doesn't flash the old effect."""
        with self.lock:
            self.stop_effect()
            if self.stream:
                self.stream.paused = True

    def stop(self) -> dict:
        """Stop live control; keyboard returns to its stored hardware effect."""
        with self.lock:
            self.stop_effect()
            self.overrides.clear()
            if self.stream:
                self.stream.close()
                self.stream = None
        return self.status()

    def status(self) -> dict:
        err = getattr(getattr(self.effect, "audio", None), "error", None)
        return {"live": self.stream is not None, "effect": self.effect.name if self.effect else None,
                "params": self.params if self.effect else None,
                "fps": round(self.fps_actual, 1), "overrides": len(self.overrides),
                "frames_sent": self.stream.frames_sent if self.stream else 0,
                "stream_error": repr(self.stream.error) if self.stream and self.stream.error else None,
                "audio_error": err}

    def close(self):
        self.stop()
        self.kb.close()

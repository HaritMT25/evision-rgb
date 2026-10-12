"""Macro pads in the app: find a pad, keep it awake, draw its keys and run their actions.

Each key and each knob movement (turn left, turn right, press) has an action:
  profile {name}            apply a saved lighting profile    profile_next / profile_prev   cycle saved profiles
  effect {effect, params}   start a live effect               layers     the layer editor's current stack
  stop                      back to the keyboard's own effect hardware {mode, ...}  store a built-in effect
  brightness {step: ±1}     keyboard brightness one step      notify {color}  flash the keyboard
  timer {minutes}           start / cancel the number-row timer
  media {key}               play_pause, next, previous, volume_up, volume_down, mute
  hotkey {keys}             a key combination such as "ctrl+shift+m"
  launch {target}           open a program, file or web address
  none
Settings: settings["pads"][family] = {"brightness": 0-100, "keys": [...], "knobs": [...]}, where a key is
{"label", "icon", "color", "action"} and a knob is {"left": action, "right": action, "press": action}.
"""
from __future__ import annotations

import copy
import io
import os
import queue
import re
import threading
import time

from . import media, streamdock
from .streamdock import FAMILIES, MODELS

MEDIA = {"play_pause": "media_play_pause", "next": "media_next", "previous": "media_previous",
         "volume_up": "media_volume_up", "volume_down": "media_volume_down", "mute": "media_volume_mute"}
_KEY_NAMES = ({"ctrl", "shift", "alt", "cmd", "enter", "esc", "tab", "space", "backspace", "delete", "home", "end",
               "page_up", "page_down", "up", "down", "left", "right", "insert", "print_screen", "caps_lock", "menu"}
              | {f"f{i}" for i in range(1, 21)})
_ALIASES = {"control": "ctrl", "win": "cmd", "windows": "cmd", "super": "cmd", "escape": "esc", "return": "enter",
            "del": "delete", "pgup": "page_up", "pgdn": "page_down", "prtsc": "print_screen"}
ACTION_FIELDS = {"none": set(), "stop": set(), "layers": set(), "profile_next": set(), "profile_prev": set(),
                 "profile": {"name"}, "effect": {"effect", "params"}, "brightness": {"step"},
                 "hardware": {"mode", "brightness", "speed", "color", "direction", "random", "variant"},
                 "notify": {"color"}, "timer": {"minutes"}, "media": {"key"}, "hotkey": {"keys"}, "launch": {"target"}}
KEY_FIELDS = {"label", "icon", "color", "action"}
KNOB_FIELDS = {"left", "right", "press"}
_HEX = re.compile(r"^[0-9a-fA-F]{6}$")


def _key(label, icon, color, action):
    return {"label": label, "icon": icon, "color": color, "action": action}


_MEDIA_KNOB = {"left": {"type": "media", "key": "volume_down"}, "right": {"type": "media", "key": "volume_up"},
               "press": {"type": "media", "key": "mute"}}
_BRIGHT_KNOB = {"left": {"type": "brightness", "step": -1}, "right": {"type": "brightness", "step": 1},
                "press": {"type": "stop"}}
_PROFILE_KNOB = {"left": {"type": "profile_prev"}, "right": {"type": "profile_next"}, "press": {"type": "layers"}}
_EFFECT_KEYS = [_key("Rainbow", "🌈", "3a1d6e", {"type": "effect", "effect": "rainbow"}),
                _key("Typing", "⌨️", "0b3d5c", {"type": "effect", "effect": "reactive"}),
                _key("Music", "🎵", "5c0b3d", {"type": "effect", "effect": "music"}),
                _key("Layers", "🧅", "3d3d0b", {"type": "layers"}),
                _key("Screen", "🖥️", "0b5c3d", {"type": "effect", "effect": "ambient"})]
_MEDIA_KEYS = [_key("Prev", "⏮️", "202024", {"type": "media", "key": "previous"}),
               _key("Play", "⏯️", "202024", {"type": "media", "key": "play_pause"}),
               _key("Next", "⏭️", "202024", {"type": "media", "key": "next"})]
_STOP_KEY = _key("Keyboard", "⌨", "26262b", {"type": "stop"})
DEFAULTS = {
    "akp03": {"brightness": 80, "keys": _EFFECT_KEYS + [_STOP_KEY] + _MEDIA_KEYS,
              "knobs": [_MEDIA_KNOB, _BRIGHT_KNOB, _PROFILE_KNOB]},
    "akp153": {"brightness": 80, "knobs": [], "keys": (
        _EFFECT_KEYS + [_STOP_KEY]
        + [_key(n.title(), "", c, {"type": "hardware", "mode": n}) for n, c in
           (("breath", "123456"), ("wave", "0b4f6e"), ("rain", "0b2f6e"), ("stars", "2a0b6e"), ("spectrum", "6e0b4f"))]
        + [_key("Bright +", "☀️", "3d3d3d", {"type": "brightness", "step": 1})]
        + _MEDIA_KEYS
        + [_key("Focus 25", "⏱️", "5c3d0b", {"type": "timer", "minutes": 25}),
           _key("Flash", "🔔", "5c0b0b", {"type": "notify", "color": "ff3000"}),
           _key("Bright −", "🔅", "2a2a2a", {"type": "brightness", "step": -1})])},
}
BLANK_KEY = _key("", "", "1a1a1e", {"type": "none"})


def parse_hotkey(text) -> list[str]:
    """'Ctrl+Shift+M' -> ['ctrl', 'shift', 'm']; raises ValueError for names that are not keys."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("hotkey must look like ctrl+shift+m")
    out = []
    for part in text.lower().replace(" ", "").split("+"):
        part = _ALIASES.get(part, part)
        if not (part in _KEY_NAMES or (len(part) == 1 and part.isprintable())):
            raise ValueError(f"unknown key {part!r} in hotkey {text!r}")
        out.append(part)
    return out


def validate_action(a, where: str) -> dict:
    if not isinstance(a, dict):
        raise ValueError(f"{where}: action must be an object like {{\"type\": \"stop\"}}")
    t = a.get("type", "none")
    if t not in ACTION_FIELDS:
        raise ValueError(f"{where}: unknown action {t!r}; choose from {sorted(ACTION_FIELDS)}")
    extra = sorted(set(a) - ACTION_FIELDS[t] - {"type"})
    if extra:
        raise ValueError(f"{where}: {t} actions have no field(s) {extra}")
    if t == "profile" and not (isinstance(a.get("name"), str) and a["name"]):
        raise ValueError(f"{where}: profile actions need the profile's name")
    if t == "effect":
        from .profiles import validate_live
        validate_live({"effect": a.get("effect"), "params": a.get("params") or {}})
    if t == "brightness" and a.get("step") not in (-1, 1):
        raise ValueError(f"{where}: brightness step is -1 or 1")
    if t == "hardware":
        from .keyboard import CONFIG_LEN, Keyboard
        if "mode" not in a:
            raise ValueError(f"{where}: hardware actions need a mode")
        Keyboard.apply_fields(bytearray(CONFIG_LEN), **{k: v for k, v in a.items() if k != "type"})
    if t == "notify" and "color" in a and not (isinstance(a["color"], str) and _HEX.match(a["color"])):
        raise ValueError(f"{where}: colour must be RRGGBB")
    if t == "timer":
        m = a.get("minutes", 25)
        if isinstance(m, bool) or not isinstance(m, (int, float)) or not 0 < m <= 600:
            raise ValueError(f"{where}: timer minutes 1..600")
    if t == "media" and a.get("key") not in MEDIA:
        raise ValueError(f"{where}: media key is one of {list(MEDIA)}")
    if t == "hotkey":
        parse_hotkey(a.get("keys"))
    if t == "launch" and not (isinstance(a.get("target"), str) and a["target"].strip()):
        raise ValueError(f"{where}: launch needs a program, file or web address")
    return {**a, "type": t}


def validate_config(family: str, patch: dict, current: dict) -> dict:
    """`current` with the changes in `patch` applied (raises ValueError). Lists replace entries by position."""
    model = FAMILIES[family]
    unknown = sorted(set(patch) - {"model", "brightness", "keys", "knobs"})
    if unknown:
        raise ValueError(f"unknown setting(s) {unknown}")
    out = copy.deepcopy(current)
    if "brightness" in patch:
        b = patch["brightness"]
        if isinstance(b, bool) or not isinstance(b, int) or not 0 <= b <= 100:
            raise ValueError("pad brightness is 0..100")
        out["brightness"] = b
    keys = patch.get("keys")
    if keys is not None:
        if not isinstance(keys, list) or len(keys) > len(model.keys):
            raise ValueError(f"this pad has {len(model.keys)} keys")
        for i, k in enumerate(keys):
            where = f"key {i + 1}"
            if not isinstance(k, dict) or set(k) - KEY_FIELDS:
                raise ValueError(f"{where}: fields are {sorted(KEY_FIELDS)}")
            label, icon, color = k.get("label", ""), k.get("icon", ""), k.get("color", BLANK_KEY["color"])
            if not isinstance(label, str) or len(label) > 24:
                raise ValueError(f"{where}: label is text of at most 24 characters")
            if not isinstance(icon, str) or len(icon) > 8:
                raise ValueError(f"{where}: icon is an emoji or a few characters")
            if not (isinstance(color, str) and _HEX.match(color)):
                raise ValueError(f"{where}: colour must be RRGGBB")
            out["keys"][i] = _key(label, icon, color.lower(), validate_action(k.get("action", {"type": "none"}), where))
    knobs = patch.get("knobs")
    if knobs is not None:
        if not isinstance(knobs, list) or len(knobs) > len(model.knobs):
            raise ValueError(f"this pad has {len(model.knobs)} knobs")
        for i, k in enumerate(knobs):
            where = f"knob {i + 1}"
            if not isinstance(k, dict) or set(k) - KNOB_FIELDS:
                raise ValueError(f"{where}: fields are {sorted(KNOB_FIELDS)}")
            out["knobs"][i] = {m: validate_action(a, f"{where} {m}") for m, a in k.items()}
    return out


def _send_keys(names: list[str]):
    from pynput.keyboard import Controller, Key
    kb = Controller()
    keys = [getattr(Key, n) if len(n) > 1 else n for n in names]
    for k in keys:
        kb.press(k)
    for k in reversed(keys):
        kb.release(k)


def _launch(target: str):
    if os.name == "nt":
        os.startfile(target)  # noqa: S606 - the user's own configured program / address
    else:
        import subprocess
        subprocess.Popen(["xdg-open", target])


class PadManager:
    SCAN_S = 2.0        # look for a pad this often while none is connected
    HEARTBEAT_S = 8.0   # newer firmware resets after ~35-60 s without a keep-alive
    STATUS_S = 0.5      # how often key highlights follow what is running

    def __init__(self, ctl):
        self.ctl = ctl
        self.clock = time.monotonic
        self.dispatch = self._submit       # how actions run; tests run them inline
        self.send_keys = _send_keys
        self.launch = _launch
        self.dock = None
        self.model = None
        self.error = None
        self.lock = threading.RLock()
        self._next_scan = self._last_beat = self._next_status = 0.0
        self._drawn: dict[int, tuple] = {}
        self._active = None
        self._q: queue.Queue = queue.Queue()
        self._worker = None
        self._stop = threading.Event()
        self._thread = None

    # ---- configuration
    def config(self, family: str) -> dict:
        saved = (self.ctl.settings.get("pads") or {}).get(family) or {}
        d = DEFAULTS[family]
        n_keys, n_knobs = len(FAMILIES[family].keys), len(FAMILIES[family].knobs)
        keys = (saved.get("keys") or d["keys"])[:n_keys]
        knobs = (saved.get("knobs") or d["knobs"])[:n_knobs]
        return {"brightness": saved.get("brightness", d["brightness"]),
                "keys": [*keys, *[BLANK_KEY] * (n_keys - len(keys))],
                "knobs": [*knobs, *[{}] * (n_knobs - len(knobs))]}

    def family_of(self, name: str | None) -> str:
        if name in FAMILIES:
            return name
        if name in MODELS:
            return MODELS[name].family
        if name is None and self.model is not None:
            return self.model.family
        if name is None:
            return "akp03"
        raise ValueError(f"unknown pad {name!r}; choose from {sorted(FAMILIES)}")

    def configure(self, patch: dict) -> dict:
        if not isinstance(patch, dict):
            raise ValueError("pad settings must be an object")
        family = self.family_of(patch.get("model"))
        cfg = validate_config(family, patch, self.config(family))
        self.ctl.update_settings({"pads": {**(self.ctl.settings.get("pads") or {}), family: cfg}})
        with self.lock:
            if self.dock is not None and self.model.family == family:
                try:
                    self.dock.set_brightness(cfg["brightness"])
                    self._draw(cfg)
                except Exception as e:  # noqa: BLE001
                    self._disconnect(e)
        return cfg

    def set_key(self, family: str | None, index: int, key: dict) -> dict:
        family = self.family_of(family)
        if not isinstance(index, int) or not 0 <= index < len(FAMILIES[family].keys):
            raise ValueError("no such key")
        keys = self.config(family)["keys"]
        keys[index] = key
        return self.configure({"model": family, "keys": keys})

    def status(self) -> dict:
        return {"running": bool(self._thread and self._thread.is_alive()), "connected": self.dock is not None,
                "model": self.model.info() if self.dock is not None else None, "error": self.error,
                "active": self._active if self.dock is not None else None,
                "supported": [{"id": m.id, "name": m.name, "family": m.family, "usb": m.info()["usb"]}
                              for m in MODELS.values()],
                "families": {f: m.info() for f, m in FAMILIES.items()},
                "configs": {f: self.config(f) for f in FAMILIES}}

    # ---- lifecycle
    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()

        def loop():
            while not self._stop.is_set():
                try:
                    self.tick()
                except Exception as e:  # noqa: BLE001 - never let the pad thread die
                    self.error = repr(e)
                self._stop.wait(0.01 if self.dock is not None else 0.25)  # no pad: only wake up to look for one
        self._thread = threading.Thread(target=loop, daemon=True, name="macropad")
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(2)
        with self.lock:
            if self.dock is not None:
                self.dock.close()
            self.dock = None

    def tick(self):
        now = self.clock()
        with self.lock:
            if self.dock is None:
                if now >= self._next_scan:
                    self._next_scan = now + self.SCAN_S
                    self._connect(now)
                return
            try:
                if now - self._last_beat >= self.HEARTBEAT_S:
                    self.dock.heartbeat()
                    self._last_beat = now
                events = self.dock.read_events()
            except Exception as e:  # noqa: BLE001 - unplugged
                self._disconnect(e)
                return
        for ev in events:
            self._on_event(ev)
        if now >= self._next_status or events:
            self._next_status = now + self.STATUS_S
            with self.lock:
                if self.dock is not None:
                    try:
                        self._draw(self.config(self.model.family))
                    except Exception as e:  # noqa: BLE001
                        self._disconnect(e)

    def _connect(self, now: float):
        found = streamdock.scan()
        if not found:
            return
        model, path = found[0]
        dock = None
        try:
            dock = streamdock.StreamDock(model, path)
            cfg = self.config(model.family)
            dock.wake()
            dock.set_brightness(cfg["brightness"])
            self.dock, self.model, self.error = dock, model, None
            self._drawn, self._last_beat = {}, now
            self._draw(cfg)
        except Exception as e:  # noqa: BLE001
            self.error = f"could not start {model.name}: {e!r}"
            if dock is not None:
                dock.close()
            self.dock = None

    def _disconnect(self, err):
        self.error = f"pad disconnected: {err!r}"
        try:
            self.dock.dev.close()
        except Exception:  # noqa: BLE001
            pass
        self.dock = None
        self._next_scan = self.clock() + self.SCAN_S

    # ---- drawing
    def _active_key(self, cfg: dict):
        st = self.ctl.engine.status()
        live = st["effect"]
        profile = getattr(self.ctl, "active_profile", None)
        acts = [k.get("action") or {} for k in cfg["keys"]]
        for i, a in enumerate(acts):
            if profile and a.get("type") == "profile" and a.get("name") == profile:
                return i
        for i, a in enumerate(acts):
            t = a.get("type")
            if (t == "effect" and live == a.get("effect") and live not in (None, "layers")) or \
               (t == "layers" and live == "layers") or (t == "stop" and live is None and not profile):
                return i
        return None

    def look(self, cfg: dict, index: int, active: bool) -> tuple:
        """What key `index` shows: (label, icon, colour, active, cover). A play/pause key shows the cover of
        what is playing: cover = (snapshot id, playing), else None."""
        k = cfg["keys"][index]
        a, cover = k.get("action") or {}, None
        if a.get("type") == "media" and a.get("key") == "play_pause":
            s = media.source().snapshot(max_age=2.0)
            if s.get("image") is not None:
                cover = (s["id"], s["playing"])
        return k.get("label", ""), k.get("icon", ""), k.get("color", "000000"), active, cover

    @staticmethod
    def _image(size, look):
        label, icon, color, active, cover = look
        im = media.source().current().get("image") if cover is not None else None
        if im is not None:
            return streamdock.render_cover(size, im, cover[1], active)
        return streamdock.render_key(size, label, color, icon, active)

    def render(self, model, cfg: dict, index: int, active: bool = False):
        return self._image(model.keys[index].size, self.look(cfg, index, active))

    def _draw(self, cfg: dict):
        """Upload the screen keys whose look changed, then show them all at once (one STP)."""
        self._active = self._active_key(cfg)
        changed = False
        for c in self.model.keys:
            if c.kind != "lcd":
                continue
            look = self.look(cfg, c.index, c.index == self._active)
            if self._drawn.get(c.index) == look:
                continue
            img = self._image(c.size, look)
            self.dock.set_image(c.index, streamdock.encode(self.model, c.index, img))
            self._drawn[c.index] = look
            changed = True
        if changed:
            self.dock.refresh()

    def preview_png(self, model_id: str | None, index: int) -> bytes:
        model = MODELS.get(model_id) or FAMILIES[self.family_of(model_id)]
        if not isinstance(index, int) or not 0 <= index < len(model.keys) or model.keys[index].size is None:
            raise ValueError("no such screen key")
        cfg = self.config(model.family)
        active = self.dock is not None and self.model.family == model.family and index == self._active
        buf = io.BytesIO()
        self.render(model, cfg, index, active).save(buf, "PNG")
        return buf.getvalue()

    # ---- input -> actions
    def _on_event(self, ev):
        cfg = self.config(self.model.family)
        action = None
        if ev.control == "key" and ev.kind == "down":
            action = cfg["keys"][ev.index].get("action")
        elif ev.control == "knob" and ev.kind == "turn":
            action = cfg["knobs"][ev.index].get("right" if ev.delta > 0 else "left")
        elif ev.control == "knob" and ev.kind == "down":
            action = cfg["knobs"][ev.index].get("press")
        if action and action.get("type", "none") != "none":
            self.dispatch(lambda a=action: self.run_action(a))

    def press(self, family: str | None, key: int | None = None, knob: int | None = None, move: str = "press"):
        """Run what a key / knob movement is set to, as if it was used on the pad."""
        cfg = self.config(self.family_of(family))
        if key is not None:
            if not isinstance(key, int) or not 0 <= key < len(cfg["keys"]):
                raise ValueError("no such key")
            action = cfg["keys"][key].get("action")
        else:
            if not isinstance(knob, int) or not 0 <= knob < len(cfg["knobs"]) or move not in KNOB_FIELDS:
                raise ValueError("no such knob / movement")
            action = cfg["knobs"][knob].get(move)
        if action and action.get("type", "none") != "none":
            self.dispatch(lambda: self.run_action(action))

    def _submit(self, fn):
        self._q.put(fn)
        if self._worker is None or not self._worker.is_alive():
            def work():
                while True:
                    f = self._q.get()
                    try:
                        f()
                    except Exception as e:  # noqa: BLE001
                        self.error = f"action failed: {e!r}"
            self._worker = threading.Thread(target=work, daemon=True, name="macropad-actions")
            self._worker.start()

    def run_action(self, a: dict):
        t, ctl = a.get("type", "none"), self.ctl
        if t == "media":
            return self.send_keys([MEDIA[a["key"]]])
        if t == "hotkey":
            return self.send_keys(parse_hotkey(a["keys"]))
        if t == "launch":
            return self.launch(a["target"])
        with ctl.lock:
            if t == "effect":
                ctl.run_live(a["effect"], a.get("params") or {})
            elif t == "layers":
                from .effects import Layers
                ctl.run_live("layers", {"layers": ctl.settings.get("layers_draft") or Layers.DEFAULT})
            elif t == "stop":
                ctl.stop_live()
            elif t == "profile":
                p = ctl.find_profile(a["name"])
                if p is None:
                    raise ValueError(f"no profile named {a['name']!r}")
                ctl.apply_profile(p)
            elif t in ("profile_next", "profile_prev"):
                ps = ctl.settings.get("profiles") or []
                if ps:
                    names = [p.get("name") for p in ps]
                    cur = names.index(ctl.active_profile) if ctl.active_profile in names else None
                    step = 1 if t == "profile_next" else -1
                    i = (0 if step > 0 else len(ps) - 1) if cur is None else (cur + step) % len(ps)
                    ctl.apply_profile(ps[i])
            elif t == "brightness":  # only the brightness: what runs (and app profiles) goes on
                b = ctl.kb.info()["brightness"]
                nb = max(0, min(4, b + int(a["step"])))
                if nb != b:  # stored on the keyboard: never write when nothing changes
                    ctl._store_brightness(nb)
            elif t == "hardware":
                ctl.set_hardware(**{k: v for k, v in a.items() if k != "type"})
            elif t == "notify":
                ctl.automation.notify(a.get("color", "ffffff"), None, 3, 0.35)
            elif t == "timer":
                if ctl.automation.timer_status():
                    ctl.automation.cancel_timer()
                else:
                    ctl.automation.start_timer(float(a.get("minutes", 25)), "")

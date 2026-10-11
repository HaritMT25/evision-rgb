"""Local HTTP JSON API (127.0.0.1 only) + the GUI's web files. One process owns the keyboard;
the GUI, CLI, MCP server and other agents all talk to it.

GET  /status | /info | /keys | /effects | /modes | /frame | /custom?colorset=N | /settings | /conflicts
     /foreground | /autostart
POST /effect   {"mode": "normal", "brightness": 3, "speed": 2, "color": "ff0000", "direction": 0}   (persistent)
POST /custom   {"colors": {"W": "ff0000"}, "default": "000000", "colorset": 0}                 (persistent)
POST /run      {"effect": "rainbow", "params": {"speed": 0.3}}                                  (live)
POST /keys     {"colors": {"W": "ff0000", "A": null}}         (live per-key override; null clears)
POST /clear    {}  remove live overrides       POST /stop {}  end live mode (and app profiles)
POST /apps     {"enabled": true, "default": {...}, "rules": [{"match": "code.exe", "effect": "reactive"}]}
POST /settings {...partial settings...}        POST /autostart {"enabled": true}
POST /notify   {"color": "ff0000", "keys": ["Esc"], "flashes": 3, "period": 0.35}   flash keys or the board
POST /timer    {"minutes": 25, "label": "focus"} | {"cancel": true}                 number-row countdown
GET|POST /automation  indicators (caps_lock, mic_mute), idle/lock dimming, night schedule, battery saver
         ({"battery": {"enabled": true}}: on battery live effects pause and the keyboard shows its own effect)
POST /close_conflict {"pid": 123}              POST /restore {}  (restore pre-session backup)
GET  /profiles                                 saved profiles (name, hardware mode, live effect)
GET  /profiles/export?name=N[&as=code]         shareable file {"format": "evision-rgb-profile", ...} or share code
POST /profiles/import  <file contents> | {"code": "evrgb1:..."}   validated, added under a free name, not applied
POST /profiles/apply   {"name": "Gaming"}      hardware + custom colours in one write, then the live part
Live effect "layers" stacks effects with key masks / blend modes; layer effect "keyboard" = the stored effect.
GET  /nowplaying | /nowplaying/cover.png       what this PC is playing (title, artist, cover colours) / its cover
POST /game/cs2 | /game/dota2                  the game's own state (Valve Game State Integration; needs the token)
GET  /games    POST /games {"auto": true}     game lighting status / switch on automatically while you play
POST /games/setup {"game": "cs2"} | /games/remove {"game": "cs2"}   write / delete our config file in the game
GET  /pad | /pad/key.png?model=akp03&i=0      macro pad state, configs, supported models / a key image
POST /pad {"model": "akp03", "brightness": 80, "keys": [...], "knobs": [...]}   POST /pad/key {"model", "index", "key"}
POST /pad/press {"model": "akp03", "key": 0} | {"knob": 1, "move": "left|right|press"}   run what it is set to
Only local programs and the app's own page are served: other Host names / Origins and non-JSON POSTs are refused.
"""
from __future__ import annotations

import hmac
import json
import mimetypes
import secrets
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import settings as settings_mod
from . import system
from . import emulate  # registers the 'emulate' and 'keyboard' effects
from . import heatmap as heatmap_mod  # registers 'heatmap'
from . import media  # registers 'nowplaying' (album colours)
from . import games  # registers 'cs2' and 'dota2' (game lighting)
from . import profiles
from .effects import EFFECTS, Engine, NAMES
from .keyboard import CONFIG_LEN, Keyboard
from .layout import KEYS
from .protocol import CMD_WRITE_CONFIG, MODES

DEFAULT_PORT = 8820
WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gui", "web")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKUP_FILES = ("config.bin", "custom0.bin", "custom1.bin", "custom2.bin", "keymap.bin")


MODE_NAME = {i: n for n, i in MODES.items()}
_STREAMS = {"open": 0}
_STREAMS_LOCK = threading.Lock()


def open_streams() -> int:
    """Preview streams (app windows) currently connected."""
    return _STREAMS["open"]


def _hex(c) -> str:
    return "%02x%02x%02x" % tuple(int(max(0, min(1, v)) * 255 + 0.5) for v in c)


def uses_stored_effect(effect: str, params: dict | None) -> bool:
    """Is this live effect drawn on top of the keyboard's stored effect ('keyboard' layer)?"""
    if effect == "keyboard":
        return True
    layers = (params or {}).get("layers") if effect == "layers" else None
    return isinstance(layers, list) and any(isinstance(l, dict) and l.get("effect") == "keyboard" for l in layers)


class Controller:
    """Shared state: engine (live effects), app profiles, automation, settings."""

    def __init__(self, engine: Engine | None = None, probes: dict | None = None, automation_hz: float = 10,
                 hook_keys: bool = True):
        self.engine = engine or Engine(Keyboard(wait=True))  # the app also starts with the keyboard unplugged
        self.kb = self.engine.kb
        self.lock = threading.RLock()
        self.apps = None
        self.settings = settings_mod.load()
        from .automation import Automation
        self.automation = Automation(self.settings.get("automation"), probes)
        emulate.set_time_scale(self.settings.get("fw_time_scale", 1.0))
        self.engine.post = self.automation.apply
        self.auto_base = False  # True while a mirror was started only to show automation
        self._auto_stop = threading.Event()
        self._auto_hz = automation_hz
        self.heatmap = heatmap_mod.KeyCounter(os.path.join(settings_mod.DIR, "heatmap.json"),
                                              enabled=self.settings.get("heatmap_enabled", False))
        self._hook = None
        self._hook_keys = hook_keys
        self._sync_key_hook()
        self.show_cb = None  # set by the desktop app: bring its window to front
        self.backup_dir = None
        self._device_ready = False
        self._first_contact()
        # boot the firmware engine (JIT compile + extraction) without blocking start-up
        threading.Thread(target=emulate.firmware_base, daemon=True).start()
        self._info_cache = (0.0, None)
        self._emu = (None, None, 0.0)  # (config key, emulator, t0) for the preview
        emulate.set_stored_effect(self._emulation_params)  # 'keyboard' layers show the stored effect
        self.active_profile = None  # name of the profile applied last, until the lighting is changed otherwise
        self._frame = (None, None)  # (engine.last, preview dict): the preview is built once per new frame
        self._saving = False        # battery saver active (on battery and switched on)
        self._on_battery = None
        self.battery_paused = None  # what the battery saver stopped: {"effect", "params", "overrides"} | {"apps"}
        self.game_on = None         # game whose lighting took over automatically ("cs2" / "dota2")
        self.game_prev = None       # what ran before it (comes back when the game ends)
        self._game_dismissed = None  # you chose other lighting during this game: don't take over again
        from .macropad import PadManager
        self.pads = PadManager(self)  # started by the app / `evrgb serve` (start_pads), never by tests
        threading.Thread(target=self._automation_loop, daemon=True).start()

    def start_pads(self):
        self.pads.start()

    def _first_contact(self):
        """Once the keyboard is there: the first-start lighting backup, and its capabilities (exact effect
        emulation needs the firmware image that matches it). Retried by the automation loop until it works."""
        if self._device_ready:
            return
        try:
            if self.backup_dir is None:
                self.backup_dir = self.ensure_backup()
            emulate.set_device_caps(self.kb.dev.capabilities())
            self._device_ready = True
        except Exception:  # noqa: BLE001 - not plugged in yet
            pass

    def ensure_backup(self) -> str:
        """On the very first start, save the keyboard's lighting before this app changes anything.
        Never overwritten afterwards, so 'restore' always means 'as it was before first use'."""
        d = self.settings.get("backup_dir") or os.path.join(settings_mod.DIR, "backup")
        if not os.path.isfile(os.path.join(d, "config.bin")):
            dev = self.kb.dev
            blobs = {"config.bin": dev.read_config(0x80), "keymap.bin": dev.read_keymap(),
                     **{f"custom{i}.bin": dev.read_custom(i) for i in range(3)}}
            os.makedirs(d, exist_ok=True)
            for name, data in blobs.items():
                with open(os.path.join(d, name), "wb") as f:
                    f.write(data)
        return d

    # -- automation: borrow the LEDs only while an indicator / flash / timer / dimming needs them
    def info(self, max_age: float = 1.0) -> dict:
        """Stored config, cached briefly (the preview polls often)."""
        t, cached = self._info_cache
        if cached is None or time.monotonic() - t > max_age:
            cached = self.kb.info()
            self._info_cache = (time.monotonic(), cached)
        return cached

    def _emulation_params(self) -> dict:
        info = self.info(0)
        p = {k: info[k] for k in ("brightness", "speed", "direction", "random", "color", "variant")}
        p["mode"] = info["mode_id"]
        p["dim"] = False
        if info["mode_id"] == MODES["custom"]:
            p["custom"] = self.kb.get_custom(0)
        return p

    def _automation_loop(self):
        while not self._auto_stop.wait(1.0 / self._auto_hz):
            try:
                self._first_contact()
                self._battery_tick()
                self._games_tick()
                need = self.automation.active()
                with self.lock:
                    eng = self.engine
                    if need and eng.effect is None:
                        eng.run("emulate", **self._emulation_params())
                        self.auto_base = True
                    elif not need and self.auto_base:
                        if eng.effect is not None and eng.effect.name == "emulate":  # never stop the user's effect
                            eng.stop()
                        self.auto_base = False
            except Exception as e:  # keyboard unplugged etc.: keep trying
                print("automation:", repr(e))

    # -- battery saver: on battery the PC stops drawing and the keyboard shows its own effect
    def _battery_saving(self) -> bool:
        self._on_battery = self.automation.probes["on_battery"]()
        return bool(self._on_battery) and self.automation.cfg["battery"]["enabled"]

    def _battery_tick(self):
        saving = self._battery_saving()
        with self.lock:
            if saving and not self._saving:
                self._saving = True
                self._pause_for_battery()
            elif not saving and self._saving:
                self._saving = False
                self._resume_after_battery()

    def _take_lighting(self) -> dict | None:
        """Stop what the PC draws now and return it (to bring back later); None if nothing of yours ran."""
        eng = self.engine
        if self.apps:
            p = {"apps": self.apps.profile}
            self.stop_apps()
            eng.stop()
            return p
        if eng.effect is not None and not (self.auto_base and eng.effect.name == "emulate"):
            p = {"effect": eng.effect.name, "params": dict(eng.params), "overrides": dict(eng.overrides)}
            eng.stop()  # the remembered live effect (settings) stays, so a restart brings it back too
            return p
        return None

    def _give_back(self, p: dict | None):
        if not p:
            return
        if "apps" in p:
            self.start_apps(p["apps"])
        else:
            self.engine.overrides.update(p["overrides"])
            self.engine.run(p["effect"], **p["params"])
        self.auto_base = False

    def _pause_for_battery(self):
        self.battery_paused = self._take_lighting()

    def _resume_after_battery(self):
        p, self.battery_paused = self.battery_paused, None
        self._give_back(p)

    def _lighting_chosen(self):
        """The user picked the lighting: nothing the battery saver or a game paused comes back over it."""
        self.battery_paused = None
        if self.game_on:
            self._game_dismissed, self.game_on, self.game_prev = self.game_on, None, None

    # -- game lighting: switches on by itself while you play (Valve Game State Integration)
    def games_settings(self) -> dict:
        g = dict(self.settings.get("games") or {})
        if not g.get("token"):
            g = {"auto": True, **g, "token": secrets.token_hex(16)}
            self.update_settings({"games": g})
        return g

    def _games_tick(self):
        cfg = self.settings.get("games") or {}
        playing = next((g for g in games.GAMES if games.feed(g).active()), None) if cfg.get("auto", True) else None
        with self.lock:
            if playing is None:
                self._game_dismissed = None
                if self.game_on:
                    eng = self.engine
                    mine = eng.effect is not None and eng.effect.name == self.game_on
                    prev, self.game_on, self.game_prev = self.game_prev, None, None
                    if mine:
                        eng.stop()
                        self._give_back(prev)
                return
            if self._saving or playing == self._game_dismissed or playing == self.game_on:
                return
            if self.game_on is None:
                self.game_prev = self._take_lighting()
            self.stop_apps()
            self.engine.run(playing)
            self.game_on, self.auto_base = playing, False

    def game_post(self, game: str, state) -> None:
        """State posted by the game itself. The token proves it came from the config file we wrote."""
        if game not in games.GAMES:
            raise KeyError(game)
        token = ((state or {}).get("auth") or {}).get("token") if isinstance(state, dict) else None
        if not isinstance(token, str) or not hmac.compare_digest(token, self.games_settings()["token"]):
            raise PermissionError("wrong game token")
        games.feed(game).update(state)

    def games_status(self) -> dict:
        found = games.find_games()
        out = {}
        for gid, g in games.GAMES.items():
            f, d = games.feed(gid), found.get(gid)
            out[gid] = {"name": g["name"], "installed": d is not None, "path": d,
                        "configured": bool(d) and games.config_written(gid, d), "receiving": f.active(),
                        "last_s": None if f.state is None else round(f.clock() - f.received, 1),
                        "launch_option": g.get("launch_option")}
        return {"auto": (self.settings.get("games") or {}).get("auto", True), "steam": games.steam_path(),
                "on": self.game_on, "games": out}

    def games_setup(self, game: str, remove: bool = False) -> dict:
        if game not in games.GAMES:
            raise KeyError(f"unknown game {game!r}")
        d = games.find_games().get(game)
        if d is None:
            raise FileNotFoundError(f"{games.GAMES[game]['name']} is not installed in any Steam library")
        if remove:
            games.remove_config(game, d)
        else:
            port = getattr(self, "port", DEFAULT_PORT)
            games.write_config(game, d, self.games_settings()["token"], port=port)
        return self.games_status()

    def battery_status(self) -> dict:
        p = self.battery_paused
        return {"saver": self.automation.cfg["battery"]["enabled"], "on_battery": self._on_battery,
                "paused": None if not p else "app profiles" if "apps" in p else p["effect"]}

    def _sync_key_hook(self):
        """Global key hook only while the heatmap is counting."""
        want = self.heatmap.enabled and self._hook_keys
        if want and self._hook is None:
            from .effects import KeyEvents
            self._hook = KeyEvents(self.heatmap.record)
            self._hook.start()
        elif not want and self._hook is not None:
            self._hook.stop()
            self._hook = None

    def heatmap_status(self) -> dict:
        counts = self.heatmap.counts()
        top = sorted(counts.items(), key=lambda kv: -kv[1])[:10]
        return {"enabled": self.heatmap.enabled, "total": sum(counts.values()), "since": self.heatmap.since,
                "top": [list(kv) for kv in top], "counts": counts}

    def close(self):
        self._auto_stop.set()
        self.pads.stop()
        prev = emulate.set_stored_effect(None)
        if prev != self._emulation_params:  # another (newer) controller registered itself: keep it
            emulate.set_stored_effect(prev)
        if self._emu[1] is not None:
            self._emu[1].stop()
        self._sync_key_hook_off()
        self.stop_apps()
        self.engine.close()

    def _sync_key_hook_off(self):
        if self._hook is not None:
            self._hook.stop()
            self._hook = None
        if self.heatmap.enabled:
            self.heatmap.save()

    # -- app profiles
    def stop_apps(self):
        if self.apps:
            self.apps.stop()
            self.apps = None

    def start_apps(self, cfg: dict):
        from .apps import AppProfiles
        self.stop_apps()
        self.apps = AppProfiles(self.engine, cfg).start()

    # -- settings
    def update_settings(self, patch: dict) -> dict:
        if "fw_time_scale" in patch:
            emulate.set_time_scale(float(patch["fw_time_scale"]))
            self._emu = (None, self._emu[1], 0.0)  # rebuild the preview emulator with the new timing
        self.settings.update(patch)
        return settings_mod.save(self.settings)

    def remember_live(self, effect: str | None, params: dict | None = None):
        self.update_settings({"last_live": {"effect": effect, "params": params or {}} if effect else None})

    # -- lighting changes shared by the HTTP API and the macro pad
    def run_live(self, effect: str, params: dict | None = None, remember: bool = True) -> dict:
        if effect not in EFFECTS:
            raise ValueError(f"unknown effect {effect!r}; choose from {list(EFFECTS)}")
        self.stop_apps()
        self._lighting_chosen()
        run_params = dict(params or {})
        if effect == "heatmap" and "counts" not in run_params:
            run_params["counts"] = self.heatmap.counts()
        st = self.engine.run(effect, **run_params)
        self.active_profile = None
        if remember:  # previews (custom-key editing) are not remembered
            self.remember_live(effect, params or {})
        return st

    def stop_live(self) -> dict:
        self.stop_apps()
        self._lighting_chosen()
        self.remember_live(None)
        self.active_profile = None
        return self.engine.stop()

    def set_hardware(self, **fields) -> dict:
        """Store a built-in effect on the keyboard. A layer stack drawn on top of the stored effect keeps
        running on top of the new one; any other live effect ends (hardware effects only show then)."""
        self.kb.apply_fields(bytearray(CONFIG_LEN), **fields)  # validate before touching the stream
        self.stop_apps()
        self._lighting_chosen()
        eng = self.engine
        keep = (eng.effect.name, dict(eng.params)) if (
            eng.effect is not None and uses_stored_effect(eng.effect.name, eng.params)) else None
        eng.pause()  # freeze the live frame while storing (no flash of the old effect)
        self.active_profile = None
        try:
            return self.kb.set_effect(**fields)
        finally:
            if keep:
                eng.run(keep[0], **keep[1])
                self.remember_live(*keep)
            else:
                eng.stop()
                self.remember_live(None)

    # -- profiles (hardware effect + custom colours + live effect / layer stack)
    def find_profile(self, name: str) -> dict | None:
        return next((p for p in self.settings.get("profiles") or [] if p.get("name") == name), None)

    def import_profile(self, doc) -> dict:
        """Validate a shared file / share code and add it under a free name. Never touches the keyboard."""
        p = profiles.read_doc(doc)
        existing = list(self.settings.get("profiles") or [])
        p["name"] = profiles.unique_name(p["name"], [x.get("name") for x in existing])
        self.update_settings({"profiles": existing + [p]})
        return p

    def apply_profile(self, p: dict) -> dict:
        """Store the hardware effect and custom colours in ONE keyboard transaction, then start the live
        part. The current live frame stays on screen while storing, so the old lighting never shows."""
        name = p.get("name") if isinstance(p, dict) else None
        p = profiles.validate_profile(p)
        hw = profiles.hardware_args(p.get("hardware") or {})
        if p.get("custom") is not None and not hw:
            hw = {"mode": "custom"}  # a per-key layout on its own means "show these colours"
        live = p.get("live")
        self.stop_apps()
        self._lighting_chosen()
        self.engine.pause()
        try:
            if hw:
                self.kb.set_effect(**hw, custom=p.get("custom"))
            if live:
                self.engine.run(live["effect"], **(live.get("params") or {}))
        except Exception:
            self.engine.stop()
            raise
        if not live:
            self.engine.stop()  # hardware effects only show when not streaming
        self.remember_live(live["effect"] if live else None, (live or {}).get("params"))
        self.active_profile = name
        return {"applied": p["name"], "live": self.engine.status()}

    def startup(self):
        """Apply the configured start-up action (used by the GUI / --background). On battery with the
        saver on, it waits until the PC is plugged in."""
        s = self.settings
        try:
            want = None
            if s.get("startup") == "apps" or (s.get("apps") or {}).get("enabled"):
                want = {"apps": s["apps"]}
            elif s.get("startup") == "restore" and s.get("last_live"):
                want = {"effect": s["last_live"]["effect"], "params": s["last_live"].get("params", {}),
                        "overrides": {}}
            if want and self._battery_saving():
                with self.lock:
                    self._saving, self.battery_paused = True, want
            elif want and "apps" in want:
                self.start_apps(want["apps"])
            elif want:
                self.engine.run(want["effect"], **want["params"])
        except Exception as e:  # never block start-up on a bad saved setting
            print("startup action failed:", repr(e))

    # -- preview of what the keyboard shows
    def frame(self) -> dict:
        eng = self.engine
        last = eng.last
        if last is not None:
            if self._frame[0] is not last:  # the engine only replaces `last` when the picture changed
                self._frame = (last, {"source": "live", "effect": eng.effect.name if eng.effect else None,
                                      "colors": {n: _hex(c) for n, c in zip(NAMES, last)}})
            return self._frame[1]
        try:
            info = self.info()
        except Exception:  # noqa: BLE001 - unplugged: the window says so instead of failing
            return {"source": "disconnected", "colors": {}}
        if info["mode_id"] == MODES["custom"]:
            return {"source": "custom", "mode": info["mode"], "colors": self.kb.get_custom(0)}
        key = tuple(info[k] for k in ("mode_id", "brightness", "speed", "direction", "random", "color", "variant"))
        if self._emu[0] != key:
            old = self._emu[1]
            if old is not None:
                old.stop()  # release its key hook
            emu = emulate.emulator(info)
            if info["mode_id"] in emulate.REACTIVE_MODES:
                emu.start()  # the preview must see key presses to mirror reactive effects
            self._emu = (key, emu, time.monotonic())
        _, emu, t0 = self._emu
        colors = emu.render(time.monotonic() - t0)
        return {"source": "hardware", "mode": info["mode"], "color": info["color"], "animated": info["mode"] != "normal",
                "colors": {n: _hex(c) for n, c in zip(NAMES, colors)}}


def make_handler(ctl: Controller):
    engine = ctl.engine

    class H(BaseHTTPRequestHandler):
        def _send(self, code, obj, ctype="application/json"):
            body = obj if isinstance(obj, (bytes, bytearray)) else json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

        def _local(self, post: bool) -> bool:
            """Only programs on this PC and the app's own page may use the API (a macro pad key can start
            programs): refuse other host names (DNS rebinding), other web origins and non-JSON posts
            (cross-site forms). Replies itself when refusing."""
            port = self.server.server_address[1]
            ours = {f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"}
            host, origin = self.headers.get("Host"), self.headers.get("Origin")
            if host is not None and host.lower() not in ours:
                self._send(403, {"error": "this API only answers 127.0.0.1 / localhost"})
            elif origin is not None and origin.lower() not in {"http://" + h for h in ours}:
                self._send(403, {"error": "requests from web pages are not allowed"})
            elif post and not self.path.startswith("/game/") and \
                    not (self.headers.get("Content-Type") or "").lower().startswith("application/json"):
                self._send(415, {"error": "send JSON (Content-Type: application/json)"})
            else:
                return True
            return False

        def _static(self, path: str) -> bool:
            if path in ("/", "/index.html"):
                rel = "index.html"
            elif path.startswith("/ui/"):
                rel = path[len("/ui/"):]
            else:
                return False
            full = os.path.normpath(os.path.join(WEB, rel))
            if not full.startswith(WEB) or not os.path.isfile(full):
                self._send(404, {"error": "not found"})
                return True
            with open(full, "rb") as f:
                self._send(200, f.read(), mimetypes.guess_type(full)[0] or "application/octet-stream")
            return True

        def _stream(self, fps: float = 60.0, heartbeat_s: float = 1.0):
            """Server-Sent Events: push the preview frame whenever it changes (checked ~60x/s) until the
            client disconnects. While the picture is still, a heartbeat notices a closed window."""
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            period = 1.0 / fps
            last, beat = None, time.monotonic()
            with _STREAMS_LOCK:
                _STREAMS["open"] += 1
            try:
                while True:
                    t = time.monotonic()
                    with ctl.lock:
                        f = ctl.frame()
                    if f != last:
                        self.wfile.write(b"data: " + json.dumps(f).encode() + b"\n\n")
                        last, beat = f, t
                    elif t - beat >= heartbeat_s:
                        self.wfile.write(b": still\n\n")
                        beat = t
                    self.wfile.flush()
                    time.sleep(max(0.0, period - (time.monotonic() - t)))
            except (ConnectionError, OSError):
                return  # browser closed the stream
            finally:
                with _STREAMS_LOCK:
                    _STREAMS["open"] -= 1

        def do_GET(self):
            u = urlparse(self.path)
            q = parse_qs(u.query)
            try:
                if not self._local(post=False):
                    return
                if self._static(u.path):
                    return
                if u.path == "/stream":
                    return self._stream()
                if u.path == "/games":  # (not under the app lock: looks through the Steam folders)
                    return self._send(200, ctl.games_status())
                if u.path == "/nowplaying":  # (not under the app lock: asking Windows can take a moment)
                    return self._send(200, media.public(media.source().snapshot(max_age=1.5)))
                if u.path == "/nowplaying/cover.png":
                    png = media.cover_png(media.source().snapshot(max_age=1.5))
                    if png is None:
                        return self._send(404, {"error": "nothing with a cover is playing"})
                    return self._send(200, png, "image/png")
                with ctl.lock:
                    if u.path == "/status":
                        st = engine.status()
                        st["keyboard"] = "connected" if ctl.kb.dev.connected else "disconnected"
                        st["timer"] = ctl.automation.timer_status()
                        st["battery"] = ctl.battery_status()
                        st["apps"] = ({"foreground": ctl.apps.app,
                                       "rule": (ctl.apps.current or {}).get("match", "default")}
                                      if ctl.apps else None)
                        return self._send(200, st)
                    if u.path == "/info":
                        return self._send(200, ctl.kb.info())
                    if u.path == "/keys":
                        return self._send(200, {k: {"slot": s, "x": x, "y": y} for k, (s, x, y) in KEYS.items()})
                    if u.path == "/effects":
                        return self._send(200, {"live": {n: (c.__doc__ or "").strip() for n, c in EFFECTS.items()},
                                                "hardware": list(MODES)})
                    if u.path == "/modes":
                        return self._send(200, [{"id": i, "name": n} for n, i in MODES.items()])
                    if u.path == "/frame":
                        return self._send(200, ctl.frame())
                    if u.path == "/custom":
                        return self._send(200, ctl.kb.get_custom(int(q.get("colorset", ["0"])[0])))
                    if u.path == "/settings":
                        return self._send(200, ctl.settings)
                    if u.path == "/heatmap":
                        return self._send(200, ctl.heatmap_status())
                    if u.path == "/automation":
                        return self._send(200, ctl.automation.cfg)
                    if u.path == "/firmware":
                        return self._send(200, emulate.firmware_status())
                    if u.path == "/profiles":
                        return self._send(200, [{"name": p.get("name"), "hardware": MODE_NAME.get((p.get("hardware") or {}).get("mode"),
                                                                                                 (p.get("hardware") or {}).get("mode")),
                                                 "custom": p.get("custom") is not None,
                                                 "live": (p.get("live") or {}).get("effect")}
                                                for p in ctl.settings.get("profiles") or []])
                    if u.path == "/profiles/export":
                        name = q.get("name", [""])[0]
                        p = ctl.find_profile(name)
                        if p is None:
                            return self._send(404, {"error": f"no profile named {name!r}"})
                        doc = profiles.export_doc(p)
                        if q.get("as", [""])[0] == "code":
                            return self._send(200, {"name": p["name"], "code": profiles.to_code(doc)})
                        return self._send(200, doc)
                    if u.path == "/pad":
                        return self._send(200, ctl.pads.status())
                    if u.path == "/pad/key.png":
                        png = ctl.pads.preview_png(q.get("model", [None])[0], int(q.get("i", ["0"])[0]))
                        return self._send(200, png, "image/png")
                    if u.path == "/conflicts":
                        return self._send(200, system.conflicts())
                    if u.path == "/foreground":
                        from .apps import foreground
                        name, title = foreground()
                        return self._send(200, {"name": name, "title": title})
                    if u.path == "/autostart":
                        return self._send(200, {"enabled": system.autostart_enabled(),
                                                "command": system.launch_command()})
                self._send(404, {"error": "not found"})
            except (KeyError, ValueError, TypeError) as e:
                self._send(400, {"error": str(e) or repr(e)})
            except Exception as e:
                self._send(500, {"error": repr(e)})

        def do_POST(self):
            try:
                n = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(n)  # read before refusing, or the client sees a reset connection
                if not self._local(post=True):
                    return
                req = json.loads(body or b"{}")
                p = self.path
                if p.startswith("/game/"):  # the game itself, several times a second: no app lock needed
                    try:
                        ctl.game_post(p[len("/game/"):], req)
                    except KeyError:
                        return self._send(404, {"error": "unknown game"})
                    except PermissionError as e:
                        return self._send(403, {"error": str(e)})
                    return self._send(200, {})
                if p in ("/games/setup", "/games/remove"):
                    try:
                        return self._send(200, ctl.games_setup(req.get("game"), remove=p.endswith("remove")))
                    except FileNotFoundError as e:
                        return self._send(404, {"error": str(e)})
                with ctl.lock:
                    ctl._info_cache = (0.0, None)
                    if p == "/notify":
                        ctl.automation.notify(req.get("color", "ffffff"), req.get("keys"),
                                              req.get("flashes", 3), req.get("period", 0.35))
                        return self._send(200, {"ok": True})
                    if p == "/timer":
                        if req.get("cancel"):
                            ctl.automation.cancel_timer()
                        else:
                            ctl.automation.start_timer(float(req["minutes"]), req.get("label", ""))
                        return self._send(200, {"timer": ctl.automation.timer_status()})
                    if p == "/heatmap":
                        if req.get("clear"):
                            ctl.heatmap.clear()
                        if "enabled" in req:
                            ctl.heatmap.enabled = bool(req["enabled"])
                            ctl.update_settings({"heatmap_enabled": ctl.heatmap.enabled})
                            ctl._sync_key_hook()
                            if not ctl.heatmap.enabled:
                                ctl.heatmap.save()
                        return self._send(200, ctl.heatmap_status())
                    if p == "/automation":
                        cfg = ctl.automation.configure(req)
                        ctl.update_settings({"automation": cfg})
                        return self._send(200, cfg)
                    if p == "/effect":
                        return self._send(200, ctl.set_hardware(**req))
                    if p == "/custom":
                        ctl.stop_apps()
                        ctl._lighting_chosen()
                        engine.pause()  # the custom-key preview stays on screen while we store it
                        ctl.remember_live(None)
                        ctl.active_profile = None
                        try:
                            ctl.kb.set_custom(req.get("colors", {}), req.get("default", "000000"),
                                              req.get("colorset", 0), req.get("activate", True))
                        finally:
                            engine.stop()
                        return self._send(200, ctl.kb.info())
                    if p == "/run":
                        return self._send(200, ctl.run_live(req.get("effect"), req.get("params", {}),
                                                            req.get("remember", True)))
                    if p == "/keys":
                        return self._send(200, engine.set_keys(req["colors"]))
                    if p == "/clear":
                        engine.clear_keys()
                        return self._send(200, engine.status())
                    if p == "/stop":
                        return self._send(200, ctl.stop_live())
                    if p == "/apps":
                        cfg = {**ctl.settings.get("apps", {}), **req}
                        ctl.update_settings({"apps": cfg})
                        ctl._lighting_chosen()
                        if cfg.get("enabled", True):
                            ctl.start_apps(cfg)
                            return self._send(200, {"apps": "running", "rules": len(cfg.get("rules", []))})
                        ctl.stop_apps()
                        engine.stop()
                        return self._send(200, {"apps": "stopped"})
                    if p == "/settings":
                        return self._send(200, ctl.update_settings(req))
                    if p == "/games":
                        if not isinstance(req.get("auto"), bool):
                            raise ValueError("auto must be true or false")
                        ctl.update_settings({"games": {**ctl.games_settings(), "auto": req["auto"]}})
                        return self._send(200, {"auto": req["auto"]})
                    if p == "/profiles/import":
                        prof = ctl.import_profile(req)
                        return self._send(200, {"name": prof["name"], "profile": prof})
                    if p == "/profiles/apply":
                        name = req.get("name") if isinstance(req, dict) else None
                        prof = ctl.find_profile(name)
                        if prof is None:
                            return self._send(404, {"error": f"no profile named {name!r}"})
                        return self._send(200, ctl.apply_profile(prof))
                    if p == "/pad":
                        return self._send(200, ctl.pads.configure(req))
                    if p == "/pad/key":
                        return self._send(200, ctl.pads.set_key(req.get("model"), req.get("index"), req.get("key")))
                    if p == "/pad/press":
                        ctl.pads.press(req.get("model"), key=req.get("key"), knob=req.get("knob"),
                                       move=req.get("move", "press"))
                        return self._send(200, {"ok": True})
                    if p == "/show":
                        if ctl.show_cb:
                            threading.Thread(target=ctl.show_cb, daemon=True).start()
                        return self._send(200, {"shown": bool(ctl.show_cb)})
                    if p == "/autostart":
                        return self._send(200, {"enabled": system.set_autostart(bool(req.get("enabled")))})
                    if p == "/close_conflict":
                        return self._send(200, {"closed": system.close_process(int(req["pid"]))})
                    if p == "/restore":
                        d = req.get("dir") or ctl.backup_dir
                        if not d or not os.path.isfile(os.path.join(d, "config.bin")):
                            raise ValueError("no lighting backup found")
                        ctl.stop_apps()
                        ctl._lighting_chosen()
                        engine.stop()
                        for cs in range(3):
                            with open(os.path.join(d, f"custom{cs}.bin"), "rb") as f:
                                ctl.kb.set_custom(f.read(), colorset=cs, activate=False)
                        with open(os.path.join(d, "config.bin"), "rb") as f:
                            cfg = f.read()[:CONFIG_LEN]
                        ctl.kb.dev.begin()
                        ctl.kb.dev.write_block(CMD_WRITE_CONFIG, cfg, 0)
                        ctl.kb.dev.end()
                        if cfg[1] == MODES["custom"]:  # refresh the firmware's per-key display buffer
                            ctl.kb.set_custom(ctl.kb.dev.read_custom(0), colorset=0)
                        return self._send(200, ctl.kb.info())
                self._send(404, {"error": "not found"})
            except (KeyError, ValueError, TypeError) as e:
                self._send(400, {"error": str(e) or repr(e)})
            except Exception as e:
                self._send(500, {"error": repr(e)})

    return H


def build_server(ctl: Controller, port: int = DEFAULT_PORT) -> ThreadingHTTPServer:
    ThreadingHTTPServer.daemon_threads = True
    httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(ctl))
    ctl.port = httpd.server_address[1]  # game config files point the game here
    return httpd


def serve(port: int = DEFAULT_PORT):
    ctl = Controller()
    ctl.start_pads()
    httpd = build_server(ctl, port)
    print(f"AK820 API on http://127.0.0.1:{port}  (Ctrl+C to stop)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        ctl.close()

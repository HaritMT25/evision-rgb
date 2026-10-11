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
GET|POST /automation  indicators (caps_lock, mic_mute), idle/lock dimming, night schedule
POST /close_conflict {"pid": 123}              POST /restore {}  (restore pre-session backup)
"""
from __future__ import annotations

import json
import mimetypes
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import settings as settings_mod
from . import system
from . import emulate  # registers the 'emulate' effect
from . import heatmap as heatmap_mod  # registers 'heatmap'
from .effects import EFFECTS, Engine, NAMES
from .keyboard import CONFIG_LEN
from .layout import KEYS
from .protocol import CMD_WRITE_CONFIG, MODES

DEFAULT_PORT = 8820
WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gui", "web")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKUP_FILES = ("config.bin", "custom0.bin", "custom1.bin", "custom2.bin", "keymap.bin")


def _hex(c) -> str:
    return "%02x%02x%02x" % tuple(int(max(0, min(1, v)) * 255 + 0.5) for v in c)


class Controller:
    """Shared state: engine (live effects), app profiles, automation, settings."""

    def __init__(self, engine: Engine | None = None, probes: dict | None = None, automation_hz: float = 10,
                 hook_keys: bool = True):
        self.engine = engine or Engine()
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
        self.backup_dir = self.ensure_backup()
        try:  # exact effect emulation needs the firmware image that matches this keyboard
            emulate.set_device_caps(self.kb.dev.capabilities())
        except Exception:
            pass
        # boot the firmware engine (JIT compile + extraction) without blocking start-up
        threading.Thread(target=emulate.firmware_base, daemon=True).start()
        self._info_cache = (0.0, None)
        self._emu = (None, None, 0.0)  # (config key, emulator, t0) for the preview
        threading.Thread(target=self._automation_loop, daemon=True).start()

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

    def startup(self):
        """Apply the configured start-up action (used by the GUI / --background)."""
        s = self.settings
        try:
            if s.get("startup") == "apps" or (s.get("apps") or {}).get("enabled"):
                self.start_apps(s["apps"])
            elif s.get("startup") == "restore" and s.get("last_live"):
                self.engine.run(s["last_live"]["effect"], **s["last_live"].get("params", {}))
        except Exception as e:  # never block start-up on a bad saved setting
            print("startup action failed:", repr(e))

    # -- preview of what the keyboard shows
    def frame(self) -> dict:
        eng = self.engine
        if eng.last is not None:
            return {"source": "live", "effect": eng.effect.name if eng.effect else None,
                    "colors": {n: _hex(c) for n, c in zip(NAMES, eng.last)}}
        info = self.info()
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

        def _stream(self, fps: float = 60.0):
            """Server-Sent Events: push the preview frame ~60x/s until the client disconnects."""
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            period = 1.0 / fps
            try:
                while True:
                    t = time.monotonic()
                    with ctl.lock:
                        f = ctl.frame()
                    self.wfile.write(b"data: " + json.dumps(f).encode() + b"\n\n")
                    self.wfile.flush()
                    time.sleep(max(0.0, period - (time.monotonic() - t)))
            except (ConnectionError, OSError):
                return  # browser closed the stream

        def do_GET(self):
            u = urlparse(self.path)
            q = parse_qs(u.query)
            try:
                if self._static(u.path):
                    return
                if u.path == "/stream":
                    return self._stream()
                with ctl.lock:
                    if u.path == "/status":
                        st = engine.status()
                        st["timer"] = ctl.automation.timer_status()
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
            except Exception as e:
                self._send(500, {"error": repr(e)})

        def do_POST(self):
            try:
                n = int(self.headers.get("Content-Length") or 0)
                req = json.loads(self.rfile.read(n) or b"{}")
                p = self.path
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
                        ctl.kb.apply_fields(bytearray(CONFIG_LEN), **req)  # validate before touching the stream
                        ctl.stop_apps()
                        engine.pause()  # freeze the live frame while storing (no flash of the old effect)
                        ctl.remember_live(None)
                        try:
                            return self._send(200, ctl.kb.set_effect(**req))
                        finally:
                            engine.stop()  # hardware effects only show when not streaming
                    if p == "/custom":
                        ctl.stop_apps()
                        engine.pause()  # the custom-key preview stays on screen while we store it
                        ctl.remember_live(None)
                        try:
                            ctl.kb.set_custom(req.get("colors", {}), req.get("default", "000000"),
                                              req.get("colorset", 0), req.get("activate", True))
                        finally:
                            engine.stop()
                        return self._send(200, ctl.kb.info())
                    if p == "/run":
                        if req.get("effect") not in EFFECTS:
                            raise ValueError(f"unknown effect {req.get('effect')!r}; choose from {list(EFFECTS)}")
                        ctl.stop_apps()
                        params = dict(req.get("params", {}))
                        if req["effect"] == "heatmap" and "counts" not in params:
                            params["counts"] = ctl.heatmap.counts()
                        st = engine.run(req["effect"], **params)
                        if req.get("remember", True):  # previews (custom-key editing) are not remembered
                            ctl.remember_live(req["effect"], req.get("params", {}))
                        return self._send(200, st)
                    if p == "/keys":
                        return self._send(200, engine.set_keys(req["colors"]))
                    if p == "/clear":
                        engine.clear_keys()
                        return self._send(200, engine.status())
                    if p == "/stop":
                        ctl.stop_apps()
                        ctl.remember_live(None)
                        return self._send(200, engine.stop())
                    if p == "/apps":
                        cfg = {**ctl.settings.get("apps", {}), **req}
                        ctl.update_settings({"apps": cfg})
                        if cfg.get("enabled", True):
                            ctl.start_apps(cfg)
                            return self._send(200, {"apps": "running", "rules": len(cfg.get("rules", []))})
                        ctl.stop_apps()
                        engine.stop()
                        return self._send(200, {"apps": "stopped"})
                    if p == "/settings":
                        return self._send(200, ctl.update_settings(req))
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
                        if not os.path.isfile(os.path.join(d, "config.bin")):
                            raise ValueError("no lighting backup found")
                        ctl.stop_apps()
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
    return ThreadingHTTPServer(("127.0.0.1", port), make_handler(ctl))


def serve(port: int = DEFAULT_PORT):
    ctl = Controller()
    httpd = build_server(ctl, port)
    print(f"AK820 API on http://127.0.0.1:{port}  (Ctrl+C to stop)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        ctl.close()

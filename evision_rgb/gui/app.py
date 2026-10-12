"""EVision RGB — desktop app: native window (Edge WebView2) + tray icon + embedded API server.

  EVision RGB.exe              open the window (or bring the running instance to front)
  EVision RGB.exe --background start in the tray only (used for "start with Windows")
  EVision RGB.exe --selftest report.json   check the built app without touching any device (release builds)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import traceback
import urllib.request

from .. import settings as settings_mod

# numba (exact firmware effect emulation) caches compiled code here; the frozen app dir is read-only
os.environ.setdefault("NUMBA_CACHE_DIR", os.path.join(settings_mod.DIR, "numba-cache"))
# ...stamped by the engine's own (bundled) source, not by the whole .exe: a new version doesn't compile it again
os.environ.setdefault("NUMBA_CACHE_LOCATOR_CLASSES", "evision_rgb.jit_cache.BundledSourceLocator,UserProvidedCacheLocator,"
                      "InTreeCacheLocator,UserWideCacheLocator,IPythonCacheLocator,ZipCacheLocator")
from ..server import DEFAULT_PORT, Controller, build_server, listen

HERE = os.path.dirname(os.path.abspath(__file__))
ICON_ICO = os.path.join(HERE, "icon.ico")
ICON_PNG = os.path.join(HERE, "web", "assets", "icon.png")
LOG = os.path.join(settings_mod.DIR, "evisionrgb.log")


def _log(*a):
    os.makedirs(settings_mod.DIR, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + " ".join(str(x) for x in a) + "\n")


def _post(port: int, path: str, body: dict | None = None, timeout: float = 2.0):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=json.dumps(body or {}).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _window_size() -> tuple[int, int]:
    """Fit the window to the screen's work area in logical (DPI-scaled) pixels."""
    try:
        import ctypes
        import ctypes.wintypes as wt
        u = ctypes.windll.user32
        r = wt.RECT()
        u.SystemParametersInfoW(0x30, 0, ctypes.byref(r), 0)  # SPI_GETWORKAREA
        dpi = u.GetDpiForSystem() if hasattr(u, "GetDpiForSystem") else 96  # 96 when DPI-unaware
        w, h = (r.right - r.left) * 96 / dpi, (r.bottom - r.top) * 96 / dpi
        return int(max(1000, min(1280, w - 40))), int(max(660, min(860, h - 30)))
    except Exception:
        return 1200, 780


class PortTaken(OSError):
    """Another program has the API's port: another EVision RGB that is still starting, or something else."""


def _running(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/status", timeout=1.5):
            return True
    except Exception:
        return False


class JsApi:
    """Functions the page can call as window.pywebview.api.* (native dialogs WebView2 lacks)."""

    def __init__(self, app: "App"):
        self._app = app

    def save_text(self, filename: str, text: str):
        """Native Save dialog; writes `text` and returns the path (None if cancelled)."""
        import webview
        save = getattr(getattr(webview, "FileDialog", None), "SAVE", None) or webview.SAVE_DIALOG
        res = self._app.window.create_file_dialog(save, save_filename=os.path.basename(filename),
                                                  file_types=("Lighting profile (*.json)", "All files (*.*)"))
        if not res:
            return None
        path = res if isinstance(res, str) else res[0]
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path


class App:
    def __init__(self, port: int, background: bool):
        self.port, self.background = port, background
        self.ctl: Controller | None = None
        self.httpd = None
        self.window = None
        self.tray = None
        self.quitting = False

    # ---------------------------------------------------------------- lifecycle
    def start_backend(self):
        try:  # first: another app with the port (one still starting) is found before anything touches the keyboard
            self.httpd = listen(self.port)
        except OSError as e:
            raise PortTaken(f"port {self.port} is in use ({e})") from e
        self.ctl = Controller()
        self.ctl.show_cb = self.show  # lets a second launch bring this window to front (POST /show)
        build_server(self.ctl, httpd=self.httpd)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.ctl.startup()
        self.ctl.start_pads()

    def show(self):
        if self.window:
            self.window.show()
            self.window.restore()

    def hide(self):
        if self.window:
            self.window.hide()

    def on_closing(self):
        if self.quitting or not self.ctl.settings.get("close_to_tray", True):
            return True  # the window closes; run() then quits (the tray icon's thread would keep the app alive)
        threading.Thread(target=self.hide, daemon=True).start()
        return False  # cancel the close: keep running in the tray

    def quit(self):
        self.quitting = True
        try:
            if self.ctl:  # app profiles, the pads (their keys blanked), OpenRGB clients and live mode end (the
                self.ctl.close()  # keyboard falls back to its stored effect); the typing heatmap is saved
            if self.httpd:
                self.httpd.shutdown()
        except Exception:
            _log("quit:", traceback.format_exc())
        if self.tray:
            self.tray.stop()
        if self.window:
            self.window.destroy()

    # ---------------------------------------------------------------- tray
    def start_tray(self):
        import pystray
        from PIL import Image

        def run(effect):
            return lambda icon, item: self._safe(lambda: _post(self.port, "/run", {"effect": effect, "params": {}}))

        def profile_names():
            return [p.get("name") for p in (self.ctl.settings.get("profiles") or []) if p.get("name")][:25]

        def apply(name):
            return lambda icon, item: self._safe(lambda: _post(self.port, "/profiles/apply", {"name": name}, timeout=15))

        def profile_items():
            names = profile_names()
            if not names:
                return [pystray.MenuItem("No saved profiles yet", None, enabled=False)]
            return [pystray.MenuItem(n, apply(n)) for n in names]

        menu = pystray.Menu(
            pystray.MenuItem("Open EVision RGB", lambda i, it: self.show(), default=True),
            pystray.MenuItem("Profiles", pystray.Menu(profile_items)),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Rainbow", run("rainbow")),
            pystray.MenuItem("Reactive typing", run("reactive")),
            pystray.MenuItem("Music visualiser", run("music")),
            pystray.MenuItem("Screen ambient", run("ambient")),
            pystray.MenuItem("System monitor", run("sysmon")),
            pystray.MenuItem("App profiles", lambda i, it: self._safe(lambda: _post(self.port, "/apps", {"enabled": True}))),
            pystray.MenuItem("Keyboard's own effect", lambda i, it: self._safe(lambda: _post(self.port, "/stop"))),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", lambda i, it: self.quit()),
        )
        self.tray = pystray.Icon("AK820Control", Image.open(ICON_PNG), "EVision RGB", menu)

        def setup(icon):
            icon.visible = True
            _log("tray icon visible")
            # Windows registers the icon a moment after it is shown; then un-hide it from the ^ overflow
            def promote():
                from .. import system
                for _ in range(10):
                    time.sleep(1)
                    if system.promote_tray_icon():
                        _log("tray icon promoted to the visible taskbar area")
                        return
            threading.Thread(target=promote, daemon=True).start()

            def refresh():  # the Windows tray menu is built once: rebuild it when profiles change; tooltip = state
                shown = profile_names()
                while not self.quitting:
                    try:
                        tip = self.ctl.tray_summary()
                        if tip != icon.title:
                            icon.title = tip
                    except Exception:
                        _log("tray tooltip:", traceback.format_exc())
                    time.sleep(3)
                    names = profile_names()
                    if names != shown:
                        shown = names
                        try:
                            icon.update_menu()
                        except Exception:
                            _log("tray menu:", traceback.format_exc())
            threading.Thread(target=refresh, daemon=True).start()
            # first run: tell the user where the icon is (Windows 11 hides new tray icons under the ^ arrow)
            if not self.ctl.settings.get("tray_hint_shown"):
                try:
                    icon.notify("EVision RGB keeps running here. Click the ^ arrow next to the clock "
                                "to find it, or drag it onto the taskbar to pin it.", "EVision RGB")
                    self.ctl.update_settings({"tray_hint_shown": True})
                except Exception:
                    _log("notify:", traceback.format_exc())

        self.tray.run_detached(setup)

    @staticmethod
    def _safe(fn):
        try:
            fn()
        except Exception:
            _log("tray action:", traceback.format_exc())

    # ---------------------------------------------------------------- window
    def run(self):
        import webview
        self.start_backend()
        self.start_tray()
        hidden = self.background and self.ctl.settings.get("start_minimized", True)
        self.window = webview.create_window(
            "EVision RGB", f"http://127.0.0.1:{self.port}/", width=_window_size()[0], height=_window_size()[1],
            min_size=(1000, 660),
            hidden=hidden, background_color="#0b0b0c", js_api=JsApi(self))
        self.window.events.closing += self.on_closing
        webview.start(gui="edgechromium", private_mode=False,
                      storage_path=os.path.join(settings_mod.DIR, "webview"), icon=ICON_ICO)
        if not self.quitting:  # window closed without tray (close_to_tray off)
            self.quit()


SELFTEST_MODULES = ("evision_rgb.server", "evision_rgb.macropad", "evision_rgb.media", "evision_rgb.profiles",
                    "evision_rgb.automation", "hid", "webview", "pystray", "PIL", "pynput.keyboard", "soundcard",
                    "numba", "pefile", "comtypes", "pycaw.pycaw", "winrt.windows.media.control",
                    "winrt.windows.storage.streams")


def selftest(path: str) -> int:
    """Check a built app without touching any keyboard or pad: every bundled part loads and works. Writes a
    JSON report to `path` (a windowed exe has no console); returns the exit code (0 = everything works)."""
    import importlib
    import platform

    import numpy as np
    checks = {}

    def check(name, fn):
        try:
            checks[name] = {"ok": True, "info": fn()}
        except Exception as e:  # noqa: BLE001 - every failure goes into the report
            checks[name] = {"ok": False, "error": repr(e)}

    def modules():
        failed = {}

        def load(n):  # each on its own thread, as the app does: COM libraries each set up their own apartment
            try:
                importlib.import_module(n)
            except Exception as e:  # noqa: BLE001
                failed[n] = repr(e)
        for n in SELFTEST_MODULES:
            t = threading.Thread(target=load, args=(n,))
            t.start()
            t.join(60)
        if failed:
            raise ImportError(f"not bundled or broken: {failed}")
        return len(SELFTEST_MODULES)

    def effects():
        from ..effects import EFFECTS
        done = []
        for name, cls in sorted(EFFECTS.items()):
            if name == "keyboard":  # the stored effect needs the app's keyboard connection
                continue
            np.asarray(cls().render(0.1), dtype=float)
            done.append(name)
        return done

    def web():
        from ..server import WEB
        need = ("index.html", "app.js", "style.css", "history.js", "layout.json")
        missing = [f for f in need if not os.path.isfile(os.path.join(WEB, f))]
        if missing:
            raise FileNotFoundError(missing)
        return len(need)

    def jit():
        from numba import njit
        if njit(lambda a: a * 2 + 1)(20) != 41:
            raise AssertionError("numba computed a wrong result")
        return "compiles"

    def firmware_engine():  # the exact engine imports (numba picks its cache locator now) without compiling
        from .. import fw8051_fast
        loc = type(fw8051_fast.advance_cycles._cache._impl._locator).__name__
        if getattr(sys, "frozen", False) and loc != "BundledSourceLocator":
            raise AssertionError(f"the engine is cached by {loc}, so every new version would compile it again")
        return f"cached by {loc}"

    def media_controls():
        from ..media import windows_fetch
        return "something is playing" if windows_fetch() else "nothing is playing"

    def pad_images():
        from ..streamdock import MODELS, encode, render_key
        m = MODELS["akp03e-rev2"]
        return len(encode(m, 0, render_key(m.keys[0].size, "Test", "202024", "🎵")))

    for name, fn in (("modules", modules), ("live effects", effects), ("app window files", web), ("numba JIT", jit),
                     ("firmware engine", firmware_engine), ("media controls", media_controls), ("pad key images", pad_images)):
        check(name, fn)
    from .. import __version__
    ok = all(c["ok"] for c in checks.values())
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"ok": ok, "version": __version__, "python": platform.python_version(),
                   "frozen": bool(getattr(sys, "frozen", False)), "checks": checks}, f, indent=1)
    return 0 if ok else 1


def main(argv=None):
    ap = argparse.ArgumentParser(prog="EVision RGB")
    ap.add_argument("--background", action="store_true", help="start in the tray (for autostart)")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--selftest", metavar="REPORT.json", help="check the app without touching any device, then exit")
    a = ap.parse_args(argv)
    if a.selftest:
        return selftest(a.selftest)

    if _running(a.port):  # single instance: bring the existing one to the front
        try:
            if not a.background:
                r = _post(a.port, "/show")
                if not r.get("shown"):  # a headless `evrgb serve` owns the keyboard: open a window onto it
                    import webview
                    w, h = _window_size()
                    webview.create_window("EVision RGB", f"http://127.0.0.1:{a.port}/", width=w, height=h,
                                          min_size=(1000, 660), background_color="#0b0b0c")
                    webview.start(gui="edgechromium", private_mode=False,
                                  storage_path=os.path.join(settings_mod.DIR, "webview"), icon=ICON_ICO)
        except Exception:
            _log("second instance:", traceback.format_exc())
        return 0
    try:
        App(a.port, a.background).run()
    except PortTaken:  # another app took the port as this one started (the check above missed it, e.g. at login)
        for _ in range(20):
            if _running(a.port):
                if not a.background:
                    try:
                        _post(a.port, "/show")
                    except Exception:
                        _log("second instance:", traceback.format_exc())
                return 0
            time.sleep(0.5)
        _log(f"port {a.port} is in use by another program: not started")
        return 1
    except Exception:
        _log("fatal:", traceback.format_exc())
        raise
    return 0


if __name__ == "__main__":
    sys.exit(main())

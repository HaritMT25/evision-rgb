"""EVision RGB — desktop app: native window (Edge WebView2) + tray icon + embedded API server.

  EVision RGB.exe              open the window (or bring the running instance to front)
  EVision RGB.exe --background start in the tray only (used for "start with Windows")
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
from ..server import DEFAULT_PORT, Controller, build_server

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


def _running(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/status", timeout=1.5):
            return True
    except Exception:
        return False


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
        self.ctl = Controller()
        self.ctl.show_cb = self.show  # lets a second launch bring this window to front (POST /show)
        self.httpd = build_server(self.ctl, self.port)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.ctl.startup()

    def show(self):
        if self.window:
            self.window.show()
            self.window.restore()

    def hide(self):
        if self.window:
            self.window.hide()

    def on_closing(self):
        if self.quitting or not self.ctl.settings.get("close_to_tray", True):
            self.quitting = True
            return True
        threading.Thread(target=self.hide, daemon=True).start()
        return False  # cancel the close: keep running in the tray

    def quit(self):
        self.quitting = True
        try:
            if self.ctl:
                self.ctl.stop_apps()
                self.ctl.engine.close()  # ends live mode; keyboard falls back to its stored effect
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

        menu = pystray.Menu(
            pystray.MenuItem("Open EVision RGB", lambda i, it: self.show(), default=True),
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
            hidden=hidden, background_color="#0b0b0c")
        self.window.events.closing += self.on_closing
        webview.start(gui="edgechromium", private_mode=False,
                      storage_path=os.path.join(settings_mod.DIR, "webview"), icon=ICON_ICO)
        if not self.quitting:  # window closed without tray (close_to_tray off)
            self.quit()


def main(argv=None):
    ap = argparse.ArgumentParser(prog="EVision RGB")
    ap.add_argument("--background", action="store_true", help="start in the tray (for autostart)")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    a = ap.parse_args(argv)

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
    except Exception:
        _log("fatal:", traceback.format_exc())
        raise
    return 0


if __name__ == "__main__":
    sys.exit(main())

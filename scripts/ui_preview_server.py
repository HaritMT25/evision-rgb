"""Serve the desktop app's UI against the simulated keyboard (tests/fake_device.py), for UI work and
UI smoke tests without hardware. Uses a throw-away settings folder; never touches a real keyboard.

    python scripts/ui_preview_server.py [port]      then open http://127.0.0.1:<port>/
"""
import datetime as dt
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "tests")]

import fake_device  # noqa: E402
from evision_rgb import settings as st  # noqa: E402


class _NoCleanup:  # fake_device.install() expects a unittest.TestCase
    def addCleanup(self, *a, **k):
        pass


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8899
    tmp = tempfile.mkdtemp(prefix="evrgb-ui-")
    st.DIR, st.PATH = tmp, os.path.join(tmp, "settings.json")
    fake_device.install(_NoCleanup())
    from evision_rgb.server import Controller, build_server
    neutral = {"caps": lambda: False, "mic_muted": lambda: False, "idle": lambda: 0.0, "locked": lambda: False,
               "now": lambda: dt.datetime(2026, 1, 1, 12)}
    ctl = Controller(probes=neutral, hook_keys=False)
    httpd = build_server(ctl, port)
    print(f"UI preview (simulated keyboard, settings in {tmp}) on http://127.0.0.1:{port}/", flush=True)
    try:
        httpd.serve_forever()
    finally:
        ctl.close()


if __name__ == "__main__":
    main()

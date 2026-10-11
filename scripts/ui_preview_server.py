"""Serve the desktop app's UI against the simulated keyboard (tests/fake_device.py), for UI work and
UI smoke tests without hardware. Uses a throw-away settings folder; never touches a real keyboard or pad.

    python scripts/ui_preview_server.py [port] [--pad akp03e-rev2]    then open http://127.0.0.1:<port>/
--pad also simulates a connected macro pad (tests/fake_pad.py); without it no pad is connected.
"""
import argparse
import datetime as dt
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "tests")]

import fake_device  # noqa: E402
from evision_rgb import settings as st  # noqa: E402


class _NoCleanup:  # the fakes' install() helpers expect a unittest.TestCase
    def addCleanup(self, *a, **k):
        pass


def demo_player():
    """'Now playing' for the preview: a made-up song with a two-colour cover."""
    import io
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (120, 120), "#ff3060")
    ImageDraw.Draw(im).rectangle([60, 0, 119, 119], fill="#2050ff")
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return {"app": "Spotify.exe", "title": "Demo Song", "artist": "Preview Band", "album": "Simulated",
            "playing": True, "cover": buf.getvalue}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("port", nargs="?", type=int, default=8899)
    ap.add_argument("--pad", help="simulate this macro pad model, e.g. akp03e-rev2 or akp153")
    a = ap.parse_args()
    tmp = tempfile.mkdtemp(prefix="evrgb-ui-")
    st.DIR, st.PATH = tmp, os.path.join(tmp, "settings.json")
    fake_device.install(_NoCleanup())
    import fake_pad
    fake_pad.install_pads(_NoCleanup(), *([fake_pad.FakePad(a.pad)] if a.pad else []))  # never a real pad
    from evision_rgb.server import Controller, build_server
    neutral = {"caps": lambda: False, "mic_muted": lambda: False, "idle": lambda: 0.0, "locked": lambda: False,
               "now": lambda: dt.datetime(2026, 1, 1, 12)}
    from evision_rgb import games, media
    media.set_source(media.NowPlaying(fetch=demo_player))  # a pretend song, never what this PC plays
    steam = os.path.join(tmp, "Steam")  # a pretend Steam with CS2 installed: "Set up" writes there, never into real games
    os.makedirs(os.path.join(steam, "steamapps", "common", "Counter-Strike Global Offensive", "game", "csgo", "cfg"))
    games.steam_path = lambda: steam
    ctl = Controller(probes=neutral, hook_keys=False)
    ctl.pads.send_keys = lambda keys: print("pad would press", "+".join(keys), flush=True)
    ctl.pads.launch = lambda target: print("pad would open", target, flush=True)
    ctl.start_pads()
    httpd = build_server(ctl, a.port)
    print(f"UI preview (simulated keyboard{', pad ' + a.pad if a.pad else ''}; settings in {tmp}) "
          f"on http://127.0.0.1:{a.port}/", flush=True)
    try:
        httpd.serve_forever()
    finally:
        ctl.close()


if __name__ == "__main__":
    main()

"""Command line: python -m evision_rgb <command> ...

  info                                   show device + current hardware effect
  keys                                   list key names
  effect MODE [-b 0-4] [-s 0-4] [-c RRGGBB] [-d N]     persistent hardware effect
  custom [--all COLOR] [KEY=COLOR ...] [--colorset 0-2]  persistent per-key colours
  live EFFECT [param=value ...]          run a live effect until Ctrl+C (rainbow, reactive, music, sysmon, ...)
  apps PROFILE.json                      app-aware lighting (switch effect by foreground program)
  serve [--port 8820]                    run the HTTP API
  restore BACKUP_DIR                     restore custom colorsets 0-2 + config from a backup folder
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time


def _val(v: str):
    for conv in (int, float):
        try:
            return conv(v)
        except ValueError:
            pass
    return {"true": True, "false": False}.get(v.lower(), v)


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:  # window titles / app names may not fit the console code page
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    p = argparse.ArgumentParser(prog="evrgb", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("info")
    sub.add_parser("keys")
    e = sub.add_parser("effect")
    e.add_argument("mode")
    e.add_argument("-b", "--brightness", type=int)
    e.add_argument("-s", "--speed", type=int)
    e.add_argument("-c", "--color")
    e.add_argument("-d", "--direction", type=int)
    e.add_argument("--random", action="store_true", default=None)
    c = sub.add_parser("custom")
    c.add_argument("--all", default="000000", help="colour for keys not listed")
    c.add_argument("--colorset", type=int, default=0)
    c.add_argument("pairs", nargs="*", help="KEY=RRGGBB")
    lv = sub.add_parser("live")
    lv.add_argument("effect")
    lv.add_argument("params", nargs="*", help="name=value")
    lv.add_argument("--seconds", type=float, default=0, help="stop after N s (0 = until Ctrl+C)")
    ap = sub.add_parser("apps")
    ap.add_argument("profile")
    s = sub.add_parser("serve")
    s.add_argument("--port", type=int, default=8820)
    r = sub.add_parser("restore")
    r.add_argument("backup_dir")
    a = p.parse_args(argv)

    if a.cmd == "keys":
        from .layout import KEYS
        print(" ".join(KEYS))
        return
    if a.cmd == "serve":
        from .server import serve
        serve(a.port)
        return

    from .keyboard import Keyboard
    with Keyboard() as kb:
        if a.cmd == "info":
            print(json.dumps(kb.info(), indent=2))
        elif a.cmd == "effect":
            print(json.dumps(kb.set_effect(a.mode, a.brightness, a.speed, a.color, a.direction, a.random), indent=2))
        elif a.cmd == "custom":
            colors = dict(pair.split("=", 1) for pair in a.pairs)
            kb.set_custom(colors, a.all, a.colorset)
            print(json.dumps(kb.info(), indent=2))
        elif a.cmd == "restore":
            from .protocol import CMD_WRITE_CONFIG
            for cs in range(3):
                kb.set_custom(open(os.path.join(a.backup_dir, f"custom{cs}.bin"), "rb").read(), colorset=cs, activate=False)
            cfg = open(os.path.join(a.backup_dir, "config.bin"), "rb").read()[:0x1D]
            kb.dev.begin(); kb.dev.write_block(CMD_WRITE_CONFIG, cfg, 0); kb.dev.end()
            if cfg[1] == 0x14:  # custom: refresh the firmware's per-key display buffer
                kb.set_custom(kb.dev.read_custom(0), colorset=0)
            print(json.dumps(kb.info(), indent=2))
        elif a.cmd == "apps":
            from .apps import AppProfiles
            from .effects import Engine
            eng = Engine(kb)
            prof = AppProfiles.from_file(eng, a.profile).start()
            last = None
            try:
                while True:
                    time.sleep(0.5)
                    if prof.app != last:
                        last = prof.app
                        print(f"{prof.app or '?'} -> {(prof.current or {}).get('effect', 'hardware effect')}", flush=True)
            except KeyboardInterrupt:
                pass
            finally:
                prof.stop()
                eng.stop()
        elif a.cmd == "live":
            from .effects import Engine
            eng = Engine(kb)
            params = {k: _val(v) for k, v in (x.split("=", 1) for x in a.params)}
            print(eng.run(a.effect, **params))
            t0 = time.monotonic()
            try:
                while not a.seconds or time.monotonic() - t0 < a.seconds:
                    time.sleep(1)
                    st = eng.status()
                    print(f"\r{st['effect']}  {st['fps']:5.1f} fps  frames={st['frames_sent']}"
                          + (f"  ERR {st['stream_error'] or st['audio_error']}" if st['stream_error'] or st['audio_error'] else ""),
                          end="", flush=True)
            except KeyboardInterrupt:
                pass
            finally:
                eng.stop()
                print()


if __name__ == "__main__":
    sys.exit(main())

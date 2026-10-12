"""Behaviour spec — the local API and the OpenRGB server hold up against misuse. Written BEFORE the fixes, from a
read-through of the code looking for security problems (2026-10-12):

- Any web page could make the app read an unlimited request body into memory before refusing the request: a page's
  text/plain POST is sent without asking first, and the app read Content-Length bytes before looking at who asked
  (a negative length read until the sender hung up). Now no body over 1 MiB is read, and a refused request's body
  only a little (so a small one still gets its answer instead of a reset connection).
- An OpenRGB program sends UpdateMode for every step of a slider it drags, and the keyboard keeps every effect
  change in its flash: each step was a write. Now changes are stored once they rest (and about once a second while
  they go on).
- POST /restore {"dir": ...} wrote any folder's config.bin to the keyboard as it was. Now only the app's own backup.
- /close_conflict ended any program whose name contained a listed one, "nexus" too, though the window only offers
  to close some of them. Now only those.
- GET /settings (and the reply to POST /settings) showed the token games use to post their state.
- The window's files were found by a prefix check: a folder next to them whose name starts the same was served too.
"""
import json
import os
import shutil
import socket
import struct
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import warnings

from fake_device import install
import test_feature_openrgb as orgb

LIMIT = 1 << 20  # the largest request body the app reads (a profile, the biggest real request, is at most 256 KB)


def wait_for(cond, secs=3.0, step=0.02):
    end = time.monotonic() + secs
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(step)
    return bool(cond())


class _App(unittest.TestCase):
    def setUp(self):
        warnings.simplefilter("ignore", ResourceWarning)
        self.kb = install(self)
        import evision_rgb.settings as st
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        for attr, val in (("DIR", self.tmp), ("PATH", os.path.join(self.tmp, "settings.json"))):
            self.addCleanup(setattr, st, attr, getattr(st, attr))
            setattr(st, attr, val)
        import datetime as _dt
        from evision_rgb.server import Controller, build_server
        self.ctl = Controller(probes={"now": lambda: _dt.datetime(2026, 1, 1, 12)}, hook_keys=False)
        self.httpd = build_server(self.ctl, 0)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(lambda: (self.httpd.shutdown(), self.ctl.close()))

    def call(self, path, body=None):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=data,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            with e:
                return e.code, json.loads(e.read() or b"{}")

    def raw(self, method, path, headers, body=b"", wait=4.0):
        """A request written by hand: the path as it is, these headers, then `body` (maybe less than its
        Content-Length says), the connection left open. The reply's status, or None if none came in `wait` s."""
        s = socket.create_connection(("127.0.0.1", self.port), timeout=wait)
        try:
            head = [f"{method} {path} HTTP/1.1", f"Host: 127.0.0.1:{self.port}"] + [f"{k}: {v}" for k, v in headers.items()]
            s.sendall(("\r\n".join(head) + "\r\n\r\n").encode() + body)
            got = b""
            while b"\r\n" not in got:
                chunk = s.recv(4096)
                if not chunk:
                    break
                got += chunk
        except OSError:  # no reply in time (or the connection was reset)
            return None
        finally:
            s.close()
        return int(got.split(b" ")[1]) if got.startswith(b"HTTP/") else None


# ------------------------------------------------------------------------------ request bodies
class RequestBodies(_App):
    JSON = {"Content-Type": "application/json"}

    def test_a_body_bigger_than_any_request_is_refused_unread(self):
        t = time.monotonic()
        code = self.raw("POST", "/stop", {**self.JSON, "Content-Length": 50 << 20}, b'{"x": 1')  # claims 50 MB
        self.assertEqual(code, 413)
        self.assertLess(time.monotonic() - t, 3.0, "answered without waiting for the rest")
        self.assertEqual(self.raw("POST", "/stop", {**self.JSON, "Content-Length": LIMIT + 1}, b"{}"), 413)

    def test_a_negative_length_is_refused(self):  # it read until the sender hung up
        self.assertEqual(self.raw("POST", "/stop", {**self.JSON, "Content-Length": -1}, b"{}"), 400)

    def test_a_web_page_is_refused_before_its_body_is_read(self):
        page = {"Content-Type": "text/plain", "Origin": "https://evil.example", "Content-Length": 900_000}
        self.assertEqual(self.raw("POST", "/run", page, b"x" * 1000), 403)  # (the rest never comes)
        plain = {"Content-Type": "text/plain", "Content-Length": 900_000}
        self.assertEqual(self.raw("POST", "/run", plain, b"x" * 1000), 415)
        self.assertIsNone(self.ctl.engine.status()["effect"])

    def test_bodies_up_to_the_limit_still_work(self):
        body = b'{"note": "' + b"x" * (LIMIT - 12) + b'"}'
        self.assertEqual(len(body), LIMIT)
        self.assertEqual(self.raw("POST", "/stop", {**self.JSON, "Content-Length": len(body)}, body, wait=10), 200)
        self.assertEqual(self.call("/run", {"effect": "rainbow", "note": "x" * 300_000})[0], 200)


# ------------------------------------------------------------------------------ OpenRGB effect changes
def mode_packet(i, m, **change):
    b = orgb.mode_data(m, 4, **change)
    return struct.pack("<Ii", 8 + len(b), i) + b


class OpenRGBEffectChanges(orgb._Server):
    def stores(self):
        """Config writes the keyboard got (each one is kept in its flash)."""
        return sum(1 for p in self.kb.writes if len(p) == 64 and p[3] == 0x06)

    def breathing(self, c):
        d = self.controller(c)
        i = next(k for k, m in enumerate(d["modes"]) if m["name"] == "Breathing")
        return i, d["modes"][i]

    def test_a_slider_drag_is_stored_once_it_rests(self):
        c = self.client(4)
        i, m = self.breathing(c)
        before = self.stores()
        for k in range(20):  # 20 steps in 0.4 s
            c.send(orgb.UPDATE_MODE, mode_packet(i, m, brightness=k % 5, colors=[(0, 0, 255 - k)]))
            time.sleep(0.02)
        c.send(orgb.UPDATE_MODE, mode_packet(i, m, brightness=1, colors=[(0, 255, 0)]))
        last = ("effect", 0x05, b"\x00\xff\x00", 1)
        self.assertTrue(wait_for(lambda: self.kb.visible() == last), "the last step is stored")
        time.sleep(0.5)
        self.assertEqual(self.kb.visible(), last)
        self.assertLessEqual(self.stores() - before, 3, "not once per step")

    def test_a_long_drag_still_shows_while_it_goes_on(self):
        c = self.client(4)
        i, m = self.breathing(c)
        before, seen = self.stores(), False
        t = time.monotonic()
        while time.monotonic() - t < 2.5:
            c.send(orgb.UPDATE_MODE, mode_packet(i, m, brightness=2, colors=[(255, 0, 0)]))
            time.sleep(0.05)
            seen = seen or self.kb.visible()[:2] == ("effect", 0x05)
        self.assertTrue(seen, "stored while the client kept sending")
        self.assertLessEqual(self.stores() - before, 5, "about once a second")

    def test_the_latest_change_wins(self):
        c = self.client(4)
        i, m = self.breathing(c)
        c.send(orgb.UPDATE_MODE, mode_packet(i, m, brightness=1, colors=[(255, 0, 0)]))
        c.send(orgb.SAVE_MODE, mode_packet(i, m, brightness=3, colors=[(0, 0, 255)]))
        blue = ("effect", 0x05, b"\x00\x00\xff", 3)
        self.assertTrue(wait_for(lambda: self.kb.visible() == blue))
        time.sleep(0.8)
        self.assertEqual(self.kb.visible(), blue)

    def test_switching_to_direct_drops_a_waiting_effect_change(self):
        c = self.client(4)
        i, m = self.breathing(c)
        n = len(self.controller(c)["leds"])
        c.send(orgb.UPDATE_MODE, mode_packet(i, m, colors=[(255, 0, 0)]))
        c.send(orgb.CUSTOM_MODE)
        c.send(orgb.UPDATE_LEDS, orgb.leds_packet([orgb.color(0, 255, 0)] * n))
        self.assertTrue(wait_for(lambda: orgb.key(self.kb, "Q") == ("direct", (0, 255, 0))))
        time.sleep(0.8)  # past the time the effect change would have been stored
        self.assertEqual(self.effect(), "openrgb", "the client's colours stay")
        self.assertNotEqual(self.kb.applied[0][1], 0x05, "Breathing was never stored")


# ------------------------------------------------------------------------------ restore, closing programs, secrets, files
class Restore(_App):
    def test_only_the_apps_own_backup_is_restored(self):
        elsewhere = os.path.join(self.tmp, "elsewhere")
        os.makedirs(elsewhere)
        cfg = bytearray(self.kb.config[:0x1D])
        cfg[1], cfg[2] = 0x05, 5  # brightness 5 turns the LEDs off
        for name, data in (("config.bin", bytes(cfg)), *((f"custom{i}.bin", bytes(384)) for i in range(3))):
            with open(os.path.join(elsewhere, name), "wb") as f:
                f.write(data)
        before = (bytes(self.kb.config), [bytes(a) for a in self.kb.areas])
        code, res = self.call("/restore", {"dir": elsewhere})
        self.assertEqual(code, 400, res)
        self.assertEqual((bytes(self.kb.config), [bytes(a) for a in self.kb.areas]), before, "nothing written")
        self.assertEqual(self.call("/restore", {})[0], 200, "the app's own backup still restores")


class ClosingPrograms(unittest.TestCase):
    """The window offers to close only some of the programs it warns about (NexusService runs as a Windows service;
    a name like "nexus" is also in programs that have nothing to do with RGB)."""

    def setUp(self):
        from evision_rgb import system
        self.system, self.running, self.closed = system, {}, []
        test = self

        class Proc:
            def __init__(self, pid):
                self.pid, self.info = pid, {"pid": pid, "name": test.running[pid]}

            def name(self):
                return test.running[self.pid]

            def terminate(self):
                test.closed.append(self.name())

            def wait(self, timeout=None):
                return 0

            kill = terminate

        class FakePsutil:
            Process = Proc
            AccessDenied = type("AccessDenied", (Exception,), {})
            TimeoutExpired = type("TimeoutExpired", (Exception,), {})

            @staticmethod
            def process_iter(attrs=None):
                return [Proc(pid) for pid in test.running]

        self.addCleanup(setattr, system, "psutil", system.psutil)
        system.psutil = FakePsutil

    def test_only_what_the_window_offers_to_close_can_be_closed(self):
        self.running = {100 + i: f"{k}-thing.exe" for i, k in enumerate(self.system.CONFLICTS)}
        found = self.system.conflicts()
        self.assertEqual(len(found), len(self.running))
        for c in found:
            self.closed.clear()
            if c["closable"]:
                self.assertTrue(self.system.close_process(c["pid"]))
                self.assertEqual(self.closed, [c["name"]])
            else:
                with self.assertRaises(ValueError):
                    self.system.close_process(c["pid"])
                self.assertEqual(self.closed, [], c["name"])

    def test_nexus_is_never_closed(self):
        self.running = {7: "NexusService.exe", 8: "Nexus Mods App.exe"}
        for pid in self.running:
            with self.assertRaises(ValueError):
                self.system.close_process(pid)
        self.assertEqual(self.closed, [])


class GameToken(_App):
    def test_the_settings_never_show_the_games_token(self):
        token = self.ctl.games_settings()["token"]
        code, s = self.call("/settings")
        self.assertEqual(code, 200)
        self.assertNotIn(token, json.dumps(s))
        self.assertIn("auto", s["games"], "the rest of the game settings are there")
        code, s = self.call("/settings", {"close_to_tray": False})
        self.assertEqual(code, 200)
        self.assertNotIn(token, json.dumps(s), "nor the reply to a change")

    def test_sending_the_settings_back_keeps_the_token(self):
        token = self.ctl.games_settings()["token"]
        games = self.call("/settings")[1]["games"]
        self.assertEqual(self.call("/settings", {"games": {**games, "auto": False}})[0], 200)
        self.assertEqual(self.ctl.games_settings()["token"], token, "the games' config files still match")
        self.assertFalse(self.ctl.games_settings()["auto"])


class WindowFiles(_App):
    def test_only_the_windows_own_folder_is_served(self):
        from evision_rgb import server
        web = os.path.join(self.tmp, "gui", "web")
        os.makedirs(web)
        os.makedirs(os.path.join(self.tmp, "gui", "web-private"))
        with open(os.path.join(web, "index.html"), "w") as f:
            f.write("<title>EVision RGB</title>")
        with open(os.path.join(self.tmp, "gui", "web-private", "secret.txt"), "w") as f:
            f.write("secret")
        self.addCleanup(setattr, server, "WEB", server.WEB)
        server.WEB = web
        self.assertEqual(self.raw("GET", "/ui/index.html", {}), 200)
        self.assertEqual(self.raw("GET", "/ui/../web-private/secret.txt", {}), 404)


if __name__ == "__main__":
    unittest.main()

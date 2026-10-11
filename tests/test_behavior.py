"""Behaviour specification for the AK820 driver, app server and effects — run against the firmware
simulator in fake_device.py (no keyboard needed):

    .venv\\Scripts\\python -m unittest discover -s tests -v

Each test name states the required behaviour.
"""
import json
import os
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import warnings

from fake_device import FakeKeyboard, install

from evision_rgb import protocol as P
from evision_rgb.keyboard import CONFIG_LEN, Keyboard, frame_from
from evision_rgb.layout import KEYS


def solid(rgb: str) -> bytes:
    return bytes.fromhex(rgb) * 128


def visible_custom(colors_by_key: dict, default="000000") -> tuple:
    return ("custom", bytes(frame_from(colors_by_key, default)))


# =============================================================================== protocol
class ProtocolSafety(unittest.TestCase):
    def setUp(self):
        self.kb = install(self)

    def test_packets_are_report4_with_valid_checksum_and_echo_is_verified(self):
        with P.AK820() as dev:
            dev.capabilities()
        pkt = self.kb.writes[-1]
        self.assertEqual(pkt[0], 4)
        self.assertEqual(sum(pkt[3:]) & 0xFFFF, pkt[1] | pkt[2] << 8)
        self.assertEqual(self.kb.bad_checksums, 0)

    def test_commands_outside_the_allow_list_are_never_sent(self):
        with P.AK820(allow_writes=True) as dev:
            for cmd in (0x04, 0x08, 0x09, 0x15, 0x17, 0x1A, 0x1C, 0xAA, 0x11):
                with self.assertRaises(P.ProtocolError):
                    dev.query(cmd)
        self.assertEqual(self.kb.writes, [])

    def test_write_commands_need_explicit_opt_in(self):
        with P.AK820() as dev:
            with self.assertRaises(P.ProtocolError):
                dev.query(P.CMD_WRITE_CONFIG, 1, 0, b"\x00")
        self.assertEqual(self.kb.writes, [])

    def test_lost_echo_is_retried(self):
        self.kb.drop_echoes = 1
        with P.AK820() as dev:
            self.assertEqual(dev.capabilities()[:2], b"\xaa\x55")
        self.assertEqual(len(self.kb.writes), 2)

    def test_permanently_silent_device_raises_instead_of_hanging(self):
        self.kb.drop_echoes = 99
        with P.AK820() as dev:
            t = time.monotonic()
            with self.assertRaises(P.ProtocolError):
                dev.capabilities()
        self.assertLess(time.monotonic() - t, 3)

    def test_echoes_from_other_programs_are_ignored_and_counted(self):
        foreign = bytearray(64); foreign[0] = 4; foreign[3] = 0x11; foreign[7] = 0xFF  # OpenRGB V1 spam
        self.kb.foreign_before_reply = [bytes(foreign)] * 3
        with P.AK820() as dev:
            self.assertEqual(dev.capabilities()[:2], b"\xaa\x55")
            self.assertEqual(dev.foreign, 3)

    def test_same_command_echo_from_another_program_is_not_mistaken_for_ours(self):
        # e.g. the AJAZZ app reading config at the same time: same cmd byte, other offset/data
        from evision_rgb.protocol import build
        other = bytearray(build(P.CMD_READ_CONFIG, 8, 0x40)); other[8:16] = b"\xee" * 8
        self.kb.foreign_before_reply = [bytes(other)]
        with P.AK820() as dev:
            self.assertEqual(dev.read_config(8), bytes(self.kb.config[:8]))

    def test_device_error_status_raises(self):
        with P.AK820() as dev:
            # forge an allowed command the simulator rejects by making it reply status 0xFF
            orig = self.kb.handle

            def reject(pkt):
                orig(pkt)
                r = bytearray(self.kb.rx.pop()); r[7] = 0xFF; self.kb.rx.append(bytes(r))
            self.kb.handle = reject
            with self.assertRaises(P.ProtocolError):
                dev.capabilities()


# =============================================================================== keyboard
class HardwareEffects(unittest.TestCase):
    def setUp(self):
        self.kb = install(self)
        self.k = Keyboard()
        self.addCleanup(self.k.close)

    def test_set_effect_changes_what_the_keyboard_shows(self):
        self.k.set_effect("breath", brightness=2, color="ff0000", speed=1)
        self.assertEqual(self.kb.visible(), ("effect", 0x05, b"\xff\x00\x00", 2))

    def test_set_effect_preserves_unknown_config_bytes(self):
        self.kb.config[19:29] = bytes(range(1, 11))
        self.k.set_effect("normal", color="0000ff")
        self.assertEqual(bytes(self.kb.config[19:29]), bytes(range(1, 11)))

    def test_brightness_5_is_rejected_because_it_turns_the_leds_off(self):
        n = len(self.kb.writes)
        with self.assertRaises(ValueError):
            self.k.set_effect("normal", brightness=5)
        self.assertEqual(len(self.kb.writes), n, "nothing may be sent for invalid input")
        self.assertNotEqual(self.kb.visible(), ("off",))

    def test_unknown_mode_names_are_rejected(self):
        with self.assertRaises(ValueError):
            self.k.set_effect("disco")

    def test_aliases_map_to_vendor_modes(self):
        self.assertEqual(self.k.set_effect("static")["mode"], "normal")


class CustomColours(unittest.TestCase):
    def setUp(self):
        self.kb = install(self)
        self.k = Keyboard()
        self.addCleanup(self.k.close)
        self.pattern = {k: ("00ff00" if x < 0.5 else "ff00ff") for k, (s, x, y) in KEYS.items()}

    def test_saving_from_a_non_custom_mode_shows_exactly_the_painted_colours(self):
        self.k.set_effect("normal", color="ff8000")
        self.k.set_custom(self.pattern)
        self.assertEqual(self.kb.visible(), visible_custom(self.pattern))

    def test_saving_to_group_2_still_shows_it_because_firmware_only_displays_area_0(self):
        self.k.set_custom(self.pattern, colorset=1)
        self.assertEqual(self.kb.visible(), visible_custom(self.pattern))
        self.assertEqual(bytes(self.kb.areas[1]), bytes(frame_from(self.pattern)), "mirrored into area 1")

    def test_switching_into_custom_never_shows_a_stale_or_previous_frame(self):
        self.kb.areas[0][:] = solid("ff0000")         # old layout on the keyboard
        self.k.set_effect("normal", color="0000ff")
        self.kb.history.clear()
        self.k.set_custom(self.pattern)
        self.assertEqual(self.kb.history, [visible_custom(self.pattern)],
                         "exactly one visible change: straight to the new layout")

    def test_activating_custom_mode_shows_whatever_is_stored(self):
        self.kb.areas[0][:] = solid("123456")
        self.k.set_effect("custom")
        self.assertEqual(self.kb.visible(), ("custom", solid("123456")))

    def test_store_without_activation_keeps_current_effect(self):
        before = self.kb.visible()
        self.k.set_custom(self.pattern, colorset=2, activate=False)
        self.assertEqual(self.kb.visible(), before)
        self.assertEqual(bytes(self.kb.areas[2]), bytes(frame_from(self.pattern)))

    def test_get_custom_returns_colours_by_key_name(self):
        self.k.set_custom({"W": "abcdef"})
        self.assertEqual(self.k.get_custom()["W"], "abcdef")
        self.assertEqual(self.k.get_custom()["Q"], "000000")


class LiveStreaming(unittest.TestCase):
    def setUp(self):
        self.kb = install(self)
        self.k = Keyboard()
        self.addCleanup(self.k.close)

    def test_stream_shows_frames_and_returns_to_stored_effect_on_close(self):
        before = self.kb.visible()
        s = self.k.stream()
        s.fill("ff0000"); s.set("W", "ffffff"); s.show()
        time.sleep(0.2)
        v = self.kb.visible()
        self.assertEqual(v[0], "direct")
        self.assertEqual(v[1][KEYS["W"][0] * 3:KEYS["W"][0] * 3 + 3], b"\xff\xff\xff")
        s.close()
        self.assertEqual(self.kb.visible(), before)

    def test_stream_keepalive_holds_the_frame_longer_than_the_firmware_timeout(self):
        s = self.k.stream()
        s.fill("00ff00"); s.show()
        time.sleep(FakeKeyboard.DIRECT_TIMEOUT * 3)
        self.assertEqual(self.kb.visible()[0], "direct")
        s.close()

    def test_if_the_app_dies_the_keyboard_falls_back_by_itself(self):
        s = self.k.stream()
        s.fill("00ff00"); s.show(); time.sleep(0.2)
        s._stop.set()  # simulate a crash: thread stops, no 0x13 sent
        time.sleep(FakeKeyboard.DIRECT_TIMEOUT + 0.2)
        self.assertEqual(self.kb.visible()[0], "effect")

    def test_unknown_key_names_raise_with_a_helpful_message(self):
        s = self.k.stream()
        self.addCleanup(s.close)
        with self.assertRaises(KeyError):
            s.set("NumLock", "ffffff")


# =============================================================================== layout
class Layout(unittest.TestCase):
    def test_every_key_has_a_unique_led_slot_and_a_position(self):
        slots = [s for s, _, _ in KEYS.values()]
        self.assertEqual(len(slots), len(set(slots)))
        for name, (s, x, y) in KEYS.items():
            self.assertTrue(0 <= x <= 1 and 0 <= y <= 1, name)

    def test_gui_layout_matches_driver_layout(self):
        with open(os.path.join(os.path.dirname(__file__), "..", "evision_rgb", "gui", "web", "layout.json")) as f:
            ui = {k["name"] for k in json.load(f)["keys"]}
        self.assertEqual(ui, set(KEYS))


# =============================================================================== app server
class ServerAPI(unittest.TestCase):
    """The HTTP API used by the GUI, MCP and agents."""

    def setUp(self):
        warnings.simplefilter("ignore", ResourceWarning)
        self.kb = install(self)
        import evision_rgb.settings as st
        tmp = tempfile.mkdtemp()
        for attr, val in (("DIR", tmp), ("PATH", os.path.join(tmp, "settings.json"))):
            self.addCleanup(setattr, st, attr, getattr(st, attr))
            setattr(st, attr, val)
        from evision_rgb.server import Controller, build_server
        import datetime as _dt
        neutral = {"caps": lambda: False, "mic_muted": lambda: False, "idle": lambda: 0.0,
                   "locked": lambda: False, "now": lambda: _dt.datetime(2026, 1, 1, 12)}
        self.ctl = Controller(probes=neutral)  # never depend on the real PC's Caps Lock / lock state
        self._writes_at_start = len(self.kb.writes)
        self.httpd = build_server(self.ctl, 0)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self._shutdown)

    def _shutdown(self):
        self.httpd.shutdown()
        self.ctl.close()

    def call(self, path, body=None):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=data,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                raw = r.read()
                return r.status, (json.loads(raw) if r.headers.get_content_type() == "application/json" else raw)
        except urllib.error.HTTPError as e:
            with e:
                return e.code, json.loads(e.read() or b"{}")

    def test_invalid_hardware_effect_is_400_and_does_not_interrupt_a_live_effect(self):
        self.call("/run", {"effect": "rainbow"})
        code, body = self.call("/effect", {"mode": "normal", "brightness": 9})
        self.assertEqual(code, 400)
        self.assertIn("brightness", body["error"])
        self.assertEqual(self.call("/status")[1]["effect"], "rainbow")

    def test_unknown_live_effect_is_400(self):
        self.assertEqual(self.call("/run", {"effect": "nope"})[0], 400)

    def test_saving_custom_while_previewing_goes_straight_to_the_saved_layout(self):
        pattern = {"W": "ff0000", "A": "00ff00", "S": "0000ff", "D": "ffffff"}
        self.call("/run", {"effect": "solid", "params": {"color": "000000", "keys": pattern}, "remember": False})
        time.sleep(0.2)
        self.kb.history.clear()
        code, info = self.call("/custom", {"colors": pattern, "colorset": 0})
        self.assertEqual(code, 200)
        self.assertEqual(info["mode"], "custom")
        self.assertFalse(self.call("/status")[1]["live"])
        final = visible_custom(pattern)
        self.assertEqual(self.kb.visible(), final)
        self.assertNotIn(("effect", 0x06, b"\x00\xff\x00", 3), self.kb.history, "old effect must not flash")
        self.assertEqual([h for h in self.kb.history if h[0] != "direct"], [final])

    def test_previews_are_not_remembered_but_real_live_effects_are(self):
        self.call("/run", {"effect": "solid", "params": {"color": "ffffff"}, "remember": False})
        self.assertIsNone(self.call("/settings")[1]["last_live"])
        self.call("/run", {"effect": "rainbow", "params": {"speed": 0.5}})
        self.assertEqual(self.call("/settings")[1]["last_live"], {"effect": "rainbow", "params": {"speed": 0.5}})
        self.call("/stop", {})
        self.assertIsNone(self.call("/settings")[1]["last_live"])

    def test_frame_mirrors_live_custom_and_hardware_states(self):
        self.call("/effect", {"mode": "normal", "color": "112233", "brightness": 2})
        f = self.call("/frame")[1]
        self.assertEqual((f["source"], f["colors"]["Esc"]), ("hardware", "112233"))
        self.call("/custom", {"colors": {"Esc": "ff0000"}})
        f = self.call("/frame")[1]
        self.assertEqual((f["source"], f["colors"]["Esc"], f["colors"]["Q"]), ("custom", "ff0000", "000000"))
        self.call("/run", {"effect": "solid", "params": {"color": "00ff00"}})
        time.sleep(0.3)
        f = self.call("/frame")[1]
        self.assertEqual((f["source"], f["colors"]["Q"]), ("live", "00ff00"))

    def test_live_key_overrides_sit_on_top_of_the_effect_and_clear(self):
        self.call("/run", {"effect": "solid", "params": {"color": "000000"}})
        self.call("/keys", {"colors": {"Esc": "ff0000"}})
        time.sleep(0.3)
        self.assertEqual(self.call("/frame")[1]["colors"]["Esc"], "ff0000")
        self.call("/clear", {})
        time.sleep(0.3)
        self.assertEqual(self.call("/frame")[1]["colors"]["Esc"], "000000")

    def test_settings_persist_to_disk(self):
        self.call("/settings", {"close_to_tray": False, "custom_active": 2})
        import evision_rgb.settings as st
        self.assertEqual(st.load()["custom_active"], 2)
        self.assertFalse(st.load()["close_to_tray"])

    def test_gui_files_are_served_and_path_traversal_is_blocked(self):
        code, body = self.call("/")
        self.assertEqual(code, 200)
        self.assertIn(b"EVision RGB", body)
        self.assertEqual(self.call("/ui/app.js")[0], 200)
        self.assertEqual(self.call("/ui/../server.py")[0], 404)
        self.assertEqual(self.call("/ui/..%2f..%2fserver.py")[0], 404)

    def test_close_conflict_refuses_programs_that_are_not_rgb_software(self):
        code, _ = self.call("/close_conflict", {"pid": os.getpid()})
        self.assertEqual(code, 400)

    def test_first_start_backs_up_the_keyboard_before_anything_is_written(self):
        import evision_rgb.settings as st
        bdir = os.path.join(st.DIR, "backup")
        for name in ("config.bin", "custom0.bin", "custom1.bin", "custom2.bin"):
            self.assertTrue(os.path.isfile(os.path.join(bdir, name)), name)
        writes = [w for w in self.kb.writes[:self._writes_at_start] if w[3] in (0x06, 0x0B, 0x12)]
        self.assertEqual(writes, [], "backup must happen before any write")

    def test_backup_is_never_overwritten_by_later_starts(self):
        import evision_rgb.settings as st
        from evision_rgb.server import Controller
        path = os.path.join(st.DIR, "backup", "config.bin")
        with open(path, "rb") as f:
            original = f.read()
        self.call("/effect", {"mode": "breath", "color": "ffffff"})
        Controller(self.ctl.engine, probes=self.ctl.automation.probes)._auto_stop.set()  # a second start
        with open(path, "rb") as f:
            self.assertEqual(f.read(), original)

    def test_restore_puts_back_the_lighting_from_before_first_use(self):
        self.kb.areas[0][:] = solid("00aa00")
        before = self.kb.visible()
        self.call("/effect", {"mode": "normal", "color": "ffffff"})
        self.call("/custom", {"colors": {"W": "ff0000"}})
        code, _ = self.call("/restore", {})
        self.assertEqual(code, 200)
        self.assertEqual(self.kb.visible(), before)


# =============================================================================== app profiles
class AppProfilesRules(unittest.TestCase):
    def setUp(self):
        from evision_rgb.apps import AppProfiles
        self.p = AppProfiles(None, {
            "default": {"effect": "rainbow"},
            "rules": [{"match": "Spotify", "effect": "music"},
                      {"match": "code.exe", "effect": "reactive"},
                      {"match": "code", "effect": "solid"},
                      {"match": "game", "effect": None}]})

    def test_match_is_case_insensitive_substring_of_process_or_title(self):
        self.assertEqual(self.p.pick("spotify.exe", "")["effect"], "music")
        self.assertEqual(self.p.pick("chrome.exe", "Spotify – Web Player")["effect"], "music")

    def test_first_matching_rule_wins(self):
        self.assertEqual(self.p.pick("Code.exe", "")["effect"], "reactive")

    def test_unmatched_apps_use_the_default(self):
        self.assertEqual(self.p.pick("explorer.exe", "")["effect"], "rainbow")

    def test_a_rule_without_effect_means_keyboard_effect(self):
        class Eng:
            stopped = False
            def stop(self): self.stopped = True
            def run(self, *a, **k): raise AssertionError("must not start a live effect")
        e = Eng(); self.p.engine = e
        self.p._apply(self.p.pick("mygame.exe", ""))
        self.assertTrue(e.stopped)


# =============================================================================== effects
class Effects(unittest.TestCase):
    def test_all_offline_effects_render_valid_colours_for_every_key(self):
        import numpy as np
        from evision_rgb.effects import EFFECTS, N
        for name, cls in EFFECTS.items():
            if name in ("reactive", "music"):
                continue
            c = np.array(cls().render(1.23))
            self.assertEqual(c.shape, (N, 3), name)
            self.assertTrue(((c >= 0) & (c <= 1)).all(), name)

    def test_music_bars_rise_with_level_and_bottom_row_lights_first(self):
        import numpy as np
        from evision_rgb.effects import Music, Y
        m = Music()
        m.audio.levels = np.full(16, 0.3)
        low = (np.array(m.render(0.1)).max(axis=1) > 0.3)
        m.audio.levels = np.full(16, 1.0)
        high = (np.array(m.render(0.2)).max(axis=1) > 0.3)
        self.assertGreater(high.sum(), low.sum())
        bottom = Y > 0.9
        self.assertTrue(low[bottom].all(), "bottom row lights first")

    def test_reactive_flash_fades_after_a_keypress(self):
        import numpy as np
        from evision_rgb.effects import INDEX, Reactive
        r = Reactive(base="000000", ripple=False, fade=0.3)
        r.press("W")
        i = INDEX["w"]
        now = np.array(r.render(0))[i].max()
        r.last[i] -= 1.0  # one second later
        later = np.array(r.render(0))[i].max()
        self.assertGreater(now, 0.8)
        self.assertLess(later, 0.05)


# =============================================================================== MCP
class MCPServer(unittest.TestCase):
    def test_exposes_the_documented_tools(self):
        import asyncio
        from evision_rgb import mcp_server
        tools = asyncio.run(mcp_server.mcp.list_tools())
        names = {t.name for t in tools}
        self.assertTrue({"keyboard_status", "set_hardware_effect", "set_custom_colors", "run_live_effect",
                         "set_live_keys", "clear_live_keys", "stop_live", "list_keys", "list_effects"} <= names)


if __name__ == "__main__":
    unittest.main()

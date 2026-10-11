"""Behaviour spec — game lighting from Valve's Game State Integration (CS2, Dota 2). Written BEFORE the
implementation.

The game itself POSTs its state to the app (Valve's official, read-only feed that tournament overlays use; nothing
is injected into the game). The keyboard shows it: CS2 health on the number row, ammo on the F-row, the team colour
on WASD, flashbangs, damage, the bomb timer; Dota 2 health and mana, ability and item cooldowns on their keys and
the respawn countdown. One click writes the game's config file; a demo shows it all without the game.
Payloads below follow the documented GSI format (numbers, nesting, "previously" sections omitted)."""
import json
import os
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import warnings

import numpy as np

from fake_device import install

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
WEB = os.path.join(ROOT, "evision_rgb", "gui", "web")
NUM = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "0"]
FROW = [f"F{i}" for i in range(1, 13)]
TOKEN = "t0ken"


def cs2(health=100, armor=100, team="CT", clip=30, clip_max=30, flashed=0, burning=0, kills=0, phase="live",
        bomb=None, activity="playing", me=True, weapon_type="Rifle", win_team=None):
    weapons = {"weapon_0": {"name": "weapon_knife", "type": "Knife", "state": "holstered"},
               "weapon_1": {"name": "weapon_ak47", "type": weapon_type, "state": "active",
                            **({"ammo_clip": clip, "ammo_clip_max": clip_max, "ammo_reserve": 90}
                               if weapon_type not in ("Knife", "Grenade", "C4") else {})}}
    rnd = {"phase": phase, **({"bomb": bomb} if bomb else {}), **({"win_team": win_team} if win_team else {})}
    return {"provider": {"name": "Counter-Strike 2", "appid": 730, "steamid": "76561190000000001"},
            "map": {"name": "de_dust2", "phase": "live", "round": 3},
            "round": rnd,
            "player": {"steamid": "76561190000000001" if me else "76561190000000002", "name": "you", "team": team,
                       "activity": activity,
                       "state": {"health": health, "armor": armor, "helmet": True, "flashed": flashed, "smoked": 0,
                                 "burning": burning, "money": 800, "round_kills": kills, "round_killhs": 0},
                       "weapons": weapons},
            "auth": {"token": TOKEN}}


def ability(name, level=1, can_cast=True, cooldown=0, passive=False, ultimate=False):
    return {"name": name, "level": level, "can_cast": can_cast, "passive": passive, "ability_active": True,
            "cooldown": cooldown, "ultimate": ultimate}


def item(name="item_blink", can_cast=True, cooldown=0, passive=False):
    return {"name": name, "purchaser": 0, "can_cast": can_cast, "cooldown": cooldown, "passive": passive} \
        if name != "empty" else {"name": "empty"}


def dota(hp=100, mp=100, alive=True, respawn=0, daytime=True, stunned=False, abilities=None, items=None,
         state="DOTA_GAMERULES_STATE_GAME_IN_PROGRESS", activity="playing"):
    abilities = abilities if abilities is not None else {
        "ability0": ability("q_spell"), "ability1": ability("w_spell", cooldown=7.5),
        "ability2": ability("e_spell", level=0, can_cast=False), "ability3": ability("passive", passive=True),
        "ability4": ability("ult", ultimate=True)}
    items = items if items is not None else {
        "slot0": item(), "slot1": item("item_bracer", passive=True), "slot2": item("item_force_staff", cooldown=12),
        "slot3": item("empty"), "slot4": item("empty"), "slot5": item("empty"), "teleport0": item("item_tpscroll")}
    return {"provider": {"name": "Dota 2", "appid": 570},
            "map": {"name": "start", "game_state": state, "daytime": daytime, "paused": False, "clock_time": 600},
            "player": {"activity": activity, "team_name": "radiant", "gold": 1200},
            "hero": {"name": "npc_dota_hero_axe", "alive": alive, "respawn_seconds": respawn, "health_percent": hp,
                     "mana_percent": mp, "stunned": stunned, "silenced": False, "hexed": False, "level": 10},
            "abilities": abilities, "items": items, "auth": {"token": TOKEN}}


def lit(out, keys, thresh=0.25):
    from evision_rgb.effects import INDEX
    return [k for k in keys if np.asarray(out)[INDEX[k.lower()]].max() > thresh]


def rgb(out, key):
    from evision_rgb.effects import INDEX
    return np.asarray(out)[INDEX[key.lower()]]


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


# ============================================================================ CS2 on the keyboard
class CS2Lighting(unittest.TestCase):
    def setUp(self):
        from evision_rgb import games
        self.clock = Clock()
        self.feed = games.GameFeed("cs2", clock=self.clock)
        self.eff = games.CS2Effect(feed=self.feed)

    def show(self, payload, dt=0.5):
        """Post a state and render dt seconds later (by default after any flash has faded)."""
        self.feed.update(payload)
        self.clock.t += dt
        return np.asarray(self.eff.render(self.clock.t - 1000.0))

    def test_health_fills_the_number_row(self):
        self.assertEqual(lit(self.show(cs2(health=100)), NUM), NUM)
        self.assertEqual(lit(self.show(cs2(health=45)), NUM), NUM[:5])
        self.assertEqual(lit(self.show(cs2(health=1)), NUM), NUM[:1])

    def test_health_colour_goes_from_green_to_red(self):
        full = rgb(self.show(cs2(health=100)), "1")
        self.assertGreater(full[1], full[0] + 0.3, "green at full health")
        self.clock.t += 2  # past the damage flash
        low = rgb(self.show(cs2(health=20)), "1")
        self.assertGreater(low[0], low[1] + 0.3, "red when low")

    def test_low_health_pulses(self):
        self.show(cs2(health=15))
        seen = set()
        for _ in range(4):
            self.clock.t += 0.1
            seen.add(round(float(rgb(self.show(cs2(health=15), dt=0.0), "1")[0]), 3))
        self.assertGreater(len(seen), 1, "the bar pulses")

    def test_ammo_in_the_magazine_fills_the_f_row(self):
        self.assertEqual(lit(self.show(cs2(clip=30, clip_max=30)), FROW), FROW)
        self.assertEqual(lit(self.show(cs2(clip=15, clip_max=30)), FROW), FROW[:6])

    def test_a_nearly_empty_magazine_turns_red(self):
        c = rgb(self.show(cs2(clip=4, clip_max=30)), "F1")
        self.assertGreater(c[0], c[2] + 0.3)

    def test_a_knife_or_grenade_shows_no_ammo(self):
        self.assertEqual(lit(self.show(cs2(weapon_type="Knife")), FROW), [])

    def test_wasd_shows_the_team_colour(self):
        t = rgb(self.show(cs2(team="T")), "W")
        self.assertGreater(t[0], t[2] + 0.3, "terrorists orange")
        ct = rgb(self.show(cs2(team="CT")), "W")
        self.assertGreater(ct[2], ct[0] + 0.3, "counter-terrorists blue")

    def test_a_flashbang_whites_out_the_keyboard(self):
        out = self.show(cs2(flashed=255))
        self.assertTrue((out.min(axis=1) > 0.85).all(), "every key nearly white")
        half = self.show(cs2(flashed=100))
        self.assertLess(half.min(axis=1).mean(), 0.85)

    def test_taking_damage_flashes_red_and_fades(self):
        self.show(cs2(health=100))
        self.clock.t += 1
        hit = self.show(cs2(health=60), dt=0.02)
        self.assertGreater(rgb(hit, "G")[0], 0.4, "a red flash over the board")
        self.clock.t += 1.5
        later = self.show(cs2(health=60), dt=0.0)
        self.assertLess(rgb(later, "G")[0], 0.2, "gone after a moment")

    def test_the_bomb_timer_counts_down_on_the_f_row(self):
        self.show(cs2(bomb=None))
        start = self.show(cs2(bomb="planted"), dt=0.0)
        self.assertEqual(len(lit(start, FROW)), 12)
        self.clock.t += 20  # half of the 40 s fuse
        mid = self.show(cs2(bomb="planted"), dt=0.0)
        self.assertIn(len(lit(mid, FROW)), (6, 7))
        self.assertGreater(rgb(mid, "F1")[0], rgb(mid, "F1")[2] + 0.3, "red")

    def test_a_defused_bomb_flashes_blue(self):
        self.show(cs2(bomb="planted"))
        out = self.show(cs2(bomb="defused"), dt=0.1)
        self.assertGreater(rgb(out, "G")[2], rgb(out, "G")[0] + 0.3)

    def test_winning_the_round_glows_green_and_losing_red(self):
        win = self.show(cs2(team="CT", phase="over", win_team="CT"), dt=0.3)
        self.assertGreater(rgb(win, "G")[1], rgb(win, "G")[0] + 0.2)
        self.show(cs2(team="CT", phase="live"))
        self.clock.t += 5
        lose = self.show(cs2(team="CT", phase="over", win_team="T"), dt=0.3)
        self.assertGreater(rgb(lose, "G")[0], rgb(lose, "G")[1] + 0.2)

    def test_dead_or_spectating_shows_a_dim_red_board_without_your_bars(self):
        out = self.show(cs2(health=0))
        self.assertEqual(lit(out, NUM), [])
        self.assertLess(out.max(), 0.35)
        spect = self.show(cs2(health=100, me=False))  # after death the feed describes the teammate you watch
        self.assertEqual(lit(spect, NUM), [])

    def test_in_the_menu_nothing_flashes(self):
        out = self.show(cs2(activity="menu", health=100))
        self.assertLess(out.max(), 0.35)


# ============================================================================ Dota 2 on the keyboard
class Dota2Lighting(unittest.TestCase):
    def setUp(self):
        from evision_rgb import games
        self.clock = Clock()
        self.feed = games.GameFeed("dota2", clock=self.clock)
        self.eff = games.Dota2Effect(feed=self.feed)

    def show(self, payload, dt=0.05):
        self.feed.update(payload)
        self.clock.t += dt
        return np.asarray(self.eff.render(self.clock.t - 1000.0))

    def test_health_on_the_number_row_and_mana_on_the_f_row(self):
        out = self.show(dota(hp=70, mp=50))
        self.assertEqual(lit(out, NUM), NUM[:7])
        self.assertEqual(lit(out, FROW), FROW[:6])
        self.assertGreater(rgb(out, "F1")[2], rgb(out, "F1")[0] + 0.3, "mana is blue")

    def test_abilities_show_ready_cooldown_unlearned_and_passive(self):
        out = self.show(dota())
        q, w, e, d, r = (rgb(out, k) for k in "QWEDR")
        self.assertGreater(q.max(), 0.6, "Q ready: bright")
        self.assertTrue(w[0] > w[2] and w.max() < 0.45, "W on cooldown: dim red")
        self.assertLess(e.max(), 0.1, "E not learned: off")
        self.assertLess(d.max(), 0.3, "a passive is only dimly lit")
        self.assertGreater(r.max(), 0.6, "the ultimate lives on R")

    def test_items_show_on_zxcvbn_and_the_tp_scroll_on_t(self):
        out = self.show(dota())
        z, x, c, v = (rgb(out, k) for k in "ZXCV")
        self.assertGreater(z.max(), 0.6, "blink ready")
        self.assertLess(x.max(), 0.3, "passive item dim")
        self.assertTrue(c[0] > c[2] and c.max() < 0.45, "on cooldown: dim red")
        self.assertLess(v.max(), 0.1, "empty slot off")
        self.assertGreater(rgb(out, "T").max(), 0.6, "TP scroll ready")

    def test_when_dead_the_number_row_counts_down_to_respawn(self):
        out = self.show(dota(alive=False, respawn=6))
        self.assertEqual(lit(out, NUM), NUM[:6])
        self.assertEqual(lit(out, list("QWERDF")), [], "no abilities while dead")

    def test_a_stun_tints_the_board_purple(self):
        out = self.show(dota(stunned=True))
        g = rgb(out, "G")
        self.assertTrue(g[0] > g[1] + 0.1 and g[2] > g[1] + 0.1, f"purple {g}")

    def test_day_and_night_change_the_background(self):
        day = rgb(self.show(dota(daytime=True)), "G")
        night = rgb(self.show(dota(daytime=False)), "G")
        self.assertGreater(day[0], day[2], "warm by day")
        self.assertGreater(night[2], night[0], "blue by night")


# ============================================================================ feeds, demo
class Feeds(unittest.TestCase):
    def test_a_feed_is_live_while_the_game_keeps_sending_while_playing(self):
        from evision_rgb import games
        clock = Clock()
        f = games.GameFeed("cs2", clock=clock)
        self.assertFalse(f.active())
        f.update(cs2())
        self.assertTrue(f.active())
        clock.t += games.ACTIVE_S + 1
        self.assertFalse(f.active(), "the game went quiet (closed or crashed)")
        f.update(cs2(activity="menu"))
        self.assertFalse(f.active(), "in the menus")

    def test_the_demo_plays_a_round_without_the_game(self):
        from evision_rgb import games
        e = games.CS2Effect(demo=True)
        frames = [np.asarray(e.render(t)) for t in np.arange(0, 24, 0.5)]
        flashes = [f.min(axis=1).mean() for f in frames]
        self.assertGreater(max(flashes), 0.8, "a flashbang somewhere in the demo")
        self.assertGreater(len({len(lit(f, NUM)) for f in frames}), 3, "health changes during the demo")

    def test_the_dota_demo_plays_without_the_game(self):
        from evision_rgb import games
        e = games.Dota2Effect(demo=True)
        frames = [np.asarray(e.render(t)) for t in np.arange(0, 20, 0.5)]
        self.assertGreater(len({len(lit(f, NUM)) for f in frames}), 3)

    def test_the_effects_are_listed(self):
        from evision_rgb import games  # noqa: F401
        from evision_rgb.effects import EFFECTS
        self.assertTrue({"cs2", "dota2"} <= set(EFFECTS))


# ============================================================================ Steam folders & config files
class SteamSetup(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        steam = os.path.join(self.tmp, "Steam")
        lib2 = os.path.join(self.tmp, "Games", "SteamLibrary")
        os.makedirs(os.path.join(steam, "steamapps"))
        os.makedirs(os.path.join(lib2, "steamapps", "common", "Counter-Strike Global Offensive", "game", "csgo", "cfg"))
        os.makedirs(os.path.join(steam, "steamapps", "common", "dota 2 beta", "game", "dota", "cfg"))
        vdf = ('"libraryfolders"\n{\n\t"0"\n\t{\n\t\t"path"\t\t"%s"\n\t\t"apps"\n\t\t{\n\t\t\t"570"\t\t"1"\n\t\t}\n\t}\n'
               '\t"1"\n\t{\n\t\t"path"\t\t"%s"\n\t\t"apps"\n\t\t{\n\t\t\t"730"\t\t"1"\n\t\t}\n\t}\n}\n'
               % (steam.replace("\\", "\\\\"), lib2.replace("\\", "\\\\")))
        with open(os.path.join(steam, "steamapps", "libraryfolders.vdf"), "w", encoding="utf-8") as f:
            f.write(vdf)
        self.steam, self.lib2 = steam, lib2

    def test_games_are_found_in_every_steam_library(self):
        from evision_rgb import games
        found = games.find_games(self.steam)
        self.assertEqual(found["cs2"], os.path.join(self.lib2, "steamapps", "common", "Counter-Strike Global Offensive"))
        self.assertEqual(found["dota2"], os.path.join(self.steam, "steamapps", "common", "dota 2 beta"))

    def test_no_steam_means_no_games(self):
        from evision_rgb import games
        self.assertEqual(games.find_games(os.path.join(self.tmp, "nope")), {})

    def test_setting_up_cs2_writes_its_config_with_our_address_and_token(self):
        from evision_rgb import games
        path = games.write_config("cs2", games.find_games(self.steam)["cs2"], "abc123", port=8820)
        self.assertTrue(path.endswith(os.path.join("csgo", "cfg", "gamestate_integration_evision_rgb.cfg")))
        with open(path, encoding="utf-8") as f:
            text = f.read()
        self.assertIn('"uri"', text)
        self.assertIn("http://127.0.0.1:8820/game/cs2", text)
        self.assertIn('"token"', text)
        self.assertIn("abc123", text)
        for part in ("player_state", "player_weapons", "round"):
            self.assertIn(f'"{part}"', text)

    def test_setting_up_dota_creates_the_gamestate_folder(self):
        from evision_rgb import games
        path = games.write_config("dota2", games.find_games(self.steam)["dota2"], "abc123", port=8820)
        self.assertTrue(path.endswith(os.path.join("cfg", "gamestate_integration", "gamestate_integration_evision_rgb.cfg")))
        with open(path, encoding="utf-8") as f:
            self.assertIn("http://127.0.0.1:8820/game/dota2", f.read())

    def test_removing_deletes_only_our_file(self):
        from evision_rgb import games
        cfg_dir = os.path.join(games.find_games(self.steam)["cs2"], "game", "csgo", "cfg")
        other = os.path.join(cfg_dir, "gamestate_integration_someone_else.cfg")
        open(other, "w").close()
        games.write_config("cs2", games.find_games(self.steam)["cs2"], "abc123")
        self.assertTrue(games.remove_config("cs2", games.find_games(self.steam)["cs2"]))
        self.assertTrue(os.path.exists(other))
        self.assertFalse(games.config_written("cs2", games.find_games(self.steam)["cs2"]))


# ============================================================================ the app: endpoints, auto switch
class GamesInTheApp(unittest.TestCase):
    def setUp(self):
        warnings.simplefilter("ignore", ResourceWarning)
        self.kb = install(self)
        import evision_rgb.settings as st
        tmp = tempfile.mkdtemp()
        for attr, val in (("DIR", tmp), ("PATH", os.path.join(tmp, "settings.json"))):
            self.addCleanup(setattr, st, attr, getattr(st, attr))
            setattr(st, attr, val)
        import datetime as _dt
        from evision_rgb import games
        self.games = games
        self.clock = Clock()
        for g in ("cs2", "dota2"):  # fresh feeds on a test clock
            self.addCleanup(games.set_feed, g, games.set_feed(g, games.GameFeed(g, clock=self.clock)))
        from evision_rgb.server import Controller, build_server
        self.ctl = Controller(probes={"now": lambda: _dt.datetime(2026, 1, 1, 12)}, hook_keys=False, automation_hz=20)
        self.ctl.update_settings({"games": {"token": TOKEN, "auto": True}})
        self.httpd = build_server(self.ctl, 0)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(lambda: (self.httpd.shutdown(), self.ctl.close()))

    def post(self, path, body, ctype="application/json"):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=json.dumps(body).encode(),
                                     headers={"Content-Type": ctype, "User-Agent": "Valve/Steam HTTP Client 1.0 (730)"})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            with e:
                return e.code, e.read()

    def wait_for(self, cond, secs=2.0):
        end = time.monotonic() + secs
        while time.monotonic() < end:
            if cond():
                return True
            time.sleep(0.02)
        return bool(cond())

    def effect(self):
        e = self.ctl.engine.effect
        return e.name if e is not None else None

    def test_the_game_can_post_its_state(self):
        code, _ = self.post("/game/cs2", cs2(health=64))
        self.assertEqual(code, 200)
        self.assertEqual(self.games.feed("cs2").state["player"]["state"]["health"], 64)

    def test_a_wrong_or_missing_token_is_refused(self):
        bad = cs2()
        bad["auth"]["token"] = "guess"
        self.assertEqual(self.post("/game/cs2", bad)[0], 403)
        none = cs2()
        del none["auth"]
        self.assertEqual(self.post("/game/cs2", none)[0], 403)
        self.assertIsNone(self.games.feed("cs2").state)

    def test_the_game_may_post_without_a_json_content_type(self):
        # the token, not the content type, proves the post came from the game's config
        self.assertEqual(self.post("/game/cs2", cs2(), ctype="text/plain")[0], 200)

    def test_unknown_games_are_404(self):
        self.assertEqual(self.post("/game/minecraft", cs2())[0], 404)

    def test_game_lighting_switches_on_while_you_play_and_back_after(self):
        self.ctl.run_live("rainbow", {"speed": 0.2})
        self.post("/game/cs2", cs2())
        self.assertTrue(self.wait_for(lambda: self.effect() == "cs2"), "game lighting took over")
        self.clock.t += self.games.ACTIVE_S + 1  # the game stopped sending (closed)
        self.assertTrue(self.wait_for(lambda: self.effect() == "rainbow"), "your effect came back")
        self.assertEqual(self.ctl.engine.params, {"speed": 0.2})

    def test_with_auto_switch_off_the_game_changes_nothing(self):
        self.ctl.update_settings({"games": {"token": TOKEN, "auto": False}})
        self.post("/game/cs2", cs2())
        time.sleep(0.3)
        self.assertIsNone(self.effect())

    def test_choosing_other_lighting_during_a_game_wins(self):
        self.post("/game/cs2", cs2())
        self.assertTrue(self.wait_for(lambda: self.effect() == "cs2"))
        self.ctl.run_live("breathing", {})
        self.post("/game/cs2", cs2(health=50))
        time.sleep(0.3)
        self.assertEqual(self.effect(), "breathing", "not taken back while the game runs")

    def test_status_reports_each_game(self):
        self.post("/game/dota2", dota())
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/games", timeout=5) as r:
            st = json.loads(r.read())
        self.assertTrue(st["auto"])
        self.assertTrue(st["games"]["dota2"]["receiving"])
        self.assertFalse(st["games"]["cs2"]["receiving"])
        self.assertIn("installed", st["games"]["cs2"])
        self.assertNotIn("token", json.dumps(st), "the token stays private")

    def test_the_demo_runs_as_a_live_effect(self):
        code, _ = self.post("/run", {"effect": "cs2", "params": {"demo": True}})
        self.assertEqual(code, 200)
        self.assertEqual(self.effect(), "cs2")


class CommandLine(unittest.TestCase):
    def test_the_cli_knows_every_live_effect_the_app_knows(self):
        import subprocess
        import sys
        run = lambda code: subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True,  # noqa: E731
                                          timeout=120).stdout.strip()
        cli = run("from evision_rgb import cli; print(sorted(cli.load_effects()))")
        app = run("import evision_rgb.server; from evision_rgb.effects import EFFECTS; print(sorted(EFFECTS))")
        self.assertIn("cs2", app)
        self.assertEqual(cli, app)


class GamesUI(unittest.TestCase):
    def read(self, *p):
        with open(os.path.join(*p), encoding="utf-8") as f:
            return f.read()

    def test_the_app_has_a_games_tab_with_setup_and_demo(self):
        html, js = self.read(WEB, "index.html"), self.read(WEB, "app.js")
        self.assertIn('id="tab-games"', html)
        for s in ("/games/setup", "/games/remove", "demo: true", "/games"):
            self.assertIn(s, js)

    def test_agents_can_see_game_lighting(self):
        self.assertIn("def game_lighting(", self.read(ROOT, "evision_rgb", "mcp_server.py"))

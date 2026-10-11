"""Game lighting from Valve's Game State Integration (CS2, Dota 2).

The game itself POSTs its state to this app (http://127.0.0.1:8820/game/<game>) whenever it changes: that is
Valve's official, read-only feed (it is what tournament overlays use); nothing is injected into the game. A
config file in the game's cfg folder tells the game where to send it and with which token.

CS2: number row = health, F-row = ammo in the magazine (the bomb timer while it is planted), WASD = team colour,
flashbangs white the board out, damage flashes red, rounds end green or red. Dota 2: number row = health, F-row =
mana, Q W E D F R = abilities (ready / cooldown / no mana), Z X C V B N + T = items and the TP scroll, the number
row counts down to respawn, stuns tint the board purple, day and night change the background.
"""
from __future__ import annotations

import math
import os
import re
import threading
import time

import numpy as np

from .effects import EFFECTS, INDEX, N, Effect, Y, rgbf

ACTIVE_S = 15.0          # the game sends a heartbeat every 5 s; quiet for this long = closed / crashed
CFG_NAME = "gamestate_integration_evision_rgb.cfg"
BOMB_S = 40.0            # CS2 C4 fuse
GAMES = {
    "cs2": {"name": "Counter-Strike 2", "appid": "730", "folder": "Counter-Strike Global Offensive",
            "cfg": ("game", "csgo", "cfg"),
            "data": ("provider", "map", "round", "player_id", "player_state", "player_weapons")},
    "dota2": {"name": "Dota 2", "appid": "570", "folder": "dota 2 beta",
              "cfg": ("game", "dota", "cfg", "gamestate_integration"),
              "data": ("provider", "map", "player", "hero", "abilities", "items"),
              "launch_option": "-gamestateintegration"},
}


def _idx(names):
    return np.array([INDEX[n.lower()] for n in names])


NUM = _idx(["1", "2", "3", "4", "5", "6", "7", "8", "9", "0"])
FROW = _idx([f"F{i}" for i in range(1, 13)])
WASD = _idx(list("WASD"))
BOTTOM = np.array([i for i in range(N) if Y[i] > 0.75])  # Shift / Ctrl rows (fire licks the bottom)


def _bar(out, keys, frac, color):
    """Light the first ceil(frac * len) keys of `keys` in `color`."""
    n = int(math.ceil(max(0.0, min(1.0, frac)) * len(keys) - 1e-9))
    out[keys[:n]] = color
    out[keys[n:]] = out[keys[n:]] * 0.25


def _mix(out, color, a):
    a = max(0.0, min(1.0, a))
    return out * (1 - a) + np.asarray(color, dtype=float) * a


def _health_colour(frac):
    if frac > 0.6:
        return np.array([0.0, 1.0, 0.25])
    if frac > 0.3:
        return np.array([1.0, 0.75, 0.0])
    return np.array([1.0, 0.0, 0.0])


# ------------------------------------------------------------------------------ the feed
class GameFeed:
    """The latest state a game posted, plus the moments things happened (hit, bomb planted, round over)."""

    def __init__(self, game: str, clock=time.monotonic):
        self.game, self.clock = game, clock
        self.state: dict | None = None
        self.received = -1e9
        self.events: dict[str, float] = {}
        self.lock = threading.Lock()

    def update(self, payload: dict):
        if not isinstance(payload, dict):
            raise ValueError("game state must be a JSON object")
        now = self.clock()
        with self.lock:
            old, self.state, self.received = self.state, payload, now
            (self._cs2_events if self.game == "cs2" else self._dota_events)(old or {}, payload, now)

    def _cs2_events(self, old, new, now):
        o, n = _cs2_me(old), _cs2_me(new)
        if o and n:
            dmg = o["health"] - n["health"]
            if dmg > 0:
                self.events["hit"], self.events["hit_size"] = now, min(1.0, dmg / 40)
            if n["kills"] > o["kills"]:
                self.events["kill"] = now
        ob, nb = (old.get("round") or {}).get("bomb"), (new.get("round") or {}).get("bomb")
        if nb != ob and nb in ("planted", "defused", "exploded"):
            self.events[nb] = now
        op, np_ = (old.get("round") or {}).get("phase"), (new.get("round") or {}).get("phase")
        if np_ == "over" and op != "over":
            self.events["over"] = now

    def _dota_events(self, old, new, now):
        oh, nh = old.get("hero") or {}, new.get("hero") or {}
        if oh.get("alive") and nh.get("alive") and nh.get("health_percent", 100) < oh.get("health_percent", 100) - 5:
            self.events["hit"] = now

    def age(self, name: str) -> float:
        return self.clock() - self.events.get(name, -1e9)

    def playing(self) -> bool:
        s = self.state or {}
        if (s.get("player") or {}).get("activity") != "playing":
            return False
        if self.game == "dota2":
            return (s.get("map") or {}).get("game_state", "") in (
                "DOTA_GAMERULES_STATE_PRE_GAME", "DOTA_GAMERULES_STATE_GAME_IN_PROGRESS",
                "DOTA_GAMERULES_STATE_STRATEGY_TIME", "DOTA_GAMERULES_STATE_HERO_SELECTION")
        return True

    def active(self) -> bool:
        """The game is running and you are in a match (not the menus)."""
        return self.clock() - self.received < ACTIVE_S and self.playing()


def _cs2_me(s: dict) -> dict | None:
    """Your own state, or None while dead / watching a teammate (the feed then describes them)."""
    p = s.get("player") or {}
    st = p.get("state")
    if not st or p.get("activity") != "playing":
        return None
    if (s.get("provider") or {}).get("steamid") not in (None, p.get("steamid")):
        return None
    return {"health": int(st.get("health", 0)), "armor": int(st.get("armor", 0)), "kills": int(st.get("round_kills", 0)),
            "flashed": int(st.get("flashed", 0)), "burning": int(st.get("burning", 0)), "team": p.get("team")}


_FEEDS: dict[str, GameFeed] = {}
_FEEDS_LOCK = threading.Lock()


def feed(game: str) -> GameFeed:
    """The shared feed the game's posts go into."""
    with _FEEDS_LOCK:
        if game not in _FEEDS:
            _FEEDS[game] = GameFeed(game)
        return _FEEDS[game]


def set_feed(game: str, f):
    """Replace a game's shared feed (tests); returns the previous one."""
    with _FEEDS_LOCK:
        prev, _FEEDS[game] = _FEEDS.get(game), f
        return prev


# ------------------------------------------------------------------------------ demos (no game needed)
def _cs2_demo(t: float) -> dict:
    """A scripted 24 s round: buy time, shooting, hits, a kill, a flashbang, a reload, the bomb, a win."""
    import copy
    t %= 24.0
    health = 100 if t < 6.5 else 73 if t < 11.5 else 41 if t < 17 else 18
    clip = 30 if t < 4 else max(24, 30 - int((t - 4) * 3)) if t < 11 else 5 if t < 13 else 0 if t < 14 else 30
    flashed = int(255 * max(0.0, 1 - (t - 9) / 2)) if 9 <= t < 11 else 0
    burning = 200 if 18 <= t < 19.5 else 0
    bomb = "planted" if 15 <= t < 21 else "defused" if t >= 21 else None
    phase = "freezetime" if t < 3 else "over" if t >= 21.5 else "live"
    s = cs2_payload(health=health, clip=clip, flashed=flashed, burning=burning, kills=1 if t >= 8 else 0,
                    phase=phase, bomb=bomb, win_team="CT" if phase == "over" else None)
    return copy.deepcopy(s)


def cs2_payload(health=100, armor=100, team="CT", clip=30, clip_max=30, flashed=0, burning=0, kills=0, phase="live",
                bomb=None, win_team=None):
    rnd = {"phase": phase, **({"bomb": bomb} if bomb else {}), **({"win_team": win_team} if win_team else {})}
    return {"provider": {"name": "Counter-Strike 2", "appid": 730, "steamid": "demo"},
            "round": rnd,
            "player": {"steamid": "demo", "team": team, "activity": "playing",
                       "state": {"health": health, "armor": armor, "flashed": flashed, "burning": burning,
                                 "round_kills": kills},
                       "weapons": {"weapon_1": {"type": "Rifle", "state": "active", "ammo_clip": clip,
                                                "ammo_clip_max": clip_max}}}}


def _dota_demo(t: float) -> dict:
    """A scripted 20 s fight: spells go on cooldown, mana drains, a stun, a death and the respawn countdown."""
    t %= 20.0
    alive = not 13 <= t < 19
    hp = 100 if t < 4 else 80 if t < 8 else 55 if t < 11 else 20 if t < 13 else 100
    mp = 100 if t < 4 else 70 if t < 8 else 40 if t < 13 else 100
    cd = lambda start, length: max(0.0, length - (t - start)) if t >= start else 0.0  # noqa: E731
    abilities = {"ability0": {"name": "q", "level": 4, "can_cast": True, "passive": False, "cooldown": cd(4, 6), "ultimate": False},
                 "ability1": {"name": "w", "level": 3, "can_cast": True, "passive": False, "cooldown": cd(8, 9), "ultimate": False},
                 "ability2": {"name": "e", "level": 2, "can_cast": True, "passive": True, "cooldown": 0, "ultimate": False},
                 "ability3": {"name": "r", "level": 1, "can_cast": mp > 50, "passive": False, "cooldown": cd(11, 30), "ultimate": True}}
    items = {"slot0": {"name": "item_blink", "can_cast": True, "passive": False, "cooldown": cd(10, 15)},
             "slot1": {"name": "item_bracer", "can_cast": False, "passive": True, "cooldown": 0},
             "teleport0": {"name": "item_tpscroll", "can_cast": True, "passive": False, "cooldown": cd(19, 60)}}
    return {"provider": {"name": "Dota 2", "appid": 570},
            "map": {"game_state": "DOTA_GAMERULES_STATE_GAME_IN_PROGRESS", "daytime": t < 10, "paused": False},
            "player": {"activity": "playing"},
            "hero": {"alive": alive, "respawn_seconds": 0 if alive else int(math.ceil(19 - t)), "health_percent": hp if alive else 0,
                     "mana_percent": mp if alive else 0, "stunned": 6 <= t < 7.5, "silenced": False, "hexed": False},
            "abilities": abilities, "items": items}


class _DemoFeed(GameFeed):
    def __init__(self, game, script):
        self.now = 0.0
        super().__init__(game, clock=lambda: self.now)
        self.script = script

    def play(self, t: float):
        self.now = t
        self.update(self.script(t))


# ------------------------------------------------------------------------------ the effects
class _GameEffect(Effect):
    fps = 30
    GAME = ""

    def __init__(self, demo: bool = False, feed=None):
        if not isinstance(demo, bool):
            raise ValueError("demo must be true or false")
        self.demo = demo
        if feed is not None and not isinstance(feed, GameFeed):  # params can come from shared profiles
            raise ValueError("feed must be a GameFeed")
        self.feed = _DemoFeed(self.GAME, _cs2_demo if self.GAME == "cs2" else _dota_demo) if demo else feed or globals()["feed"](self.GAME)

    def render(self, t):
        if self.demo:
            self.feed.play(t)
        with self.feed.lock:
            s = dict(self.feed.state or {})
        return np.clip(self.draw(s), 0.0, 1.0)


class CS2Effect(_GameEffect):
    """Counter-Strike 2 (Game State Integration): health on the number row, ammo (or the bomb timer) on the F-row,
    team colour on WASD, flashbangs, hits, kills and round results. demo=true plays a scripted round."""
    name = "cs2"
    GAME = "cs2"
    TEAM = {"T": np.array([1.0, 0.55, 0.0]), "CT": np.array([0.2, 0.45, 1.0])}

    def draw(self, s: dict) -> np.ndarray:
        f = self.feed
        me = _cs2_me(s)
        p = s.get("player") or {}
        team = self.TEAM.get(p.get("team"), np.array([0.8, 0.8, 0.8]))
        rnd = s.get("round") or {}
        if (p.get("activity") != "playing") or not s:
            return np.tile(team * 0.12, (N, 1))
        if me is None or me["health"] <= 0:
            out = np.tile([0.18, 0.0, 0.0], (N, 1))  # dead / watching a teammate
        else:
            out = np.tile(team * 0.12, (N, 1))
            if rnd.get("phase") == "freezetime":
                out = out * (1.4 + 0.6 * math.sin(f.clock() * 3))
            out[WASD] = team
            frac = me["health"] / 100
            col = _health_colour(frac)
            if frac <= 0.3:
                col = col * (0.8 + 0.2 * math.sin(f.clock() * 2 * math.pi * 2))  # low: the bar pulses
            _bar(out, NUM, frac, col)
            w = next((w for w in ((p.get("weapons") or {}).values()) if w.get("state") == "active"), {})
            if "ammo_clip_max" in w and w.get("ammo_clip_max"):
                af = w.get("ammo_clip", 0) / w["ammo_clip_max"]
                _bar(out, FROW, af, np.array([1.0, 0.2, 0.0]) if af <= 0.25 else np.array([1.0, 0.92, 0.75]))
            else:
                out[FROW] = out[FROW] * 0.25
            if me["burning"]:
                flick = 0.5 + 0.5 * math.sin(f.clock() * 23)
                out[BOTTOM] = _mix(out[BOTTOM], [1.0, 0.35, 0.0], me["burning"] / 255 * flick)
        if rnd.get("bomb") == "planted":
            left = max(0.0, BOMB_S - f.age("planted")) / BOMB_S
            _bar(out, FROW, left, np.array([1.0, 0.0, 0.0]))
            beat = 1.0 - left * 0.9  # beeps speed up as the fuse burns down
            if (f.age("planted") * (1 + 6 * beat)) % 1.0 < 0.15:
                out[INDEX["esc"]] = [1.0, 0.0, 0.0]
        if f.age("defused") < 1.5:
            out = _mix(out, [0.1, 0.35, 1.0], 1 - f.age("defused") / 1.5)
        if f.age("exploded") < 2.0:
            out = _mix(out, [1.0, 0.6, 0.2], 1 - f.age("exploded") / 2.0)
        if rnd.get("phase") == "over" and f.age("over") < 2.5:
            won = rnd.get("win_team") == p.get("team")
            out = _mix(out, [0.0, 1.0, 0.3] if won else [0.8, 0.0, 0.0], 0.7 * (1 - f.age("over") / 2.5))
        if me is not None:
            if f.age("kill") < 0.4:
                out = _mix(out, [1.0, 0.8, 0.2], 0.5 * (1 - f.age("kill") / 0.4))
            if f.age("hit") < 0.4:
                out = _mix(out, [1.0, 0.0, 0.0], f.events.get("hit_size", 0.5) * (1 - f.age("hit") / 0.4))
            if me["flashed"]:
                out = _mix(out, [1.0, 1.0, 1.0], me["flashed"] / 255)
        return out


ABILITY_KEYS = ["q", "w", "e", "d", "f"]   # normal abilities in slot order; the ultimate lives on R
ITEM_KEYS = ["z", "x", "c", "v", "b", "n"]


def _slot_colour(a: dict, ready):
    """Colour of an ability / item key; None = nothing there (not learned, empty slot)."""
    if not a or a.get("name") in (None, "empty") or a.get("level", 1) == 0:
        return None
    if a.get("passive"):
        return np.array([0.2, 0.2, 0.2])
    if a.get("cooldown", 0) > 0:
        return np.array([0.35, 0.02, 0.0])
    if not a.get("can_cast", True):
        return np.array([0.0, 0.05, 0.35])  # no mana
    return np.asarray(ready, dtype=float)


class Dota2Effect(_GameEffect):
    """Dota 2 (Game State Integration): health on the number row, mana on the F-row, abilities on Q W E D F R,
    items on Z X C V B N and the TP scroll on T (bright = ready, dim red = cooldown), respawn countdown, stuns.
    demo=true plays a scripted fight."""
    name = "dota2"
    GAME = "dota2"

    def draw(self, s: dict) -> np.ndarray:
        f = self.feed
        if not s or not f.playing():
            return np.tile([0.1, 0.06, 0.02], (N, 1))
        m, hero = s.get("map") or {}, s.get("hero") or {}
        base = np.array([0.12, 0.07, 0.0]) if m.get("daytime", True) else np.array([0.0, 0.03, 0.14])
        out = np.tile(base, (N, 1))
        if not hero.get("alive", True):
            out = np.tile([0.15, 0.0, 0.0], (N, 1))
            _bar(out, NUM, min(10, hero.get("respawn_seconds", 0)) / 10, np.array([1.0, 0.1, 0.0]))
            return out
        hp = hero.get("health_percent", 100) / 100
        col = _health_colour(hp)
        if hp <= 0.25:
            col = col * (0.8 + 0.2 * math.sin(f.clock() * 2 * math.pi * 2))  # low: the bar pulses
        _bar(out, NUM, hp, col)
        _bar(out, FROW, hero.get("mana_percent", 100) / 100, np.array([0.1, 0.35, 1.0]))
        abilities = [a for k, a in sorted((s.get("abilities") or {}).items(), key=lambda kv: _num(kv[0]))
                     if isinstance(a, dict) and not re.match(r"^(plus_|special_bonus|generic_hidden)", a.get("name", ""))]
        normal = [a for a in abilities if not a.get("ultimate")]
        ult = next((a for a in abilities if a.get("ultimate")), None)
        items = s.get("items") or {}
        slots = list(zip(ABILITY_KEYS, normal, [[0.0, 0.9, 1.0]] * 5)) + [("r", ult, [1.0, 0.3, 1.0])]             + [(k, items.get(f"slot{i}") or {}, [1.0, 0.75, 0.25]) for i, k in enumerate(ITEM_KEYS)]             + [("t", items.get("teleport0") or {}, [0.4, 1.0, 0.4])]
        for key, a, ready in slots:
            c = _slot_colour(a or {}, ready)
            i = INDEX[key]
            out[i] = out[i] * 0.5 if c is None else c  # nothing there: a dimmed key, not a black hole
        if hero.get("stunned") or hero.get("hexed") or hero.get("silenced"):
            out = _mix(out, [0.6, 0.0, 0.8], 0.45 + 0.15 * math.sin(f.clock() * 10))
        if f.age("hit") < 0.3:
            out = _mix(out, [1.0, 0.0, 0.0], 0.5 * (1 - f.age("hit") / 0.3))
        return out


def _num(key: str) -> int:
    m = re.search(r"\d+$", key)
    return int(m.group()) if m else 0


EFFECTS["cs2"] = CS2Effect
EFFECTS["dota2"] = Dota2Effect


# ------------------------------------------------------------------------------ Steam folders & config files
def steam_path() -> str | None:
    """Where Steam is installed (from the registry on Windows)."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as k:
            return os.path.normpath(winreg.QueryValueEx(k, "SteamPath")[0])
    except Exception:
        for p in (r"C:\Program Files (x86)\Steam", os.path.expanduser("~/.steam/steam")):
            if os.path.isdir(p):
                return p
        return None


def _vdf_paths(text: str) -> list[str]:
    """Library folder paths from steamapps/libraryfolders.vdf ("path" values; \\\\ unescaped)."""
    return [m.group(1).replace("\\\\", "\\") for m in re.finditer(r'"path"\s+"((?:[^"\\]|\\.)*)"', text)]


def find_games(steam: str | None = None) -> dict[str, str]:
    """{game id: install folder} for the supported games in every Steam library."""
    steam = steam if steam is not None else steam_path()
    if not steam or not os.path.isdir(steam):
        return {}
    libs = [steam]
    try:
        with open(os.path.join(steam, "steamapps", "libraryfolders.vdf"), encoding="utf-8", errors="replace") as f:
            libs += _vdf_paths(f.read())
    except OSError:
        pass
    found = {}
    for gid, g in GAMES.items():
        for lib in dict.fromkeys(os.path.normpath(p) for p in libs):
            d = os.path.join(lib, "steamapps", "common", g["folder"])
            if os.path.isdir(d):
                found[gid] = d
                break
    return found


def config_path(game: str, game_dir: str) -> str:
    return os.path.join(game_dir, *GAMES[game]["cfg"], CFG_NAME)


def config_text(game: str, token: str, port: int = 8820) -> str:
    g = GAMES[game]
    data = "\n".join(f'\t\t"{d}"\t"1"' for d in g["data"])
    return (f'"EVision RGB"\n{{\n\t"uri"\t\t"http://127.0.0.1:{port}/game/{game}"\n\t"timeout"\t"1.1"\n'
            f'\t"buffer"\t"0.0"\n\t"throttle"\t"0.05"\n\t"heartbeat"\t"5.0"\n'
            f'\t"auth"\n\t{{\n\t\t"token"\t"{token}"\n\t}}\n\t"data"\n\t{{\n{data}\n\t}}\n}}\n')


def write_config(game: str, game_dir: str, token: str, port: int = 8820) -> str:
    """Tell the game to send its state here: writes our own file into its cfg folder (nothing else changes)."""
    if game not in GAMES:
        raise KeyError(f"unknown game {game!r}")
    if not re.fullmatch(r"[0-9a-zA-Z_-]{4,64}", token):
        raise ValueError("bad token")
    path = config_path(game, game_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(config_text(game, token, port))
    return path


def config_written(game: str, game_dir: str) -> bool:
    return os.path.isfile(config_path(game, game_dir))


def remove_config(game: str, game_dir: str) -> bool:
    path = config_path(game, game_dir)
    if os.path.isfile(path):
        os.remove(path)
        return True
    return False

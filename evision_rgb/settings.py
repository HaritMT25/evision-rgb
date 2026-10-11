"""Persistent user settings for the GUI/daemon: %APPDATA%\\EVisionRGB\\settings.json"""
from __future__ import annotations

import copy
import json
import os

_BASE = os.environ.get("APPDATA", os.path.expanduser("~"))
DIR = os.path.join(_BASE, "EVisionRGB")
OLD_DIR = os.path.join(_BASE, "AK820Control")  # name used before v0.2
PATH = os.path.join(DIR, "settings.json")

DEFAULTS = {
    "startup": "restore",         # what to do when the app starts: none | restore (last live effect) | apps
    "start_minimized": True,      # when launched at login, stay in the tray
    "close_to_tray": True,        # closing the window keeps effects running in the tray
    "last_live": None,            # {"effect": ..., "params": {...}} last live effect started
    "apps": {"enabled": False, "default": {"effect": "rainbow", "params": {"speed": 0.12, "brightness": 0.6}},
             "rules": [{"match": "spotify", "effect": "music", "params": {}},
                       {"match": "code.exe", "effect": "reactive", "params": {"base": "000818"}}]},
    "profiles": [],               # [{"name":..., "hardware": {...}, "custom": {...}|None, "live": {...}|None}]
    "palette": ["ff0000", "ff7200", "fff005", "00e000", "0099ff", "3d5afe", "5e01d2",
                "ee00f1", "ff008a", "ffa800", "8dff8d", "3defff", "004a9f", "ffffff"],
    "backup_dir": None,
    "custom_groups": [None, None, None],  # per-key layouts saved in the app (keyboard displays one)
    "custom_active": 0,
    "tray_hint_shown": False,
    "heatmap_enabled": False,     # typing heatmap is opt-in (privacy)
    "fw_time_scale": 1.0,         # calibration of exact effect emulation speed (1.0 = datasheet)
    "layers_draft": None,         # the layer editor's current stack (list of layers), kept between sessions
    "pads": {},                   # macro pad set-up per family: {"akp03": {"brightness", "keys", "knobs"}}
    "games": {"auto": True},      # game lighting; + "token" (made on first use, the games' config files carry it)
}


def load() -> dict:
    s = copy.deepcopy(DEFAULTS)
    path = PATH
    if not os.path.exists(path) and os.path.exists(os.path.join(OLD_DIR, "settings.json")):
        path = os.path.join(OLD_DIR, "settings.json")  # migrate settings from the old app name
    try:
        with open(path, encoding="utf-8") as f:
            s.update(json.load(f))
    except (OSError, ValueError):
        pass
    return s


def save(s: dict) -> dict:
    os.makedirs(DIR, exist_ok=True)
    tmp = PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(s, f, indent=2)
    os.replace(tmp, PATH)
    return s

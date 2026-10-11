"""Keyboard-shortcut database for the 'hold a modifier to see shortcuts' hints.

hints(app, modifiers) -> {key name: category}. Modifier combos are written in the fixed order
ctrl+alt+shift+win. App entries extend the global ones; user entries (settings) extend both.
"""
from __future__ import annotations

ORDER = ("ctrl", "alt", "shift", "win")
CATEGORY_COLOURS = {
    "edit": "00e5ff", "file": "00ff6a", "find": "ffd000", "nav": "3d7bff",
    "view": "b04dff", "window": "ff8a00", "app": "ff2d95",
}

GLOBAL = {
    "ctrl": {"edit": ["C", "V", "X", "Z", "Y", "A"], "file": ["S", "O", "N", "P"], "find": ["F", "H"],
             "nav": ["Left", "Right", "Home", "End", "Backspace", "Delete"]},
    "ctrl+shift": {"edit": ["Z"], "nav": ["Left", "Right", "Home", "End"]},
    "alt": {"window": ["Tab", "F4"], "nav": ["Left", "Right"]},
    "win": {"window": ["D", "Tab", "Up", "Down", "Left", "Right", "Grave"], "app": ["E", "R", "I", "A", "N", "X"],
            "edit": ["V", "Period"], "file": ["L"]},
    "shift+win": {"edit": ["S"], "window": ["Left", "Right"]},
    "ctrl+win": {"window": ["D", "Left", "Right", "F4"]},
    "ctrl+alt": {"window": ["Delete", "Tab"]},
}

BROWSER = {
    "ctrl": {"view": ["T", "W", "Tab", "R", "Equal", "Minus", "0"], "nav": ["L", "1", "2", "3", "4", "5", "6", "7", "8", "9"],
             "app": ["H", "J", "D"], "find": ["F", "G"]},
    "ctrl+shift": {"view": ["T", "N", "Tab", "B"], "app": ["Delete", "J", "I"]},
    "alt": {"nav": ["Left", "Right", "Home", "D"]},
}

APPS = {
    "code.exe": {
        "ctrl": {"app": ["P", "Grave", "B", "J", "K", "G", "Comma"], "edit": ["Slash", "D", "L", "RBracket", "LBracket", "Enter"],
                 "find": ["F", "H"], "view": ["Equal", "Minus", "Backslash", "1", "2", "3"]},
        "ctrl+shift": {"app": ["P", "E", "F", "G", "X", "D", "Grave", "B", "M"], "edit": ["K", "L", "Enter", "Up", "Down"]},
        "alt": {"edit": ["Up", "Down"], "nav": ["Left", "Right"]},
        "shift+alt": {"edit": ["Up", "Down", "F"]},
    },
    "chrome.exe": BROWSER, "msedge.exe": BROWSER, "firefox.exe": BROWSER, "brave.exe": BROWSER, "opera.exe": BROWSER,
    "explorer.exe": {
        "ctrl": {"file": ["N", "W", "E", "L"], "view": ["Equal", "Minus"]},
        "ctrl+shift": {"file": ["N"], "view": ["1", "2", "3", "4", "5", "6"]},
        "alt": {"nav": ["Up", "Left", "Right", "Enter", "D"], "view": ["P"]},
    },
    "windowsterminal.exe": {
        "ctrl": {"edit": ["C", "V"], "view": ["Equal", "Minus", "0", "Tab"], "find": ["F"]},
        "ctrl+shift": {"view": ["T", "W", "N", "D", "P", "Space", "1", "2", "3"], "edit": ["C", "V"]},
        "alt": {"window": ["Enter", "Left", "Right", "Up", "Down"]},
        "shift+alt": {"window": ["Equal", "Minus"]},
    },
    "winword.exe": {"ctrl": {"edit": ["B", "I", "U", "E", "L", "R", "J", "K", "LBracket", "RBracket"]}},
    "excel.exe": {"ctrl": {"edit": ["B", "I", "U", "D", "R", "K", "Semicolon", "1"], "nav": ["Up", "Down", "PageUp", "PageDown"]}},
}


def combo(mods) -> str:
    return "+".join(m for m in ORDER if m in mods)


def hints(app: str, mods, custom: dict | None = None) -> dict[str, str]:
    """Keys that do something with exactly these modifiers held, mapped to a category."""
    c = combo(mods)
    if not c:
        return {}
    out: dict[str, str] = {}
    app = (app or "").lower()
    custom = {k.lower(): v for k, v in (custom or {}).items()}
    table = APPS.get(app, {})
    for source in (GLOBAL, table, custom.get(app, {}), custom.get("*", {})):  # app entries extend the global ones
        for cat, keys in source.get(c, {}).items():
            for k in keys:
                out[k] = cat
    return out

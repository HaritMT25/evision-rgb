"""Shareable lighting profiles: validation, export files and share codes.

A profile is {"name", "hardware": {set_effect fields}, "custom": {key: colour}, "live": {"effect", "params"}}
with at least one of the last three. Export file:
    {"format": "evision-rgb-profile", "version": 1, "app": ..., "profile": {...}}
Share code: "evrgb1:" + base64url(zlib(JSON of the export file)), short enough to paste into a chat.
Everything imported is validated before it is stored, and nothing reaches the keyboard until the
profile is applied.
"""
from __future__ import annotations

import base64
import binascii
import json
import unicodedata
import zlib

from .keyboard import CONFIG_LEN, Keyboard, parse_color
from .layout import KEYS
from .protocol import MODES

FORMAT, VERSION = "evision-rgb-profile", 1
CODE_PREFIX = "evrgb1:"
MAX_JSON = 256 * 1024     # a profile is a few KB; anything bigger is not a profile
MAX_CODE = 96 * 1024
HARDWARE_FIELDS = ("mode", "brightness", "speed", "color", "direction", "random", "colorset", "variant")
_KEYS = {k.lower() for k in KEYS}


def clean_name(name) -> str:
    """Names are shown in the app: no markup or control/format characters, single spaces, <= 60 chars."""
    s = name if isinstance(name, str) else ""
    s = "".join(ch for ch in s if ch not in "<>" and not unicodedata.category(ch).startswith("C"))
    s = " ".join(s.split())[:60].strip()
    return s or "Imported profile"


def unique_name(name: str, taken) -> str:
    """`name`, or `name (2)`, `name (3)`, ... so an import never replaces an existing profile."""
    taken = set(taken)
    if name not in taken:
        return name
    i = 2
    while f"{name} ({i})" in taken:
        i += 1
    return f"{name} ({i})"


def hardware_args(hw: dict) -> dict:
    """set_effect() keyword arguments for a profile's hardware part. Outside custom mode byte 18 is the
    effect variant (Surmount presets 0..3), which older profiles stored as 'colorset'."""
    args = dict(hw)
    custom = args.get("mode") in (MODES["custom"], "custom")
    if "colorset" in args and "variant" not in args and not custom:
        args["variant"] = args.pop("colorset")
    return args


def validate_live(live) -> dict:
    """Check a live part by building the effect (all options validated, nothing started)."""
    from .effects import EFFECTS
    if not isinstance(live, dict) or not isinstance(live.get("effect"), str):
        raise ValueError('live must look like {"effect": "rainbow", "params": {...}}')
    unknown = sorted(set(live) - {"effect", "params"})
    if unknown:
        raise ValueError(f"live: unknown field(s) {unknown}")
    name, params = live["effect"], live.get("params") or {}
    if name not in EFFECTS:
        raise ValueError(f"live: unknown effect {name!r}; choose from {sorted(EFFECTS)}")
    if not isinstance(params, dict):
        raise ValueError("live: params must be an object")
    try:
        EFFECTS[name](**params)
    except (TypeError, ValueError, KeyError) as e:
        raise ValueError(f"live ({name}): {e}") from e
    return dict(live)


def validate_profile(p) -> dict:
    """A cleaned copy of profile `p`; raises ValueError with the reason. Never touches the keyboard."""
    if not isinstance(p, dict):
        raise ValueError("profile must be an object")
    out = {"name": clean_name(p.get("name"))}
    hw = p.get("hardware")
    if hw is not None:
        if not isinstance(hw, dict):
            raise ValueError("hardware must be an object")
        unknown = sorted(set(hw) - set(HARDWARE_FIELDS))
        if unknown:
            raise ValueError(f"hardware: unknown field(s) {unknown}; allowed: {list(HARDWARE_FIELDS)}")
        try:
            Keyboard.apply_fields(bytearray(CONFIG_LEN), **hardware_args(hw))
        except (TypeError, ValueError, KeyError) as e:
            raise ValueError(f"hardware: {e}") from e
        out["hardware"] = dict(hw)
    custom = p.get("custom")
    if custom is not None:
        if not isinstance(custom, dict):
            raise ValueError("custom must be an object of key: colour")
        for key, c in custom.items():
            if str(key).lower() not in _KEYS:
                raise ValueError(f"custom: unknown key {key!r}")
            try:
                parse_color(c)
            except (TypeError, ValueError) as e:
                raise ValueError(f"custom: bad colour for {key}: {c!r}") from e
        out["custom"] = dict(custom)
    if p.get("live") is not None:
        out["live"] = validate_live(p["live"])
    if len(out) == 1:
        raise ValueError("profile has nothing to apply (no hardware, custom or live part)")
    return out


def export_doc(profile: dict) -> dict:
    from . import __version__
    return {"format": FORMAT, "version": VERSION, "app": f"EVision RGB {__version__}",
            "device": "AJAZZ AK820 (USB 320F:505B)", "profile": profile}


def to_code(doc: dict) -> str:
    raw = json.dumps(doc, separators=(",", ":")).encode()
    return CODE_PREFIX + base64.urlsafe_b64encode(zlib.compress(raw, 9)).decode().rstrip("=")


def from_code(code) -> dict:
    """Decode a share code without ever inflating more than MAX_JSON bytes (zip bombs)."""
    if not isinstance(code, str):
        raise ValueError("share code must be text")
    code = "".join(code.split())  # pasted codes may be wrapped or padded with spaces
    if not code.startswith(CODE_PREFIX):
        raise ValueError(f"not an EVision RGB share code (they start with {CODE_PREFIX})")
    body = code[len(CODE_PREFIX):]
    if len(body) > MAX_CODE:
        raise ValueError("share code is too large")
    try:
        packed = base64.b64decode(body + "=" * (-len(body) % 4), altchars=b"-_", validate=True)
    except (binascii.Error, ValueError) as e:
        raise ValueError("share code is damaged (not base64)") from e
    d = zlib.decompressobj()
    try:
        raw = d.decompress(packed, MAX_JSON + 1)
    except zlib.error as e:
        raise ValueError("share code is damaged") from e
    if len(raw) > MAX_JSON or d.unconsumed_tail:
        raise ValueError("share code is too large to be a profile")
    if not d.eof:
        raise ValueError("share code is incomplete")
    try:
        return json.loads(raw)
    except (UnicodeDecodeError, ValueError) as e:
        raise ValueError("share code is damaged (not JSON)") from e


def read_doc(obj) -> dict:
    """Validated profile from an export file (parsed JSON) or {"code": "evrgb1:..."}."""
    if isinstance(obj, dict) and "code" in obj and "format" not in obj:
        obj = from_code(obj["code"])
    if not isinstance(obj, dict) or obj.get("format") != FORMAT:
        raise ValueError("not an EVision RGB profile file")
    v = obj.get("version")
    if isinstance(v, bool) or not isinstance(v, int) or not 1 <= v <= VERSION:
        raise ValueError(f"profile file version {v!r} is not supported (this app reads up to {VERSION}); "
                         "update EVision RGB")
    if "profile" not in obj:
        raise ValueError("the file contains no profile")
    return validate_profile(obj["profile"])

"""MCP (stdio) server exposing the AK820 to AI agents. Proxies to the HTTP daemon
(`evrgb serve`, 127.0.0.1:8820) and starts it if needed, so a single process owns the USB device.

Register with Claude Code:
  claude mcp add evision-rgb -- <venv>/Scripts/python.exe -m evision_rgb.mcp_server
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

try:  # mcp >= 2
    from mcp.server.mcpserver import MCPServer as FastMCP
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP

from .server import DEFAULT_PORT

BASE = f"http://127.0.0.1:{DEFAULT_PORT}"
mcp = FastMCP("evision-rgb")


def _call(path: str, body: dict | None = None) -> dict:
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(BASE + path, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return json.loads(e.read() or b'{"error": "http error"}')


def _ensure_daemon():
    try:
        _call("/status")
        return
    except (urllib.error.URLError, ConnectionError):
        pass
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
    subprocess.Popen([sys.executable, "-m", "evision_rgb", "serve"], creationflags=flags,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(50):
        time.sleep(0.2)
        try:
            _call("/status")
            return
        except (urllib.error.URLError, ConnectionError):
            pass
    raise RuntimeError("could not start the evision-rgb HTTP daemon")


def call(path: str, body: dict | None = None) -> dict:
    _ensure_daemon()
    return _call(path, body)


@mcp.tool()
def keyboard_status() -> dict:
    """Current state: stored hardware effect (mode, brightness 0-4, speed 0-4, colour) and live-effect status."""
    return {"hardware": call("/info"), "live": call("/status")}


@mcp.tool()
def list_keys() -> dict:
    """Key names usable in other tools, with physical x,y positions (0..1, origin top-left)."""
    return call("/keys")


@mcp.tool()
def list_effects() -> dict:
    """Available live (host-rendered) effects with their parameters, and hardware (firmware) modes."""
    return call("/effects")


@mcp.tool()
def set_hardware_effect(mode: str, brightness: int | None = None, speed: int | None = None,
                        color: str | None = None, direction: int | None = None) -> dict:
    """Persistent firmware effect that keeps running without the PC software (saved on the keyboard).
    mode (AJAZZ names): corrugated, cloud, serpentine, spectrum, breath, normal (=static), reaction, ripples,
    traverse, stars, flowers, roll, wave, cartoon, rain, scan, surmount, speed, custom.
    brightness 0-4, speed 0-4 (0 = fastest), color 'RRGGBB'. Stops any live effect."""
    body = {k: v for k, v in dict(mode=mode, brightness=brightness, speed=speed, color=color,
                                  direction=direction).items() if v is not None}
    return call("/effect", body)


@mcp.tool()
def set_custom_colors(colors: dict[str, str], default: str = "000000", colorset: int = 0) -> dict:
    """Persistent per-key colours saved on the keyboard (custom mode). colors: {"W": "ff0000", ...};
    keys not listed get `default`. colorset 0-2. Stops any live effect. Avoid calling in a loop (flash writes)."""
    return call("/custom", {"colors": colors, "default": default, "colorset": colorset})


@mcp.tool()
def run_live_effect(effect: str, params: dict | None = None) -> dict:
    """Start a host-rendered live effect (not saved; ends when stopped). effect: rainbow, breathing, gradient,
    solid, reactive (typing ripples), music (audio visualiser of whatever is playing), sysmon (CPU/RAM bars),
    ambient (screen colours), layers (stack several effects).
    Example params: rainbow {"speed": 0.3}, breathing {"color": "ff0040", "period": 3},
    music {"style": "bars"|"vu"|"beat"|"ripple"|"pulse"}, reactive {"base": "000010", "color": "ffffff"},
    layers {"layers": [{"effect": "keyboard"},
                       {"effect": "solid", "params": {"color": "ff0000"}, "keys": ["W", "A", "S", "D"]},
                       {"effect": "reactive", "params": {"base": "000000"}, "blend": "screen", "opacity": 0.8}]}
    (bottom layer first; keys = where a layer shows, default all; blend normal|add|max|multiply|screen;
    effect "keyboard" = the effect stored on the keyboard)."""
    return call("/run", {"effect": effect, "params": params or {}})


@mcp.tool()
def set_live_keys(colors: dict[str, str | None]) -> dict:
    """Instantly colour specific keys on top of the live effect (or on black if none is running).
    Ideal for notifications/highlighting. colors: {"Esc": "ff0000"}; value null removes that override."""
    return call("/keys", {"colors": colors})


@mcp.tool()
def clear_live_keys() -> dict:
    """Remove all per-key live overrides."""
    return call("/clear", {})


@mcp.tool()
def notify(color: str = "ffffff", keys: list[str] | None = None, flashes: int = 3, period: float = 0.35) -> dict:
    """Flash the keyboard (or only `keys`) to get the user's attention, e.g. when a long task finishes.
    Works on top of any effect and hands the keyboard back afterwards."""
    return call("/notify", {"color": color, "keys": keys, "flashes": flashes, "period": period})


@mcp.tool()
def start_timer(minutes: float, label: str = "") -> dict:
    """Show a countdown on the number row (fills left to right); the board flashes when time is up."""
    return call("/timer", {"minutes": minutes, "label": label})


@mcp.tool()
def cancel_timer() -> dict:
    """Cancel the running number-row timer."""
    return call("/timer", {"cancel": True})


@mcp.tool()
def stop_live() -> dict:
    """Stop live control; the keyboard returns to its stored hardware effect."""
    return call("/stop", {})


@mcp.tool()
def list_profiles() -> list:
    """Saved lighting profiles. Each stores a hardware effect, optional per-key colours and an optional
    live effect or layer stack."""
    return call("/profiles")


@mcp.tool()
def apply_profile(name: str) -> dict:
    """Apply a saved lighting profile by name. It switches the stored effect, the custom colours and
    the live layers in one go."""
    return call("/profiles/apply", {"name": name})


@mcp.tool()
def export_profile(name: str) -> dict:
    """Share code ("evrgb1:...") for a saved profile. Someone else can import it into EVision RGB."""
    return call(f"/profiles/export?name={urllib.parse.quote(name)}&as=code")


@mcp.tool()
def import_profile(code: str) -> dict:
    """Import a profile from a share code ("evrgb1:...") without applying it. Returns the name it was
    saved under; it never replaces an existing profile."""
    return call("/profiles/import", {"code": code})


def main():
    mcp.run()


if __name__ == "__main__":
    main()

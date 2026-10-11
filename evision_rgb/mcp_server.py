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
    solid, reactive (typing ripples), music (audio visualiser of whatever is playing), sysmon (CPU/RAM bars).
    Example params: rainbow {"speed": 0.3}, breathing {"color": "ff0040", "period": 3},
    music {"style": "bars"|"pulse"}, reactive {"base": "000010", "color": "ffffff"}."""
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
def stop_live() -> dict:
    """Stop live control; the keyboard returns to its stored hardware effect."""
    return call("/stop", {})


def main():
    mcp.run()


if __name__ == "__main__":
    main()

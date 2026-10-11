# evision-rgb

Open-source lighting control for **EVision-based keyboards** (USB vendor `0x320F`), starting with the
**AJAZZ AK820** wired RGB. No vendor software needed: per-key colours, the keyboard's built-in effects,
live effects rendered by your PC (~80 fps), music sync, a desktop app, an HTTP API and an MCP server for AI agents.

> Not affiliated with AJAZZ or EVision. Product names are used only to describe compatibility.

## Supported devices
| Device | USB id | Status |
|---|---|---|
| AJAZZ AK820 (wired RGB, fw V0103) | `320F:505B` | **verified**: everything below tested on hardware |
| Marvo KG980A, Koorui MK104, Skillkorp K5, Mars Gaming MK5 | `320F:505B` | same protocol reported; layout differs, untested |
| AJAZZ AK820 **Pro** | `0C45:8009` | different chip and protocol, not supported |

## Features
- **Built-in effects**: all 18 firmware effects with brightness, speed, direction and colour. Stored on the keyboard.
- **Per-key colours**: paint keys in the app, save to the keyboard (works with the app closed).
- **Live effects**: rainbow, breathing, gradient, typing ripples, **music visualiser** (WASAPI loopback, bars or pulse),
  **system monitor** (CPU/RAM bars).
- **App profiles**: lighting follows the program in front (Spotify → music, VS Code → typing ripples, …).
- **Desktop app**: AJAZZ-style UI with a live preview of the keyboard, tray icon, start with Windows, conflict detector.
- **Automation**: local HTTP JSON API and an **MCP server**, so Claude or another agent can drive the lights.
- **Safe by design**: only commands confirmed from the vendor app are on an allow-list. Firmware-update,
  keymap and macro writes are never sent. Everything you write is read back and verified.
- The app backs up the keyboard's lighting the first time it connects, and can restore it any time.

## Install (from source, Windows)
```powershell
git clone https://github.com/HaritMT25/evision-rgb && cd evision-rgb
python -m venv .venv; .venv\Scripts\activate
pip install -e ".[effects,mcp,gui]"
evision-rgb          # desktop app
```
Build a standalone exe: `pip install -e ".[dev]"` then `sh scripts/build_exe.sh` → `dist\EVision RGB\EVision RGB.exe`.

**Close other RGB software first** (the AJAZZ app, OpenRGB, Nexus, SignalRGB). They write to the same channel. Nexus runs
OpenRGB in the background, which sends this keyboard the wrong protocol. The app detects these and offers to close them.

## CLI
```powershell
evrgb info
evrgb effect breath -b 3 -s 1 -c 00a0ff            # brightness 0..4 (5 turns the LEDs off on the AK820)
evrgb custom --all 000010 W=ff0000 A=ff0000 S=ff0000 D=ff0000
evrgb live music                                    # also: rainbow, reactive, sysmon, breathing, gradient, solid
evrgb apps profiles\example.json
evrgb serve                                         # HTTP API on 127.0.0.1:8820
```

## Python
```python
from evision_rgb import Keyboard
with Keyboard() as kb:
    kb.set_effect("normal", brightness=3, color="ff0080")       # stored on the keyboard
    kb.set_custom({"W": "ff0000", "Esc": "00ff00"})              # per-key, stored
    with kb.stream() as s:                                       # live, not stored
        s.fill("000010"); s.set("Enter", "ffffff"); s.show()
```

## HTTP API & MCP
`GET /status /info /keys /effects /modes /frame /custom /settings` ·
`POST /effect /custom /run /keys /clear /stop /apps /restore`. See `evision_rgb/server.py`.

MCP (Claude Code): `claude mcp add evision-rgb -- <venv>\Scripts\python.exe -m evision_rgb.mcp_server`
gives the tools `keyboard_status, list_keys, list_effects, set_hardware_effect, set_custom_colors, run_live_effect,
set_live_keys, clear_live_keys, stop_live`.

## Tests
Behaviour specs run against a **firmware simulator** (`tests/fake_device.py`) that models the observed firmware
behaviour, including its quirks. A mutation check proves the suite catches real past bugs.
```powershell
cd tests; python -m unittest                    # behaviour + unit tests, no keyboard needed
python ..\scripts\mutation_check.py              # each known past bug must make the suite fail
$env:AK820_HW=1; python -m unittest test_hardware   # optional checks on a real keyboard (restores state)
```

## How it works
See **[docs/PROTOCOL.md](docs/PROTOCOL.md)** for the full EVision V2 protocol, LED map and firmware quirks.

## License
MIT. See [LICENSE](LICENSE).

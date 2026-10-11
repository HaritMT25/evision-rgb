# Macro pads (AJAZZ AKP03 / AKP153 and Mirabox twins)

AJAZZ's "stream dock" macro pads have keys with small screens, plain buttons and knobs. They connect over one
USB HID interface (usage page `0xFFA0`). EVision RGB draws the keys and runs an action when a key is pressed or
a knob is turned. Actions can drive the keyboard's lighting, media keys, hotkeys and programs.

> **Status: written from the protocol as implemented by open-source drivers, tested against a strict simulator
> (`tests/fake_pad.py`), not yet on a real pad.** If something looks wrong on your pad, open an issue with the
> model name and USB id.

## Supported models
| Family | Model | USB id | Protocol | Screens |
|---|---|---|---|---|
| AKP03: 6 screen keys, 3 buttons, 3 knobs | AJAZZ AKP03 / AKP03E / AKP03R | `0300:1001` / `1002` / `1003` | 2 | 60×60 |
| | AJAZZ AKP03E / AKP03R (rev. 2) | `0300:3002` / `3003` | 3 | 64×64, turned 90° |
| | Mirabox N3 | `6602:1000`, `6602:1002` | 2 | 60×60 |
| | Mirabox N3 (v3) | `6603:1002`, `6603:1003` | 3 | 64×64, turned 90° |
| AKP153: 15 screen keys + 3 side keys | AJAZZ AKP153 / AKP153E / AKP153R | `5548:6674` / `0300:1010` / `0300:1020` | 1 | 85×85, side 80×80 |
| | AJAZZ AKP153E / AKP153R (rev. 2) | `0300:3010` / `3011` | 3 | 96×96, side 80×80 |
| | Mirabox HSV293S | `5548:6670` | 1 | 85×85, side 80×80 |

Models are matched by USB vendor **and** product id: the vendor ids are shared by unrelated products.
Adding a model is one line in `streamdock.MODELS` when it shares a family layout.

## What the keys can do
Each key, and each knob turn left / turn right / press, has one action:

| Action | Fields | Does |
|---|---|---|
| `profile` | `name` | applies a saved lighting profile |
| `profile_next` / `profile_prev` | | cycles through the saved profiles (made for a knob) |
| `effect` | `effect`, `params` | starts a live effect |
| `layers` | | runs the layer editor's current stack |
| `stop` | | back to the keyboard's own stored effect |
| `hardware` | `mode`, … | stores a built-in effect on the keyboard |
| `brightness` | `step` (±1) | keyboard brightness one step (0–4) |
| `notify` | `color` | flashes the keyboard |
| `timer` | `minutes` | starts, or cancels, the number-row timer |
| `media` | `key` | `play_pause`, `next`, `previous`, `volume_up`, `volume_down`, `mute` |
| `hotkey` | `keys` | sends a key combination such as `ctrl+shift+m` |
| `launch` | `target` | opens a program, file or web address |

The key whose effect, layer stack or profile is running gets a white frame on the pad.

Settings live in `settings.json` under `pads.<family>`:
`{"brightness": 0-100, "keys": [{"label", "icon", "color", "action"}], "knobs": [{"left", "right", "press"}]}`.
API: `GET /pad`, `POST /pad` (partial changes; lists replace entries by position), `POST /pad/key`,
`POST /pad/press {"key": 0}` or `{"knob": 1, "move": "right"}`, `GET /pad/key.png?model=akp03&i=0`.
MCP tools: `pad_status`, `set_pad_key`.

Because a pad action can launch programs, the local API refuses requests from web pages. POSTs must be JSON,
browser `Origin`s other than the app's own are rejected, and so are `Host` names other than 127.0.0.1 / localhost
(DNS rebinding).

## Wire protocol
Sources: [mirajazz](https://github.com/4ndv/mirajazz) (Rust, MPL-2.0),
[Bitfocus Companion's Mirabox module](https://github.com/bitfocus/companion-surface-mirabox-stream-dock) (MIT),
the [OpenDeck AKP plugins](https://github.com/4ndv) and the
[Mirabox StreamDock SDK](https://github.com/MiraboxSpace/StreamDock-Device-SDK) (MIT; its transport is a closed DLL).
No code was copied; `streamdock.py` is a fresh implementation of the documented behaviour.

- **Output report:** report id `0x00` + 512 bytes (protocol 1) or 1024 bytes (protocols 2, 3), zero padded.
- **Command:** `"CRT\0\0"` + verb + arguments. The only verbs ever sent:

  | Verb | Arguments | Meaning |
  |---|---|---|
  | `DIS` | | wake the screens |
  | `LIG` | `00 00 <0-100>` | screen brightness |
  | `BAT` | `<length: 4 bytes big-endian> <key id>` | the next packets are that many bytes of JPEG |
  | `STP` | | show what was uploaded (sent once per batch of images) |
  | `CLE` | `00 00 00 <key id or FF>` / `00 00 'D' 'C'` | clear one key / all / blank on exit |
  | `CONNECT` | | keep-alive, every 8 s (newer firmware resets after 35–60 s without it) |
  | `HAN` | | screens off |

  Boot-logo (`LOG`), device-config (`QUCMD`) and mode (`MOD`) writes are persistent or model specific and are
  never sent; the code refuses any verb that is not on the list above.
- **Images:** JPEG, at most 10 KB, at the key's size, turned clockwise by the model's angle (AKP153: 270°,
  AKP03 rev. 2 / N3 v3: 90°).
- **Input report:** `"ACK\0\0OK\0\0"`, then the event code (byte 9) and state (byte 10). Code `0xFF`
  acknowledges a write and `0x00` means "nothing pressed". Protocols 1 and 2 send one report per click (on release),
  so a click becomes press + release. Protocol 3 sends a press (state `0x01`) and a release (`0x00` or `0x02`,
  depending on firmware).
- **AKP153 key ids**, in reading order: `0D 0A 07 04 01 | 10`, `0E 0B 08 05 02 | 11`, `0F 0C 09 06 03 | 12`
  (the last column holds the side keys). These ids are the same for drawing and input.
- **AKP03 ids:** screen keys `01`–`06`, buttons `25 30 31`. Knobs (left / top / right): turn `90/91`, `50/51`, `60/61`;
  press `33`, `35`, `34`.

## Not supported yet
AKP05 / Mirabox N4 (touch strip, 4 knobs) and AKP815. Their layouts are known (see `analysis/` notes) but drawing
ids differ from input ids. They will be added once someone can test one.

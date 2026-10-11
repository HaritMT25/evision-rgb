# Exact built-in effects: running the keyboard firmware

The keyboard draws its 18 built-in effects with its own firmware, and the host cannot read the LEDs back.
Hand-written approximations kept drifting from the real effects, so the app now **executes the keyboard's
firmware code** in an 8051 emulator to get the exact frames. These are used for the on-screen preview and as
the base layer under overlays (Caps Lock, timers, notifications, shortcut hints).

The firmware image is **not part of this repository**. At runtime the app reads it from the official AJAZZ
updater installed on the user's PC (like emulators that need a BIOS dump). It uses the image only if its
capability block matches the connected keyboard's. Without it, the approximations in `emulate.py` are used.

## Image
- Source: the updater `…\AJAZZ\<product>\Updata\*.exe`, PE resource type `DOWNLOAD`, id 129 (language 0).
  Plain, unencrypted binary. File offset = code address.
- MCU: EVision **VS11K28A**, a WCH **CH555** clone: E8051 1T core, 64 KB flash, 256 B iRAM, 8 KB xRAM,
  hardware RGB-LED PWM for 128 LEDs.
- `0x0000–0x27FF`: bootloader area (not in the update). `0x2800`: application (`LJMP` to the Keil C51 start-up,
  then `main`). The capability block returned by host command `0x03` is stored right after the entry jump, followed
  by the list of supported mode ids (`01–0F, 11, 10, 12, 14, FE`).

## Memory map used by the emulator (fw V0103)
| What | Where |
|---|---|
| Config block (same layout as host command `0x06`) | XDATA `0x0A64` (mode `0x0A65`, brightness `0x0A66`, speed `0x0A67`, direction `0x0A68`, random `0x0A69`, RGB `0x0A6A–0x0A6C`, variant `0x0A76`) |
| Custom-colour areas | CODE `0xA700 + 0x200 × area`. **The displayed area is chosen by config byte 25** |
| LED frame read by the LED hardware | XDATA `0x066D`, 128 × `[0xFF, R, G, B]`, brightness already applied (×~0.78 at 4) |
| Per-key phase / intensity (Rain, Reaction, Traverse) | XDATA `0x05DA + slot` |
| Per-key hue / colour index (Flowers, random colours) | XDATA `0x09B5 + slot` |
| Key → LED slot map (91 entries, `≥0x80` = no LED) | CODE `0x31C1` |
| Effect counter (ticks) | XDATA `0x0A48:0x0A49` |

## Control flow
- **main loop:** key handling → settings/USB processing (ends by calling the tick routine `0x947F`) →
  lighting task `0x8B5C` → USB → indicators, repeat.
- **lighting task:** gated by USB-configured `0x05C6 == 1`, start-up delay `0x0A5F ≥ 30` and the power-on show flag
  `0x0A4B`. Mode `0xFE` = host streaming; modes 1–20 dispatch through a jump table at `0x8C13` (Corrugated `0x7533`,
  Cloud `0x777C`, Serpentine `0x81A3`, Spectrum `0xDB97`, Breath `0x920A`, Normal `0x8F6D`, Reaction `0xEF27`,
  Ripples `0xED22`, Traverse `0xEC3F`, Stars `0xE0E5`, Flowers `0x9CE1`, Roll `0x96CD`, Wave `0xE05C`, Cartoon `0x7FC9`,
  Rain `0x887D`, Scan `0xD8E9`, Surmount `0x90C1`, Speed `0x95A7`, Custom `0x8CBA`).
- **key presses:** handler `0xDF3C` (slot in `0x08EB`, needs the "new key event" flag `0x09A9`). Reactive modes:
  Reaction, Ripples, Traverse, Scan, Surmount, Speed, Custom.
- **output stage** `0x8CB1` (`0xDCD7` side LED, `0xED4D` indicator LEDs). The emulator skips it; the LED hardware
  reads the frame buffer by DMA.

## Timing
- CH555 datasheet: `CLOCK_CFG[2:0] = 110b` gives Fsys = Fpll/3 = **32 MHz** (the CH554's table would say 24 MHz).
- Timer1 runs at Fsys/12 and is reloaded with 800 counts by its ISR body (`0xE36E`): **one interrupt every 300 µs**.
  The ISR advances several counters; the tick routine turns every 2 interrupts into one effect tick.
- The JIT core counts approximate E8051 clock cycles, and emulated time advances by CPU time between interrupts,
  as on the chip.
- **Verified on hardware:** Breath at middle speed shows 8 breaths in 20 s, as the emulation predicts.

## State the real keyboard has that a fresh emulator does not
- **Power-up RAM:** the C start-up clears only internal RAM, so per-key tables start as garbage. The emulator seeds the
  phase and hue tables deterministically. Without this, Rain flashes the whole board and Flowers moves in lockstep.
- **Key-press history:** random-colour and reactive modes keep per-key state from earlier presses, and the app cannot know
  presses made before it started. From then on the preview and overlay engines receive presses (physical transitions
  only, never Windows auto-repeat).
- After a mode change the emulator **settles for 2 s**, so left-over per-key values decay as they do on the keyboard.

## Engines and tests
- `fw8051.py` is the reference interpreter. `fw8051_fast.py` is the numba JIT core, bit-identical to it in differential
  tests (11.5 M firmware instructions, all modes) and 5–200× faster than real time.
- `tests/test_firmware_effects.py` encodes behaviour observed on the physical keyboard. It runs against the real firmware
  locally and is skipped in CI.
- `work/uniformity_scan.py` (local tool) flags modes where the whole board behaves identically when it should not.

## Reproducing the analysis
Ghidra 11 (8051 language, raw binary at base 0) with `scripts/ghidra_fw_seed.py` (seeds functions at `LCALL` targets),
`scripts/ghidra_listing.py` and `scripts/ghidra_mkfunc.py`. Beware of Ghidra's carry idiom `x - ((c << 7) >> 7)`:
the shift yields −1, so the expression **adds** 1. Misreading it once put the Rain table at the wrong address.

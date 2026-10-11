# Built-in effect emulation: fidelity checklist

The keyboard renders its 18 built-in effects internally. The app approximates them on the PC for the
on-screen preview and as the base under overlays (Caps Lock, timers, notifications).
Rows marked *observed* were matched to a description of the real AK820 (fw V0103). The rest are approximations.
Please open an issue describing what the real effect does if one looks wrong.

| id | effect | emulation | status |
|---|---|---|---|
| 01 | Corrugated | horizontal rainbow wave | looks right (user) |
| 02 | Cloud | drifting soft colour blobs | approximation |
| 03 | Serpentine | rainbow along a snake path | approximation |
| 04 | Spectrum | diagonal rainbow | approximation |
| 05 | Breath | fade in/out in the colour (hue cycles if random) | approximation |
| 06 | Normal | steady colour | exact |
| 07 | Reaction | pressed keys flash and fade | approximation |
| 08 | Ripples | ring from the pressed key | approximation |
| 09 | Traverse | **reactive**: key press sends light both ways along its row | **observed** |
| 0A | Stars | random twinkles | approximation |
| 0B | Flowers | random multicolour blooms | approximation |
| 0C | Roll | vertical rainbow scroll | approximation |
| 0D | Wave | rainbow wave (diagonal) | approximation |
| 0E | Cartoon | cluster around H/J/K grows, fades, returns | **observed** |
| 0F | Rain | drops falling down the columns with a trail | **observed** |
| 10 | Scan | bar scanning back and forth | approximation |
| 11 | Surmount | solid preset colour (byte 18: 0 rose, 1 yellow, 2 green, 3 cyan); typing drains it toward blue, idle recovers in ~2 s | **firmware** |
| 12 | Speed | colour pulses enter from one edge (direction) and ripple across, gaps between | **observed** |
| 14 | Custom | stored per-key layout | exact |

Note: the **Random / multi-colour** flag (config byte 5) overrides the chosen colour in effects such as Traverse.
The app now turns it off when you pick a colour.

**Since v0.3 the app runs the keyboard's own firmware code** for these effects when the official AJAZZ updater is installed
(see docs/FIRMWARE.md); the table above describes the fallback approximation used otherwise.

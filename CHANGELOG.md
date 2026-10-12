# Changelog

## 0.7.0 (2026-10-11)
- **Dim the keyboard on battery** (Automation, off by default): the battery saver can also store a lower brightness
  on the keyboard while the laptop runs on battery, since its lights are powered by the laptop's USB port. Plugging
  in brings the old brightness back, unless you changed it meanwhile; this works across a restart too, and for a
  keyboard that was unplugged at the time. `POST /automation {"battery": {"brightness": 1}}` (0..4, null = leave it).
- **Recent events** (Other Settings, `GET /events`, MCP tool `recent_events`, `events.log`): the keyboard
  disconnecting and coming back, battery and mains, what the battery saver paused, dimmed and gave back, game lighting
  and OpenRGB programs taking over, and background errors (each one once a minute), with times. Before, those errors
  went nowhere in the desktop app.
- **Copy diagnostics** (Other Settings, `GET /diagnostics`): versions, the keyboard's state, what is running, the
  settings and the recent events in one block for a bug report, with tokens hidden and your home folder shown as ~.

## 0.6.0 (2026-10-11)
- New live effects after FastLED's classic examples (MIT): **Fire** (a flame in every column, after Fire2012),
  **Ocean** (blue-green waves with whitecaps, after Pacifica), **Twinkle** (keys twinkling like stars, after
  TwinkleFox) and **Digital rain**. They work as layers too.

## 0.5.0 (2026-10-11)
- **Fix: the keyboard reconnects by itself** after it is replugged or its USB connection is reset (sleep, power
  saving). Before, the app kept sending into a dead connection until it was restarted, so the keyboard stayed on its
  own effect whatever you chose. The app also starts without the keyboard and takes over when it is plugged in; the
  status line says when it is disconnected.
- **OpenRGB programs**: the app can answer as an OpenRGB SDK server (Other Settings), so Artemis, SignalRGB with its
  OpenRGB bridge, Home Assistant and OpenRGB scripts light the keyboard through it. Indicators stay on top and your
  lighting comes back when they close. See docs/OPENRGB.md.
- The preview shows keys that glow faintly on the keyboard (like System monitor's dark background) instead of black.
- `evrgb live` knows the game and OpenRGB effects.

## 0.4.0 (2026-10-11)
- **Game lighting** for Counter-Strike 2 and Dota 2 from Valve's Game State Integration: health, ammo, the bomb
  timer, team colour, flashbangs and hits; Dota abilities, items, cooldowns and the respawn countdown. One click
  writes the game's config file; lighting switches on while you play and back afterwards; a demo shows it without
  the game. Games tab, `POST /game/<game>`, `GET /games`, MCP tool `game_lighting`.
- **Album colours**: a live effect that lights the keys in the colours of the cover of whatever is playing
  (Spotify, YouTube in a browser, any player in Windows' media controls), drifting, painted as the cover or as one
  colour, dimmed while paused. No account or API key. A macro pad's play/pause key shows the cover.
  `GET /nowplaying`, `/nowplaying/cover.png`, MCP tool `now_playing`.
- **Battery saver** (on by default): on battery, live effects, layer stacks and app profiles pause and the keyboard
  shows its own built-in effect; plugging in brings back exactly what was running. Indicators keep working.
- **Lighter on CPU**: a picture that doesn't change is drawn about 10 times a second instead of 60 and isn't resent
  to the keyboard (a keep-alive holds it); the app window's preview only receives frames that changed.
- **Downloads**: every version is built on GitHub and published as a Windows zip with checksums and third-party
  licence notices; the built app checks itself (`--selftest`) before it is published.
- Docs: screenshots, a guide for adding other EVision keyboards, contributor notes.

## 0.3.0 (2026-10-11)
- Layer editor: stack live effects with key masks, blend modes and opacity; the keyboard's own effect can be a layer.
- Profiles you can share as files or `evrgb1:` codes; undo/redo for custom colours and layers.
- AJAZZ AKP03 / AKP153 macro pads: key images, actions per key and knob.
- The local API only answers programs on this PC (no web pages, no DNS rebinding).

## 0.2.x (2026-10-10)
- First release: built-in effects, per-key colours, live effects (rainbow, typing ripples, music, screen ambient,
  system monitor), automation (Caps Lock / mic indicators, notifications, timer, idle / lock / night dimming),
  shortcut hints and a typing heatmap, app profiles, desktop app, HTTP API and MCP server.
- Exact preview of the keyboard's built-in effects by running its own firmware code in an 8051 emulator.

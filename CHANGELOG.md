# Changelog

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

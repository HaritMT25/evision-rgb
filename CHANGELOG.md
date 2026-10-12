# Changelog

## 0.7.1 (2026-10-12)
- **Fixes in the app window**, from a review of it:
  - *Light Settings* sends only what you change. Moving one slider stored every field as the form showed it, which
    undid a profile picked from the tray or the pad meanwhile; the form also follows such changes when the window
    comes back, and when the keyboard reconnects.
  - Choosing *Profiles…* at the top of the list no longer applies the first profile, and picking the current profile
    again applies it again (the list is ready for a new pick; the current profile is named beside it).
  - Renaming a profile can't give two profiles the same name, and the macro pad keys that apply it follow the new name
    (they failed). `POST /profiles/rename {"name": "Gaming", "to": "Gaming night"}`.
  - Switching custom colour groups, or *Discard*, no longer ends your lighting: a discarded preview brings back what you
    had (`POST /preview/end`). The first save of another group keeps the layout the keyboard showed as group 1 (it
    was lost).
  - Saving app profiles keeps the default's options, and a refused save puts the switch back.
  - Applying a profile with a layer stack from the window keeps it the current profile (the pad's highlight and *next
    profile* follow it); removing the last layer of a running stack stops it.
  - A pad key whose action is still being set up no longer stops the other keys being saved, and typing in the
    automation or pad fields while a change saves is no longer lost.
- **Recent events**: an unplugged keyboard is the one "keyboard disconnected" event, not also an automation error
  every minute until it is back.

## 0.7.0 (2026-10-12)
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
- The **tray icon's tooltip** says what is running (by the names the app uses) and anything unusual: the keyboard
  disconnected, the battery saver's pause or dim.
- **Fixes: your lighting always comes back.** On battery while a game or an OpenRGB program had the keys, the battery
  saver kept *their* lighting to bring back, so after the game ended or the program left you got its stale frame
  instead of your effect; now your lighting is what waits for the charger. Plugging in while an OpenRGB program
  streams no longer draws over it and loses your effect. Live key colours stay when an indicator or a notification
  ends. Picking other lighting switches app profiles off for good (the App Profiles switch shows it), so the next
  start brings back what you chose; restoring the backup or switching app profiles off is also what stays.
- **Faster first start after an update**: the exact-effect engine compiled for your PC is kept for the next version
  (wherever you unzip it) unless the engine itself changed, instead of being compiled again (up to a minute of one
  CPU core). Its cache now lives with the app's settings in `%APPDATA%\EVisionRGB\numba-cache`.
- **Lighter start**: the keyboard's firmware (for the exact preview) is taken from the AJAZZ updater without parsing
  the rest of that program, which took about 2 s and a ~95 MB memory peak at every start; the updater is no longer
  kept locked while the app runs.
- **Security fixes**, from reading the code for problems: a web page could make the app read any amount of data
  into memory (it read a request's body before checking who sent it); now no request body over 1 MiB is read, and
  a refused request's body hardly at all. `POST /restore` restores only the app's own backup (it took any folder),
  `POST /close_conflict` closes only the programs the window offers to close (not NexusService, nor other programs
  with such a name), `GET /settings` no longer shows the token games post their state with, and only the window's
  own folder is served.
- **OpenRGB programs** that change a built-in effect step by step (a slider) no longer store it on the keyboard at
  every step: the keyboard keeps every change in its flash memory, so a change is stored once the changes rest for
  0.3 s, and about once a second while they go on.
- **Fix: the keyboard is only asked for the memory the official app uses.** `evrgb custom --colorset 3` (the window
  numbers the groups 1 to 3), or the same through the API, wrote past the three custom colour areas into the
  keyboard's program memory. Now it is refused before anything is sent, and the driver refuses any command outside
  the ranges the official app uses.
- **A settings file the app can't read is kept**: one saved by Notepad as "UTF-8 with BOM" is read as it is, and a
  damaged one is kept as `settings.json.bad-<date>` (Recent events says so) instead of being replaced at the next
  save, with your profiles, pad set-up and app rules gone.
- **More fixes from a second review**: starting before the keyboard is plugged in no longer leaves it black when the
  app restores a layer stack over the keyboard's own effect; the macro pad's brightness knob only changes the
  brightness (it ended the live effect and switched app profiles off); app profiles with *Keyboard's own effect* as
  the default apply it at once; app rules with wrong options are refused with the reason, and a rule that fails
  anyway shows in Recent events; clearing live key colours gives the keyboard back instead of leaving it black; the
  mic-mute indicator no longer opens the microphone again ~18 times a second; Shift+Alt shortcut hints show in VS Code
  and Windows Terminal; `evrgb live solid color=990000` works, and so do `evrgb apps` rules with any effect.
- **Fixes from a third review**: custom colours no longer change shade when you save them (the keyboard previewed
  them with the gamma live effects use); the desktop app saves the typing heatmap when it quits, and every minute
  while it counts (it never did, so the counts were lost); a heatmap brought back at start or by an app rule shows
  your counts and follows your typing (it was black); with *Close to tray* off, closing the window quits the app;
  indicators, notifications and the *keyboard* layer keep the stored colours' shade; clearing live key colours gives
  the keyboard back after the pad's brightness knob or the battery saver too; the preview of a custom layout no
  longer reads it from the keyboard 60 times a second; a second copy of the app started at the same moment
  (autostart and a click at login) brings the first one's window forward instead of running beside it.

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

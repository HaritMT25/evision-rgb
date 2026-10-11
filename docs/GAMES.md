# Game lighting (CS2, Dota 2)

The keyboard shows what happens in the game: your health, ammo, the bomb timer, ability cooldowns. It uses Valve's
**Game State Integration**, the official feed that tournament overlays and coaching tools use. The game itself sends
its state to the app on this PC; nothing is injected into the game and nothing is read from its memory.

## Set up
1. Open the app's **Games** tab. It looks through your Steam libraries for the games.
2. Click **Set up** next to the game. This writes one file into the game's `cfg` folder:
   `gamestate_integration_evision_rgb.cfg`. It tells the game to send its state to `http://127.0.0.1:8820` with a
   private token. **Remove set-up** deletes exactly that file again.
3. **Dota 2 only:** in Steam, right-click Dota 2 → Properties → Launch options, add `-gamestateintegration`.
4. Start the game. With "Switch to game lighting while you play" on, the keyboard switches to game lighting in a
   match and back to your own lighting when you leave it or close the game.

**Try the demo** plays a scripted round on the keyboard, without the game.

## What the keys show
**Counter-Strike 2**
- Number row: health (green, yellow, then pulsing red when low).
- F-row: bullets left in the magazine (red when nearly empty). While the bomb is planted it becomes the 40-second
  fuse, and Esc beeps faster as it runs down.
- WASD: your team colour (T orange, CT blue).
- The whole board: a flashbang whites it out, a hit flashes red, a kill glints gold, a defuse flashes blue, and the
  round ends green or red. Dead or watching a teammate: dim red.

**Dota 2**
- Number row: health. F-row: mana.
- Q W E D F: abilities in slot order, R: the ultimate. Bright = ready, dim red = cooldown, dim blue = not enough mana,
  dim = passive.
- Z X C V B N: items, T: the TP scroll (same colours).
- While dead, the number row counts down to respawn. Stuns, hexes and silences tint the board purple. The background
  is warm by day and blue at night.

## Privacy and safety
- The game sends only its own game state, and only to this PC. The app answers it only with the token from the
  config file it wrote; other programs and web pages can't post fake game state.
- Game State Integration is a documented Valve feature for exactly this purpose. The config file is plain text that
  you can read.

## If it doesn't switch
- The Games tab shows "Receiving the game's state" while the game sends updates. If it never does: check that the
  config file is in the game's `cfg` folder (CS2: `game\csgo\cfg`, Dota 2: `game\dota\cfg\gamestate_integration`)
  and, for Dota 2, the launch option.
- The app must be running when the game starts sending. The game retries by itself.

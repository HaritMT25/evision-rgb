"""Re-introduce known past bugs one at a time and check the behaviour suite catches each one.
Restores every file afterwards. Run from the project root:

    python scripts/mutation_check.py [text ...]   (only the mutants whose name contains one of the texts)
A mutant may end with options: {"only": "win32" | "coarse-timers" | "numba" | "pefile"} (a bug that only exists there,
or that only specs needing that optional package can see), {"tests": [modules]}
(the test modules likely to catch it run first; otherwise the ones that mention the mutated module do)."""
import os, re, shutil, subprocess, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = next((p for p in (os.path.join(ROOT, ".venv", "Scripts", "python.exe"), os.path.join(ROOT, ".venv", "bin", "python"))
           if os.path.exists(p)), sys.executable)

MUTANTS = {
    "brightness 5 allowed (turns LEDs off)": ("evision_rgb/keyboard.py", "MAX_BRIGHTNESS = 4", "MAX_BRIGHTNESS = 5"),
    "custom saved in two transactions (flash/stale)": ("evision_rgb/keyboard.py",
        "        self._commit(cfg, frame)\n        if colorset:",
        "        self._commit(cfg)\n        self._write_custom(frame, 0)\n        if colorset:"),
    "custom written to the selected group only (shows wrong layout)": ("evision_rgb/keyboard.py",
        "cfg = self.apply_fields(bytearray(self.dev.read_config(CONFIG_LEN)), mode=\"custom\", colorset=0)\n        self._commit(cfg, frame)",
        "cfg = self.apply_fields(bytearray(self.dev.read_config(CONFIG_LEN)), mode=\"custom\", colorset=colorset)\n        self._commit(cfg)\n        self._write_custom(frame, colorset)"),
    "live stopped before saving (flash of old effect)": ("evision_rgb/server.py",
        "engine.pause()  # the custom-key preview stays on screen while we store it",
        "engine.stop()"),
    "allow-list bypassed": ("evision_rgb/protocol.py",
        "        if cmd not in READ_ONLY | ALLOWED_WRITES:\n            raise",
        "        if False:\n            raise"),
    "echo matched on command byte only (app-style bug)": ("evision_rgb/protocol.py",
        "if r[0] == REPORT_ID and r[1:4] == pkt[1:4] and r[5:7] == pkt[5:7]:",
        "if r[0] == REPORT_ID and r[3] == pkt[3]:"),
    "lighting backup re-taken on every start (restore loses the original)": ("evision_rgb/server.py",
        'if not os.path.isfile(os.path.join(d, "config.bin")):\n            dev = self.kb.dev',
        'if True:\n            dev = self.kb.dev'),
    "ambient: no smoothing (flicker)": ("evision_rgb/effects.py",
        "a = 1 - math.exp(-dt / self.smooth) if self.smooth > 0 else 1.0", "a = 1.0"),
    "ambient: screen captured every frame (CPU hog)": ("evision_rgb/effects.py",
        "if t - self.last_cap >= self.period - 1e-9:", "if True:"),
    "ambient: screen mirrored left/right": ("evision_rgb/effects.py",
        "np.minimum((X * gw).astype(int), gw - 1)", "np.minimum(((1 - X) * gw).astype(int), gw - 1)"),
    "music beat: re-triggers on held bass": ("evision_rgb/effects.py",
        "rising = bass > self.bass_prev + 0.02", "rising = True"),
    "music vu: ignores level": ("evision_rgb/effects.py",
        "level = float(np.mean(self.disp)) * 1.05", "level = 1.05"),
    "automation: indicator hidden by night dimming": ("evision_rgb/automation.py",
        "            for i, rgb in self._indicators():\n                out[i] = rgb\n            out *= self._power_factor()",
        "            out *= self._power_factor()\n            for i, rgb in self._indicators():\n                out[i] = rgb * self._night_factor()"),
    "automation: night window ignores midnight wrap": ("evision_rgb/automation.py",
        "inside = s <= cur < e if s <= e else (cur >= s or cur < e)", "inside = s <= cur < e"),
    "automation: flashes never expire": ("evision_rgb/automation.py",
        "self.flashes = [f for f in self.flashes if t - f[\"t0\"] < f[\"n\"] * f[\"period\"]]", "pass"),
    "automation: stops the user's own live effect when it ends": ("evision_rgb/server.py",
        "if eng.effect is not None and eng.effect.name == \"emulate\":", "if eng.effect is not None:"),
    "automation: never hands the keyboard back": ("evision_rgb/server.py",
        "                            eng.stop()\n                        self.auto_base = False",
        "                            pass\n                        self.auto_base = False"),
    "timer: fills right to left": ("evision_rgb/automation.py",
        "lit = NUMBER_ROW[pos <= prog + 1e-9]", "lit = NUMBER_ROW[(1 - pos) <= prog + 1e-9]"),
    'emulation: overlay base is a flat colour (the user-reported bug)': ('evision_rgb/server.py',
        'eng.run("emulate", **self._emulation_params())',
        'eng.run("solid", color=self.info(0)["color"])'),
    'emulation: preview frozen in time': ('evision_rgb/server.py',
        'colors = emu.render(time.monotonic() - t0)',
        'colors = emu.render(0.0)'),
    'emulation: direction ignored': ('evision_rgb/emulate.py',
        'self.x = (1 - X) if int(direction) == 1 else X',
        'self.x = X'),
    'hints: shown instantly (flicker during Ctrl+C)': ('evision_rgb/automation.py',
        'if not mods or t - self._mods_since < int(h["delay_ms"]) / 1000:',
        'if not mods:'),
    'hints: rest of the board not dimmed': ('evision_rgb/automation.py',
        'out *= float(self.cfg["hints"]["dim"])',
        'pass'),
    'heatmap: counts while disabled (privacy)': ('evision_rgb/heatmap.py',
        'if self.enabled and key in KEYS:',
        'if key in KEYS:'),
    "heatmap: clear doesn't persist": ('evision_rgb/heatmap.py',
        '            self.since = dt.date.today().isoformat()\n        self.save()',
        '            self.since = dt.date.today().isoformat()'),
    'traverse: lights every row': ('evision_rgb/emulate.py',
        'row = np.abs(YY - py) < 0.02',
        'row = np.ones(N, bool)'),
    'rain: drops fall upward': ('evision_rgb/emulate.py',
        'd[1] += dt * d[2] * r',
        'd[1] -= dt * d[2] * r'),
    'speed: one continuous band (no gaps)': ('evision_rgb/emulate.py',
        'band = np.exp(-((x - pos) / 0.06) ** 2)',
        'band = np.ones(N)'),
    'key input: Windows auto-repeat re-triggers effects (double ripple)': ('evision_rgb/effects.py',
        'if vk is None or vk in self.down:   # auto-repeat while held',
        'if vk is None:'),
    'engine: render loop takes the lock stop() holds (2 s stall per stop)': ('evision_rgb/effects.py',
        '            for i, c in dict(self.overrides).items():\n                colors[i] = c',
        '            with self.lock:\n                for i, c in self.overrides.items():\n                    colors[i] = c'),
    # feature 4: layers, profile sharing, undo/redo
    'layers: a layer paints every key (mask ignored)': ('evision_rgb/effects.py',
        'out = mixed if L.mask is None else np.where(L.mask[:, None], mixed, out)',
        'out = mixed'),
    'layers: opacity ignored': ('evision_rgb/effects.py',
        '                mixed = out + (mixed - out) * L.opacity',
        '                pass'),
    'layers: firmware LED values gamma-corrected twice (too dark)': ('evision_rgb/effects.py',
        '                c = c ** (1 / GAMMA)',
        '                pass'),
    'layers: switched-off layers still run (music captures audio while off)': ('evision_rgb/effects.py',
        '            if L.enabled:  # a switched-off music/ambient layer must not capture anything',
        '            if True:'),
    'layers: unknown keys accepted (fails later, mid-stream)': ('evision_rgb/effects.py',
        '                if i is None:\n                    raise ValueError(f"{where}: unknown key {key!r}")\n                mask[i] = True',
        '                if i is not None:\n                    mask[i] = True'),
    'layers: stack on the stored effect dropped when the stored effect changes': ('evision_rgb/server.py',
        '            if keep:\n                eng.run(keep[0], **keep[1])',
        '            if False:\n                eng.run(keep[0], **keep[1])'),
    'profiles: import reuses an existing name': ('evision_rgb/profiles.py',
        '    if name not in taken:\n        return name',
        '    return name'),
    'profiles: shared names keep markup (script injection)': ('evision_rgb/profiles.py',
        's = "".join(ch for ch in s if ch not in "<>" and not unicodedata.category(ch).startswith("C"))',
        's = s'),
    'profiles: oversized share codes not refused (zip bomb)': ('evision_rgb/profiles.py',
        '    if len(raw) > MAX_JSON or d.unconsumed_tail:',
        '    if False:'),
    'profiles: files from newer versions accepted': ('evision_rgb/profiles.py',
        'not 1 <= v <= VERSION', 'not 1 <= v'),
    'profiles: importing applies the profile to the keyboard right away': ('evision_rgb/server.py',
        '        self.update_settings({"profiles": existing + [p]})\n        return p',
        '        self.update_settings({"profiles": existing + [p]})\n        self.apply_profile(p)\n        return p'),
    'apply: live effect stopped first (old lighting flashes)': ('evision_rgb/server.py',
        '        self._lighting_chosen()\n        self.engine.pause()\n        try:\n            if hw:',
        '        self._lighting_chosen()\n        self.engine.stop()\n        try:\n            if hw:'),
    'apply: live layers never resume after storing (stream left paused)': ('evision_rgb/effects.py',
        'self.stream.paused = False', 'self.stream.paused = self.stream.paused'),
    'apply: custom colours written in a second transaction (stale frame)': ('evision_rgb/keyboard.py',
        '        self._commit(cfg, frame)\n        return self._decode(bytes(cfg))',
        '        self._commit(cfg, None if new_frame is not None else frame)\n        if new_frame is not None:\n'
        '            self._write_custom(new_frame, 0)\n        return self._decode(bytes(cfg))'),
    'undo: snapshots stored by reference (later edits rewrite history)': ('evision_rgb/gui/web/history.js',
        '        const snap = copy(s);', '        const snap = s;'),
    'undo: a new edit keeps the redo stack': ('evision_rgb/gui/web/history.js',
        '        stack = stack.slice(0, i + 1);', '        stack = stack.slice(0);'),
    'undo: history grows without limit': ('evision_rgb/gui/web/history.js',
        '        if (stack.length > limit + 1) stack.shift();', ''),
    # macro pads + local-only API
    'pad: commands outside the allow-list can be sent': ('evision_rgb/streamdock.py',
        '        if verb not in VERBS:', '        if False:'),
    'pad: key images not turned for the model': ('evision_rgb/streamdock.py',
        '    if model.rotate:\n        im = im.transpose', '    if False:\n        im = im.transpose'),
    'pad: protocol 1/2 clicks never released': ('evision_rgb/streamdock.py',
        '            return [PadEvent(control, c.index, "down"), PadEvent(control, c.index, "up")]',
        '            return [PadEvent(control, c.index, "down")]'),
    'pad: repeated press reports re-trigger keys (protocol 3)': ('evision_rgb/streamdock.py',
        '        if pressed == (k in self.held):\n            return []', '        if False:\n            return []'),
    'pad: release code 0x02 taken for a press': ('evision_rgb/streamdock.py',
        'pressed, k = state == 0x01, (control, c.index)', 'pressed, k = state != 0x00, (control, c.index)'),
    'pad: no keep-alive (newer pads reset)': ('evision_rgb/macropad.py',
        '                if now - self._last_beat >= self.HEARTBEAT_S:', '                if False:'),
    'pad: unplugged pad never reconnects': ('evision_rgb/macropad.py',
        '        self._next_scan = self.clock() + self.SCAN_S', '        self._next_scan = float("inf")'),
    'pad: one refresh per image (slow, flickers)': ('evision_rgb/macropad.py',
        '            self._drawn[c.index] = look\n            changed = True\n        if changed:\n            self.dock.refresh()',
        '            self._drawn[c.index] = look\n            self.dock.refresh()\n            changed = True\n        if False:\n            pass'),
    'pad: running effect not highlighted': ('evision_rgb/macropad.py',
        '        self._active = self._active_key(cfg)', '        self._active = None'),
    'pad: brightness knob goes past 4 (5 turns the LEDs off)': ('evision_rgb/macropad.py',
        'nb = max(0, min(4, b + int(a["step"])))', 'nb = max(0, b + int(a["step"]))'),
    'pad: profile dial does not wrap around': ('evision_rgb/macropad.py',
        'i = (0 if step > 0 else len(ps) - 1) if cur is None else (cur + step) % len(ps)',
        'i = (0 if step > 0 else len(ps) - 1) if cur is None else min(cur + step, len(ps) - 1)'),
    'pad config: hotkeys with unknown keys accepted': ('evision_rgb/macropad.py',
        '    if t == "hotkey":\n        parse_hotkey(a.get("keys"))', '    if t == "hotkey":\n        pass'),
    'local API: cross-site form posts accepted (CSRF)': ('evision_rgb/server.py',
        '            elif post and not self.path.startswith("/game/") and \\\n                    not (self.headers.get("Content-Type") or "").lower().startswith("application/json"):',
        '            elif False:'),
    'local API: requests from other web origins accepted': ('evision_rgb/server.py',
        '            elif origin is not None and origin.lower() not in {"http://" + h for h in ours}:',
        '            elif False:'),
    'local API: any Host name accepted (DNS rebinding)': ('evision_rgb/server.py',
        '            if host is not None and host.lower() not in ours:', '            if False:'),
    # -- battery saver & CPU savings
    'still frames: unchanged frames resent every frame (CPU, USB)': ('evision_rgb/effects.py',
        "            if frame != sent:  # an unchanged frame is not resent: the stream's keep-alive holds it",
        '            if True:'),
    'still frames: changed frames never sent': ('evision_rgb/effects.py',
        "            if frame != sent:  # an unchanged frame is not resent: the stream's keep-alive holds it",
        '            if sent is None:'),
    'still frames: drawing never slows down': ('evision_rgb/effects.py',
        '            self.idle = now - changed >= self.idle_after', '            self.idle = False'),
    'still frames: drawing slowed while moving (choppy)': ('evision_rgb/effects.py',
        '            if self.idle:  # slow rate; a key press or an overlay ends the wait at once', '            if True:'),
    'still frames: a key press does not wake a still effect': ('evision_rgb/effects.py',
        '            self.callback(name)\n            wake_engines()', '            self.callback(name)'),
    'still frames: a live key colour does not wake a still effect': ('evision_rgb/effects.py',
        '                self.stop()\n            self.wake()\n        return self.status()', '                self.stop()\n        return self.status()'),
    'still frames: stop waits for the slow idle frame': ('evision_rgb/effects.py',
        '                self._stop.set()\n                self.wake()', '                self._stop.set()'),
    'still frames: full-speed frames paced by Event.wait (30 fps where its timeout is coarse, e.g. Windows)': (
        'evision_rgb/effects.py', '                time.sleep(max(0.0, period - (time.monotonic() - now)))',
        '                self._wake.wait(max(0.0, period - (time.monotonic() - now)))', {"only": "coarse-timers"}),
    'still frames: a notification does not wake a still effect': ('evision_rgb/automation.py',
        '"n": max(1, int(flashes)), "period": max(0.05, float(period))})\n        wake_engines()',
        '"n": max(1, int(flashes)), "period": max(0.05, float(period))})'),
    'preview: still pictures resent to the window 60x/s': ('evision_rgb/server.py',
        '                    if f != last:\n                        self.wfile.write(b"data: "',
        '                    if True:\n                        self.wfile.write(b"data: "'),
    'preview: no heartbeat (a closed window keeps its stream thread)': ('evision_rgb/server.py',
        '                    elif t - beat >= heartbeat_s:', '                    elif False:'),
    'battery: unplugging pauses nothing': ('evision_rgb/server.py',
        '            if saving and not self._saving:', '            if False:'),
    'battery: plugging in brings nothing back': ('evision_rgb/server.py',
        '        p, self.battery_paused = self.battery_paused, None\n        eng = self.engine',
        '        p, self.battery_paused = None, None\n        eng = self.engine'),
    'battery: the saver switch is ignored': ('evision_rgb/server.py',
        '        return bool(self._on_battery) and self.automation.cfg["battery"]["enabled"]',
        '        return bool(self._on_battery)'),
    'battery: a PC without a battery counts as on battery': ('evision_rgb/server.py',
        '        return bool(self._on_battery) and self.automation.cfg["battery"]["enabled"]',
        '        return self._on_battery is not False and self.automation.cfg["battery"]["enabled"]'),
    'battery: an effect you chose on battery is replaced when plugging in': ('evision_rgb/server.py',
        '        over it."""\n        self.battery_paused = None', '        over it."""'),
    'battery: start-up on battery starts the effect anyway': ('evision_rgb/server.py',
        '            if want and self._battery_saving():', '            if False:'),
    'battery: the indicator mirror is paused as if it were your effect': ('evision_rgb/server.py',
        '        if eng.effect is not None and not (self.auto_base and eng.effect.name == "emulate"):',
        '        if eng.effect is not None:'),
    'battery: live key colours lost over a pause': ('evision_rgb/server.py',
        '            self.engine.overrides.update(p["overrides"])\n            self.engine.run',
        '            self.engine.run'),
    'battery: app profiles keep running on battery': ('evision_rgb/server.py',
        '        if self.apps:\n            p = {"apps": self.apps.profile}',
        '        if False:\n            p = {"apps": self.apps.profile}'),
    'battery: the setting accepts anything': ('evision_rgb/automation.py',
        '    if not isinstance(cfg["battery"]["enabled"], bool):', '    if False:'),
    # -- battery saver: dim the keyboard on battery
    'battery dim: plugging in never brings the brightness back': ('evision_rgb/server.py',
        '        back = rec["from"] if rec is not None and cur == rec["to"] else cur', '        back = cur'),
    'battery dim: a brightness you set on battery is undone': ('evision_rgb/server.py',
        '        back = rec["from"] if rec is not None and cur == rec["to"] else cur',
        '        back = rec["from"] if rec is not None else cur'),
    'battery dim: a dimmer keyboard is made brighter on battery': ('evision_rgb/server.py',
        'if want is not None and back > want else (back, None)', 'if want is not None else (back, None)'),
    'battery dim: the dim is not remembered': ('evision_rgb/server.py',
        '        if new is not None and new != rec:  #', '        if False:  #'),
    'battery dim: a failing write is tried again on every tick': ('evision_rgb/server.py',
        '        if time.monotonic() < self._dim_retry:', '        if False:'),
    'battery dim: a dim left from before a restart stays': ('evision_rgb/server.py',
        '        self._dim_for = _UNSET      #', '        self._dim_for = None        #'),
    'battery dim: an unplugged keyboard is never dimmed': ('evision_rgb/server.py',
        'tried again on the next tick\n            return', 'tried again on the next tick\n            self._dim_for = want\n            return'),
    'battery dim: your effect resumes before the brightness is restored': ('evision_rgb/server.py',
        '                self._battery_dim(False)  #', '                pass  #'),
    'battery dim: a live effect stops at the brightness change': ('evision_rgb/server.py',
        '            if keep:\n                params =', '            if False:\n                params ='),
    'battery dim: the setting accepts anything': ('evision_rgb/automation.py',
        '    if b is not None and (isinstance(b, bool)', '    if False and (isinstance(b, bool)'),
    # -- event log
    'events: the keyboard going away is not logged': ('evision_rgb/server.py',
        '        if c != self._kb_seen:', '        if False:'),
    'events: power changes are not logged': ('evision_rgb/server.py',
        '        if self._on_battery in (True, False) and self._on_battery != self._power_seen:', '        if False:'),
    'events: the same error is logged every tick': ('evision_rgb/events.py',
        '            if now - self._errors.get(key, -1e18) < ERROR_EVERY_S:\n                return',
        '            if False:\n                return'),
    'events: the file grows forever': ('evision_rgb/events.py',
        '>= self.max_bytes:', '>= self.max_bytes * 1000:'),
    'events: earlier events are forgotten at start': ('evision_rgb/events.py',
        '        for line in self._tail():', '        for line in []:'),
    'events: a file that cannot be written breaks the app': ('evision_rgb/events.py',
        '            except OSError:\n                pass\n        return ev', '            except ZeroDivisionError:\n                pass\n        return ev'),
    'events: every event ever is listed': ('evision_rgb/events.py',
        'collections.deque(maxlen=KEEP)', 'collections.deque()'),
    'events: what the battery saver paused is not logged': ('evision_rgb/server.py',
        '        if self.battery_paused:\n            self.events.add', '        if False:\n            self.events.add'),
    # -- diagnostics
    'diagnostics: tokens are shown': ('evision_rgb/server.py',
        'if "token" in str(k).lower() and x', 'if "secret" in str(k).lower() and x'),
    'diagnostics: the home folder is shown': ('evision_rgb/server.py',
        '        return home.sub("~", v)', '        return v'),
    'diagnostics: the home folder hides only in one letter case': ('evision_rgb/server.py',
        '(?![\\w.-])", re.I)', '(?![\\w.-])", 0)'),
    'diagnostics: settings keys are not scrubbed': ('evision_rgb/server.py',
        '        return {_scrub(k, home): ', '        return {k: '),
    # -- tray tooltip
    'tray: a disconnected keyboard is not mentioned': ('evision_rgb/server.py',
        '        if not self.kb.dev.connected:\n            lines.append', '        if False:\n            lines.append'),
    'tray: app profiles are not mentioned': ('evision_rgb/server.py',
        '(" (app profiles)" if self.apps else "")', '("")'),
    'tray: effects by their internal id': ('evision_rgb/server.py',
        '        name = lambda e: EFFECT_NAMES.get(e, e)', '        name = lambda e: e'),
    'diagnostics: an unplugged keyboard breaks it': ('evision_rgb/server.py',
        '        except Exception as e:  # noqa: BLE001 - unplugged: say so', '        except ZeroDivisionError as e:'),
    # -- handing the lighting between its owners (review findings)
    'handoffs: the battery saver pauses a game as if it were your lighting': ('evision_rgb/server.py',
        '        if self.game_on:  # the game only borrowed the keys', '        if False:  # the game only borrowed the keys'),
    'handoffs: plugging in draws your lighting over an OpenRGB program': ('evision_rgb/server.py',
        '        if p and self.ext_on and eng.effect is not None and eng.effect.name == "openrgb":', '        if False:'),
    'handoffs: a program that left on battery comes back on the charger': ('evision_rgb/server.py',
        '                if (self.battery_paused or {}).get("effect") == "openrgb":  #', '                if False:  #'),
    'handoffs: a program leaving on battery starts your lighting on battery': ('evision_rgb/server.py',
        '            if self._saving:  # on battery nothing of yours runs', '            if False:  # on battery nothing of yours runs'),
    'handoffs: live key colours vanish when the indicator mirror ends': ('evision_rgb/server.py',
        '                            if eng.overrides:  # live key colours', '                            if False:  # live key colours'),
    'handoffs: app profiles you replaced come back at the next start': ('evision_rgb/server.py',
        '        if persist and cfg.get("enabled"):', '        if False:'),
    'handoffs: a preview switches app profiles off for good': ('evision_rgb/server.py',
        'self._apps_off(persist=remember)', 'self._apps_off(persist=True)'),
    'handoffs: the battery saver switches app profiles off for good': ('evision_rgb/server.py',
        '            p = {"apps": self.apps.profile}\n            self.stop_apps()',
        '            p = {"apps": self.apps.profile}\n            self._apps_off()'),
    'handoffs: a restored backup is replaced by the old effect at the next start': ('evision_rgb/server.py',
        '                        ctl.remember_live(None)  # the restored lighting', '                        pass  # the restored lighting'),
    'handoffs: app profiles off brings the old effect back at the next start': ('evision_rgb/server.py',
        '                        ctl.remember_live(None)  # off means', '                        pass  # off means'),
    'handoffs: putting back no game feed leaves None for every later controller': ('evision_rgb/games.py',
        '        if f is not None:\n            _FEEDS[game] = f', '        if True:\n            _FEEDS[game] = f'),
    # -- packaged app: numba cache of the firmware engine
    'jit cache: stamped by the .exe, so every version compiles it again': ('evision_rgb/jit_cache.py',
        '        st = os.stat(self._py_file)\n        return _content_hash(self._py_file, st.st_mtime, st.st_size)',
        '        st = os.stat(sys.executable)\n        return _content_hash(sys.executable, st.st_mtime, st.st_size)',
        {"only": "numba"}),
    'jit cache: a changed engine runs the old compiled code': ('evision_rgb/jit_cache.py',
        '        return _content_hash(self._py_file, st.st_mtime, st.st_size)', '        return b"always the same"',
        {"only": "numba"}),
    'jit cache: numba is taken over outside the packaged app too': ('evision_rgb/jit_cache.py',
        '        if not getattr(sys, "frozen", False) or not config.CACHE_DIR:', '        if not config.CACHE_DIR:',
        {"only": "numba"}),
    'jit cache: other packages\' functions are taken over too': ('evision_rgb/jit_cache.py',
        '        if not str(getattr(py_func, "__module__", "")).startswith("evision_rgb."):', '        if False:',
        {"only": "numba"}),
    # -- found by a second review (2026-10-12)
    'review: the keyboard is asked for memory past what the official app uses': ('evision_rgb/protocol.py',
        '        if not any(lo <= offset and offset + max(length, len(data)) <= hi for lo, hi in RANGES.get(cmd, ((0, 0),))):',
        '        if False:'),
    'review: custom colours are sent to a group that does not exist': ('evision_rgb/keyboard.py',
        '        self.check_colorset(colorset)\n        frame = bytes(colors)', '        frame = bytes(colors)'),
    'review: custom colours are read from a group that does not exist': ('evision_rgb/keyboard.py',
        '        raw = self.dev.read_custom(self.check_colorset(colorset))', '        raw = self.dev.read_custom(colorset)'),
    'review: evrgb custom takes any --colorset': ('evision_rgb/cli.py',
        '    c.add_argument("--colorset", type=int, default=0, choices=range(3))',
        '    c.add_argument("--colorset", type=int, default=0)'),
    'review: a refused /custom stops the live effect anyway': ('evision_rgb/server.py',
        '                        colorset = Keyboard.check_colorset(req.get("colorset", 0))  #',
        '                        colorset = req.get("colorset", 0)  #'),
    'review: an effect that cannot start is left half running (black keyboard)': ('evision_rgb/effects.py',
        '            try:\n                eff.start()  # before it is published: an effect that cannot start is never left half running\n'
        '            except Exception:\n                eff.stop()\n                if self.stream:  # nothing live now: the keyboard shows its own effect\n'
        '                    self.stream.close()\n                    self.stream = None\n                raise\n'
        '            self._ensure_stream()\n            self.stream.paused = False  # resume after pause() (e.g. a profile stored first, then its live part)\n'
        '            self.effect = eff\n            self.params = dict(params)\n            self._stop = threading.Event()\n',
        '            self._ensure_stream()\n            self.stream.paused = False  # resume after pause() (e.g. a profile stored first, then its live part)\n'
        '            self.effect = eff\n            self.params = dict(params)\n            self._stop = threading.Event()\n            eff.start()\n'),
    'review: the keyboard layer needs the keyboard the moment it starts': ('evision_rgb/emulate.py',
        '            except (ProtocolError, OSError):\n                self._next_try = time.monotonic() + self.RETRY_S\n                return None\n',
        '            except (ProtocolError, OSError):\n                raise\n'),
    'review: the pad brightness knob ends the live effect and app profiles': ('evision_rgb/macropad.py',
        '                    ctl._store_brightness(nb)', '                    ctl.set_hardware(brightness=nb)'),
    "review: app profiles with the keyboard's own default do nothing at first": ('evision_rgb/apps.py',
        '            if rule is not self.current or not self._applied:', '            if rule is not self.current:'),
    'review: a rule without a match crashes app profiles or matches every program': ('evision_rgb/apps.py',
        '            if isinstance(m, str) and m.strip() and m.lower() in hay:', '            if m.lower() in hay:'),
    'review: /apps accepts rules that cannot run': ('evision_rgb/server.py',
        '                        cfg = check({**ctl.settings.get("apps", {}), **req})',
        '                        cfg = {**ctl.settings.get("apps", {}), **req}'),
    'review: a failing app rule is only printed (no event)': ('evision_rgb/server.py',
        '        self.apps = AppProfiles(self.engine, cfg, on_error=lambda app, e: self._log_error(f"app profiles ({app})", e))',
        '        self.apps = AppProfiles(self.engine, cfg)'),
    'review: a settings file with a BOM is not read (and replaced)': ('evision_rgb/settings.py',
        '        with open(path, encoding="utf-8-sig") as f:', '        with open(path, encoding="utf-8") as f:'),
    'review: an unreadable settings file is overwritten at the next save': ('evision_rgb/settings.py',
        '                os.replace(PATH, kept)\n', '                pass\n'),
    'review: a settings file that is not an object crashes the start': ('evision_rgb/settings.py',
        '        if not isinstance(data, dict):\n            raise ValueError("not a JSON object")\n', ''),
    'review: an unreadable settings file is not in the event log': ('evision_rgb/server.py',
        '        if settings_mod.problem:\n            self.events.add(settings_mod.problem)\n', ''),
    'review: clearing live keys leaves the keyboard black': ('evision_rgb/effects.py',
        '            if self.under_keys:  # the black base only held them: the keyboard shows its own effect again\n'
        '                self.stop()\n', ''),
    'review: taking the last live key away leaves the keyboard black': ('evision_rgb/effects.py',
        '            elif self.under_keys and not self.overrides:\n                self.stop()\n', ''),
    'review: after an indicator, clearing live keys leaves the keyboard black': ('evision_rgb/server.py',
        '                                eng.run_under_keys()', '                                eng.run("solid", color="000000")'),
    'review: evrgb live turns colours like 990000 into numbers': ('evision_rgb/cli.py',
        '            params = _params(a.effect, (x.split("=", 1) for x in a.params))',
        '            params = {k: _val(v) for k, v in (x.split("=", 1) for x in a.params)}'),
    'review: evrgb apps knows only some effects': ('evision_rgb/cli.py',
        '            load_effects()  # (rules may use any of them)\n', ''),
    'review: the mic indicator opens Core Audio again whenever another thread asks': ('evision_rgb/automation.py',
        '            if self.vol is not None and owner is not me and owner is not None and owner.is_alive():\n'
        '                return self.value\n', ''),
    'review: shift+alt hints never show': ('evision_rgb/shortcuts.py',
        '            if _same(written, c):', '            if written == c:'),
    # -- found by a review of the window (2026-10-12): its server side (the window's side: scripts/ui_smoke.mjs)
    'ui review: renaming gives two profiles the same name': ('evision_rgb/server.py',
        '        if new != name and self.find_profile(new) is not None:', '        if False:'),
    'ui review: renaming accepts no name': ('evision_rgb/server.py',
        '        if not isinstance(to, str) or not to.strip():', '        if not isinstance(to, str):'),
    'ui review: pad keys keep a renamed profile\'s old name': ('evision_rgb/server.py',
        '            return {**a, "name": new} if isinstance(a, dict) and a.get("type") == "profile" and a.get("name") == name else a',
        '            return a'),
    'ui review: a renamed current profile stops being the current one': ('evision_rgb/server.py',
        '        if self.active_profile == name:\n            self.active_profile = new\n', ''),
    'ui review: ending a preview brings nothing back': ('evision_rgb/server.py',
        '        else:\n            self._give_back(want)\n        return self.engine.status()',
        '        return self.engine.status()'),
    'ui review: an unplugged keyboard is an automation error every minute in the events': ('evision_rgb/server.py',
        '                if isinstance(e, (ProtocolError, OSError)) and not self.kb.dev.connected:', '                if False:'),
    'ui review: ending a preview on battery starts your lighting on battery': ('evision_rgb/server.py',
        '        if want and self._saving:\n            self.battery_paused = want\n        else:\n            self._give_back(want)',
        '        self._give_back(want)'),
    # -- found by a third review (2026-10-12)
    'review 2: custom colours change shade when saved (previewed with the live gamma)': ('evision_rgb/effects.py',
        '        self.raw = bool(raw)', '        self.raw = False'),
    'review 2: the custom tab previews with the live gamma': ('evision_rgb/gui/web/app.js',
        'keys: S.custom, raw: true }', 'keys: S.custom }'),
    'review 2: quitting the app loses the typing heatmap': ('evision_rgb/gui/app.py',
        '                self.ctl.close()  # keyboard falls back', '                self.ctl.engine.close()  # keyboard falls back'),
    'review 2: closing the window with close-to-tray off does not quit': ('evision_rgb/gui/app.py',
        '            return True  # the window closes; run() then quits',
        '            self.quitting = True\n            return True  # the window closes; run() then quits'),
    'review 2: heatmap counts are only saved at quit (lost in a crash)': ('evision_rgb/server.py',
        '        if self.heatmap.unsaved and time.monotonic() - self._heatmap_saved >= HEATMAP_SAVE_S:', '        if False:'),
    'review 2: a heatmap brought back at start is black': ('evision_rgb/heatmap.py',
        '        self.counter = _SOURCE["counter"] if counts is None else None', '        self.counter = None'),
    'review 2: the heatmap does not follow the typing': ('evision_rgb/heatmap.py',
        '        if self.counter is not None and t - self._drawn >= self.REFRESH_S:',
        '        if self.counter is not None and self._drawn < 0:'),
    'review 2: indicators draw the stored colours darker (the approximation)': ('evision_rgb/emulate.py',
        '        self.level = 0.2 + 0.8 * max(0, min(4, int(brightness))) / 4 if dim else 1.0\n        self.raw = not dim\n',
        '        self.level = 0.2 + 0.8 * max(0, min(4, int(brightness))) / 4 if dim else 1.0\n'),
    "review 2: the pad's brightness knob turns the live keys' black base into an effect": ('evision_rgb/server.py',
        '                eng.under_keys = keep[2]  #', '                pass  #'),
    "review 2: a battery round trip turns the live keys' black base into an effect": ('evision_rgb/server.py',
        '            self.engine.under_keys = bool(p.get("under_keys"))  #', '            pass  #'),
    'review 2: the preview reads the custom colours for every frame': ('evision_rgb/server.py',
        '"colors": _preview_hexes(self.stored_custom())}', '"colors": _preview_hexes(self.kb.get_custom(0))}'),
    'review 2: new custom colours show a second late in the preview': ('evision_rgb/server.py',
        '                    ctl._forget_cached()', '                    ctl._info_cache = (0.0, None)'),
    'review 2: a second app can listen on the same port (Windows)': ('evision_rgb/server.py',
        '    allow_reuse_address = os.name != "nt"\n\n    def server_bind(self):\n        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):',
        '    allow_reuse_address = True\n\n    def server_bind(self):\n        if False:', {"only": "win32"}),
    'review 2: the app touches the keyboard before it has its port': ('evision_rgb/gui/app.py',
        '        try:  # first: another app with the port (one still starting) is found before anything touches the keyboard\n'
        '            self.httpd = listen(self.port)\n        except OSError as e:\n'
        '            raise PortTaken(f"port {self.port} is in use ({e})") from e\n        self.ctl = Controller()\n',
        '        self.ctl = Controller()\n'
        '        try:  # first: another app with the port (one still starting) is found before anything touches the keyboard\n'
        '            self.httpd = listen(self.port)\n        except OSError as e:\n'
        '            raise PortTaken(f"port {self.port} is in use ({e})") from e\n'),
    'review 2: a second launch does not bring the first window forward': ('evision_rgb/gui/app.py',
        '            if _running(a.port):\n                if not a.background:',
        '            if _running(a.port):\n                if False:'),
    # -- the firmware image from the AJAZZ updater
    'firmware lookup: the whole updater is parsed for one resource (~2 s, ~95 MB at every start)': (
        'evision_rgb/firmware.py', '    pe = pefile.PE(path, fast_load=True)', '    pe = pefile.PE(path, fast_load=False)',
        {"only": "pefile"}),
    'firmware lookup: the resources are never read (no exact preview)': ('evision_rgb/firmware.py',
        '        pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_RESOURCE"]])\n',
        '', {"only": "pefile"}),
    'firmware lookup: the updater stays open (locked) after reading it': ('evision_rgb/firmware.py',
        '    finally:\n        pe.close()', '    finally:\n        pass', {"only": "pefile"}),
    # -- hardening (a read-through for security problems, 2026-10-12)
    'hardening: a body of any size is read (a web page can fill the memory)': ('evision_rgb/server.py',
        'MAX_BODY = 1 << 20  #', 'MAX_BODY = 1 << 40  #'),
    'hardening: a refused request is read to the end first': ('evision_rgb/server.py',
        '            self._discard(length)\n            self._send(*no)', '            self.rfile.read(length)\n            self._send(*no)'),
    'hardening: a negative Content-Length reads until the sender hangs up': ('evision_rgb/server.py',
        '                if not 0 <= n <= MAX_BODY:  #', '                if n > MAX_BODY:  #'),
    'hardening: every OpenRGB effect change is stored on the keyboard (flash wear)': ('evision_rgb/openrgb.py',
        '            else:\n                self.modes.put(conn, index, mode)', '            else:\n                self.app.orgb_mode(conn, index, mode)'),
    'hardening: effect changes a client keeps sending are never stored': ('evision_rgb/openrgb.py',
        '                    left = min(self.last + MODE_REST_S, self.first + MODE_EVERY_S) - time.monotonic()',
        '                    left = self.last + MODE_REST_S - time.monotonic()'),
    'hardening: a waiting effect change is stored after the client switched to Direct': ('evision_rgb/openrgb.py',
        '        elif pid == CUSTOM_MODE:\n            self.modes.drop()\n', '        elif pid == CUSTOM_MODE:\n'),
    'hardening: /restore writes any folder it is given': ('evision_rgb/server.py',
        '                        d = ctl.backup_dir\n                        if req.get("dir") not in (None, d):',
        '                        d = req.get("dir") or ctl.backup_dir\n                        if req.get("dir") not in (None, d):'),
    'hardening: any program with a listed name can be closed (NexusService)': ('evision_rgb/system.py',
        '    if not any(k in p.name().lower() for k in CLOSABLE):', '    if not any(k in p.name().lower() for k in CONFLICTS):'),
    'hardening: GET /settings shows the games token': ('evision_rgb/server.py',
        '                        return self._send(200, ctl.public_settings())', '                        return self._send(200, ctl.settings)'),
    'hardening: the reply to POST /settings shows the games token': ('evision_rgb/server.py',
        '                        return self._send(200, ctl.public_settings(ctl.update_settings(req)))',
        '                        return self._send(200, ctl.update_settings(req))'),
    'hardening: settings sent back replace the games token (their config files stop matching)': ('evision_rgb/server.py',
        '                        if isinstance(req, dict) and isinstance(req.get("games"), dict):', '                        if False:'),
    'hardening: a folder next to the window files is served': ('evision_rgb/server.py',
        '            if not full.startswith(WEB + os.sep) or not os.path.isfile(full):',
        '            if not full.startswith(WEB) or not os.path.isfile(full):'),
    # -- now playing / album colours
    'album: the cover is read on every poll (slow)': ('evision_rgb/media.py',
        '        if track != self._track:\n            self._track = track', '        if True:\n            self._track = track'),
    'album: grey backgrounds and black borders count as colours': ('evision_rgb/media.py',
        '    vivid = [c for c in cands if c[2] >= 0.25 and c[3] >= 0.2]', '    vivid = list(cands)'),
    'album: near-identical shades listed twice': ('evision_rgb/media.py',
        '        if all(sum((a - b) ** 2 for a, b in zip(rgb, o)) > 40 ** 2 for o in out):', '        if True:'),
    'album: paused music at full brightness': ('evision_rgb/media.py',
        '        return out if s["playing"] else out * self.paused', '        return out'),
    'album: a new track jumps in without a fade': ('evision_rgb/media.py',
        '            if self.out is not None and self.fade > 0:', '            if False:'),
    'album: cover painted mirrored': ('evision_rgb/media.py',
        '        self.cell = np.minimum((Y * gh).astype(int), gh - 1) * gw + np.minimum((X * gw).astype(int), gw - 1)',
        '        self.cell = np.minimum((Y * gh).astype(int), gh - 1) * gw + np.minimum(((1 - X) * gw).astype(int), gw - 1)'),
    'album: keeps asking the player after the effect stops': ('evision_rgb/media.py',
        '            self.users = max(0, self.users - 1)', '            self.users = max(1, self.users - 1)'),
    'album: a failed player query forgets the track': ('evision_rgb/media.py',
        '            self._set({**self._snap, "error": repr(e)})', '            self._set(_empty(error=repr(e)))'),
    'album: a closed player leaves its track on screen': ('evision_rgb/media.py',
        '            self._track, self._art = None, (None, [], None)\n            self._set(_empty())',
        '            self._set({**self._snap, "playing": False})'),
    'pad: the play key never shows the cover': ('evision_rgb/macropad.py',
        '            if s.get("image") is not None:\n                cover = (s["id"], s["playing"])',
        '            if False:\n                cover = (s["id"], s["playing"])'),
    'pad: the play key is not redrawn for a new track or pause': ('evision_rgb/macropad.py',
        '                cover = (s["id"], s["playing"])', '                cover = (0, True)'),
    # -- game lighting
    'cs2: the health bar ignores your health': ('evision_rgb/games.py',
        '            frac = me["health"] / 100', '            frac = 1.0'),
    'cs2: a flashbang does not white out the board': ('evision_rgb/games.py',
        '            if me["flashed"]:\n                out = _mix(out, [1.0, 1.0, 1.0], me["flashed"] / 255)', '            if False:\n                out = _mix(out, [1.0, 1.0, 1.0], me["flashed"] / 255)'),
    'cs2: taking damage never flashes': ('evision_rgb/games.py',
        '            if dmg > 0:', '            if False:'),
    'cs2: the bomb timer does not count down': ('evision_rgb/games.py',
        '            left = max(0.0, BOMB_S - f.age("planted")) / BOMB_S', '            left = 1.0'),
    "cs2: a spectated teammate's health shows as yours": ('evision_rgb/games.py',
        '    if (s.get("provider") or {}).get("steamid") not in (None, p.get("steamid")):', '    if False:'),
    'cs2: lost rounds glow green too': ('evision_rgb/games.py',
        '            won = rnd.get("win_team") == p.get("team")', '            won = True'),
    'dota: the ultimate is not on R': ('evision_rgb/games.py',
        '[("r", ult, [1.0, 0.3, 1.0])]', '[("f", ult, [1.0, 0.3, 1.0])]'),
    'dota: cooldowns look ready': ('evision_rgb/games.py',
        '    if a.get("cooldown", 0) > 0:', '    if False:'),
    'dota: a dead hero still shows bars and abilities': ('evision_rgb/games.py',
        '        if not hero.get("alive", True):', '        if False:'),
    'games: any token is accepted': ('evision_rgb/server.py',
        '        if not isinstance(token, str) or not hmac.compare_digest(token, self.games_settings()["token"]):', '        if not isinstance(token, str):'),
    'games: your lighting does not come back after the game': ('evision_rgb/server.py',
        '                    if mine:\n                        eng.stop()\n                        self._give_back(prev)', '                    if mine:\n                        eng.stop()'),
    'games: the game takes over again after you chose other lighting': ('evision_rgb/server.py',
        '            if self._saving or playing == self._game_dismissed or playing == self.game_on:', '            if self._saving or playing == self.game_on:'),
    'games: the auto switch ignores its setting': ('evision_rgb/server.py',
        '        playing = next((g for g in games.GAMES if games.feed(g).active()), None) if cfg.get("auto", True) else None', '        playing = next((g for g in games.GAMES if games.feed(g).active()), None)'),
    'games: a closed game keeps its lighting': ('evision_rgb/games.py',
        '        return self.clock() - self.received < ACTIVE_S and self.playing()', '        return self.playing()'),
    "games: the game's own posts need a JSON content type": ('evision_rgb/server.py',
        '            elif post and not self.path.startswith("/game/") and \\\n', '            elif post and \\\n'),
    # -- USB connection comes and goes
    "usb: a stale handle is never opened again (the user's frozen keyboard)": ('evision_rgb/protocol.py',
        '            except OSError:  # stale handle (hidapi: "read error" / "write error"): open again, then retry once\n                self._reopen()\n                return self._transact(pkt, cmd, timeout_ms, retries)', '            except OSError:  # stale handle (hidapi: "read error" / "write error"): open again, then retry once\n                raise'),
    'usb: reconnect attempts are not rate-limited (busy loop while unplugged)': ('evision_rgb/protocol.py',
        '        if now < self._next_open:', '        if False:'),
    'usb: a recovered stream keeps reporting the old error': ('evision_rgb/keyboard.py',
        '                    self.frames_sent += 1\n                    self.error = None', '                    self.frames_sent += 1'),
    'usb: the app does not start without the keyboard': ('evision_rgb/server.py',
        '        self.engine = engine or Engine(Keyboard(wait=True))', '        self.engine = engine or Engine(Keyboard())'),
    'usb: no lighting backup when the keyboard is plugged in later': ('evision_rgb/server.py',
        '                self._first_contact()\n                self._watch_keyboard()', '                self._watch_keyboard()'),
    'usb: the status never says disconnected': ('evision_rgb/server.py',
        '                        st["keyboard"] = "connected" if ctl.kb.dev.connected else "disconnected"', '                        st["keyboard"] = "connected"'),
    # -- the preview shows what glows
    'faint keys: lit-but-faint keys drawn black in the preview': ('evision_rgb/server.py',
        '    if m < LIFT_BELOW:', '    if False:'),
    'faint keys: unlit keys drawn as dim colours in the preview': ('evision_rgb/server.py',
        '    if float((led * 255 + 0.5).max()) < 1:\n        return "000000"', '    if False:\n        return "000000"'),
    # -- OpenRGB SDK server
    'openrgb: client colours get gamma-corrected (OpenRGB sends LED values)': ('evision_rgb/openrgb.py',
        '    raw = True\n', '    raw = False\n'),
    'openrgb: LEDs land on the wrong keys': ('evision_rgb/openrgb.py',
        '        frame[start:start + len(arr)] = arr', '        frame[start:start + len(arr)] = arr[::-1]'),
    'openrgb: your lighting does not come back when the client leaves': ('evision_rgb/server.py',
        '            self.events.add("the OpenRGB program left")\n            if mine:\n                eng.stop()\n                self._give_back(prev)', '            self.events.add("the OpenRGB program left")\n            if mine:\n                eng.stop()'),
    'openrgb: the client takes the lighting back after you chose other lighting': ('evision_rgb/server.py',
        '        if self._ext_dismissed or (eng.effect is not None and eng.effect.name == "openrgb"):', '        if eng.effect is not None and eng.effect.name == "openrgb":'),
    'openrgb: listens on every network interface': ('evision_rgb/openrgb.py',
        '    def start(self, port: int = 6742, host: str = "127.0.0.1"):', '    def start(self, port: int = 6742, host: str = "0.0.0.0"):'),
    'openrgb: a huge packet is waited for (memory, hang)': ('evision_rgb/openrgb.py',
        '                if size > MAX_PACKET:\n                    return', '                if False:\n                    return'),
    'openrgb: version-0 clients get the vendor field': ('evision_rgb/openrgb.py',
        '(_text(state["vendor"]) if v >= 1 else b"")', '_text(state["vendor"])'),
    'openrgb: old clients get brightness fields': ('evision_rgb/openrgb.py',
        '    if v >= 3:\n        out += struct.pack("<II", m["brightness_min"], m["brightness_max"])', '    if True:\n        out += struct.pack("<II", m["brightness_min"], m["brightness_max"])'),
    'openrgb: version-4 zones miss their segment count': ('evision_rgb/openrgb.py',
        '    if v >= 4:\n        zone += struct.pack', '    if False:\n        zone += struct.pack'),
    "openrgb: a client's built-in effect is not stored": ('evision_rgb/server.py',
        '            self.set_hardware(**fields)\n            self._ext_dismissed = True', '            self._ext_dismissed = True'),
    'openrgb: loading a profile does nothing': ('evision_rgb/server.py',
        '        p = self.find_profile(name)\n        if p is not None:', '        p = self.find_profile(name)\n        if False:'),
    # -- classic effects
    'scenes: fire heat never rises': ('evision_rgb/scenes.py',
        '        h[:, 1:] = 0.55 * h[:, 1:] + 0.45 * h[:, :-1]\n', ''),
    'scenes: fire turns blue before yellow': ('evision_rgb/scenes.py',
        'np.clip(3 * heat - 1, 0, 1), np.clip(3 * heat - 2, 0, 1)]', 'np.clip(3 * heat - 2, 0, 1), np.clip(3 * heat - 1, 0, 1)]'),
    'scenes: twinkles switch on and off without fading': ('evision_rgb/scenes.py',
        '        v = np.where(x < 1, np.sin(np.pi * np.minimum(x, 1)) ** 2, 0.0)', '        v = np.where(x < 1, 1.0, 0.0)'),
    'scenes: twinkle ignores its colours': ('evision_rgb/scenes.py',
        '        colour = self.palette[(self.pick', '        colour = np.ones(3) + 0 * self.palette[(self.pick'),
    'scenes: digital rain falls upwards': ('evision_rgb/scenes.py',
        '            d[1] += d[2] * self.speed', '            d[1] -= d[2] * self.speed'),
    'scenes: the same seed gives a different show': ('evision_rgb/scenes.py',
        '        self.rng = np.random.default_rng(self.seed)', '        self.rng = np.random.default_rng()'),
    # -- releases
    'release: a failing self-test check still passes the build': ('evision_rgb/gui/app.py',
        '    ok = all(c["ok"] for c in checks.values())', '    ok = True'),
    'release: notes without the install steps': ('scripts/release_notes.py',
        '    return section(version) + "\\n" + INSTALL.format(v=version)', '    return section(version)'),
    'release: no note on replacing the LGPL parts': ('scripts/third_party_notices.py',
        '''             "pynput and pystray are under the GNU LGPL v3. They are bundled unmodified; their source is at",
             "https://github.com/moses-palmer/pynput and https://github.com/moses-palmer/pystray. To use a modified",
             "version, rebuild the app from its source (https://github.com/HaritMT25/evision-rgb) with",
             "scripts/build_exe.sh after installing yours.", "",
''', ''),
}

# the spec module of each feature runs first for its mutants (by mutant-name prefix)
HINTS = {"still frames:": ["test_feature_battery"], "preview:": ["test_feature_battery"], "battery:": ["test_feature_battery"],
         "battery dim:": ["test_feature_battery_dim"], "events:": ["test_feature_events"],
         "diagnostics:": ["test_feature_diagnostics"], "tray:": ["test_feature_tray"],
         "handoffs:": ["test_feature_handoffs"], "jit cache:": ["test_jit_cache"],
         "hardening:": ["test_feature_hardening"], "firmware lookup:": ["test_firmware_lookup"],
         "review:": ["test_feature_review_fixes"], "review 2:": ["test_feature_review2"],
         "ui review:": ["test_feature_ui_review"],
         "album:": ["test_feature_nowplaying"], "pad: the play key": ["test_feature_nowplaying"], "pad": ["test_feature_macropad"],
         "local API": ["test_feature_macropad"], "layers": ["test_feature_layers"], "profile": ["test_feature_layers"],
         "games:": ["test_feature_games"], "cs2:": ["test_feature_games"], "dota:": ["test_feature_games"],
         "release:": ["test_release"], "usb:": ["test_feature_reconnect"],
         "faint keys:": ["test_behavior"], "openrgb:": ["test_feature_openrgb"],
         "scenes:": ["test_feature_scenes"]}


def coarse_timers() -> bool:
    """Does threading.Event.wait round its timeout up to the 15.6 ms Windows timer tick here? (On machines where
    something keeps a 1 ms timer resolution, e.g. some CI runners, it doesn't, and that mutant is not a bug.)"""
    import threading
    import time
    e, waits = threading.Event(), []
    for _ in range(7):
        t = time.perf_counter()
        e.wait(0.0167)
        waits.append(time.perf_counter() - t)
    return sorted(waits)[3] > 0.025


def has(module: str) -> bool:
    """Is this optional package (numba, pefile: the "emulation" extra) installed for the suite's Python? Its specs
    are skipped without it."""
    return subprocess.run([PY, "-c", f"import {module}"], capture_output=True).returncode == 0


ONLY = {"win32": lambda: sys.platform == "win32", "coarse-timers": coarse_timers, "numba": lambda: has("numba"),
        "pefile": lambda: has("pefile")}


def hint(name):
    return next((mods for prefix, mods in HINTS.items() if name.startswith(prefix)), [])


def drop_bytecode(path):
    """Delete cached bytecode of `path`: a same-length mutant restored within the same second would otherwise
    keep running from __pycache__ in every later test run."""
    cache = os.path.join(os.path.dirname(path), "__pycache__")
    stem = os.path.splitext(os.path.basename(path))[0] + "."
    for f in os.listdir(cache) if os.path.isdir(cache) else []:
        if f.startswith(stem):
            os.remove(os.path.join(cache, f))


TESTS = os.path.join(ROOT, "tests")


META = {"test_mutation_list"}  # checks this file's MUTANTS against the code: ANY applied mutant breaks it, so it
#                                would "catch" every mutant. It runs in the baseline and restored suites only.


def ordered_modules(rel, first=()):
    """Every behaviour test module, the likeliest to catch a mutant of `rel` first: `first`, then those mentioning it."""
    mods = sorted(f[:-3] for f in os.listdir(TESTS) if f.startswith("test_") and f.endswith(".py") and f[:-3] not in META)
    stem = os.path.splitext(os.path.basename(rel))[0]
    mentions = [m for m in mods if re.search(rf"\b{stem}\b", open(os.path.join(TESTS, m + ".py"), encoding="utf-8").read())]
    order = [m for m in first if m in mods] + mentions + mods
    return list(dict.fromkeys(order))


def suite(failfast=False, modules=()):
    """Run the behaviour suite without writing bytecode (mutated code must never be cached). failfast: stop at
    the first failure (a mutant only needs one failing test to count as caught); modules: in this order."""
    return subprocess.run([PY, "-B", "-m", "unittest"] + (["-f"] if failfast else []) + list(modules), cwd=TESTS,
                          capture_output=True, text=True, timeout=600, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})


def failures(r):
    return [l.split(" ")[1] for l in r.stderr.splitlines() if l.startswith(("FAIL:", "ERROR:"))]


def whole_suite(what):
    """The unmutated suite, once more if it fails: a timing test can miss on a busy machine (a CI runner); a real
    failure fails again. (A flake in a mutant run can only make a mutant look caught, never missed.)"""
    r = suite()
    if r.returncode != 0:
        print(f"{what} suite failed once, running it again:", ", ".join(failures(r)[:5]), flush=True)
        print("\n".join(l for l in r.stderr.splitlines() if "Error:" in l)[:1500], flush=True)
        r = suite()
    return r


base = whole_suite("baseline")  # a failing suite would make every mutant look caught
if base.returncode != 0:
    print("baseline suite FAILS, nothing to measure:", ", ".join(failures(base)[:5]) or base.stderr[-500:])
    sys.exit(1)

SELECT = [a.lower() for a in sys.argv[1:]]
failed_to_catch = []
for name, (rel, old, new, *opt) in MUTANTS.items():
    if SELECT and not any(s in name.lower() for s in SELECT):
        continue
    opts = opt[0] if opt else {}
    only = opts.get("only")
    if only and not ONLY[only]():
        print(f"[n/a] {name}: only where {only}", flush=True); continue
    path = os.path.join(ROOT, rel)
    src = open(path, encoding="utf-8").read()
    if old not in src:
        print(f"[skip] {name}: pattern not found"); failed_to_catch.append(name); continue
    newline = "\r\n" if b"\r\n" in open(path, "rb").read() else "\n"  # the mutant differs only by the mutation
    shutil.copy(path, path + ".bak")
    try:
        drop_bytecode(path)
        with open(path, "w", encoding="utf-8", newline=newline) as f:
            f.write(src.replace(old, new, 1))
        r = suite(failfast=True, modules=ordered_modules(rel, list(opts.get("tests", ())) + hint(name)))
        caught = r.returncode != 0
        print(f"[{'caught' if caught else 'MISSED'}] {name}" + (f"  <- {', '.join(failures(r)[:3])}" if caught else ""),
              flush=True)
        if not caught:
            failed_to_catch.append(name)
    finally:
        shutil.move(path + ".bak", path)
        os.utime(path)  # newer than anything cached from the mutant
        drop_bytecode(path)

after = whole_suite("restored")  # every file restored: the suite must pass again
if after.returncode != 0:
    print("suite FAILS after restoring the files:", ", ".join(failures(after)[:5]))
    failed_to_catch.append("(suite broken after restore)")
print("all mutants caught" if not failed_to_catch else f"NOT caught: {failed_to_catch}")
sys.exit(1 if failed_to_catch else 0)

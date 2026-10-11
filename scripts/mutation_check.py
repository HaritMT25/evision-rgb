"""Re-introduce known past bugs one at a time and check the behaviour suite catches each one.
Restores every file afterwards. Run from the project root."""
import os, shutil, subprocess, sys

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
        '                            if keep:\n                                engine.run(keep[0], **keep[1])',
        '                            if False:\n                                engine.run(keep[0], **keep[1])'),
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
        '        self.stop_apps()\n        self.engine.pause()\n        try:\n            if hw:',
        '        self.stop_apps()\n        self.engine.stop()\n        try:\n            if hw:'),
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
}

failed_to_catch = []
for name, (rel, old, new) in MUTANTS.items():
    path = os.path.join(ROOT, rel)
    src = open(path, encoding="utf-8").read()
    if old not in src:
        print(f"[skip] {name}: pattern not found"); failed_to_catch.append(name); continue
    shutil.copy(path, path + ".bak")
    try:
        open(path, "w", encoding="utf-8").write(src.replace(old, new, 1))
        r = subprocess.run([PY, "-m", "unittest"], cwd=os.path.join(ROOT, "tests"),
                           capture_output=True, text=True, timeout=300)
        caught = r.returncode != 0
        tests = [l.split(" ")[1] for l in r.stderr.splitlines() if l.startswith(("FAIL:", "ERROR:"))]
        print(f"[{'caught' if caught else 'MISSED'}] {name}" + (f"  <- {', '.join(tests[:3])}" if caught else ""))
        if not caught:
            failed_to_catch.append(name)
    finally:
        shutil.move(path + ".bak", path)
print("all mutants caught" if not failed_to_catch else f"NOT caught: {failed_to_catch}")
sys.exit(1 if failed_to_catch else 0)

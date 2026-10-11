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
        '            elif post and not (self.headers.get("Content-Type") or "").lower().startswith("application/json"):',
        '            elif False:'),
    'local API: requests from other web origins accepted': ('evision_rgb/server.py',
        '            elif origin is not None and origin.lower() not in {"http://" + h for h in ours}:',
        '            elif False:'),
    'local API: any Host name accepted (DNS rebinding)': ('evision_rgb/server.py',
        '            if host is not None and host.lower() not in ours:', '            if False:'),
}

def drop_bytecode(path):
    """Delete cached bytecode of `path`: a same-length mutant restored within the same second would otherwise
    keep running from __pycache__ in every later test run."""
    cache = os.path.join(os.path.dirname(path), "__pycache__")
    stem = os.path.splitext(os.path.basename(path))[0] + "."
    for f in os.listdir(cache) if os.path.isdir(cache) else []:
        if f.startswith(stem):
            os.remove(os.path.join(cache, f))


def suite():
    """Run the behaviour suite without writing bytecode (mutated code must never be cached)."""
    return subprocess.run([PY, "-B", "-m", "unittest"], cwd=os.path.join(ROOT, "tests"), capture_output=True,
                          text=True, timeout=300, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})


def failures(r):
    return [l.split(" ")[1] for l in r.stderr.splitlines() if l.startswith(("FAIL:", "ERROR:"))]


base = suite()  # a failing suite would make every mutant look caught
if base.returncode != 0:
    print("baseline suite FAILS, nothing to measure:", ", ".join(failures(base)[:5]) or base.stderr[-500:])
    sys.exit(1)

failed_to_catch = []
for name, (rel, old, new) in MUTANTS.items():
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
        r = suite()
        caught = r.returncode != 0
        print(f"[{'caught' if caught else 'MISSED'}] {name}" + (f"  <- {', '.join(failures(r)[:3])}" if caught else ""),
              flush=True)
        if not caught:
            failed_to_catch.append(name)
    finally:
        shutil.move(path + ".bak", path)
        os.utime(path)  # newer than anything cached from the mutant
        drop_bytecode(path)

after = suite()  # every file restored: the suite must pass again
if after.returncode != 0:
    print("suite FAILS after restoring the files:", ", ".join(failures(after)[:5]))
    failed_to_catch.append("(suite broken after restore)")
print("all mutants caught" if not failed_to_catch else f"NOT caught: {failed_to_catch}")
sys.exit(1 if failed_to_catch else 0)

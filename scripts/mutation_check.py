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

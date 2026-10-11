# Contributing

Thanks for helping. A few things keep this project safe for everyone's keyboard.

## Safety rules
- Only packets whose structure is known from the vendor app or a capture go to a device. The keyboard driver has an
  allow-list (`evision_rgb/protocol.py`); never widen it to experiment. Firmware updates, key-map and macro writes,
  bootloader commands and the pads' boot-logo / config commands are out of scope.
- Everything stored on a keyboard is read back and verified. The app backs up the lighting on first start.
- No vendor binaries, firmware images, decompiled code or captures with personal data go into the repo. The
  exact effect preview reads the firmware from the AJAZZ updater on the user's own PC at run time.

## How changes are made
1. **Write the behaviour spec first.** Tests in `tests/` say what should happen, in plain sentences
   (`test_unplugging_pauses_a_live_effect_and_the_keyboard_shows_its_own`). Run them and see them fail.
2. Implement until they pass. Everything runs against simulators: `tests/fake_device.py` (keyboard firmware
   behaviour, quirks included) and `tests/fake_pad.py` (macro pads). No hardware is needed.
3. Add the bugs you just made impossible to `scripts/mutation_check.py`. It re-introduces each one and the suite must
   fail every time; that proves the tests can catch the bug.

```powershell
python -m venv .venv; .venv\Scripts\activate
pip install -e ".[app,mcp,dev]"
cd tests; python -m unittest                      # ~2 minutes, no keyboard needed
python ..\scripts\mutation_check.py battery        # the mutants whose name contains "battery"
python ..\scripts\mutation_check.py                # all of them (an hour or more)
```

The app window can be developed against the simulator too:
```powershell
python scripts\ui_preview_server.py 8899 --pad akp03e-rev2    # simulated keyboard, pad and song
node scripts\ui_smoke.mjs 8899 shots --pad                     # drives the UI in headless Edge, fails on JS errors
```

## Style
- Match the surrounding code: small functions, comments only where the code can't say it.
- User-facing text is short and plain; say what happens, not how it is implemented.

Adding a keyboard: see [docs/ADDING_A_KEYBOARD.md](docs/ADDING_A_KEYBOARD.md).

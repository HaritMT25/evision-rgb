#!/bin/sh
# Build dist/EVision RGB/EVision RGB.exe  (run from the project root in Git Bash)
set -e
W=$(pwd -W)
.venv/Scripts/pyinstaller.exe --noconfirm --windowed --name "EVision RGB" --icon "$W/evision_rgb/gui/icon.ico" \
  --distpath dist --workpath build/pyi --specpath build --paths "$W" \
  --add-data "$W/evision_rgb/gui/web;evision_rgb/gui/web" --add-data "$W/evision_rgb/gui/icon.ico;evision_rgb/gui" \
  --collect-all soundcard --collect-submodules webview --collect-data webview --collect-submodules evision_rgb \
  --hidden-import pynput.keyboard._win32 --hidden-import pynput.mouse._win32 --hidden-import pystray._win32 \
  --collect-all numba --collect-all llvmlite --hidden-import pefile   --add-data "$W/evision_rgb/fw8051_fast.py;evision_rgb"   --hidden-import clr --hidden-import pycaw.pycaw --collect-submodules comtypes --collect-all winrt --exclude-module evision_rgb.mcp_server --exclude-module mcp --exclude-module cryptography --exclude-module tkinter --exclude-module matplotlib \
  scripts/app_entry.py
# numba caches the compiled firmware engine in %APPDATA% keyed by its source file's modification time, and PyInstaller
# dates the bundled copy at build time: give it the time of its last commit instead, so a rebuild or an update unzipped
# over the old one doesn't compile it again (~60 s of a CPU core after every start of a new build)
F="dist/EVision RGB/_internal/evision_rgb/fw8051_fast.py"
T=$(git log -1 --format=%cI -- evision_rgb/fw8051_fast.py 2>/dev/null || true)
if [ -n "$T" ] && git diff --quiet HEAD -- evision_rgb/fw8051_fast.py 2>/dev/null; then
  touch -d "$T" "$F"
else
  touch -r evision_rgb/fw8051_fast.py "$F"  # edited since its last commit (or no git): the file's own time
fi

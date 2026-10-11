#!/bin/sh
# Build dist/EVision RGB/EVision RGB.exe  (run from the project root in Git Bash)
set -e
W=$(pwd -W)
.venv/Scripts/pyinstaller.exe --noconfirm --windowed --name "EVision RGB" --icon "$W/evision_rgb/gui/icon.ico" \
  --distpath dist --workpath build/pyi --specpath build --paths "$W" \
  --add-data "$W/evision_rgb/gui/web;evision_rgb/gui/web" --add-data "$W/evision_rgb/gui/icon.ico;evision_rgb/gui" \
  --collect-all soundcard --collect-submodules webview --collect-data webview --collect-submodules evision_rgb \
  --hidden-import pynput.keyboard._win32 --hidden-import pynput.mouse._win32 --hidden-import pystray._win32 \
  --hidden-import clr --exclude-module tkinter --exclude-module matplotlib \
  scripts/app_entry.py

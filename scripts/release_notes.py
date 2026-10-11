"""Release notes for a version: its CHANGELOG.md section plus how to install the download.

    python scripts/release_notes.py 0.4.0 > notes.md
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

INSTALL = """
### Install
1. Download `EVision-RGB-{v}-windows-x64.zip` below and unzip it anywhere (for example `Documents\\EVision RGB`).
2. Close other RGB software (the AJAZZ app, OpenRGB, Nexus, SignalRGB): they talk to the same keyboard channel.
3. Run `EVision RGB.exe`. Windows shows "Windows protected your PC" because the app is not code-signed:
   click **More info**, then **Run anyway**. You can check the download first: its SHA-256 is in the `.sha256` file
   (`Get-FileHash EVision-RGB-{v}-windows-x64.zip` in PowerShell).

Windows 10 or 11, 64-bit. No AJAZZ software or Python needed. The app backs up your keyboard's lighting the first
time it connects and can restore it at any time. Licences of the bundled components: `THIRD-PARTY-NOTICES.txt`.
"""


def section(version: str) -> str:
    with open(os.path.join(ROOT, "CHANGELOG.md"), encoding="utf-8") as f:
        text = f.read()
    m = re.search(rf"^## {re.escape(version)}\b.*?$(.*?)(?=^## |\Z)", text, re.M | re.S)
    if not m:
        raise SystemExit(f"CHANGELOG.md has no section for {version}")
    return m.group(1).strip()


def notes(version: str) -> str:
    return section(version) + "\n" + INSTALL.format(v=version)


if __name__ == "__main__":
    sys.stdout.write(notes(sys.argv[1]))

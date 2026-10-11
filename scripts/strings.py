"""Extract ASCII and UTF-16LE strings with file offsets. usage: strings.py file [minlen]"""
import re, sys
sys.stdout.reconfigure(encoding="utf-8")
data = open(sys.argv[1], "rb").read()
n = int(sys.argv[2]) if len(sys.argv) > 2 else 5
for m in re.finditer(rb"[\x20-\x7e]{%d,}" % n, data):
    print(f"A {m.start():08x} {m.group().decode()}")
# UTF-16LE incl. CJK
for m in re.finditer(rb"(?:[\x20-\x7e]\x00|[\x00-\xff][\x4e-\x9f]){%d,}" % n, data):
    try:
        s = m.group().decode("utf-16le")
    except UnicodeDecodeError:
        continue
    print(f"W {m.start():08x} {s}")

"""Hold a layout-verification pattern in direct mode for N seconds (default 90).
Rows coloured red/orange/yellow/green/cyan/blue top->bottom, RCtrl+Fn magenta,
every slot NOT in the layout white (so a white key = mapping error)."""
import os, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from evision_rgb.protocol import AK820, MAP_SIZE
from evision_rgb.layout import KEYS

row_cols = [(255, 0, 0), (255, 80, 0), (255, 200, 0), (0, 255, 0), (0, 200, 255), (0, 0, 255)]
frame = bytearray(b"\xff\xff\xff" * MAP_SIZE)
for name, (slot, x, y) in KEYS.items():
    c = row_cols[min(5, round(y * 5))]
    if name in ("RCtrl", "Fn"):
        c = (255, 0, 255)
    frame[slot * 3:slot * 3 + 3] = bytes(c)

secs = float(sys.argv[1]) if len(sys.argv) > 1 else 90
with AK820(allow_writes=True) as kb:
    t = time.monotonic()
    while time.monotonic() - t < secs:
        kb.direct(bytes(frame))
        time.sleep(0.1)
    kb.direct_end()

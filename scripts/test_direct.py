"""Direct-mode (0x12, non-persistent) streaming test.
Phase A 5 s: all green.  Phase B 8 s: rainbow sweeping left->right.  Then 0x13 (end direct)."""
import colorsys, os, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from evision_rgb.protocol import AK820, MAP_SIZE

with AK820(allow_writes=True) as kb:
    frames, t0 = 0, time.monotonic()
    while time.monotonic() - t0 < 5:
        kb.direct(b"\x00\xff\x00" * MAP_SIZE)
        frames += 1
    print(f"phase A: {frames} frames in 5 s = {frames/5:.1f} fps")
    frames, t1 = 0, time.monotonic()
    while (t := time.monotonic() - t1) < 8:
        buf = bytearray()
        for slot in range(MAP_SIZE):
            col = slot // 8
            r, g, b = colorsys.hsv_to_rgb(((col / 16) - t * 0.5) % 1.0, 1, 1)
            buf += bytes((int(r * 255), int(g * 255), int(b * 255)))
        kb.direct(bytes(buf))
        frames += 1
    print(f"phase B: {frames} frames in 8 s = {frames/8:.1f} fps")
    kb.direct_end()
    print("direct end sent")

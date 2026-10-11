"""Write test: upload a solid colour to custom colorset N with begin/0x0B/end, verify via 0x0A.
usage: test_custom_write.py RRGGBB [colorset]   (or 'restore <backup_dir>')"""
import os, sys
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)
from evision_rgb.protocol import AK820, CMD_WRITE_CUSTOM, MAP_SIZE

if sys.argv[1] == "restore":
    frames = {cs: open(os.path.join(sys.argv[2], f"custom{cs}.bin"), "rb").read() for cs in range(3)}
else:
    rgb = bytes.fromhex(sys.argv[1])
    cs = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    frames = {cs: rgb * MAP_SIZE}

with AK820(allow_writes=True, verbose=False) as kb:
    for cs, frame in frames.items():
        kb.begin()
        kb.write_block(CMD_WRITE_CUSTOM, frame, 512 * cs)
        kb.end()
        back = kb.read_custom(cs)
        print(f"colorset {cs}: readback {'MATCH' if back == frame else 'MISMATCH'}")
        if back != frame:
            print(" wrote:", frame[:24].hex(" "), "\n  read:", back[:24].hex(" "))
    print("config now:", kb.read_config(0x14).hex(" "))

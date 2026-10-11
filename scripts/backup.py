"""Read-only: back up device state to analysis/backup/<timestamp>/ as .bin files."""
import os, sys, time
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)
from evision_rgb.protocol import AK820, CMD_READ_1B, ProtocolError

out = os.path.join(ROOT, "analysis", "backup", time.strftime("%Y%m%d_%H%M%S"))
os.makedirs(out)
with AK820() as kb:
    blobs = {"caps": kb.capabilities(), "config": kb.read_config(0x80),
             "keymap": kb.read_keymap(), "table1b": kb.read_block(CMD_READ_1B, 0x80)}
    for cs in range(8):
        try:
            blobs[f"custom{cs}"] = kb.read_custom(cs)
        except ProtocolError as e:
            print(f"colorset {cs}: {e}")
            break
for k, v in blobs.items():
    open(os.path.join(out, k + ".bin"), "wb").write(v)
    print(f"{k:10s} {len(v):4d} B  nonzero={sum(1 for b in v if b)}")
print("saved to", out)

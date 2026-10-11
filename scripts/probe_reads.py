"""Step 2: read-only dumps — config (0x05), custom colors (0x0A), keymap (0x07), table (0x1B)."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from evision_rgb.protocol import AK820, CMD_READ_1B


def hexdump(b, width=24):
    for i in range(0, len(b), width):
        print(f"  {i:04x}: {b[i:i+width].hex(' ')}")


with AK820() as kb:
    print("config 0x05 [0..0x80):")
    hexdump(kb.read_config(0x80))
    for cs in range(2):
        print(f"custom colors 0x0A colorset {cs} (128 x RGB):")
        hexdump(kb.read_custom(cs), 24)
    print("keymap 0x07 (128 x 3):")
    km = kb.read_keymap()
    for slot in range(128):
        e = km[slot*3:slot*3+3]
        if e != b"\x00\x00\x00":
            print(f"  slot {slot:3d} (col {slot//8:2d} row {slot%8}): {e.hex(' ')}")
    print("table 0x1B [0..0x80):")
    hexdump(kb.read_block(CMD_READ_1B, 0x80))

"""Step 1 device test: read-only capabilities query (cmd 0x03). Sends one packet."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from evision_rgb.protocol import AK820

with AK820(verbose=True) as kb:
    caps = kb.capabilities()
    print("caps:", caps.hex(" "))
    if caps[:2] == b"\xaa\x55":
        print(f"magic AA55 OK; map_size byte={caps[5]:#x} macros byte={caps[6]:#x}")

"""Flicker experiment: live preview (white) -> save custom pattern in ONE begin/end transaction
(config mode=custom + colours) -> end live. Repeated 3x alternating two patterns."""
import os, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from evision_rgb import Keyboard, KEYS, frame_from
from evision_rgb.keyboard import CONFIG_LEN, O_MODE, O_COLORSET
from evision_rgb.protocol import CMD_WRITE_CONFIG, CMD_WRITE_CUSTOM, MAP_SIZE

A = bytes(frame_from({k: ("00ff00" if x < 0.5 else "ff00ff") for k, (s, x, y) in KEYS.items()}))
B = bytes(frame_from({k: ("00ffff" if y < 0.5 else "ffff00") for k, (s, x, y) in KEYS.items()}))
with Keyboard() as kb:
    for i, pat in enumerate((B, A, B)):
        s = kb.stream()
        s.fill("ffffff"); s.show(); time.sleep(1.5)          # "preview"
        s.paused = True
        cfg = bytearray(kb.dev.read_config(CONFIG_LEN)); cfg[O_MODE] = 0x14; cfg[O_COLORSET] = 0
        kb.dev.begin()
        kb.dev.write_block(CMD_WRITE_CONFIG, bytes(cfg), 0)
        kb.dev.write_block(CMD_WRITE_CUSTOM, pat, 0)
        kb.dev.end()
        s.close()                                           # 0x13 -> stored custom
        ok = kb.dev.read_custom(0) == pat and kb.dev.read_config(CONFIG_LEN)[O_MODE] == 0x14
        print(f"round {i+1}: stored ok={ok}")
        time.sleep(2.5)

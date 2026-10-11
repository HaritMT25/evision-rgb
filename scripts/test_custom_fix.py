"""Repro of the GUI bug: keyboard in a non-custom mode, then save custom colours."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from evision_rgb import Keyboard, KEYS
with Keyboard() as kb:
    print(kb.set_effect("normal", brightness=3, color="0000ff"))
    cols = {k: ("00ff00" if x < 0.5 else "ff00ff") for k, (s, x, y) in KEYS.items()}
    kb.set_custom(cols, colorset=0)
    print(kb.info())

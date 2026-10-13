"""Independent driver for the AJAZZ AK820 wired RGB keyboard (USB 320F:505B, EVision V2 protocol)."""
from .keyboard import Keyboard, Stream, parse_color, frame_from
from .layout import KEYS
from .protocol import MODES, AK820, ProtocolError

__all__ = ["Keyboard", "Stream", "KEYS", "MODES", "AK820", "ProtocolError", "parse_color", "frame_from"]
__version__ = "0.7.2"

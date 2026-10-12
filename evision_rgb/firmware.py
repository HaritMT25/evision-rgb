"""Run the keyboard's own firmware lighting code (AK820 fw V0103, 8051) to get exact effect frames.

The firmware image is NOT shipped with this project. It is extracted at runtime from the official
AJAZZ updater on the user's PC (``Updata\\*.exe``, PE resource type "DOWNLOAD", id 129), and the image
is accepted only if its capability block matches the connected keyboard's.

Firmware facts used here (from static analysis, see docs/FIRMWARE.md):
- app code starts at 0x2800 (0x2800: LJMP 0xDFD0 startup -> main at 0xE905)
- XDATA 0x0A64: config block (same layout as host command 0x06); 0x0A65 = mode
- CODE 0xA700 + 0x200*area: custom colours (area chosen by config byte 25)
- XDATA 0x066D: LED frame, 128 x [0xFF, R, G, B], brightness already applied
- lighting task 0x8B5C (dispatch table 0x8C13); tick routine 0x947F advances the effect
  counter every 2 timer interrupts. Timer1 ISR body = 0xE36E (reloads TH1:TL1 for 800 counts and
  advances the ISR-driven counters). CH555 datasheet: CLOCK_CFG=6 -> Fsys = Fpll/3 = 32 MHz,
  Timer1 = Fsys/12 -> 300 us per interrupt -> 1 effect tick = 0.6 ms (scaled by `time_scale`)
- gates: XDATA 0x05C6 = USB configured, 0x0A5F >= 30 start-up delay, 0x0A4B = power-on show
"""
from __future__ import annotations

import glob
import os

import numpy as np

from .fw8051 import CPU8051

APP_BASE = 0x2800
STARTUP, MAIN = 0xDFD0, 0xE905
INIT_CALLS = (0x2FE2, 0xE587, 0xDC37, 0xEEF3, 0xE16D, 0xEF80)
TICK_FN, LIGHT_FN = 0x947F, 0x8B5C
POST_CALL = 0x8CB1          # LCALL 0xDCD7 ; LCALL 0xED4D (hardware output stage, not needed here)
CFG, MODE, CUSTOM_BASE, FRAME = 0x0A64, 0x0A65, 0xA700, 0x066D
ISR_FN = 0xE36E             # Timer1 interrupt body (plain function ending in RET)
FSYS = 32e6                 # CH555: CLOCK_CFG=6 -> Fpll/3
ISR_CYCLES = 800 * 12       # Timer1 at Fsys/12, 800 counts -> 300 us
ISR_S = ISR_CYCLES / FSYS
PASS_OVERHEAD = 400         # cycles of the other main-loop work per pass (key/USB bookkeeping)
ISR_OVERHEAD = 40           # interrupt entry/exit (register saves, RETI)
IDLE_THRESHOLD = 1500       # a pass cheaper than this did no effect work
HUE_TABLE = 0x09B5          # per-key hue/colour index (Flowers, Rain colours); uninitialised RAM on hardware
PHASE_TABLE = 0x05DA        # per-key phase/intensity (Rain drops, Reaction); uninitialised RAM on hardware
CAPS_SIG = bytes.fromhex("aa5500000d805000020000")


def extract_from_updater(path: str) -> list[bytes]:
    """Firmware images embedded in an official updater exe (resource type 'DOWNLOAD'). Only its resources are
    parsed (all of it, with ~90 000 relocations, took ~2 s and a ~95 MB peak), and the file is closed again."""
    import pefile
    pe = pefile.PE(path, fast_load=True)
    try:
        pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_RESOURCE"]])
        out = []
        for t in getattr(pe, "DIRECTORY_ENTRY_RESOURCE", type("x", (), {"entries": []})).entries:
            if str(t.name) != "DOWNLOAD":
                continue
            for e in t.directory.entries:
                for lang in e.directory.entries:
                    d = lang.data.struct
                    blob = pe.get_data(d.OffsetToData, d.Size)
                    if blob[APP_BASE:APP_BASE + 3][:1] == b"\x02" and CAPS_SIG in blob[APP_BASE:APP_BASE + 64]:
                        out.append(blob)
        return out
    finally:
        pe.close()


def find_firmware(caps: bytes | None = None, search: list[str] | None = None) -> bytes | None:
    """Locate the AK820 firmware image on this PC. `caps` (device reply to cmd 0x03) must match."""
    roots = search or [os.path.join(os.environ.get(v, ""), "AJAZZ") for v in ("ProgramFiles(x86)", "ProgramFiles")]
    for root in roots:
        for exe in sorted(glob.glob(os.path.join(root, "**", "Updata", "*.exe"), recursive=True), reverse=True):
            try:
                for img in extract_from_updater(exe):
                    if caps is None or caps[:34] == img[APP_BASE + 3:APP_BASE + 3 + 34][:len(caps[:34])]:
                        return img
            except Exception:
                continue
    return None


class FirmwareLighting:
    """The firmware's lighting engine running in an 8051 interpreter."""

    def __init__(self, image: bytes, fast: bool = True, time_scale: float = 1.0, seed: int = 0x5EED):
        if fast:
            from .fw8051_fast import FastCPU
            cpu = FastCPU(image)
        else:
            cpu = CPU8051(image)
        cpu.pc = STARTUP
        cpu.run_until(MAIN)
        for fn in INIT_CALLS:
            cpu.call(fn)
        cpu.code[POST_CALL:POST_CALL + 3] = [0x22, 0, 0]  # RET: skip the hardware output stage
        cpu.xdata[0x05C6] = 1     # USB configured
        cpu.xdata[0x0A5F] = 30    # start-up delay elapsed
        cpu.xdata[0x0A4B] = 0     # no power-on show
        # external RAM is not cleared by the C start-up code, so on real hardware tables like the
        # per-key hue counters start with power-up garbage; emulate that deterministically
        rng = np.random.default_rng(seed)
        cpu.xdata[HUE_TABLE:HUE_TABLE + 128] = [int(v) for v in rng.integers(0, 0xC0, 128)]
        cpu.xdata[PHASE_TABLE:PHASE_TABLE + 128] = [int(v) for v in rng.integers(0, 0xC0, 128)]
        self.cpu = cpu
        self.time_scale = time_scale
        self.ticks = 0
        self._carry = 0.0

    def configure(self, config: bytes):
        """Apply a host-format config block (bytes 0.. of command 0x06 / 0x05)."""
        self.cpu.xdata[CFG:CFG + len(config)] = list(config)

    def set_custom(self, frame: bytes, area: int = 0):
        a = CUSTOM_BASE + 0x200 * area
        self.cpu.code[a:a + 384] = list(frame)

    def advance(self, seconds: float):
        """Advance emulated time. With the JIT core, time is CPU-cycle accurate: long effect frames
        delay ticks like on the real chip. The reference core uses one pass per interrupt."""
        cpu = self.cpu
        if hasattr(cpu, "state") and len(cpu.state) >= 4:
            from .fw8051_fast import advance_cycles
            st = cpu.state
            if st[3] == 0:
                st[3] = st[2] + ISR_CYCLES
            until = st[2] + int(seconds * FSYS / self.time_scale)
            rc = advance_cycles(cpu.code, cpu.iram, cpu.sfr, cpu.xdata, st, until, ISR_CYCLES,
                                ISR_FN, TICK_FN, LIGHT_FN, PASS_OVERHEAD, ISR_OVERHEAD, IDLE_THRESHOLD)
            if rc != 0:
                raise RuntimeError(f"firmware emulation stopped (code {rc}, pc={int(st[0]):#06x})")
            return
        period = ISR_S * self.time_scale
        self._carry += seconds
        n = int(self._carry / period)
        self._carry -= n * period
        for _ in range(n):
            cpu.call(ISR_FN)
            cpu.call(TICK_FN)
            cpu.call(LIGHT_FN)
        self.ticks += n

    def frame(self) -> np.ndarray:
        """(128, 3) uint8 RGB per LED slot, as the LEDs show it (brightness applied)."""
        buf = np.asarray(self.cpu.xdata[FRAME:FRAME + 512], dtype=np.uint8).reshape(128, 4)
        return buf[:, 1:4].copy()

    KEY_FN = 0xDF3C        # per-key-press handler (dispatches by mode; slot in XDATA 0x08EB)

    def press(self, slot: int):
        """Feed a key press (LED slot) to the firmware's own key handler (reactive effects)."""
        self.cpu.xdata[0x09A9] = 1      # "new key event" flag set by the key scanner
        self.cpu.xdata[0x08EB] = slot
        self.cpu.call(self.KEY_FN)
        self.cpu.xdata[0x09A9] = 0      # the main loop clears it after handling

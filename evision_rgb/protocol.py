"""EVision V2 protocol driver for AJAZZ AK820 wired (USB 320F:505B).

Transport: vendor HID collection MI_01 / UsagePage 0xFF1C / Usage 0x92, report ID 4,
64-byte reports. The device echoes every packet back on the input report.

Packet: [0]=0x04 [1..2]=checksum LE [3]=cmd [4]=len [5..6]=offset LE [7]=status [8..63]=data
checksum = uint16 sum of bytes [3..63].

Only commands whose use by the official app is confirmed are implemented. Commands
that rewrite key maps, macros or capabilities are deliberately NOT implemented.
"""
from __future__ import annotations

import threading
import time

import hid

VID, PID = 0x320F, 0x505B
USAGE_PAGE, USAGE = 0xFF1C, 0x92
REPORT_ID = 0x04
PACKET = 64
CHUNK = 0x38  # max data bytes per packet

# confirmed command ids
CMD_BEGIN = 0x01
CMD_END = 0x02
CMD_READ_CAPS = 0x03
CMD_READ_CONFIG = 0x05
CMD_WRITE_CONFIG = 0x06
CMD_READ_KEYMAP = 0x07
CMD_READ_CUSTOM = 0x0A
CMD_WRITE_CUSTOM = 0x0B
CMD_DIRECT = 0x12
CMD_DIRECT_END = 0x13
CMD_READ_1B = 0x1B

READ_ONLY = {CMD_READ_CAPS, CMD_READ_CONFIG, CMD_READ_KEYMAP, CMD_READ_CUSTOM, CMD_READ_1B}
ALLOWED_WRITES = {CMD_BEGIN, CMD_END, CMD_WRITE_CONFIG, CMD_WRITE_CUSTOM, CMD_DIRECT, CMD_DIRECT_END}

MAP_SIZE = 128  # LED slots, index = column*8 + row

# Hardware (firmware) effects. Names/order from the official AK820 app (lan_en.xml + default_light.json),
# whose list values equal the device mode ids (6 = static and 20 = custom verified on hardware).
MODES = {
    "corrugated": 0x01, "cloud": 0x02, "serpentine": 0x03, "spectrum": 0x04, "breath": 0x05,
    "normal": 0x06, "reaction": 0x07, "ripples": 0x08, "traverse": 0x09, "stars": 0x0A,
    "flowers": 0x0B, "roll": 0x0C, "wave": 0x0D, "cartoon": 0x0E, "rain": 0x0F, "scan": 0x10,
    "surmount": 0x11, "speed": 0x12, "custom": 0x14,
}
MODE_ALIASES = {"static": 0x06, "breathing": 0x05, "reactive": 0x07, "ripple": 0x08, "starlight": 0x0A}


class ProtocolError(RuntimeError):
    pass


def checksum(buf: bytes | bytearray) -> int:
    return sum(buf[3:PACKET]) & 0xFFFF


def build(cmd: int, length: int = 0, offset: int = 0, data: bytes = b"") -> bytearray:
    if len(data) > CHUNK:
        raise ValueError("data too long for one packet")
    buf = bytearray(PACKET)
    buf[0] = REPORT_ID
    buf[3] = cmd
    buf[4] = length
    buf[5] = offset & 0xFF
    buf[6] = (offset >> 8) & 0xFF
    buf[8:8 + len(data)] = data
    c = checksum(buf)
    buf[1], buf[2] = c & 0xFF, c >> 8
    return buf


def find_path() -> bytes:
    for d in hid.enumerate(VID, PID):
        if d["usage_page"] == USAGE_PAGE and d["usage"] == USAGE:
            return d["path"]
    raise ProtocolError("AK820 vendor interface (0xFF1C/0x92) not found")


class AK820:
    def __init__(self, path: bytes | None = None, allow_writes: bool = False, verbose: bool = False):
        self.dev = hid.device()
        self.dev.open_path(path or find_path())
        # read(n) with no timeout becomes a non-blocking poll; read(n, ms) still waits.
        self.dev.set_nonblocking(1)
        self.allow_writes = allow_writes
        self.verbose = verbose
        self.lock = threading.RLock()
        self.foreign = 0  # echoes of packets some other program sent

    def close(self):
        self.dev.close()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()

    # ---- raw transaction -------------------------------------------------
    def _drain(self):
        for _ in range(256):
            if not self.dev.read(PACKET):
                break

    def query(self, cmd: int, length: int = 0, offset: int = 0, data: bytes = b"",
              timeout_ms: int = 300, retries: int = 2) -> bytes:
        if cmd not in READ_ONLY | ALLOWED_WRITES:
            raise ProtocolError(f"command {cmd:#04x} is not on the allow-list")
        if cmd in ALLOWED_WRITES and not self.allow_writes:
            raise ProtocolError(f"command {cmd:#04x} writes to the device; open with allow_writes=True")
        pkt = build(cmd, length, offset, data)
        with self.lock:
            # Every allowed command is idempotent, so a missed echo (e.g. another
            # program writing to the same channel) is handled by resending.
            for _attempt in range(retries + 1):
                self._drain()
                if self.verbose:
                    print(">>", pkt.hex(" "))
                n = self.dev.write(bytes(pkt))
                if n < 0:
                    raise ProtocolError(f"write failed: {self.dev.error()}")
                deadline = time.monotonic() + timeout_ms / 1000
                while time.monotonic() < deadline:
                    r = self.dev.read(PACKET, max(1, int((deadline - time.monotonic()) * 1000)))
                    if not r:
                        continue
                    r = bytes(r)
                    if self.verbose:
                        print("<<", r.hex(" "))
                    if r[0] == REPORT_ID and r[1:4] == pkt[1:4] and r[5:7] == pkt[5:7]:
                        if r[7] != 0:
                            raise ProtocolError(f"device status {r[7]:#04x} for cmd {cmd:#04x}")
                        return r
                    if r[0] == REPORT_ID and r[1:4] != pkt[1:4]:
                        self.foreign += 1
            raise ProtocolError(f"no reply to cmd {cmd:#04x}")

    def read_block(self, cmd: int, total: int, offset: int = 0) -> bytes:
        out = bytearray()
        while len(out) < total:
            n = min(CHUNK, total - len(out))
            r = self.query(cmd, n, offset + len(out))
            out += r[8:8 + n]
        return bytes(out)

    def write_block(self, cmd: int, data: bytes, offset: int = 0):
        for i in range(0, len(data), CHUNK):
            part = data[i:i + CHUNK]
            self.query(cmd, len(part), offset + i, part)

    # ---- reads -----------------------------------------------------------
    def capabilities(self) -> bytes:
        return self.query(CMD_READ_CAPS, 0x22)[8:8 + 0x22]

    def read_config(self, length: int = 0x1A, offset: int = 0) -> bytes:
        return self.read_block(CMD_READ_CONFIG, length, offset)

    def read_custom(self, colorset: int = 0) -> bytes:
        return self.read_block(CMD_READ_CUSTOM, 3 * MAP_SIZE, 512 * colorset)

    def read_keymap(self, slots: int = MAP_SIZE) -> bytes:
        return self.read_block(CMD_READ_KEYMAP, 3 * slots)

    # ---- writes ----------------------------------------------------------
    def begin(self):
        self.query(CMD_BEGIN)

    def end(self):
        self.query(CMD_END)

    def direct(self, rgb: bytes):
        """Stream a full frame (MAP_SIZE*3 bytes, R,G,B per slot). Not persisted."""
        if len(rgb) != 3 * MAP_SIZE:
            raise ValueError("frame must be 384 bytes")
        self.write_block(CMD_DIRECT, rgb)

    def direct_end(self):
        self.query(CMD_DIRECT_END)

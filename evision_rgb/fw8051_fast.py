"""JIT-compiled (numba) MCS-51 core with the same semantics as fw8051.CPU8051.

Used to run the firmware's lighting code faster than real time. Correctness is checked by
differential tests against the reference interpreter (tests/test_fw8051.py).
"""
from __future__ import annotations

import numpy as np
from numba import njit

ACC, B, PSW, SP, DPL, DPH, P2 = 0xE0, 0xF0, 0xD0, 0x81, 0x82, 0x83, 0xA0
SENTINEL = 0xFFFE


def _cycle_table():
    """Approximate clock cycles per opcode for a 1T E8051 core (CH55x): ~1 cycle per byte, plus
    extra for external memory (MOVX/MOVC), jumps/calls/returns and MUL/DIV."""
    length = [1] * 256
    two = {0x05, 0x15, 0x24, 0x25, 0x34, 0x35, 0x44, 0x45, 0x54, 0x55, 0x64, 0x65, 0x94, 0x95, 0x42, 0x52, 0x62,
           0x40, 0x50, 0x60, 0x70, 0x80, 0x74, 0x76, 0x77, 0x72, 0x82, 0x92, 0xA0, 0xA2, 0xB0, 0xB2, 0xC2, 0xD2,
           0x86, 0x87, 0xA6, 0xA7, 0xC0, 0xD0, 0xC5, 0xE5, 0xF5}
    two |= set(range(0x78, 0x80)) | set(range(0x88, 0x90)) | set(range(0xA8, 0xB0)) | set(range(0xD8, 0xE0))
    three = {0x02, 0x12, 0x10, 0x20, 0x30, 0x43, 0x53, 0x63, 0x75, 0x85, 0x90, 0xD5} | set(range(0xB4, 0xC0))
    for op in range(256):
        if (op & 0x1F) in (0x01, 0x11) or op in two:
            length[op] = 2
        elif op in three:
            length[op] = 3
    cyc = list(length)
    for op in (0xE0, 0xE2, 0xE3, 0xF0, 0xF2, 0xF3):
        cyc[op] += 1
    for op in (0x83, 0x93):
        cyc[op] += 2
    for op in range(256):
        if (op & 0x1F) in (0x01, 0x11) or op in (0x02, 0x12, 0x22, 0x32, 0x73, 0x80):
            cyc[op] += 2
        if op in (0x10, 0x20, 0x30, 0x40, 0x50, 0x60, 0x70, 0xD5) or 0xB4 <= op <= 0xBF or 0xD8 <= op <= 0xDF:
            cyc[op] += 1
    cyc[0xA4] = cyc[0x84] = 4
    return np.array(cyc, dtype=np.int64)


CYC = _cycle_table()


@njit(cache=True, inline="always")
def _parity(a):
    a ^= a >> 4
    a ^= a >> 2
    a ^= a >> 1
    return a & 1


@njit(cache=True, inline="always")
def _rd(iram, sfr, addr):
    if addr < 0x80:
        return iram[addr]
    if addr == PSW:
        return (sfr[PSW] & 0xFE) | _parity(sfr[ACC])
    return sfr[addr]


@njit(cache=True, inline="always")
def _wr(iram, sfr, addr, v):
    if addr < 0x80:
        iram[addr] = v & 0xFF
    else:
        sfr[addr] = v & 0xFF


@njit(cache=True, inline="always")
def _reg(sfr, n):
    return ((sfr[PSW] >> 3) & 3) * 8 + n


@njit(cache=True, inline="always")
def _bitaddr(bit):
    if bit < 0x80:
        return 0x20 + (bit >> 3)
    return bit & 0xF8


@njit(cache=True, inline="always")
def _rbit(iram, sfr, bit):
    return (_rd(iram, sfr, _bitaddr(bit)) >> (bit & 7)) & 1


@njit(cache=True, inline="always")
def _wbit(iram, sfr, bit, v):
    a = _bitaddr(bit)
    cur = sfr[PSW] if a == PSW else _rd(iram, sfr, a)
    if v:
        cur |= 1 << (bit & 7)
    else:
        cur &= ~(1 << (bit & 7))
    _wr(iram, sfr, a, cur)


@njit(cache=True, inline="always")
def _rel(pc, off):
    if off & 0x80:
        off -= 256
    return (pc + off) & 0xFFFF


@njit(cache=True, inline="always")
def _add(sfr, v, carry):
    a = sfr[ACC]
    r = a + v + carry
    psw = sfr[PSW] & 0x3B
    if r > 0xFF:
        psw |= 0x80
    if (a & 0x0F) + (v & 0x0F) + carry > 0x0F:
        psw |= 0x40
    if (a ^ r) & (v ^ r) & 0x80:
        psw |= 0x04
    sfr[PSW] = psw
    sfr[ACC] = r & 0xFF


@njit(cache=True, inline="always")
def _subb(sfr, v):
    a = sfr[ACC]
    c = sfr[PSW] >> 7
    r = a - v - c
    psw = sfr[PSW] & 0x3B
    if r < 0:
        psw |= 0x80
    if (a & 0x0F) - (v & 0x0F) - c < 0:
        psw |= 0x40
    if (a ^ v) & (a ^ r) & 0x80:
        psw |= 0x04
    sfr[PSW] = psw
    sfr[ACC] = r & 0xFF


@njit(cache=True)
def run(code, iram, sfr, xdata, state, stop_pc, max_steps):
    """Execute until pc == stop_pc. state = [pc, steps]. Returns 0 ok, 1 step limit, 2 illegal opcode."""
    pc = state[0]
    steps = 0
    cyc = 0
    while pc != stop_pc:
        if steps >= max_steps:
            state[0] = pc
            state[1] += steps
            state[2] += cyc
            return 1
        op = code[pc]
        pc = (pc + 1) & 0xFFFF
        steps += 1
        cyc += CYC[op]
        lo = op & 0x0F
        hi = op >> 4
        # ---- operand-family groups (A op src) ------------------------------------------------
        if lo >= 4 and hi in (2, 3, 4, 5, 6, 9, 0xE) and not (op == 0xE4):
            if lo == 4:
                v = code[pc]; pc = (pc + 1) & 0xFFFF
            elif lo == 5:
                v = _rd(iram, sfr, code[pc]); pc = (pc + 1) & 0xFFFF
            elif lo < 8:
                v = iram[iram[_reg(sfr, lo - 6)]]
            else:
                v = iram[_reg(sfr, lo - 8)]
            if hi == 2:
                _add(sfr, v, 0)
            elif hi == 3:
                _add(sfr, v, sfr[PSW] >> 7)
            elif hi == 4:
                sfr[ACC] = sfr[ACC] | v
            elif hi == 5:
                sfr[ACC] = sfr[ACC] & v
            elif hi == 6:
                sfr[ACC] = sfr[ACC] ^ v
            elif hi == 9:
                _subb(sfr, v)
            else:  # 0xE5..0xEF MOV A,src
                sfr[ACC] = v
            continue
        if (op & 0x1F) == 0x01:  # AJMP
            a8 = code[pc]; pc = (pc + 1) & 0xFFFF
            pc = (pc & 0xF800) | ((op & 0xE0) << 3) | a8
            continue
        if (op & 0x1F) == 0x11:  # ACALL
            a8 = code[pc]; pc = (pc + 1) & 0xFFFF
            sp = (sfr[SP] + 1) & 0xFF; iram[sp] = pc & 0xFF
            sp = (sp + 1) & 0xFF; iram[sp] = pc >> 8; sfr[SP] = sp
            pc = (pc & 0xF800) | ((op & 0xE0) << 3) | a8
            continue
        if op == 0x00:
            pass
        elif op == 0x02:
            pc = (code[pc] << 8) | code[(pc + 1) & 0xFFFF]
        elif op == 0x12:
            t = (code[pc] << 8) | code[(pc + 1) & 0xFFFF]
            pc = (pc + 2) & 0xFFFF
            sp = (sfr[SP] + 1) & 0xFF; iram[sp] = pc & 0xFF
            sp = (sp + 1) & 0xFF; iram[sp] = pc >> 8; sfr[SP] = sp
            pc = t
        elif op == 0x22 or op == 0x32:
            sp = sfr[SP]; h = iram[sp]; sp = (sp - 1) & 0xFF; l = iram[sp]; sfr[SP] = (sp - 1) & 0xFF
            pc = (h << 8) | l
        elif op == 0x80:
            pc = _rel((pc + 1) & 0xFFFF, code[pc])
        elif op == 0x73:
            pc = (sfr[ACC] + ((sfr[DPH] << 8) | sfr[DPL])) & 0xFFFF
        elif op == 0x40 or op == 0x50 or op == 0x60 or op == 0x70:
            r = code[pc]; pc = (pc + 1) & 0xFFFF
            cy = sfr[PSW] >> 7
            if (op == 0x40 and cy) or (op == 0x50 and not cy) or (op == 0x60 and sfr[ACC] == 0) or (op == 0x70 and sfr[ACC] != 0):
                pc = _rel(pc, r)
        elif op == 0x20 or op == 0x30 or op == 0x10:
            bit = code[pc]; r = code[(pc + 1) & 0xFFFF]; pc = (pc + 2) & 0xFFFF
            b = _rbit(iram, sfr, bit)
            if op == 0x20 and b:
                pc = _rel(pc, r)
            elif op == 0x30 and not b:
                pc = _rel(pc, r)
            elif op == 0x10 and b:
                _wbit(iram, sfr, bit, 0); pc = _rel(pc, r)
        elif hi == 0xB and lo >= 4:  # CJNE
            if lo == 4:
                x = sfr[ACC]; y = code[pc]; pc = (pc + 1) & 0xFFFF
            elif lo == 5:
                x = sfr[ACC]; y = _rd(iram, sfr, code[pc]); pc = (pc + 1) & 0xFFFF
            elif lo < 8:
                x = iram[iram[_reg(sfr, lo - 6)]]; y = code[pc]; pc = (pc + 1) & 0xFFFF
            else:
                x = iram[_reg(sfr, lo - 8)]; y = code[pc]; pc = (pc + 1) & 0xFFFF
            r = code[pc]; pc = (pc + 1) & 0xFFFF
            if x < y:
                sfr[PSW] = sfr[PSW] | 0x80
            else:
                sfr[PSW] = sfr[PSW] & 0x7F
            if x != y:
                pc = _rel(pc, r)
        elif op == 0xD5:
            d = code[pc]; r = code[(pc + 1) & 0xFFFF]; pc = (pc + 2) & 0xFFFF
            v = (_rd(iram, sfr, d) - 1) & 0xFF; _wr(iram, sfr, d, v)
            if v:
                pc = _rel(pc, r)
        elif hi == 0xD and lo >= 8:
            r = code[pc]; pc = (pc + 1) & 0xFFFF
            ra = _reg(sfr, lo - 8); v = (iram[ra] - 1) & 0xFF; iram[ra] = v
            if v:
                pc = _rel(pc, r)
        elif op == 0xE4:
            sfr[ACC] = 0
        elif op == 0x74:
            sfr[ACC] = code[pc]; pc = (pc + 1) & 0xFFFF
        elif op in (0x42, 0x43, 0x52, 0x53, 0x62, 0x63):
            d = code[pc]; pc = (pc + 1) & 0xFFFF
            if op & 1:
                v = code[pc]; pc = (pc + 1) & 0xFFFF
            else:
                v = sfr[ACC]
            x = _rd(iram, sfr, d)
            if hi == 4:
                x |= v
            elif hi == 5:
                x &= v
            else:
                x ^= v
            _wr(iram, sfr, d, x)
        elif op == 0x04:
            sfr[ACC] = (sfr[ACC] + 1) & 0xFF
        elif op == 0x14:
            sfr[ACC] = (sfr[ACC] - 1) & 0xFF
        elif op == 0x05 or op == 0x15:
            d = code[pc]; pc = (pc + 1) & 0xFFFF
            _wr(iram, sfr, d, _rd(iram, sfr, d) + (1 if op == 0x05 else -1))
        elif op in (0x06, 0x07, 0x16, 0x17):
            a = iram[_reg(sfr, op & 1)]
            iram[a] = (iram[a] + (1 if hi == 0 else -1)) & 0xFF
        elif hi <= 1 and lo >= 8:
            ra = _reg(sfr, lo - 8)
            iram[ra] = (iram[ra] + (1 if hi == 0 else -1)) & 0xFF
        elif op == 0xA3:
            d = (((sfr[DPH] << 8) | sfr[DPL]) + 1) & 0xFFFF
            sfr[DPH] = d >> 8; sfr[DPL] = d & 0xFF
        elif op == 0x03:
            a = sfr[ACC]; sfr[ACC] = (a >> 1) | ((a & 1) << 7)
        elif op == 0x23:
            a = sfr[ACC]; sfr[ACC] = ((a << 1) | (a >> 7)) & 0xFF
        elif op == 0x13:
            a = sfr[ACC]; c = sfr[PSW] >> 7
            sfr[PSW] = (sfr[PSW] & 0x7F) | ((a & 1) << 7); sfr[ACC] = (a >> 1) | (c << 7)
        elif op == 0x33:
            a = sfr[ACC]; c = sfr[PSW] >> 7
            sfr[PSW] = (sfr[PSW] & 0x7F) | ((a >> 7) << 7); sfr[ACC] = ((a << 1) | c) & 0xFF
        elif op == 0xC4:
            a = sfr[ACC]; sfr[ACC] = ((a << 4) | (a >> 4)) & 0xFF
        elif op == 0xF4:
            sfr[ACC] = (~sfr[ACC]) & 0xFF
        elif op == 0xD4:
            a = sfr[ACC]; cy = sfr[PSW] >> 7
            if (a & 0x0F) > 9 or (sfr[PSW] & 0x40):
                a += 6
            if a > 0xFF:
                cy = 1
            if ((a >> 4) & 0x1F) > 9 or cy:
                a += 0x60
            if a > 0xFF:
                cy = 1
            sfr[ACC] = a & 0xFF
            sfr[PSW] = (sfr[PSW] & 0x7F) | (cy << 7)
        elif op == 0xA4:
            r = sfr[ACC] * sfr[B]
            sfr[ACC] = r & 0xFF; sfr[B] = (r >> 8) & 0xFF
            sfr[PSW] = (sfr[PSW] & 0x7B) | (0x04 if r > 0xFF else 0)
        elif op == 0x84:
            b = sfr[B]
            psw = sfr[PSW] & 0x7B
            if b == 0:
                psw |= 0x04
            else:
                q = sfr[ACC] // b; rmd = sfr[ACC] % b; sfr[ACC] = q; sfr[B] = rmd
            sfr[PSW] = psw
        elif op == 0x75:
            d = code[pc]; v = code[(pc + 1) & 0xFFFF]; pc = (pc + 2) & 0xFFFF
            _wr(iram, sfr, d, v)
        elif op == 0x85:
            s = code[pc]; d = code[(pc + 1) & 0xFFFF]; pc = (pc + 2) & 0xFFFF
            _wr(iram, sfr, d, _rd(iram, sfr, s))
        elif op == 0xF5:
            _wr(iram, sfr, code[pc], sfr[ACC]); pc = (pc + 1) & 0xFFFF
        elif op == 0x76 or op == 0x77:
            iram[iram[_reg(sfr, op & 1)]] = code[pc]; pc = (pc + 1) & 0xFFFF
        elif op == 0x86 or op == 0x87:
            _wr(iram, sfr, code[pc], iram[iram[_reg(sfr, op & 1)]]); pc = (pc + 1) & 0xFFFF
        elif op == 0xA6 or op == 0xA7:
            iram[iram[_reg(sfr, op & 1)]] = _rd(iram, sfr, code[pc]); pc = (pc + 1) & 0xFFFF
        elif op == 0xF6 or op == 0xF7:
            iram[iram[_reg(sfr, op & 1)]] = sfr[ACC]
        elif hi == 7 and lo >= 8:
            iram[_reg(sfr, lo - 8)] = code[pc]; pc = (pc + 1) & 0xFFFF
        elif hi == 8 and lo >= 8:
            _wr(iram, sfr, code[pc], iram[_reg(sfr, lo - 8)]); pc = (pc + 1) & 0xFFFF
        elif hi == 0xA and lo >= 8:
            iram[_reg(sfr, lo - 8)] = _rd(iram, sfr, code[pc]); pc = (pc + 1) & 0xFFFF
        elif hi == 0xF and lo >= 8:
            iram[_reg(sfr, lo - 8)] = sfr[ACC]
        elif op == 0x90:
            sfr[DPH] = code[pc]; sfr[DPL] = code[(pc + 1) & 0xFFFF]; pc = (pc + 2) & 0xFFFF
        elif op == 0x93:
            sfr[ACC] = code[(sfr[ACC] + ((sfr[DPH] << 8) | sfr[DPL])) & 0xFFFF]
        elif op == 0x83:
            sfr[ACC] = code[(sfr[ACC] + pc) & 0xFFFF]
        elif op == 0xE0:
            sfr[ACC] = xdata[(sfr[DPH] << 8) | sfr[DPL]]
        elif op == 0xF0:
            xdata[(sfr[DPH] << 8) | sfr[DPL]] = sfr[ACC]
        elif op == 0xE2 or op == 0xE3:
            sfr[ACC] = xdata[(sfr[P2] << 8) | iram[_reg(sfr, op & 1)]]
        elif op == 0xF2 or op == 0xF3:
            xdata[(sfr[P2] << 8) | iram[_reg(sfr, op & 1)]] = sfr[ACC]
        elif op == 0xC5:
            d = code[pc]; pc = (pc + 1) & 0xFFFF
            t = _rd(iram, sfr, d); _wr(iram, sfr, d, sfr[ACC]); sfr[ACC] = t
        elif op == 0xC6 or op == 0xC7:
            a = iram[_reg(sfr, op & 1)]; t = iram[a]; iram[a] = sfr[ACC]; sfr[ACC] = t
        elif op == 0xD6 or op == 0xD7:
            a = iram[_reg(sfr, op & 1)]; t = iram[a]
            iram[a] = (t & 0xF0) | (sfr[ACC] & 0x0F); sfr[ACC] = (sfr[ACC] & 0xF0) | (t & 0x0F)
        elif hi == 0xC and lo >= 8:
            ra = _reg(sfr, lo - 8); t = iram[ra]; iram[ra] = sfr[ACC]; sfr[ACC] = t
        elif op == 0xC0:
            v = _rd(iram, sfr, code[pc]); pc = (pc + 1) & 0xFFFF
            sp = (sfr[SP] + 1) & 0xFF; iram[sp] = v; sfr[SP] = sp
        elif op == 0xD0:
            d = code[pc]; pc = (pc + 1) & 0xFFFF
            sp = sfr[SP]; v = iram[sp]; sfr[SP] = (sp - 1) & 0xFF
            _wr(iram, sfr, d, v)
        elif op == 0xC3:
            sfr[PSW] = sfr[PSW] & 0x7F
        elif op == 0xD3:
            sfr[PSW] = sfr[PSW] | 0x80
        elif op == 0xB3:
            sfr[PSW] = sfr[PSW] ^ 0x80
        elif op == 0xC2 or op == 0xD2:
            _wbit(iram, sfr, code[pc], 1 if op == 0xD2 else 0); pc = (pc + 1) & 0xFFFF
        elif op == 0xB2:
            bit = code[pc]; pc = (pc + 1) & 0xFFFF
            _wbit(iram, sfr, bit, 1 - _rbit(iram, sfr, bit))
        elif op == 0xA2:
            b = _rbit(iram, sfr, code[pc]); pc = (pc + 1) & 0xFFFF
            sfr[PSW] = (sfr[PSW] & 0x7F) | (b << 7)
        elif op == 0x92:
            _wbit(iram, sfr, code[pc], sfr[PSW] >> 7); pc = (pc + 1) & 0xFFFF
        elif op in (0x72, 0xA0, 0x82, 0xB0):
            b = _rbit(iram, sfr, code[pc]); pc = (pc + 1) & 0xFFFF
            cy = sfr[PSW] >> 7
            if op == 0x72:
                cy = cy | b
            elif op == 0xA0:
                cy = cy | (1 - b)
            elif op == 0x82:
                cy = cy & b
            else:
                cy = cy & (1 - b)
            sfr[PSW] = (sfr[PSW] & 0x7F) | (cy << 7)
        else:
            state[0] = (pc - 1) & 0xFFFF
            state[1] += steps
            state[2] += cyc
            return 2
    state[0] = pc
    state[1] += steps
    state[2] += cyc
    return 0



@njit(cache=True)
def call_fn(code, iram, sfr, xdata, state, addr, max_steps):
    """Push a return sentinel and run the subroutine at `addr` until it returns."""
    sp = (sfr[SP] + 1) & 0xFF
    iram[sp] = SENTINEL & 0xFF
    sp = (sp + 1) & 0xFF
    iram[sp] = SENTINEL >> 8
    sfr[SP] = sp
    state[0] = addr
    return run(code, iram, sfr, xdata, state, SENTINEL, max_steps)


@njit(cache=True)
def advance_cycles(code, iram, sfr, xdata, state, until, isr_period, isr_fn, tick_fn, light_fn,
                   pass_overhead, isr_overhead, idle_threshold):
    """Emulate firmware time until the cycle counter state[2] reaches `until`.

    Timer interrupts fire every `isr_period` cycles (state[3] = cycle of the next one). Between them the
    main loop runs passes (tick routine + lighting task). A long pass delays the next tick exactly as on
    the chip: interrupts during it only bump a counter that the tick routine resets.
    """
    while state[2] < until:
        if state[2] >= state[3]:
            rc = call_fn(code, iram, sfr, xdata, state, isr_fn, 1000000)
            if rc != 0:
                return rc
            state[2] += isr_overhead
            state[3] += isr_period
            continue
        c0 = state[2]
        rc = call_fn(code, iram, sfr, xdata, state, tick_fn, 5000000)
        if rc != 0:
            return rc
        rc = call_fn(code, iram, sfr, xdata, state, light_fn, 5000000)
        if rc != 0:
            return rc
        state[2] += pass_overhead
        if state[2] - c0 < idle_threshold and state[2] < state[3]:
            state[2] = state[3]          # nothing to do until the next interrupt
    return 0


class FastCPU:
    """Same interface subset as CPU8051 (code/iram/sfr/xdata, call, run_until) on numpy arrays."""

    def __init__(self, code: bytes):
        self.code = np.zeros(65536, dtype=np.int64)
        self.code[:len(code)] = np.frombuffer(bytes(code), dtype=np.uint8)
        self.iram = np.zeros(256, dtype=np.int64)
        self.sfr = np.zeros(256, dtype=np.int64)
        self.xdata = np.zeros(65536, dtype=np.int64)
        self.sfr[SP] = 0x07
        self.state = np.zeros(4, dtype=np.int64)   # pc, steps, cycles, next interrupt (cycles)

    @property
    def steps(self) -> int:
        return int(self.state[1])

    def _go(self, stop, max_steps):
        rc = run(self.code, self.iram, self.sfr, self.xdata, self.state, stop, max_steps)
        if rc == 1:
            raise RuntimeError(f"step limit (pc={int(self.state[0]):#06x})")
        if rc == 2:
            raise RuntimeError(f"illegal opcode at {int(self.state[0]):#06x}")

    def call(self, addr: int, max_steps: int = 5_000_000):
        sp = (self.sfr[SP] + 1) & 0xFF
        self.iram[sp] = SENTINEL & 0xFF
        sp = (sp + 1) & 0xFF
        self.iram[sp] = SENTINEL >> 8
        self.sfr[SP] = sp
        self.state[0] = addr
        self._go(SENTINEL, max_steps)

    def run_until(self, addr: int, max_steps: int = 5_000_000):
        self._go(addr, max_steps)

    @property
    def pc(self):
        return int(self.state[0])

    @pc.setter
    def pc(self, v):
        self.state[0] = v

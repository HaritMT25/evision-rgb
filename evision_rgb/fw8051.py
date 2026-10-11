"""Minimal MCS-51 (8051) interpreter used to run the keyboard firmware's own lighting code.

The AK820's MCU (EVision VS11K28A, a WCH CH555 clone) is an 8051. Instead of re-implementing each
built-in effect by hand, the app executes the firmware's effect routines here and reads the LED
colours they produce, so previews and overlays match the hardware by construction.

Memory: 64 KB CODE, 256 B IRAM, 128 B SFR (0x80-0xFF), 64 KB XDATA. Only the core instruction set
is emulated; peripherals are plain SFR bytes (callers seed timers / random sources as needed).
"""
from __future__ import annotations

ACC, B, PSW, SP, DPL, DPH, P2 = 0xE0, 0xF0, 0xD0, 0x81, 0x82, 0x83, 0xA0
RETURN_SENTINEL = 0xFFFE


class CPUError(RuntimeError):
    pass


class CPU8051:
    def __init__(self, code: bytes):
        self.code = bytearray(65536)
        self.code[:len(code)] = code
        self.iram = bytearray(256)
        self.sfr = bytearray(256)       # index = SFR address (0x80-0xFF used)
        self.xdata = bytearray(65536)
        self.pc = 0
        self.sfr[SP] = 0x07
        self.steps = 0
        self.sfr_read_hooks: dict[int, callable] = {}
        self.sfr_write_hooks: dict[int, callable] = {}
        self._ops = self._build()

    # ------------------------------------------------------------------ memory helpers
    @property
    def a(self) -> int:
        return self.sfr[ACC]

    @a.setter
    def a(self, v: int):
        self.sfr[ACC] = v & 0xFF

    @property
    def cy(self) -> int:
        return self.sfr[PSW] >> 7

    @cy.setter
    def cy(self, v):
        self.sfr[PSW] = (self.sfr[PSW] & 0x7F) | (0x80 if v else 0)

    @property
    def dptr(self) -> int:
        return self.sfr[DPH] << 8 | self.sfr[DPL]

    @dptr.setter
    def dptr(self, v: int):
        self.sfr[DPH], self.sfr[DPL] = (v >> 8) & 0xFF, v & 0xFF

    def _reg_addr(self, n: int) -> int:
        return ((self.sfr[PSW] >> 3) & 3) * 8 + n

    def R(self, n: int) -> int:
        return self.iram[self._reg_addr(n)]

    def setR(self, n: int, v: int):
        self.iram[self._reg_addr(n)] = v & 0xFF

    def rd(self, addr: int) -> int:  # direct
        if addr < 0x80:
            return self.iram[addr]
        if addr == PSW:
            return self._psw_with_parity()
        h = self.sfr_read_hooks.get(addr)
        return h() & 0xFF if h else self.sfr[addr]

    def wr(self, addr: int, v: int):
        v &= 0xFF
        if addr < 0x80:
            self.iram[addr] = v
        else:
            self.sfr[addr] = v
            h = self.sfr_write_hooks.get(addr)
            if h:
                h(v)

    def _psw_with_parity(self) -> int:
        p = bin(self.sfr[ACC]).count("1") & 1
        return (self.sfr[PSW] & 0xFE) | p

    def rdi(self, ri: int) -> int:  # indirect @Ri (full 256 B IRAM)
        return self.iram[self.R(ri)]

    def wri(self, ri: int, v: int):
        self.iram[self.R(ri)] = v & 0xFF

    def bit_addr(self, bit: int) -> tuple[int, int]:
        if bit < 0x80:
            return 0x20 + (bit >> 3), bit & 7
        return bit & 0xF8, bit & 7

    def rbit(self, bit: int) -> int:
        a, n = self.bit_addr(bit)
        return (self.rd(a) >> n) & 1

    def wbit(self, bit: int, v: int):
        a, n = self.bit_addr(bit)
        cur = self.rd(a) if a != PSW else self.sfr[PSW]
        self.wr(a, (cur | (1 << n)) if v else (cur & ~(1 << n)))

    def push(self, v: int):
        sp = (self.sfr[SP] + 1) & 0xFF
        self.sfr[SP] = sp
        self.iram[sp] = v & 0xFF

    def pop(self) -> int:
        sp = self.sfr[SP]
        v = self.iram[sp]
        self.sfr[SP] = (sp - 1) & 0xFF
        return v

    def fetch(self) -> int:
        v = self.code[self.pc]
        self.pc = (self.pc + 1) & 0xFFFF
        return v

    def rel(self, off: int) -> int:
        return (self.pc + (off - 256 if off & 0x80 else off)) & 0xFFFF

    # ------------------------------------------------------------------ arithmetic
    def _add(self, v: int, carry: int = 0):
        a = self.a
        r = a + v + carry
        psw = self.sfr[PSW] & 0x3B  # clear CY, AC, OV
        if r > 0xFF:
            psw |= 0x80
        if (a & 0x0F) + (v & 0x0F) + carry > 0x0F:
            psw |= 0x40
        if ((a ^ r) & (v ^ r) & 0x80):
            psw |= 0x04
        self.sfr[PSW] = psw
        self.a = r

    def _subb(self, v: int):
        a, c = self.a, self.cy
        r = a - v - c
        psw = self.sfr[PSW] & 0x3B
        if r < 0:
            psw |= 0x80
        if (a & 0x0F) - (v & 0x0F) - c < 0:
            psw |= 0x40
        if ((a ^ v) & (a ^ r) & 0x80):
            psw |= 0x04
        self.sfr[PSW] = psw
        self.a = r

    # ------------------------------------------------------------------ execution
    def call(self, addr: int, max_steps: int = 5_000_000) -> int:
        """Call a subroutine and run until it returns. Returns executed instruction count."""
        self.push(RETURN_SENTINEL & 0xFF)
        self.push(RETURN_SENTINEL >> 8)
        self.pc = addr
        start = self.steps
        ops = self._ops
        while self.pc != RETURN_SENTINEL:
            op = self.fetch()
            ops[op](op)
            self.steps += 1
            if self.steps - start > max_steps:
                raise CPUError(f"no return from {addr:#06x} after {max_steps} steps (pc={self.pc:#06x})")
        return self.steps - start

    def run_until(self, addr: int, max_steps: int = 5_000_000) -> int:
        """Run from the current PC until PC == addr (e.g. boot code until main)."""
        start = self.steps
        ops = self._ops
        while self.pc != addr:
            op = self.fetch()
            ops[op](op)
            self.steps += 1
            if self.steps - start > max_steps:
                raise CPUError(f"did not reach {addr:#06x} within {max_steps} steps (pc={self.pc:#06x})")
        return self.steps - start

    def _build(self):
        c = self
        ops = [None] * 256

        def ill(op):
            raise CPUError(f"illegal opcode {op:#04x} at {(c.pc - 1) & 0xFFFF:#06x}")

        # --- jumps / calls
        def ajmp(op):
            lo = c.fetch()
            c.pc = (c.pc & 0xF800) | ((op & 0xE0) << 3) | lo

        def acall(op):
            lo = c.fetch()
            c.push(c.pc & 0xFF); c.push(c.pc >> 8)
            c.pc = (c.pc & 0xF800) | ((op & 0xE0) << 3) | lo

        for op in range(0x01, 0x100, 0x20):
            ops[op] = ajmp
            ops[op + 0x10] = acall

        def ljmp(op):
            hi, lo = c.fetch(), c.fetch(); c.pc = hi << 8 | lo

        def lcall(op):
            hi, lo = c.fetch(), c.fetch()
            c.push(c.pc & 0xFF); c.push(c.pc >> 8); c.pc = hi << 8 | lo

        def ret(op):
            hi = c.pop(); lo = c.pop(); c.pc = hi << 8 | lo

        def sjmp(op):
            r = c.fetch(); c.pc = c.rel(r)

        def jmp_a_dptr(op):
            c.pc = (c.a + c.dptr) & 0xFFFF

        ops[0x02], ops[0x12], ops[0x22], ops[0x32], ops[0x80], ops[0x73] = ljmp, lcall, ret, ret, sjmp, jmp_a_dptr

        def jcond(cond):
            def f(op):
                r = c.fetch()
                if cond():
                    c.pc = c.rel(r)
            return f

        ops[0x40] = jcond(lambda: c.cy)
        ops[0x50] = jcond(lambda: not c.cy)
        ops[0x60] = jcond(lambda: c.a == 0)
        ops[0x70] = jcond(lambda: c.a != 0)

        def jb(op):
            bit, r = c.fetch(), c.fetch()
            if c.rbit(bit):
                c.pc = c.rel(r)

        def jnb(op):
            bit, r = c.fetch(), c.fetch()
            if not c.rbit(bit):
                c.pc = c.rel(r)

        def jbc(op):
            bit, r = c.fetch(), c.fetch()
            if c.rbit(bit):
                c.wbit(bit, 0); c.pc = c.rel(r)

        ops[0x20], ops[0x30], ops[0x10] = jb, jnb, jbc

        def cjne(get_a, get_b):
            def f(op):
                x = get_a(); y = get_b(); r = c.fetch()
                c.cy = x < y
                if x != y:
                    c.pc = c.rel(r)
            return f

        ops[0xB4] = cjne(lambda: c.a, lambda: c.fetch())
        ops[0xB5] = cjne(lambda: c.a, lambda: c.rd(c.fetch()))
        for i in (0, 1):
            ops[0xB6 + i] = (lambda i: cjne(lambda: c.rdi(i), lambda: c.fetch()))(i)
        for n in range(8):
            ops[0xB8 + n] = (lambda n: cjne(lambda: c.R(n), lambda: c.fetch()))(n)

        def djnz_dir(op):
            d, r = c.fetch(), c.fetch()
            v = (c.rd(d) - 1) & 0xFF; c.wr(d, v)
            if v:
                c.pc = c.rel(r)

        ops[0xD5] = djnz_dir
        for n in range(8):
            def djnz(op, n=n):
                r = c.fetch(); v = (c.R(n) - 1) & 0xFF; c.setR(n, v)
                if v:
                    c.pc = c.rel(r)
            ops[0xD8 + n] = djnz

        # --- operand families: (#imm, direct, @R0, @R1, R0..R7) at low nibbles 4..F
        def src(low):
            if low == 4:
                return lambda: c.fetch()
            if low == 5:
                return lambda: c.rd(c.fetch())
            if low in (6, 7):
                return lambda i=low - 6: c.rdi(i)
            return lambda n=low - 8: c.R(n)

        for low in range(4, 16):
            g = src(low)
            ops[0x20 + low] = (lambda g: lambda op: c._add(g()))(g)
            ops[0x30 + low] = (lambda g: lambda op: c._add(g(), c.cy))(g)
            ops[0x40 + low] = (lambda g: lambda op: setattr(c, "a", c.a | g()))(g)
            ops[0x50 + low] = (lambda g: lambda op: setattr(c, "a", c.a & g()))(g)
            ops[0x60 + low] = (lambda g: lambda op: setattr(c, "a", c.a ^ g()))(g)
            ops[0x90 + low] = (lambda g: lambda op: c._subb(g()))(g)
            ops[0xE0 + low] = (lambda g: lambda op: setattr(c, "a", g()))(g) if low != 4 else None
        ops[0xE4] = lambda op: setattr(c, "a", 0)  # CLR A
        ops[0x74] = lambda op: setattr(c, "a", c.fetch())  # MOV A,#imm

        # ORL/ANL/XRL direct,A / direct,#imm
        def logic_dir(fn, imm):
            def f(op):
                d = c.fetch(); v = c.fetch() if imm else c.a
                c.wr(d, fn(c.rd(d), v))
            return f

        ops[0x42], ops[0x43] = logic_dir(lambda x, y: x | y, 0), logic_dir(lambda x, y: x | y, 1)
        ops[0x52], ops[0x53] = logic_dir(lambda x, y: x & y, 0), logic_dir(lambda x, y: x & y, 1)
        ops[0x62], ops[0x63] = logic_dir(lambda x, y: x ^ y, 0), logic_dir(lambda x, y: x ^ y, 1)

        # INC / DEC
        ops[0x04] = lambda op: setattr(c, "a", c.a + 1)
        ops[0x14] = lambda op: setattr(c, "a", c.a - 1)

        def incdec_dir(d):
            def f(op):
                a = c.fetch(); c.wr(a, c.rd(a) + d)
            return f

        ops[0x05], ops[0x15] = incdec_dir(1), incdec_dir(-1)
        for i in (0, 1):
            ops[0x06 + i] = (lambda i: lambda op: c.wri(i, c.rdi(i) + 1))(i)
            ops[0x16 + i] = (lambda i: lambda op: c.wri(i, c.rdi(i) - 1))(i)
        for n in range(8):
            ops[0x08 + n] = (lambda n: lambda op: c.setR(n, c.R(n) + 1))(n)
            ops[0x18 + n] = (lambda n: lambda op: c.setR(n, c.R(n) - 1))(n)
        ops[0xA3] = lambda op: setattr(c, "dptr", c.dptr + 1)

        # rotates / misc on A
        ops[0x03] = lambda op: setattr(c, "a", (c.a >> 1) | ((c.a & 1) << 7))
        ops[0x23] = lambda op: setattr(c, "a", ((c.a << 1) | (c.a >> 7)) & 0xFF)

        def rrc(op):
            a = c.a; nc = a & 1; c.a = (a >> 1) | (c.cy << 7); c.cy = nc

        def rlc(op):
            a = c.a; nc = a >> 7; c.a = ((a << 1) | c.cy) & 0xFF; c.cy = nc

        ops[0x13], ops[0x33] = rrc, rlc
        ops[0xC4] = lambda op: setattr(c, "a", ((c.a << 4) | (c.a >> 4)) & 0xFF)
        ops[0xF4] = lambda op: setattr(c, "a", ~c.a)

        def da(op):
            a = c.a; cy = c.cy
            if (a & 0x0F) > 9 or (c.sfr[PSW] & 0x40):
                a += 6
            if a > 0xFF:
                cy = 1
            if ((a >> 4) & 0x1F) > 9 or cy:
                a += 0x60
            if a > 0xFF:
                cy = 1
            c.a = a; c.cy = cy

        ops[0xD4] = da

        def mul(op):
            r = c.a * c.sfr[B]
            c.a = r; c.sfr[B] = r >> 8
            c.sfr[PSW] = (c.sfr[PSW] & 0x7B) | (0x04 if r > 0xFF else 0)

        def div(op):
            b = c.sfr[B]
            psw = c.sfr[PSW] & 0x7B
            if b == 0:
                psw |= 0x04
            else:
                q, r = divmod(c.a, b); c.a = q; c.sfr[B] = r
            c.sfr[PSW] = psw

        ops[0xA4], ops[0x84] = mul, div

        # MOV family
        def mov_dir_imm(op):
            d = c.fetch(); c.wr(d, c.fetch())

        def mov_dir_dir(op):
            s = c.fetch(); d = c.fetch(); c.wr(d, c.rd(s))

        ops[0x75], ops[0x85] = mov_dir_imm, mov_dir_dir
        ops[0xF5] = lambda op: c.wr(c.fetch(), c.a)
        for i in (0, 1):
            ops[0x76 + i] = (lambda i: lambda op: c.wri(i, c.fetch()))(i)
            ops[0x86 + i] = (lambda i: lambda op: c.wr(c.fetch(), c.rdi(i)))(i)
            ops[0xA6 + i] = (lambda i: lambda op: c.wri(i, c.rd(c.fetch())))(i)
            ops[0xF6 + i] = (lambda i: lambda op: c.wri(i, c.a))(i)
        for n in range(8):
            ops[0x78 + n] = (lambda n: lambda op: c.setR(n, c.fetch()))(n)
            ops[0x88 + n] = (lambda n: lambda op: c.wr(c.fetch(), c.R(n)))(n)
            ops[0xA8 + n] = (lambda n: lambda op: c.setR(n, c.rd(c.fetch())))(n)
            ops[0xF8 + n] = (lambda n: lambda op: c.setR(n, c.a))(n)

        def mov_dptr(op):
            hi, lo = c.fetch(), c.fetch(); c.dptr = hi << 8 | lo

        ops[0x90] = mov_dptr
        ops[0x93] = lambda op: setattr(c, "a", c.code[(c.a + c.dptr) & 0xFFFF])
        ops[0x83] = lambda op: setattr(c, "a", c.code[(c.a + c.pc) & 0xFFFF])
        ops[0xE0] = lambda op: setattr(c, "a", c.xdata[c.dptr])
        ops[0xF0] = lambda op: c.xdata.__setitem__(c.dptr, c.a)
        for i in (0, 1):
            ops[0xE2 + i] = (lambda i: lambda op: setattr(c, "a", c.xdata[c.sfr[P2] << 8 | c.R(i)]))(i)
            ops[0xF2 + i] = (lambda i: lambda op: c.xdata.__setitem__(c.sfr[P2] << 8 | c.R(i), c.a))(i)

        # XCH
        def xch_dir(op):
            d = c.fetch(); t = c.rd(d); c.wr(d, c.a); c.a = t

        ops[0xC5] = xch_dir
        for i in (0, 1):
            def xch_i(op, i=i):
                t = c.rdi(i); c.wri(i, c.a); c.a = t

            def xchd(op, i=i):
                t = c.rdi(i); c.wri(i, (t & 0xF0) | (c.a & 0x0F)); c.a = (c.a & 0xF0) | (t & 0x0F)
            ops[0xC6 + i], ops[0xD6 + i] = xch_i, xchd
        for n in range(8):
            def xch_r(op, n=n):
                t = c.R(n); c.setR(n, c.a); c.a = t
            ops[0xC8 + n] = xch_r

        # stack
        ops[0xC0] = lambda op: c.push(c.rd(c.fetch()))
        ops[0xD0] = lambda op: c.wr(c.fetch(), c.pop())

        # bit ops
        ops[0xC3] = lambda op: setattr(c, "cy", 0)
        ops[0xD3] = lambda op: setattr(c, "cy", 1)
        ops[0xB3] = lambda op: setattr(c, "cy", not c.cy)
        ops[0xC2] = lambda op: c.wbit(c.fetch(), 0)
        ops[0xD2] = lambda op: c.wbit(c.fetch(), 1)

        def cpl_bit(op):
            b = c.fetch(); c.wbit(b, not c.rbit(b))

        ops[0xB2] = cpl_bit
        ops[0xA2] = lambda op: setattr(c, "cy", c.rbit(c.fetch()))
        ops[0x92] = lambda op: c.wbit(c.fetch(), c.cy)
        ops[0x72] = lambda op: setattr(c, "cy", c.cy | c.rbit(c.fetch()))
        ops[0xA0] = lambda op: setattr(c, "cy", c.cy | (not c.rbit(c.fetch())))
        ops[0x82] = lambda op: setattr(c, "cy", c.cy & c.rbit(c.fetch()))
        ops[0xB0] = lambda op: setattr(c, "cy", c.cy & (not c.rbit(c.fetch())))

        ops[0x00] = lambda op: None  # NOP
        return [f or ill for f in ops]

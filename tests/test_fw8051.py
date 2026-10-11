"""Instruction-level tests for the 8051 interpreter (hand-assembled programs, known results)."""
import unittest

from evision_rgb.fw8051 import CPU8051, CPUError


def run(prog: bytes, setup=None):
    cpu = CPU8051(b"\x00" * 0x100 + prog + b"\x22")  # program at 0x100, RET appended
    if setup:
        setup(cpu)
    cpu.call(0x100)
    return cpu


class Arithmetic(unittest.TestCase):
    def test_add_sets_carry_aux_and_overflow(self):
        c = run(bytes([0x74, 0x7F, 0x24, 0x01]))          # MOV A,#7F ; ADD A,#1
        self.assertEqual(c.a, 0x80)
        self.assertEqual(c.cy, 0)
        self.assertTrue(c.sfr[0xD0] & 0x04, "OV")
        self.assertTrue(c.sfr[0xD0] & 0x40, "AC")
        c = run(bytes([0x74, 0xFF, 0x24, 0x02]))
        self.assertEqual((c.a, c.cy), (0x01, 1))

    def test_addc_and_subb_use_carry(self):
        c = run(bytes([0xD3, 0x74, 0x10, 0x34, 0x01]))    # SETB C; MOV A,#10; ADDC A,#1
        self.assertEqual(c.a, 0x12)
        c = run(bytes([0xD3, 0x74, 0x10, 0x94, 0x01]))    # SETB C; MOV A,#10; SUBB A,#1
        self.assertEqual((c.a, c.cy), (0x0E, 0))
        c = run(bytes([0xC3, 0x74, 0x00, 0x94, 0x01]))    # borrow
        self.assertEqual((c.a, c.cy), (0xFF, 1))

    def test_16bit_compare_idiom_used_by_the_firmware(self):
        # SETB C; MOV A,#lo; SUBB A,#0x3C; MOV A,#hi; SUBB A,#0  -> C set when (hi:lo) <= 0x3C
        prog = lambda hi, lo: bytes([0xD3, 0x74, lo, 0x94, 0x3C, 0x74, hi, 0x94, 0x00])
        self.assertEqual(run(prog(0, 0x3C)).cy, 1)
        self.assertEqual(run(prog(0, 0x3D)).cy, 0)

    def test_mul_and_div(self):
        c = run(bytes([0x74, 0x2A, 0x75, 0xF0, 0x0B, 0xA4]))  # 42*11 = 462 = 0x01CE
        self.assertEqual((c.a, c.sfr[0xF0]), (0xCE, 0x01))
        c = run(bytes([0x74, 0x64, 0x75, 0xF0, 0x07, 0x84]))  # 100/7 = 14 r 2
        self.assertEqual((c.a, c.sfr[0xF0]), (14, 2))

    def test_rotates_swap_cpl_and_da(self):
        self.assertEqual(run(bytes([0x74, 0x81, 0x23])).a, 0x03)          # RL
        self.assertEqual(run(bytes([0x74, 0x81, 0x03])).a, 0xC0)          # RR
        self.assertEqual(run(bytes([0xD3, 0x74, 0x00, 0x13])).a, 0x80)    # RRC with C
        self.assertEqual(run(bytes([0x74, 0x12, 0xC4])).a, 0x21)          # SWAP
        self.assertEqual(run(bytes([0x74, 0x0F, 0xF4])).a, 0xF0)          # CPL A
        self.assertEqual(run(bytes([0x74, 0x09, 0x24, 0x08, 0xD4])).a, 0x17)  # BCD 9+8


class DataMovement(unittest.TestCase):
    def test_registers_follow_the_selected_bank(self):
        c = run(bytes([0x78, 0x11, 0x75, 0xD0, 0x08, 0x78, 0x22]))  # R0=11; PSW=bank1; R0=22
        self.assertEqual((c.iram[0x00], c.iram[0x08]), (0x11, 0x22))

    def test_movx_movc_and_indirect(self):
        prog = bytes([0x90, 0x12, 0x34, 0x74, 0x5A, 0xF0,       # MOV DPTR,#1234; MOV A,#5A; MOVX @DPTR,A
                      0xE4, 0xE0, 0xF5, 0x30,                    # CLR A; MOVX A,@DPTR; MOV 30h,A
                      0x78, 0x30, 0xE6, 0xF5, 0x31,              # MOV R0,#30h; MOV A,@R0; MOV 31h,A
                      0x90, 0x00, 0x10, 0x74, 0x02, 0x93, 0xF5, 0x32])  # MOVC A,@A+DPTR (code[0x12])
        def setup(c):
            c.code[0x12] = 0x99
        c = run(prog, setup)
        self.assertEqual((c.xdata[0x1234], c.iram[0x30], c.iram[0x31], c.iram[0x32]), (0x5A, 0x5A, 0x5A, 0x99))

    def test_mov_direct_direct_has_source_first(self):
        c = run(bytes([0x75, 0x40, 0x77, 0x85, 0x40, 0x41]))  # MOV 40h,#77; MOV 41h,40h
        self.assertEqual(c.iram[0x41], 0x77)

    def test_push_pop_and_xch(self):
        c = run(bytes([0x74, 0x11, 0xC0, 0xE0, 0x74, 0x22, 0xD0, 0xF0, 0x75, 0x50, 0x33, 0xC5, 0x50]))
        self.assertEqual((c.sfr[0xF0], c.a, c.iram[0x50]), (0x11, 0x33, 0x22))


class ControlFlow(unittest.TestCase):
    def test_cjne_sets_carry_and_branches(self):
        # MOV A,#5; CJNE A,#9,+2; MOV 30h,#1 (skipped); MOV 31h,#2
        c = run(bytes([0x74, 0x05, 0xB4, 0x09, 0x03, 0x75, 0x30, 0x01, 0x75, 0x31, 0x02]))
        self.assertEqual((c.iram[0x30], c.iram[0x31], c.cy), (0, 2, 1))

    def test_djnz_loop(self):
        # MOV R2,#5; loop: INC 30h; DJNZ R2,loop
        c = run(bytes([0x7A, 0x05, 0x05, 0x30, 0xDA, 0xFC]))
        self.assertEqual(c.iram[0x30], 5)

    def test_lcall_ret_and_jump_table(self):
        # 0x100: MOV A,#2 ; MOV DPTR,#0x110 ; JMP @A+DPTR ... table at 0x110: SJMP x / SJMP y
        prog = bytearray(0x40)
        prog[0:6] = bytes([0x74, 0x02, 0x90, 0x01, 0x10, 0x73])
        prog[0x10:0x14] = bytes([0x80, 0x10, 0x80, 0x14])        # 0x110 -> 0x122, 0x112 -> 0x128
        prog[0x22:0x26] = bytes([0x75, 0x30, 0xAA, 0x22])
        prog[0x28:0x2C] = bytes([0x75, 0x30, 0xBB, 0x22])
        c = run(bytes(prog))
        self.assertEqual(c.iram[0x30], 0xBB)

    def test_bit_addressing(self):
        # SETB 20h.1 (bit 01); JB 01,+3; MOV 30h,#1; MOV 31h,#2; CLR ACC.7 path
        c = run(bytes([0xD2, 0x01, 0x20, 0x01, 0x03, 0x75, 0x30, 0x01, 0x75, 0x31, 0x02]))
        self.assertEqual((c.iram[0x20], c.iram[0x30], c.iram[0x31]), (0x02, 0, 2))
        c = run(bytes([0x74, 0xFF, 0xC2, 0xE7]))  # CLR ACC.7
        self.assertEqual(c.a, 0x7F)

    def test_runaway_code_is_stopped(self):
        cpu = CPU8051(b"\x00" * 0x100 + bytes([0x80, 0xFE]))  # SJMP $
        with self.assertRaises(CPUError):
            cpu.call(0x100, max_steps=1000)


if __name__ == "__main__":
    unittest.main()

# Ghidra headless script (Jython) for the 8051 keyboard firmware image (raw, code space = file offset).
# Seeds disassembly at the application entry (0x2800) and at every plausible LCALL/LJMP target, then
# lets auto-analysis build functions. Run as -preScript is not possible for raw; use as postScript then re-analyse.
from ghidra.app.cmd.disassemble import DisassembleCommand
from ghidra.program.model.address import AddressSet

prog = currentProgram
space = prog.getAddressFactory().getDefaultAddressSpace()
mem = prog.getMemory()
blk = mem.getBlocks()[0]
start, end = blk.getStart().getOffset(), blk.getEnd().getOffset()
APP = 0x2800


def b(off):
    return mem.getByte(space.getAddress(off)) & 0xff


def dis(off):
    DisassembleCommand(space.getAddress(off), None, True).applyTo(prog, monitor)


dis(APP)
targets = set()
for off in range(APP, end - 2):
    op = b(off)
    if op in (0x12, 0x02):  # LCALL / LJMP addr16
        t = (b(off + 1) << 8) | b(off + 2)
        if APP <= t < end and b(t) != 0xff:
            targets.add((op, t))
calls = sorted(t for op, t in targets if op == 0x12)
print("seeding %d LCALL targets" % len(calls))
for t in calls:
    dis(t)
    if getFunctionAt(space.getAddress(t)) is None:
        createFunction(space.getAddress(t), None)
if getFunctionAt(space.getAddress(APP)) is None:
    createFunction(space.getAddress(APP), "app_entry")
analyzeChanges(prog)
print("functions now: %d" % prog.getFunctionManager().getFunctionCount())

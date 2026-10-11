# Ghidra headless postScript: disassemble + create functions at the given hex addresses, then print
# their decompiled C. args: <out_path> <addr1> [addr2 ...]
from ghidra.app.cmd.disassemble import DisassembleCommand
from ghidra.app.decompiler import DecompInterface
import codecs

args = getScriptArgs()
out = args[0]
space = currentProgram.getAddressFactory().getDefaultAddressSpace()
ifc = DecompInterface()
ifc.openProgram(currentProgram)
with codecs.open(out, "w", "utf-8") as f:
    for a in args[1:]:
        addr = space.getAddress(int(a, 16))
        DisassembleCommand(addr, None, True).applyTo(currentProgram, monitor)
        fn = getFunctionAt(addr) or createFunction(addr, "fn_" + a)
        analyzeChanges(currentProgram)
        fn = getFunctionAt(addr)
        f.write("\n//==== %s\n" % a)
        if fn is None:
            f.write("/* could not create function */\n")
            continue
        res = ifc.decompileFunction(fn, 60, monitor)
        f.write(res.getDecompiledFunction().getC() if res.decompileCompleted() else "/* decompile failed */")
        # raw listing too
        listing = currentProgram.getListing()
        for cu in listing.getCodeUnits(fn.getBody(), True):
            f.write("// %s  %s\n" % (cu.getAddress(), cu.toString()))
print("done")

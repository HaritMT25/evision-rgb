# Ghidra headless postScript (Jython): decompile every function to one text file.
# args: <output_path>
from ghidra.app.decompiler import DecompInterface, DecompileOptions
from ghidra.util.task import ConsoleTaskMonitor
import codecs

prog = currentProgram
out_path = getScriptArgs()[0].replace("PROGNAME", prog.getName().split(".")[0])
ifc = DecompInterface()
opts = DecompileOptions()
ifc.setOptions(opts)
ifc.openProgram(prog)
mon = ConsoleTaskMonitor()
fm = prog.getFunctionManager()
n = 0
with codecs.open(out_path, "w", "utf-8") as f:
    for func in fm.getFunctions(True):
        if func.isThunk() or func.isExternal():
            continue
        res = ifc.decompileFunction(func, 60, mon)
        code = res.getDecompiledFunction().getC() if res.decompileCompleted() else "/* decompile failed */"
        f.write("\n//==== FUNC %s @ %s size=%d\n" % (func.getName(), func.getEntryPoint(), func.getBody().getNumAddresses()))
        f.write(code)
        n += 1
print("decompiled %d functions" % n)

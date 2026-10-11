# Ghidra headless postScript: write the full disassembly listing (address, bytes, instruction, refs) to a file.
# args: <output_path>
import codecs

out = getScriptArgs()[0]
listing = currentProgram.getListing()
with codecs.open(out, "w", "utf-8") as f:
    for cu in listing.getCodeUnits(True):
        a = cu.getAddress()
        fn = getFunctionAt(a)
        if fn:
            f.write("\n;===== %s\n" % fn.getName())
        lbl = getSymbolAt(a)
        if lbl and not fn:
            f.write("%s:\n" % lbl.getName())
        try:
            bs = " ".join("%02x" % (x & 0xff) for x in cu.getBytes())
        except Exception:
            bs = ""
        refs = ",".join(str(r.getToAddress()) for r in cu.getReferencesFrom())
        f.write("%s  %-12s %s%s\n" % (a, bs, cu.toString(), ("  ; ->" + refs) if refs else ""))
print("listing written")

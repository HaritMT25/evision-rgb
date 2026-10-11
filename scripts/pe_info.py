import sys, pefile, datetime
sys.stdout.reconfigure(encoding="utf-8")

for path in sys.argv[1:]:
    pe = pefile.PE(path)
    print("=" * 80)
    print(path)
    print("machine", hex(pe.FILE_HEADER.Machine), "ts",
          datetime.datetime.utcfromtimestamp(pe.FILE_HEADER.TimeDateStamp),
          "imagebase", hex(pe.OPTIONAL_HEADER.ImageBase))
    for s in pe.sections:
        print(f"  sec {s.Name.rstrip(b'\\0').decode(errors='replace'):8} va={s.VirtualAddress:#x} vsz={s.Misc_VirtualSize:#x} raw={s.SizeOfRawData:#x} ent={s.get_entropy():.2f}")
    if hasattr(pe, "DIRECTORY_ENTRY_IMPORT"):
        for e in pe.DIRECTORY_ENTRY_IMPORT:
            names = [(i.name.decode() if i.name else f"#{i.ordinal}") for i in e.imports]
            print(f"  [{e.dll.decode()}] ({len(names)})")
            print("     " + ", ".join(names))
    if hasattr(pe, "DIRECTORY_ENTRY_EXPORT"):
        print("  EXPORTS:", [ (x.name.decode() if x.name else x.ordinal) for x in pe.DIRECTORY_ENTRY_EXPORT.symbols][:80])
    if hasattr(pe, "DIRECTORY_ENTRY_DEBUG"):
        for d in pe.DIRECTORY_ENTRY_DEBUG:
            if d.entry and hasattr(d.entry, "PdbFileName"):
                print("  PDB:", d.entry.PdbFileName)

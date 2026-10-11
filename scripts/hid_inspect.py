"""Read-only HID inspector for 320F:505B. Opens each collection with zero access
rights (no read/write) and queries HidP_GetCaps. Sends nothing to the device."""
import ctypes, ctypes.wintypes as wt, sys
import hid

sys.stdout.reconfigure(encoding="utf-8")
VID, PID = 0x320F, 0x505B

hidlib = ctypes.WinDLL("hid.dll")
k32 = ctypes.WinDLL("kernel32", use_last_error=True)


class HIDP_CAPS(ctypes.Structure):
    _fields_ = [("Usage", wt.USHORT), ("UsagePage", wt.USHORT),
                ("InputReportByteLength", wt.USHORT), ("OutputReportByteLength", wt.USHORT),
                ("FeatureReportByteLength", wt.USHORT), ("Reserved", wt.USHORT * 17),
                ("NumberLinkCollectionNodes", wt.USHORT),
                ("NumberInputButtonCaps", wt.USHORT), ("NumberInputValueCaps", wt.USHORT),
                ("NumberInputDataIndices", wt.USHORT),
                ("NumberOutputButtonCaps", wt.USHORT), ("NumberOutputValueCaps", wt.USHORT),
                ("NumberOutputDataIndices", wt.USHORT),
                ("NumberFeatureButtonCaps", wt.USHORT), ("NumberFeatureValueCaps", wt.USHORT),
                ("NumberFeatureDataIndices", wt.USHORT)]


class HIDP_VALUE_CAPS(ctypes.Structure):
    _fields_ = [("UsagePage", wt.USHORT), ("ReportID", ctypes.c_ubyte), ("IsAlias", ctypes.c_ubyte),
                ("BitField", wt.USHORT), ("LinkCollection", wt.USHORT), ("LinkUsage", wt.USHORT),
                ("LinkUsagePage", wt.USHORT), ("IsRange", ctypes.c_ubyte), ("IsStringRange", ctypes.c_ubyte),
                ("IsDesignatorRange", ctypes.c_ubyte), ("IsAbsolute", ctypes.c_ubyte),
                ("HasNull", ctypes.c_ubyte), ("Reserved", ctypes.c_ubyte), ("BitSize", wt.USHORT),
                ("ReportCount", wt.USHORT), ("Reserved2", wt.USHORT * 5), ("UnitsExp", wt.ULONG),
                ("Units", wt.ULONG), ("LogicalMin", wt.LONG), ("LogicalMax", wt.LONG),
                ("PhysicalMin", wt.LONG), ("PhysicalMax", wt.LONG),
                ("UsageMin", wt.USHORT), ("UsageMax", wt.USHORT), ("x1", wt.USHORT * 6)]


k32.CreateFileW.restype = wt.HANDLE
INVALID = wt.HANDLE(-1).value


def caps(path):
    h = k32.CreateFileW(path, 0, 3, None, 3, 0, None)  # access=0: query only
    if h == INVALID:
        return f"open failed err={ctypes.get_last_error()}"
    try:
        pp = ctypes.c_void_p()
        if not hidlib.HidD_GetPreparsedData(wt.HANDLE(h), ctypes.byref(pp)):
            return "no preparsed"
        c = HIDP_CAPS()
        hidlib.HidP_GetCaps(pp, ctypes.byref(c))
        out = (f"UP={c.UsagePage:#06x} U={c.Usage:#04x} in={c.InputReportByteLength} "
               f"out={c.OutputReportByteLength} feat={c.FeatureReportByteLength}")
        for kind, n, name in ((0, c.NumberInputValueCaps, "inV"), (1, c.NumberOutputValueCaps, "outV"),
                              (2, c.NumberFeatureValueCaps, "featV")):
            if n:
                arr = (HIDP_VALUE_CAPS * n)()
                ln = wt.USHORT(n)
                hidlib.HidP_GetValueCaps(kind, arr, ctypes.byref(ln), pp)
                for v in arr[:ln.value]:
                    out += (f"\n      {name}: RID={v.ReportID} UP={v.UsagePage:#06x} bits={v.BitSize}"
                            f" count={v.ReportCount} usage={v.UsageMin:#x}")
        hidlib.HidD_FreePreparsedData(pp)
        return out
    finally:
        k32.CloseHandle(wt.HANDLE(h))


for d in hid.enumerate(VID, PID):
    p = d["path"].decode()
    print(f"if={d['interface_number']} up={d['usage_page']:#06x} u={d['usage']:#04x} "
          f"rel={d['release_number']:#06x} prod={d['product_string']!r} mfr={d['manufacturer_string']!r}")
    print("   ", p)
    print("   ", caps(p))

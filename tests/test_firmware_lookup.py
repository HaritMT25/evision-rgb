"""Behaviour spec — finding the keyboard's firmware in the AJAZZ updater is quick and light. Written BEFORE the fix.

The exact effect preview runs the keyboard's own firmware, which the app takes from the official updater installed on
the PC (an .exe that carries the image as a resource). Reading it parsed the WHOLE updater, with its ~90 000
relocation entries: about 2 s and a ~95 MB memory peak at every start (memory Windows seldom got back from the app),
for one resource. Now only the resources are read.

The real updater is not part of this repo, so a synthetic one, with as many relocations, stands in for it here and on
CI; where the real one is installed it is checked too.
"""
import glob
import importlib.util
import os
import struct
import tempfile
import tracemalloc
import unittest

PEFILE = importlib.util.find_spec("pefile") is not None  # the "emulation" extra (the desktop app always bundles it)


def _align(n, a):
    return (n + a - 1) // a * a


def updater_exe(image: bytes, relocations: int = 90_000) -> bytes:
    """A minimal 32-bit Windows exe like the AJAZZ updater: `image` as resource DOWNLOAD / 129 / language 0, and a
    .reloc section with that many base relocations."""
    fa, sa = 0x200, 0x1000
    name = "DOWNLOAD".encode("utf-16-le")
    type_dir, name_dir, data_entry, name_at = 24, 48, 72, 88  # (the root directory is at 0)
    blob_at = _align(name_at + 2 + len(name), 16)
    rsrc_rva = sa
    rsrc = bytearray(blob_at + len(image))
    directory = lambda named, ids: struct.pack("<IIHHHH", 0, 0, 0, 0, named, ids)
    rsrc[0:24] = directory(1, 0) + struct.pack("<II", 0x80000000 | name_at, 0x80000000 | type_dir)
    rsrc[type_dir:type_dir + 24] = directory(0, 1) + struct.pack("<II", 129, 0x80000000 | name_dir)
    rsrc[name_dir:name_dir + 24] = directory(0, 1) + struct.pack("<II", 0, data_entry)
    rsrc[data_entry:data_entry + 16] = struct.pack("<IIII", rsrc_rva + blob_at, len(image), 0, 0)
    rsrc[name_at:name_at + 2 + len(name)] = struct.pack("<H", len(name) // 2) + name
    rsrc[blob_at:] = image
    reloc = bytearray()
    for b in range(0, relocations, 128):  # blocks of 128 HIGHLOW entries
        n = min(128, relocations - b)
        reloc += struct.pack("<II", rsrc_rva, 8 + 2 * n) + b"".join(struct.pack("<H", 0x3000 | (k * 4 % 0x1000))
                                                                     for k in range(n))
    reloc_rva = rsrc_rva + _align(len(rsrc), sa)
    dirs = [(0, 0)] * 16
    dirs[2], dirs[5] = (rsrc_rva, len(rsrc)), (reloc_rva, len(reloc))
    opt = struct.pack("<HBBIIIIIIIIIHHHHHHIIIIHHIIIIII", 0x10B, 14, 0, 0, len(rsrc) + len(reloc), 0, 0, rsrc_rva,
                      rsrc_rva, 0x400000, sa, fa, 6, 0, 0, 0, 6, 0, 0, reloc_rva + _align(len(reloc), sa), fa, 0, 2, 0,
                      0x100000, 0x1000, 0x100000, 0x1000, 0, 16) + b"".join(struct.pack("<II", *d) for d in dirs)
    coff = struct.pack("<HHIIIHH", 0x14C, 2, 0, 0, 0, len(opt), 0x0102)
    section = lambda nm, data, rva, raw, flags: struct.pack("<8sIIIIIIHHI", nm, len(data), rva, _align(len(data), fa),
                                                           raw, 0, 0, 0, 0, flags)
    head = bytearray(fa)
    head[:2], head[0x3C:0x40] = b"MZ", struct.pack("<I", 0x40)
    pe = b"PE\0\0" + coff + opt + section(b".rsrc", rsrc, rsrc_rva, fa, 0x40000040) + \
        section(b".reloc", reloc, reloc_rva, fa + _align(len(rsrc), fa), 0x42000040)
    head[0x40:0x40 + len(pe)] = pe
    return bytes(head) + bytes(rsrc).ljust(_align(len(rsrc), fa), b"\0") + bytes(reloc).ljust(_align(len(reloc), fa), b"\0")


def peak_mb(fn):
    tracemalloc.start()
    try:
        out = fn()
        return out, tracemalloc.get_traced_memory()[1] / 1e6
    finally:
        tracemalloc.stop()


@unittest.skipUnless(PEFILE, "pefile (the emulation extra) is not installed")
class SyntheticUpdater(unittest.TestCase):
    def setUp(self):
        from evision_rgb import firmware
        self.fw = firmware
        self.caps = firmware.CAPS_SIG + bytes(range(34 - len(firmware.CAPS_SIG)))
        self.image = bytes(firmware.APP_BASE) + b"\x02\xdf\xd0" + self.caps + bytes(200) + b"the rest of the image"
        self.root = tempfile.mkdtemp()
        folder = os.path.join(self.root, "AK820 Keyboard", "Updata")
        os.makedirs(folder)
        self.exe = os.path.join(folder, "updater-V0103.exe")
        with open(self.exe, "wb") as f:
            f.write(updater_exe(self.image))

    def test_the_image_is_found_in_the_updater(self):
        self.assertEqual(self.fw.extract_from_updater(self.exe), [self.image])
        self.assertEqual(self.fw.find_firmware(search=[self.root]), self.image)
        self.assertEqual(self.fw.find_firmware(self.caps, search=[self.root]), self.image, "the keyboard's own")
        other = bytes([self.caps[0] ^ 1]) + self.caps[1:]
        self.assertIsNone(self.fw.find_firmware(other, search=[self.root]), "not another keyboard's")

    def test_only_the_resources_are_read(self):
        found, peak = peak_mb(lambda: self.fw.extract_from_updater(self.exe))
        self.assertEqual(found, [self.image])
        self.assertLess(peak, 10, f"{peak:.0f} MB: the whole updater was parsed (its relocations are ~95 MB)")

    def test_the_updater_is_not_kept_open(self):  # (it was, until a full garbage collection, locking the file)
        self.fw.extract_from_updater(self.exe)
        if os.path.exists("/proc/self/maps"):  # Linux: is it still mapped?
            with open("/proc/self/maps", encoding="utf-8", errors="replace") as f:
                self.assertNotIn(os.path.realpath(self.exe), f.read())
        else:  # Windows refuses to rename a file while a mapping of it is open
            os.replace(self.exe, self.exe + ".old")
            os.remove(self.exe + ".old")


REAL = sorted(glob.glob(os.path.join(os.environ.get("ProgramFiles(x86)", ""), "AJAZZ", "**", "Updata", "*.exe"),
                        recursive=True)) if os.name == "nt" else []


@unittest.skipUnless(PEFILE and REAL, "the official AJAZZ updater is not installed here")
class RealUpdater(unittest.TestCase):
    def test_the_real_updater_gives_the_same_image_quickly(self):
        import pefile
        from evision_rgb import firmware
        img, peak = peak_mb(firmware.find_firmware)
        self.assertIsNotNone(img)
        self.assertLess(peak, 10, f"{peak:.0f} MB")
        full = []
        for exe in REAL:
            pe = pefile.PE(exe)  # everything, as before
            for t in pe.DIRECTORY_ENTRY_RESOURCE.entries:
                if str(t.name) == "DOWNLOAD":
                    full += [pe.get_data(lang.data.struct.OffsetToData, lang.data.struct.Size)
                             for e in t.directory.entries for lang in e.directory.entries]
            pe.close()
        self.assertIn(img, full)


if __name__ == "__main__":
    unittest.main()

"""Behaviour spec — the packaged app keeps the compiled firmware engine across versions. Written BEFORE the fix.

In a frozen (PyInstaller) app numba stamps a function's cache with a hash of the whole .exe, so every new version
of the app compiled the exact firmware engine again (up to a minute of a CPU core after each update). Where it cached
also depended on the machine the app was BUILT on: its source path is the build machine's, so on other PCs numba fell
back to its own user-wide folder instead of the app's.

evision_rgb.jit_cache.BundledSourceLocator (switched on by the desktop app through numba's
NUMBA_CACHE_LOCATOR_CLASSES) uses the copy of the engine's source that ships in the bundle, stamped by its contents,
in the app's numba-cache folder: a new version reuses the compiled engine unless the engine itself changed.
Outside the packaged app, and for other packages' functions, numba is left alone.
"""
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
MISSING_BUILD = r"Z:\no-such-build-machine\evision_rgb\fakeeng.py"  # built on GitHub: not on this PC
ENGINE = "from numba import njit\n\n\n@njit(cache=True)\ndef add1(x):\n    return x + 1\n"

RUNNER = r'''
import os, sys, types
root, meipass, cache_dir, exe, ours, built_at = sys.argv[1:7]
os.environ["NUMBA_CACHE_DIR"] = cache_dir
if ours == "1":
    os.environ["NUMBA_CACHE_LOCATOR_CLASSES"] = ("evision_rgb.jit_cache.BundledSourceLocator,UserProvidedCacheLocator,"
                                                 "InTreeCacheLocator,UserWideCacheLocator,IPythonCacheLocator,"
                                                 "ZipCacheLocator")
sys.path.insert(0, root)
sys.frozen, sys._MEIPASS, sys.executable = True, meipass, exe
src = open(os.path.join(meipass, "evision_rgb", "fakeeng.py"), encoding="utf-8").read()
mod = types.ModuleType("evision_rgb.fakeeng")
mod.__file__ = built_at
sys.modules["evision_rgb.fakeeng"] = mod
exec(compile(src, built_at, "exec"), mod.__dict__)
r = mod.add1(1)
loc = mod.add1._cache._impl._locator
print("result", r, "hits", sum(mod.add1.stats.cache_hits.values()), "misses", sum(mod.add1.stats.cache_misses.values()),
      "locator", type(loc).__name__, "in", loc.get_cache_path())
'''


class PackagedApp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.meipass = os.path.join(self.tmp, "_internal")
        os.makedirs(os.path.join(self.meipass, "evision_rgb"))
        self.write_engine(ENGINE)
        self.cache = os.path.join(self.tmp, "numba-cache")
        self.runner = os.path.join(self.tmp, "runner.py")
        with open(self.runner, "w", encoding="utf-8") as f:
            f.write(RUNNER)

    def write_engine(self, text):
        with open(os.path.join(self.meipass, "evision_rgb", "fakeeng.py"), "w", encoding="utf-8") as f:
            f.write(text)

    def build_tree(self):
        """The app built on THIS PC: its source path exists (the developer's checkout)."""
        path = os.path.join(self.tmp, "checkout", "evision_rgb", "fakeeng.py")
        os.makedirs(os.path.dirname(path))
        with open(path, "w", encoding="utf-8") as f:
            f.write(ENGINE)
        return path

    def run_app(self, exe="EVision RGB 0.7.0.exe", ours=True, built_at=MISSING_BUILD):
        exe_path = os.path.join(self.tmp, exe)
        with open(exe_path, "wb") as f:
            f.write(exe.encode() * 100)  # every version's .exe has different bytes
        env = {k: v for k, v in os.environ.items() if not k.startswith("NUMBA_")}  # (gui/app.py may have set them)
        p = subprocess.run([sys.executable, self.runner, ROOT, self.meipass, self.cache, exe_path,
                            "1" if ours else "0", built_at], capture_output=True, text=True, timeout=300, cwd=self.tmp,
                           env=env)
        return p.returncode, (p.stdout + p.stderr)

    def test_stock_numba_compiles_the_engine_again_for_every_new_exe(self):  # the problem, on a checkout
        built_at = self.build_tree()
        for exe in ("EVision RGB 0.7.0.exe", "EVision RGB 0.7.1.exe"):
            code, out = self.run_app(exe, ours=False, built_at=built_at)
            self.assertEqual(code, 0, out)
            self.assertIn("hits 0 misses 1", out, exe)

    def test_a_new_version_reuses_the_compiled_engine_on_any_pc(self):
        code, out = self.run_app("EVision RGB 0.7.0.exe")
        self.assertEqual(code, 0, out)
        self.assertIn("result 2 hits 0 misses 1 locator BundledSourceLocator in " + self.cache, out)
        code, out = self.run_app("EVision RGB 0.7.1.exe")
        self.assertEqual(code, 0, out)
        self.assertIn("result 2 hits 1 misses 0", out, "loaded from the cache, not compiled again")

    def test_it_uses_the_bundled_copy_even_where_the_build_tree_exists(self):
        built_at = self.build_tree()
        self.assertEqual(self.run_app("EVision RGB 0.7.0.exe", built_at=built_at)[0], 0)
        code, out = self.run_app("EVision RGB 0.7.1.exe", built_at=built_at)
        self.assertEqual(code, 0, out)
        self.assertIn("hits 1 misses 0 locator BundledSourceLocator", out)

    def test_a_changed_engine_is_compiled_again(self):
        self.assertEqual(self.run_app()[0], 0)
        self.write_engine(ENGINE.replace("x + 1", "(x + 2) - 1"))
        code, out = self.run_app("EVision RGB 0.8.0.exe")
        self.assertEqual(code, 0, out)
        self.assertIn("result 2 hits 0 misses 1", out)


class LeavesNumbaAlone(unittest.TestCase):
    def test_outside_the_packaged_app_and_for_other_packages_it_steps_aside(self):
        from evision_rgb.jit_cache import BundledSourceLocator

        def f():
            pass
        self.assertIsNone(BundledSourceLocator.from_function(f, __file__), "not frozen: numba's own locators")
        f.__module__ = "numpy.something"
        saved = {n: getattr(sys, n) for n in ("frozen", "_MEIPASS") if hasattr(sys, n)}
        try:
            sys.frozen, sys._MEIPASS = True, tempfile.mkdtemp()
            self.assertIsNone(BundledSourceLocator.from_function(f, __file__), "another package's function")
        finally:
            for n in ("frozen", "_MEIPASS"):
                if n in saved:
                    setattr(sys, n, saved[n])
                elif hasattr(sys, n):
                    delattr(sys, n)


class TheAppUsesIt(unittest.TestCase):
    def test_the_desktop_app_switches_it_on_and_its_selftest_checks_the_engine(self):
        with open(os.path.join(ROOT, "evision_rgb", "gui", "app.py"), encoding="utf-8") as f:
            src = f.read()
        at = src.index("NUMBA_CACHE_LOCATOR_CLASSES")
        self.assertIn("evision_rgb.jit_cache.BundledSourceLocator", src[at:at + 300])
        self.assertLess(at, src.index("from ..server import"), "set before numba is imported")
        self.assertIn('"firmware engine"', src, "the frozen selftest loads the real engine")


if __name__ == "__main__":
    unittest.main()

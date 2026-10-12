"""numba cache locator for the packaged desktop app (switched on in gui/app.py with NUMBA_CACHE_LOCATOR_CLASSES).

In a frozen app numba stamps a function's cache with a hash of the whole .exe, so every new version compiled the
exact firmware engine again (up to a minute of a CPU core), and where it cached depended on the build machine (the
function's source path is the one the app was built from). The engine's source ships in the bundle
(_internal/evision_rgb/fw8051_fast.py, see scripts/build_exe.sh) and imports only numpy and numba, so it is stamped
by the contents of that copy instead, in the app's NUMBA_CACHE_DIR: a new version reuses the compiled engine unless
the engine itself changed. numba's own checks (its version, the CPU, the argument types) still apply.
Verified by tests/test_jit_cache.py (a simulated frozen app) and the frozen --selftest ("firmware engine").
"""
from __future__ import annotations

import functools
import hashlib
import os
import sys

from numba.core import config
from numba.core.caching import _CacheLocator


@functools.lru_cache(maxsize=None)
def _content_hash(path: str, mtime: float, size: int) -> bytes:  # (mtime, size): re-hash if the file changes
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).digest()


class BundledSourceLocator(_CacheLocator):
    """This package's jitted functions in the packaged app: the bundled source, cached in NUMBA_CACHE_DIR."""

    def __init__(self, py_func, py_file: str):
        self._py_file = py_file
        self._lineno = py_func.__code__.co_firstlineno
        self._cache_path = os.path.join(config.CACHE_DIR, "evision_rgb")

    def get_cache_path(self) -> str:
        return self._cache_path

    def get_source_stamp(self):
        st = os.stat(self._py_file)
        return _content_hash(self._py_file, st.st_mtime, st.st_size)

    def get_disambiguator(self) -> str:
        return str(self._lineno)

    @classmethod
    def from_function(cls, py_func, py_file):
        """None (numba's own locators take over) outside the packaged app or for other packages' functions."""
        if not getattr(sys, "frozen", False) or not config.CACHE_DIR:
            return None
        if not str(getattr(py_func, "__module__", "")).startswith("evision_rgb."):
            return None
        bundled = os.path.join(getattr(sys, "_MEIPASS", ""), "evision_rgb", os.path.basename(py_file))
        if not os.path.isfile(bundled):
            return None
        self = cls(py_func, bundled)
        try:
            self.ensure_cache_path()
        except OSError:  # the cache folder can't be written: numba's other locators decide
            return None
        return self

"""Licence notices for everything bundled into the desktop app (written next to the exe in a release).

    python scripts/third_party_notices.py [out-file] [--build "build/pyi/EVision RGB" --dist "dist/EVision RGB"]

Lists the app's components (and everything they depend on) with version and licence, then the licence texts the
packages ship. With --build/--dist it also lists whatever PyInstaller actually put into that build, so the
notices cover exactly what is distributed."""
import argparse
import os
import re
import sys
from importlib import metadata

ROOTS = ["hidapi", "numpy", "psutil", "pynput", "soundcard", "pycaw", "pywebview", "pystray", "pillow", "numba",
         "pefile", "winrt-runtime", "winrt-Windows.Foundation", "winrt-Windows.Foundation.Collections",
         "winrt-Windows.Media.Control", "winrt-Windows.Storage.Streams"]
LICENSE_FILE = re.compile(r"(^|/)(LICEN[CS]E|COPYING|NOTICE|AUTHORS)[^/]*$", re.I)


def _name(req: str) -> str | None:
    if ";" in req and "extra ==" in req:  # optional extras of a dependency are not installed by us
        return None
    if ";" in req and "sys_platform" in req and "win32" not in req and sys.platform == "win32":
        return None
    return re.split(r"[\s<>=!~;\[(]", req.strip(), maxsplit=1)[0]


def closure(roots):
    seen, todo = {}, list(roots)
    while todo:
        n = todo.pop()
        key = n.lower().replace("_", "-")
        if key in seen:
            continue
        try:
            d = metadata.distribution(n)
        except metadata.PackageNotFoundError:
            continue
        seen[key] = d
        todo += [m for m in (_name(r) for r in (d.requires or [])) if m]
    return seen


def licence(d) -> str:
    m = d.metadata
    lic = m.get("License-Expression") or ""
    if not lic:
        lic = (m.get("License") or "").strip().splitlines()[0] if (m.get("License") or "").strip() else ""
    if not lic or len(lic) > 80:
        cls = [c.split("::")[-1].strip() for c in m.get_all("Classifier") or [] if c.startswith("License ::")]
        lic = ", ".join(cls) or lic[:80] or "see licence text"
    return lic


def bundled(build_dir: str | None, dist_dir: str | None) -> set[str]:
    """Distribution names of the third-party modules inside a PyInstaller build (pure modules from the PYZ table
    of contents, compiled ones from the files next to the exe)."""
    names = set()
    if build_dir:
        with open(os.path.join(build_dir, "PYZ-00.toc"), encoding="utf-8", errors="replace") as f:
            names |= {m.split(".")[0] for m in re.findall(r"\('([A-Za-z0-9_.]+)',", f.read())}
    internal = os.path.join(dist_dir, "_internal") if dist_dir else None
    if internal and os.path.isdir(internal):
        names |= {re.split(r"[.-]", e, maxsplit=1)[0] for e in os.listdir(internal)}
    owners = metadata.packages_distributions()
    return {d for n in names if n not in sys.stdlib_module_names for d in owners.get(n, [])}


def main(out=None, build_dir=None, dist_dir=None):
    dists = closure(ROOTS + sorted(bundled(build_dir, dist_dir)))
    dists.pop("evision-rgb", None)  # the app itself (MIT, LICENSE next to this file)
    lines = ["EVision RGB bundles the following open-source components. Their licence texts follow the list.", "",
             "pynput and pystray are under the GNU LGPL v3. They are bundled unmodified; their source is at",
             "https://github.com/moses-palmer/pynput and https://github.com/moses-palmer/pystray. To use a modified",
             "version, rebuild the app from its source (https://github.com/HaritMT25/evision-rgb) with",
             "scripts/build_exe.sh after installing yours.", "",
             "The Python runtime is included under the PSF License, with the libraries it ships (OpenSSL, libffi and",
             "others): https://docs.python.org/3/license.html", "",
             f"{'Package':38s} {'Version':14s} Licence"]
    for key in sorted(dists):
        d = dists[key]
        lines.append(f"{d.metadata['Name']:38s} {d.version:14s} {licence(d)}")
    lines.append("")
    for key in sorted(dists):
        d = dists[key]
        texts = [f for f in (d.files or []) if LICENSE_FILE.search(str(f).replace("\\", "/"))]
        for f in texts:
            try:
                body = f.locate().read_text(encoding="utf-8", errors="replace").strip()
            except (OSError, ValueError):
                continue
            if body:
                lines += ["=" * 100, f"{d.metadata['Name']} {d.version}: {f.name}", "=" * 100, body, ""]
    text = "\n".join(lines) + "\n"
    if out:
        with open(out, "w", encoding="utf-8") as fh:
            fh.write(text)
    else:
        sys.stdout.write(text)
    return len(dists)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("out", nargs="?")
    ap.add_argument("--build", help="PyInstaller work folder of the build (has PYZ-00.toc)")
    ap.add_argument("--dist", help="the built app's folder")
    a = ap.parse_args()
    n = main(a.out, a.build, a.dist)
    print(f"{n} packages", file=sys.stderr)

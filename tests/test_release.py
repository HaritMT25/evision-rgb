"""Behaviour spec — releases. Written BEFORE the implementation.

Every version is built on GitHub and published as a Windows zip that needs no Python: the built app checks itself
before it is published (`--selftest`), the zip carries a checksum and the licences of everything bundled, and the
release notes come from CHANGELOG.md. Versions agree everywhere."""
import json
import os
import re
import sys
import tempfile
import unittest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "scripts"))


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


class SelfTest(unittest.TestCase):
    def run_selftest(self):
        from evision_rgb.gui import app
        out = os.path.join(tempfile.mkdtemp(), "report.json")
        code = app.selftest(out)
        with open(out, encoding="utf-8") as f:
            return code, json.load(f)

    def test_the_report_names_every_check(self):
        code, r = self.run_selftest()
        self.assertEqual(set(r["checks"]), {"modules", "live effects", "app window files", "numba JIT",
                                            "media controls", "pad key images"})
        from evision_rgb import __version__
        self.assertEqual(r["version"], __version__)
        self.assertEqual(code, 0 if r["ok"] else 1)

    def test_the_parts_every_build_has_work(self):
        _, r = self.run_selftest()
        for name in ("live effects", "app window files", "pad key images"):
            self.assertTrue(r["checks"][name]["ok"], (name, r["checks"][name]))
        self.assertIn("nowplaying", r["checks"]["live effects"]["info"])

    def test_a_missing_part_fails_the_build(self):
        from evision_rgb.gui import app
        orig = app.SELFTEST_MODULES
        app.SELFTEST_MODULES = orig + ("a_module_that_was_not_bundled",)
        self.addCleanup(setattr, app, "SELFTEST_MODULES", orig)
        code, r = self.run_selftest()
        self.assertEqual(code, 1)
        self.assertFalse(r["ok"])
        self.assertIn("a_module_that_was_not_bundled", r["checks"]["modules"]["error"])

    def test_the_app_takes_the_selftest_flag_without_touching_any_device(self):
        from evision_rgb.gui import app
        out = os.path.join(tempfile.mkdtemp(), "r.json")
        import evision_rgb.protocol as proto
        import evision_rgb.streamdock as sd
        boom = type("NoDevices", (), {"__getattr__": lambda self, k: (_ for _ in ()).throw(AssertionError("device touched"))})()
        for mod in (proto, sd):
            self.addCleanup(setattr, mod, "hid", mod.hid)
            mod.hid = boom
        app.main(["--selftest", out])
        with open(out, encoding="utf-8") as f:
            self.assertNotIn("device touched", f.read())


class Notices(unittest.TestCase):
    def test_every_bundled_package_is_listed_with_its_licence(self):
        import third_party_notices as tpn
        from importlib import metadata
        out = os.path.join(tempfile.mkdtemp(), "n.txt")
        n = tpn.main(out)
        with open(out, encoding="utf-8") as f:
            text = f.read()
        installed = []
        for root in tpn.ROOTS:  # what this environment has of the app's components (a release build has all)
            try:
                installed.append(metadata.distribution(root).metadata["Name"])
            except metadata.PackageNotFoundError:
                pass
        self.assertIn("numpy", installed)
        self.assertGreaterEqual(n, len(installed))
        for name in installed:
            self.assertRegex(text, rf"(?im)^{re.escape(name)}\s+\S+\s+\S", name)
        self.assertIn("=" * 100, text, "licence texts follow the list")

    def test_lgpl_parts_say_how_to_replace_them(self):
        import third_party_notices as tpn
        out = os.path.join(tempfile.mkdtemp(), "n.txt")
        tpn.main(out)
        with open(out, encoding="utf-8") as f:
            head = f.read().split("=" * 100)[0]
        self.assertIn("pynput and pystray are under the GNU LGPL", head)
        self.assertIn("scripts/build_exe.sh", head)


class ReleaseNotes(unittest.TestCase):
    def test_notes_are_the_changelog_section_plus_install_steps(self):
        import release_notes
        from evision_rgb import __version__
        notes = release_notes.section(__version__)
        top = read("CHANGELOG.md").split("\n## ", 2)[1]  # this version's section: its first entry is in the notes
        self.assertIn(top.splitlines()[1].strip()[:60], notes)
        full = release_notes.notes(__version__)
        self.assertIn("More info", full, "how to get past the SmartScreen warning")
        self.assertIn(".sha256", full)
        self.assertIn(f"EVision-RGB-{__version__}-windows-x64.zip", full)

    def test_an_unknown_version_has_no_notes(self):
        import release_notes
        with self.assertRaises(SystemExit):
            release_notes.section("9.9.9")


class Versions(unittest.TestCase):
    def test_the_version_agrees_everywhere(self):
        from evision_rgb import __version__
        self.assertEqual(re.search(r'^version = "([^"]+)"', read("pyproject.toml"), re.M).group(1), __version__)
        self.assertEqual(re.search(r"^## (\S+)", read("CHANGELOG.md"), re.M).group(1), __version__,
                         "the newest CHANGELOG entry is this version")


class Workflow(unittest.TestCase):
    def test_a_version_tag_builds_checks_and_publishes(self):
        wf = read(".github", "workflows", "release.yml")
        for needle in ('tags: ["v*"]', "--selftest", "third_party_notices.py", "sha256", "gh release create",
                       "release_notes.py", "app-constraints.txt", "Version matches the tag"):
            self.assertIn(needle, wf)

    def test_the_app_extra_bundles_everything(self):
        py = read("pyproject.toml")
        self.assertRegex(py, r'app = \["evision-rgb\[gui,effects,pads,media,emulation\]"\]')
        self.assertIn("emulation = [", py)


class Docs(unittest.TestCase):
    def test_readme_explains_the_download(self):
        r = read("README.md")
        self.assertIn("/releases", r)
        self.assertIn("More info", r)

    def test_screenshots_in_the_readme_exist(self):
        shots = re.findall(r"\]\((docs/screenshots/[^)]+)\)", read("README.md"))
        self.assertGreaterEqual(len(shots), 4)
        for s in shots:
            self.assertTrue(os.path.isfile(os.path.join(ROOT, s)), s)

    def test_contributors_find_the_rules_and_the_keyboard_guide(self):
        self.assertIn("mutation_check.py", read("CONTRIBUTING.md"))
        self.assertIn("allow-list", read("docs", "ADDING_A_KEYBOARD.md"))
        self.assertIn("CONTRIBUTING.md", read("README.md"))

    def test_public_files_do_not_point_at_the_private_log(self):
        for rel in ("README.md", "CONTRIBUTING.md", os.path.join("docs", "ADDING_A_KEYBOARD.md"),
                    os.path.join("tests", "test_hardware.py")):
            self.assertNotIn("PROGRESS_LOG", read(rel), rel)

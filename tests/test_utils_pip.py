# tests/test_utils_pip.py
import sys
import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock

# --- path bootstrap
THIS_FILE = Path(__file__).resolve()
REPO_ROOT = THIS_FILE.parent.parent
SRC_DIR = REPO_ROOT / "src"
TESTS_DIR = REPO_ROOT / "tests"

for p in (SRC_DIR, TESTS_DIR):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from deph.utils.pip import Pip, version_lt, RunResult


class TestUtilsPip(unittest.TestCase):
    def test_version_lt(self):
        self.assertTrue(version_lt("1.0.0", "2.0.0"))
        self.assertFalse(version_lt("2.0.0", "1.0.0"))
        # invalid versions treated as not comparable/older -> False per implementation
        self.assertFalse(version_lt("not-a-version", "1.0.0"))

    def test_is_installed_uses_cached_version(self):
        p = Pip()
        p._get_installed_version = MagicMock(return_value="1.2.3")
        self.assertEqual(p.is_installed("somepkg"), "1.2.3")
        p._get_installed_version = MagicMock(return_value=None)
        self.assertFalse(p.is_installed("somepkg"))

    def test_ensure_installs_when_missing_and_upgrades_when_older(self):
        p = Pip()

        # Not installed -> install called (no upgrade)
        p._get_installed_version = MagicMock(return_value=None)
        p.install = MagicMock(return_value={"status": "installed"})
        r = p.ensure("aa", min_version="1.2.0")
        self.assertEqual(r.get("status"), "installed")
        p.install.assert_called_with("aa", version="1.2.0", version_constraint=">=", upgrade=False, env=None, quiet=False)

        # Installed but too old -> upgrade
        p._get_installed_version = MagicMock(return_value="1.0.0")
        p.install = MagicMock(return_value={"status": "upgraded"})
        r = p.ensure("bb", min_version="2.0.0")
        self.assertEqual(r.get("status"), "upgraded")
        p.install.assert_called_with("bb", version="2.0.0", version_constraint=">=", upgrade=True, env=None, quiet=False)

        # Already meets requirement -> ok message
        p._get_installed_version = MagicMock(return_value="3.0.0")
        r = p.ensure("cc", min_version="2.0.0")
        self.assertEqual(r.get("status"), "ok")
        self.assertIn("already", r.get("message", ""))
        self.assertEqual(r.get("installed_version"), "3.0.0")

    def test_install_parses_json_report_when_supported(self):
        p = Pip()
        p._pip_supports_report = MagicMock(return_value=True)
        fake_report = {"install": [{"metadata": {"name": "requests"}}]}
        p._run = MagicMock(return_value=RunResult(0, json.dumps(fake_report), ""))
        res = p.install("requests", version="2.32.3", report_on_stdout=True)
        self.assertIsInstance(res, dict)
        self.assertIn("install", res)

    def test_available_versions_parsing(self):
        p = Pip()
        # Simulate pip index search output line
        stdout = "Available versions: 1.0.0, 1.2.0, 2.0.0\n"
        p._run = MagicMock(return_value=RunResult(0, stdout, ""))
        vers = p.available_versions("dummy")
        self.assertEqual(vers[0], "2.0.0")


if __name__ == "__main__":
    unittest.main()

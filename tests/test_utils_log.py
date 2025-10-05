# tests/test_utils_log.py
import io
import sys
import unittest
import logging
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path

# --- path bootstrap so `python -m unittest discover -s tests -p "test_*.py"` works
THIS_FILE = Path(__file__).resolve()
REPO_ROOT = THIS_FILE.parent.parent          # repo root
SRC_DIR = REPO_ROOT / "src"                  # contains package: src/deph/...
TESTS_DIR = REPO_ROOT / "tests"              # this folder

for p in (SRC_DIR, TESTS_DIR):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from deph.utils import log


class TestUtilsLog(unittest.TestCase):
    def test_info_goes_to_stdout_and_warning_to_stderr(self):
        log.init(level=logging.INFO)
        out = io.StringIO()
        err = io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            log.emit("hello-info", level="info")
            log.emit("hello-warn", level="warning")
        so = out.getvalue()
        se = err.getvalue()
        self.assertIn("hello-info", so)
        self.assertIn("hello-warn", se)
        self.assertNotIn("hello-info", se)

    def test_debug_suppressed_at_info_level(self):
        log.init(level=logging.INFO)
        out = io.StringIO()
        err = io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            log.emit("dbg", level="debug")
        # No debug output expected at INFO level
        self.assertNotIn("dbg", out.getvalue())
        self.assertNotIn("dbg", err.getvalue())


if __name__ == "__main__":
    unittest.main()


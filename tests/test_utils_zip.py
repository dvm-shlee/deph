# tests/test_utils_zip.py
import io
import os
import sys
import zipfile
import unittest
from pathlib import Path

# --- path bootstrap
THIS_FILE = Path(__file__).resolve()
REPO_ROOT = THIS_FILE.parent.parent
SRC_DIR = REPO_ROOT / "src"
TESTS_DIR = REPO_ROOT / "tests"

for p in (SRC_DIR, TESTS_DIR):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from deph.utils import zip as zutil


def _build_inmemory_zip() -> zipfile.ZipFile:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        # Explicit dir entries
        zf.writestr("dir1/", b"")
        zf.writestr("dir1/sub/", b"")
        # Files
        zf.writestr("a.txt", b"A")
        zf.writestr("dir1/b.txt", b"B")
        zf.writestr("dir1/sub/c.txt", b"C")
    buf.seek(0)
    return zipfile.ZipFile(buf, "r")


class TestUtilsZip(unittest.TestCase):
    def test_walk_and_fetch_files_dirs(self):
        zf = _build_inmemory_zip()
        # fetch files by exact, wildcard, regex
        exact = zutil.fetch_files_in_zip(zf, "a.txt", wildcard=False)
        self.assertEqual(len(exact), 1)
        self.assertEqual(exact[0].name, "a.txt")

        wild = zutil.fetch_files_in_zip(zf, "*.txt", wildcard=True)
        self.assertGreaterEqual(len(wild), 3)

        regex = zutil.fetch_files_in_zip(zf, "ignored", regex=r"b\.txt")
        self.assertEqual(len(regex), 1)
        self.assertEqual(regex[0].name, "b.txt")

        # fetch dirs
        dirs = zutil.fetch_dirs_in_zip(zf, "dir1", match_scope="basename")
        self.assertEqual(len(dirs), 1)
        root = dirs[0]
        # dir1 contains sub and b.txt
        self.assertTrue(any(d.name == "sub" for d in root.dirs))
        self.assertTrue(any(f.name == "b.txt" for f in root.files))

    def test_zippedfile_and_zippeddir_isolate_and_to_filename(self):
        zf = _build_inmemory_zip()
        file_entry = zutil.fetch_files_in_zip(zf, "a.txt", wildcard=False)[0]
        # FileBuffer isolate
        buf = file_entry.isolate()
        self.assertEqual(buf.bytes(), b"A")

        # ZippedDir isolate (add_root)
        dir_entry = zutil.fetch_dirs_in_zip(zf, "dir1", match_scope="basename")[0]
        new_zip = dir_entry.isolate(add_root=True, root_name="root")
        names = sorted(new_zip.namelist())
        self.assertIn("root/b.txt", names)
        self.assertIn("root/sub/c.txt", names)

        # Persist to disk within repo tests directory
        out_dir = TESTS_DIR / "_tmp_utilzip"
        out_dir.mkdir(exist_ok=True)
        out_zip_path = out_dir / "out.zip"
        import warnings
        with warnings.catch_warnings(record=True) as wrec:
            warnings.simplefilter("always")
            zutil.to_filename(new_zip, str(out_zip_path))
        # No duplicate directory warnings expected
        msgs = [str(w.message) for w in wrec]
        self.assertFalse(any("Duplicate name" in m for m in msgs))
        self.assertTrue(out_zip_path.is_file())
        with zipfile.ZipFile(str(out_zip_path), "r") as zchk:
            n = sorted(zchk.namelist())
            self.assertIn("root/b.txt", n)
            self.assertIn("root/sub/c.txt", n)


if __name__ == "__main__":
    unittest.main()

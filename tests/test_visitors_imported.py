# tests/test_visitors_imported.py
import ast
import sys
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

from deph.visitors.imported import ImportCollector


class TestVisitorsImported(unittest.TestCase):
    def test_static_and_dynamic_imports(self):
        src = """
import math as m
from os.path import join as j
import importlib
dyn = importlib.import_module('json')
"""
        tree = ast.parse(src)
        coll = ImportCollector(tree)
        imp = coll.imported
        # static aliases
        self.assertIn('m', imp)
        self.assertIn('j', imp)
        # dynamic alias
        self.assertIn('dyn', imp)
        self.assertFalse(imp['dyn'].use_star)
        self.assertTrue(imp['dyn'].is_dynamic)
        # module/package resolution for stdlib
        self.assertEqual(imp['m'].module, 'math')
        self.assertEqual(imp['j'].module, 'os')

    def test_star_and_relative_and_kw_package(self):
        src = """
from math import *
from .submod import thing
import importlib
pkgmod = importlib.import_module('x', package='a.b')
"""
        # Parse in package context: even without real pkg, level is captured in AST
        tree = ast.parse(src)
        coll = ImportCollector(tree)
        imp = coll.imported
        # Star import recorded (key is synthesized to include module path)
        self.assertTrue(any(v.use_star for v in imp.values()))
        # Relative import level preserved
        rel_items = [v for v in imp.values() if v.level]
        self.assertTrue(any(getattr(v, 'level', 0) == 1 for v in rel_items))
        # import_module with package kw resolved to module/submodule
        self.assertIn('pkgmod', imp)
        self.assertEqual(imp['pkgmod'].module, 'a')
        self.assertEqual(imp['pkgmod'].submodule, 'b')


if __name__ == "__main__":
    unittest.main()

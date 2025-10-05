# tests/test_visitors_lowlevel_usage.py
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

from deph.visitors.lowlevel import LowLevelCollector
from deph.visitors.usage import NameUsageCollector, roots_in_expr


SAMPLE_SRC = """
import math

def outer(a):
    def inner(b):
        return b * 2
    return inner(a) + 1

class C:
    def m(self, x):
        y = math.sqrt(x)
        return y
"""


class TestVisitorsLowLevelAndUsage(unittest.TestCase):
    def test_lowlevel_pruning(self):
        tree = ast.parse(SAMPLE_SRC)
        coll = LowLevelCollector(tree)
        defs = coll.defs
        names = {d.name for d in defs}
        self.assertIn('outer', names)
        self.assertIn('C', names)
        # inner function should not appear as a top-level def
        self.assertNotIn('inner', names)
        # pruned code of outer should not contain def inner(
        outer = next(d for d in defs if d.name == 'outer')
        self.assertNotIn('def inner(', outer.code)

    def test_usage_collector(self):
        tree = ast.parse(SAMPLE_SRC)
        # examine class method body for math usage
        cdef = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'C')
        mdef = next(n for n in cdef.body if isinstance(n, ast.FunctionDef) and n.name == 'm')
        coll = NameUsageCollector()
        # Collect params (visit args via the FunctionDef) then visit statements for attribute roots
        coll.visit(mdef)  # will register params and name, but not visit body
        for stmt in mdef.body:
            coll.visit(stmt)
        # 'math' should be in attr_roots (used via attribute)
        self.assertIn('math', coll.attr_roots)
        # params recognized
        self.assertIn('self', coll.params)
        self.assertIn('x', coll.params)

        # roots_in_expr for an attribute expression
        expr = ast.parse('pkg.mod.attr').body[0].value  # type: ignore[attr-defined]
        r = roots_in_expr(expr)
        self.assertEqual(r, {'pkg'})


if __name__ == "__main__":
    unittest.main()

# tests/test_isolator.py
import io
import re
import sys
import unittest
from contextlib import redirect_stderr
from pathlib import Path

# --- path bootstrap so `python -m unittest discover -s tests -p "test_*.py"` works
THIS_FILE = Path(__file__).resolve()
REPO_ROOT = THIS_FILE.parent.parent          # repo root
SRC_DIR = REPO_ROOT / "src"                  # contains package: src/deph/...
TESTS_DIR = REPO_ROOT / "tests"              # this folder (so 'import test_samples' works)

for p in (SRC_DIR, TESTS_DIR):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

# package modules live under src/deph
from deph.isolator import Isolator

# IMPORTANT: import as local module name (no 'tests.' prefix) to keep module __name__ stable
import test_samples as S


def run_isolation(targets, *, isolator_kwargs=None):
    """
    Helper: run Isolator on a list of entry objects.

    Returns
    -------
    result : AttrDefaultDict
        Dict-like result from Isolator with keys: 'source', 'warnings',
        'reqs_pypi', 'reqs_unknown', 'unbound'.
    stderr : str
        Captured stderr (for warnings printed by Isolator).
    """
    isolator_kwargs = isolator_kwargs or {}
    iso = Isolator(**isolator_kwargs)
    with io.StringIO() as err, redirect_stderr(err):
        result = iso.isolate(list(targets))
        stderr = err.getvalue()
    return result, stderr


class TestIsolatorBasic(unittest.TestCase):
    def test_bare_name_var_pulls_function(self):
        """LOCAL_OBJ = f_no_import should cause f_no_import to appear in defs."""
        result, _ = run_isolation([S.uses_bare_name])
        # f_no_import should be part of the output because LOCAL_OBJ references it
        self.assertIn("def f_no_import(", result.source)

    def test_vars_dedup(self):
        """Same module var referenced from multiple entries should appear only once."""
        result, _ = run_isolation([S.uses_bare_name, S.also_uses_bare_name])
        # Within the emitted source, the assignment for LOCAL_OBJ should appear only once
        assign_lines = [ln for ln in result.source.splitlines() if ln.strip().startswith("LOCAL_OBJ = ")]
        self.assertLessEqual(len(assign_lines), 1, f"Duplicate LOCAL_OBJ assignments: {assign_lines}")

    def test_imports_sorted_and_deduped(self):
        """Imports should be deterministic and deduped."""
        result, _ = run_isolation([S.f_stdlib_inside, S.f_attr_uses_textwrap])
        # We expect the alias import line for textwrap and possibly others; ensure unique
        self.assertRegex(result.source, r"(?m)^import textwrap as _tw$")
        # No duplicated identical import lines
        lines = [l for l in result.source.splitlines() if l.startswith("import ") or l.startswith("from ")]
        self.assertEqual(len(lines), len(set(lines)))

    def test_dynamic_import_keep_or_drop(self):
        """Top-level dynamic imports should respect keep_dynamic_imports flag."""
        # keep_dynamic_imports=True (default) -> code should include the dynamic import line
        result_keep, _ = run_isolation([S.f_dynamic_json_loader])
        self.assertRegex(result_keep.source, r"(?m)^_json\s*=\s*importlib\.import_module\('json'\)$")

        # keep_dynamic_imports=False -> dynamic top-level import omitted
        result_drop, _ = run_isolation(
            [S.f_dynamic_json_loader],
            isolator_kwargs={"keep_dynamic_imports": False},
        )
        self.assertNotRegex(result_drop.source, r"(?m)^_json\s*=\s*importlib\.import_module\('json'\)$")
        # but the function body remains and uses JSON_OBJ
        self.assertIn("return JSON_OBJ.dumps", result_drop.source)

    def test_unbound_warns_and_stderr(self):
        """Unbound names should be returned and printed to stderr."""
        result, stderr = run_isolation([S.f_calls_unknown])
        # warnings contain our symbol
        self.assertTrue(any("not_defined_anywhere" in w for w in result.warnings))
        self.assertIn("not_defined_anywhere", stderr)

    def test_section_order(self):
        """Order = imports -> variables -> definitions."""
        result, _ = run_isolation([S.simple_add])
        # crude check: first non-comment lines should start with import or be empty before vars/defs
        clean = [ln for ln in result.source.splitlines() if not ln.startswith("#")]
        # find indices
        try:
            i_import = min(i for i, ln in enumerate(clean) if ln.startswith("import") or ln.startswith("from "))
        except ValueError:
            i_import = -1
        
        max_lines = len(clean) + 1
        i_vars = next((i for i, ln in enumerate(clean) if re.match(r"^[A-Za-z_][A-Za-z0-9_]*\s*=", ln)), max_lines)
        i_def = next((i for i, ln in enumerate(clean) if ln.startswith("class ") or ln.startswith("def ")), max_lines + 1)
        
        has_imports = i_import != -1
        has_vars = i_vars < max_lines
        has_defs = i_def < max_lines + 1

        if has_imports and has_vars:
            self.assertLess(i_import, i_vars, "Imports should come before variables")
        if has_vars and has_defs:
            self.assertLess(i_vars, i_def, "Variables should come before definitions")
        if has_imports and has_defs:
            self.assertLess(i_import, i_def, "Imports should come before definitions")


class TestIsolatorAdvanced(unittest.TestCase):
    def test_reject_stdlib_entry_by_policy(self):
        """
        analyzing a function from standard library or external package as an entry
        should raise an error (we use textwrap.dedent (defined as STDLIB_OBJ in sample) as a representative).
        """
        with self.assertRaises(Exception):
            # Isolator delegates to Analyzer which rejects external entries
            run_isolation([S.STDLIB_OBJ])
        
    def test_default_collapse_behavior(self):
        """
        By default, analyzer collapses inner functions and class methods in DefItem.code
        (collapse_inner_funcs=True, collapse_methods=True).
        """
        result_class, _ = run_isolation([S.C])
        self.assertNotIn("def m_no_import(", result_class.source)
        self.assertNotIn("def m_stdlib_inside(", result_class.source)

        result_nested, _ = run_isolation([S.outer_with_inner])
        self.assertNotIn("def inner(", result_nested.source)

    def test_metaclass_and_decorator_roots(self):
        """Metaclass and decorator references should be captured (defs present)."""
        result, _ = run_isolation([S.C])
        # Meta class definition present
        self.assertIn("class Meta(type):", result.source)
        # decorator function present
        self.assertIn("def deco_add_attr(", result.source)

    def test_attribute_roots_and_comprehension(self):
        """
        Attribute roots (_tw.dedent via STDLIB_OBJ) and math (comprehension) should be
        resolved as imports when the entries reference them.
        """
        result, _ = run_isolation([S.f_attr_uses_textwrap, S.f_comprehension_attr])
        # alias import in code
        self.assertRegex(result.source, r"(?m)^import textwrap as _tw$")
        # math should be present due to comprehension use
        self.assertTrue(any(("math." in ln or ln.strip() == "import math") for ln in result.source.splitlines()))

    def test_multiple_entries_emit_both(self):
        """Isolating multiple entries should include both in the source."""
        result, _ = run_isolation([S.simple_add, S.C.m_no_import])
        # simple function present
        self.assertIn("def simple_add(", result.source)
        # with collapse_methods=True, class 'C' should be present rather than the method
        self.assertIn("class C(", result.source)


if __name__ == "__main__":
    unittest.main()

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
    code : str
        Generated isolated code.
    warnings : list[str]
        Warning messages returned by Isolator.
    report : dict-like
        Analyzer report (entries/def_items/imports/vars/unbound).
    stderr : str
        Captured stderr (for warnings printed by Isolator).
    """
    isolator_kwargs = isolator_kwargs or {}
    iso = Isolator(**isolator_kwargs)
    with io.StringIO() as err, redirect_stderr(err):
        code, warnings, report = iso.isolate(list(targets))
        stderr = err.getvalue()
    return code, warnings, report, stderr


class TestIsolatorBasic(unittest.TestCase):
    def test_bare_name_var_pulls_function(self):
        """LOCAL_OBJ = f_no_import should cause f_no_import to appear in defs."""
        code, warnings, report, _ = run_isolation([S.uses_bare_name], isolator_kwargs={"header_comment": False})
        # f_no_import should be part of the output because LOCAL_OBJ references it
        self.assertTrue(any(d.name == "f_no_import" for d in report["def_items"]))
        self.assertIn("def f_no_import(", code)

    def test_vars_dedup(self):
        """Same module var referenced from multiple entries should appear only once."""
        _, _, report, _ = run_isolation(
            [S.uses_bare_name, S.also_uses_bare_name],
            isolator_kwargs={"header_comment": False},
        )
        # Within a single module, VarsItem names should be unique
        for mod, vs in report["vars"].items():
            names = [v.name for v in vs]
            self.assertEqual(len(names), len(set(names)), f"Duplicate vars in module {mod}: {names}")

    def test_imports_sorted_and_deduped(self):
        """Imports should be deterministic and deduped."""
        code, _, _, _ = run_isolation([S.f_stdlib_inside, S.f_attr_uses_textwrap], isolator_kwargs={"header_comment": False})
        # We expect the alias import line for textwrap and possibly others; ensure unique
        self.assertRegex(code, r"(?m)^import textwrap as _tw$")
        # No duplicated identical import lines
        lines = [l for l in code.splitlines() if l.startswith("import ") or l.startswith("from ")]
        self.assertEqual(len(lines), len(set(lines)))

    def test_dynamic_import_keep_or_drop(self):
        """Top-level dynamic imports should respect keep_dynamic_imports flag."""
        # keep_dynamic_imports=True (default) -> code should include the dynamic import line
        code_keep, _, _, _ = run_isolation([S.f_dynamic_json_loader], isolator_kwargs={"header_comment": False})
        self.assertRegex(code_keep, r"(?m)^_json\s*=\s*importlib\.import_module\('json'\)$")

        # keep_dynamic_imports=False -> dynamic top-level import omitted
        code_drop, _, _, _ = run_isolation(
            [S.f_dynamic_json_loader],
            isolator_kwargs={"header_comment": False, "keep_dynamic_imports": False},
        )
        self.assertNotRegex(code_drop, r"(?m)^_json\s*=\s*importlib\.import_module\('json'\)$")
        # but the function body remains and uses JSON_OBJ
        self.assertIn("return JSON_OBJ.dumps", code_drop)

    def test_unbound_warns_header_and_stderr(self):
        """Unbound names should appear in header and be printed to stderr."""
        code, warnings, _, stderr = run_isolation([S.f_calls_unknown])
        # header (first ~6 lines) should include WARNINGS section
        header_first_lines = "\n".join(code.splitlines()[:8])
        self.assertIn("WARNINGS:", header_first_lines)
        # list contains our symbol
        self.assertTrue(any("not_defined_anywhere" in w for w in warnings))
        self.assertIn("not_defined_anywhere", stderr)

    def test_section_order(self):
        """Order = imports -> variables -> definitions."""
        code, *_ = run_isolation([S.simple_add], isolator_kwargs={"header_comment": False})
        # crude check: first non-comment lines should start with import or be empty before vars/defs
        clean = [ln for ln in code.splitlines() if not ln.startswith("#")]
        # find indices
        try:
            i_import = min(i for i, ln in enumerate(clean) if ln.startswith("import") or ln.startswith("from "))
        except ValueError:
            i_import = -1
        i_vars = next((i for i, ln in enumerate(clean) if re.match(r"^[A-Za-z_][A-Za-z0-9_]*\s*=", ln)), 10**9)
        i_def = next((i for i, ln in enumerate(clean) if ln.startswith("class ") or ln.startswith("def ")), 10**9)
        self.assertTrue(i_import == -1 or i_import < i_vars <= i_def)


class TestIsolatorAdvanced(unittest.TestCase):
    def test_reject_stdlib_entry_by_policy(self):
        """
        analyzing a function from standard library or external package as an entry
        should raise an error (we use textwrap.dedent (defined as STDLIB_OBJ in sample) as a representative).
        """
        with self.assertRaises(Exception):
            _, _, _, _ = run_isolation([S.STDLIB_OBJ], isolator_kwargs={"header_comment": False})
        
    def test_default_collapse_behavior(self):
        """
        By default, analyzer collapses inner functions and class methods in DefItem.code
        (collapse_inner_funcs=True, collapse_methods=True).
        """
        _, _, report_class, _ = run_isolation([S.C], isolator_kwargs={"header_comment": False})
        code_class = ""
        for def_ in report_class.def_items:
            code_class += (def_.code + '\n')
        self.assertNotIn("def m_no_import(", code_class)
        self.assertNotIn("def m_stdlib_inside(", code_class)

        _, _, report_nested, _ = run_isolation([S.outer_with_inner], isolator_kwargs={"header_comment": False})
        code_nested = ""
        for def_ in report_nested.def_items:
            code_nested += (def_.code + '\n')
        self.assertNotIn("def inner(", code_nested)

    def test_metaclass_and_decorator_roots(self):
        """Metaclass and decorator references should be captured (defs present)."""
        code, _, _, _ = run_isolation([S.C], isolator_kwargs={"header_comment": False})
        # Meta class definition present
        self.assertIn("class Meta(type):", code)
        # decorator function present
        self.assertIn("def deco_add_attr(", code)

    def test_attribute_roots_and_comprehension(self):
        """
        Attribute roots (_tw.dedent via STDLIB_OBJ) and math (comprehension) should be
        resolved as imports when the entries reference them.
        """
        code, _, _, _ = run_isolation(
            [S.f_attr_uses_textwrap, S.f_comprehension_attr],
            isolator_kwargs={"header_comment": False},
        )
        # alias import in code
        self.assertRegex(code, r"(?m)^import textwrap as _tw$")
        # math should be present due to comprehension use
        self.assertTrue(any(("math." in ln or ln.strip() == "import math") for ln in code.splitlines()))

    def test_multiple_entries_header_lists_all(self):
        """Header should list all entries by module.name."""
        code, _, report, _ = run_isolation([S.simple_add, S.C.m_no_import])
        hdr = "\n".join(code.splitlines()[:8])
        self.assertIn("Entries:", hdr)
        # our module is imported as 'test_samples'
        self.assertIn("test_samples.simple_add", hdr)
        # function entry inside class often resolved to the class entry; accept at least 2 entries shown
        self.assertTrue(hdr.count(",") >= 1)


if __name__ == "__main__":
    unittest.main()

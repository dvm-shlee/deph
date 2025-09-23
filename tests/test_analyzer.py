# tests/test_analyzer.py
import sys
import unittest
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
from deph.analyzer import DependencyAnalyzer

# IMPORTANT: import as local module name (no 'tests.' prefix) to keep module __name__ stable
import test_samples as S


class TestAnalyzerBasic(unittest.TestCase):
    def test_simple_function(self):
        """Analyze a simple function with no external dependencies."""
        analyzer = DependencyAnalyzer()
        report = analyzer.analyze(S.simple_add)

        self.assertEqual(len(report["entries"]), 1)
        self.assertEqual(report["entries"][0]["name"], "simple_add")
        self.assertEqual(len(report["def_items"]), 1)
        self.assertEqual(report["def_items"][0].name, "simple_add")
        self.assertEqual(len(report["vars"]), 0)
        self.assertEqual(len(report["unbound"]), 0)
        # The only import should be `from __future__ import annotations` from test_samples.py
        self.assertIn("test_samples", report["imports"])
        future_import = report["imports"]["test_samples"]["annotations"]
        self.assertEqual(future_import.module, "__future__")

    def test_function_with_stdlib_dependency(self):
        """Analyze a function that uses a stdlib module imported at the top level."""
        analyzer = DependencyAnalyzer()
        report = analyzer.analyze(S.f_stdlib_inside)

        self.assertEqual(report["def_items"][0].name, "f_stdlib_inside")
        # 'math' should be in the imports for the test_samples module
        self.assertIn("test_samples", report["imports"])
        self.assertIn("math", report["imports"]["test_samples"])
        self.assertEqual(report["imports"]["test_samples"]["math"].module, "math")

    def test_function_with_thirdparty_dependency(self):
        """Analyze a function that uses a third-party module (numpy)."""
        analyzer = DependencyAnalyzer()
        report = analyzer.analyze(S.f_numpy_outside)

        self.assertIn("test_samples", report["imports"])
        self.assertIn("np", report["imports"]["test_samples"])
        self.assertEqual(report["imports"]["test_samples"]["np"].module, "numpy")

    def test_dependency_on_module_variable(self):
        """A function using a module-level variable should pull it into the 'vars' section."""
        analyzer = DependencyAnalyzer()
        report = analyzer.analyze(S.f_attr_uses_textwrap)

        # The function itself
        self.assertTrue(any(d.name == "f_attr_uses_textwrap" for d in report["def_items"]))
        # The variable it uses
        self.assertIn("test_samples", report["vars"])
        self.assertTrue(any(v.name == "STDLIB_OBJ" for v in report["vars"]["test_samples"]))
        # The import required by the variable
        self.assertIn("test_samples", report["imports"])
        self.assertIn("_tw", report["imports"]["test_samples"])

    def test_dependency_on_another_function_via_variable(self):
        """A function calling another local function via a module-level variable."""
        analyzer = DependencyAnalyzer()
        report = analyzer.analyze(S.uses_bare_name)

        defs = {d.name for d in report["def_items"]}
        self.assertIn("uses_bare_name", defs)
        self.assertIn("f_no_import", defs) # f_no_import is pulled in via LOCAL_OBJ

        variables = {v.name for v in report["vars"]["test_samples"]}
        self.assertIn("LOCAL_OBJ", variables)

    def test_unbound_name_detection(self):
        """A function using an undefined name should report it as 'unbound'."""
        analyzer = DependencyAnalyzer()
        report = analyzer.analyze(S.f_calls_unknown)

        self.assertIn("not_defined_anywhere", report["unbound"])

    def test_reject_external_entrypoint(self):
        """Analyzer should raise ValueError for stdlib/third-party entry points."""
        analyzer = DependencyAnalyzer()
        with self.assertRaisesRegex(ValueError, "belongs to external module"):
            analyzer.analyze(S.STDLIB_OBJ) # textwrap.dedent
        with self.assertRaisesRegex(ValueError, "belongs to external module"):
            analyzer.analyze(S.np.array) # numpy.array


class TestAnalyzerOptions(unittest.TestCase):
    def test_collapse_methods(self):
        """With collapse_methods=True (default), methods should not be in def_items."""
        analyzer = DependencyAnalyzer(collapse_methods=True)
        report = analyzer.analyze(S.C)
        def_names = {d.name for d in report["def_items"]}
        self.assertIn("C", def_names)
        self.assertNotIn("m_no_import", def_names)

        """With collapse_methods=False, methods should be included."""
        analyzer_no_collapse = DependencyAnalyzer(collapse_methods=False)
        report_no_collapse = analyzer_no_collapse.analyze(S.C.m_no_import)
        def_names_no_collapse = {d.name for d in report_no_collapse["def_items"]}
        self.assertIn("C", def_names_no_collapse)
        self.assertIn("m_no_import", def_names_no_collapse)

    def test_collapse_inner_functions(self):
        """With collapse_inner_funcs=True (default), nested functions are excluded."""
        analyzer = DependencyAnalyzer(collapse_inner_funcs=True)
        report = analyzer.analyze(S.outer_with_inner)
        def_names = {d.name for d in report["def_items"]}
        self.assertIn("outer_with_inner", def_names)
        self.assertNotIn("inner", def_names)

        """With collapse_inner_funcs=False, nested functions are included if referenced."""
        analyzer_no_collapse = DependencyAnalyzer(collapse_inner_funcs=False, analyze_nested="referenced_only")
        report_no_collapse = analyzer_no_collapse.analyze(S.outer_with_inner)
        def_names_no_collapse = {d.name for d in report_no_collapse["def_items"]}
        self.assertIn("outer_with_inner", def_names_no_collapse)
        self.assertIn("inner", def_names_no_collapse)

    def test_analyze_nested_options(self):
        """Test 'analyze_nested' strategies: 'all', 'referenced_only', 'none'."""
        # 'none': inner function 'inner' should not be analyzed or included.
        analyzer_none = DependencyAnalyzer(analyze_nested="none", collapse_inner_funcs=False)
        report_none = analyzer_none.analyze(S.outer_with_inner)
        self.assertNotIn("inner", {d.name for d in report_none["def_items"]})

        # 'referenced_only': 'inner' is referenced, so it should be included.
        analyzer_ref = DependencyAnalyzer(analyze_nested="referenced_only", collapse_inner_funcs=False)
        report_ref = analyzer_ref.analyze(S.outer_with_inner)
        self.assertIn("inner", {d.name for d in report_ref["def_items"]})

        # 'all': 'inner' should be included regardless of reference.
        analyzer_all = DependencyAnalyzer(analyze_nested="all", collapse_inner_funcs=False)
        report_all = analyzer_all.analyze(S.outer_with_inner)
        self.assertIn("inner", {d.name for d in report_all["def_items"]})

    def test_class_with_decorator_and_metaclass(self):
        """Analyzing a class should pull in its decorators and metaclass."""
        analyzer = DependencyAnalyzer()
        report = analyzer.analyze(S.C)
        def_names = {d.name for d in report["def_items"]}
        self.assertIn("C", def_names)
        self.assertIn("Meta", def_names) # The metaclass
        self.assertIn("deco_add_attr", def_names) # The decorator on a method

if __name__ == "__main__":
    unittest.main()

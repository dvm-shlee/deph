# tests/test_integration_analyzer_isolator_visitors.py
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

from deph.analyzer import DependencyAnalyzer
from deph.isolator import Isolator
import test_samples as S


class TestIntegrationAnalyzerIsolatorVisitors(unittest.TestCase):
    def test_dynamic_import_flagged_and_vars_collected(self):
        analyzer = DependencyAnalyzer()
        report = analyzer.analyze(S.f_dynamic_json_loader)
        # dynamic import alias exists and is flagged
        self.assertIn('test_samples', report['imports'])
        self.assertIn('_json', report['imports']['test_samples'])
        self.assertTrue(report['imports']['test_samples']['_json'].is_dynamic)
        # module var JSON_OBJ captured
        self.assertIn('test_samples', report['vars'])
        self.assertTrue(any(v.name == 'JSON_OBJ' for v in report['vars']['test_samples']))

    def test_isolator_renders_expected_sections(self):
        iso = Isolator()
        result = iso.isolate([S.f_attr_uses_textwrap])
        src = result.source
        # Has imports
        self.assertRegex(src, r"(?m)^import textwrap as _tw$")
        # Has defs
        self.assertIn("def f_attr_uses_textwrap(", src)


if __name__ == "__main__":
    unittest.main()


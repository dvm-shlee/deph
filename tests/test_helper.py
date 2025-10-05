# tests/test_helper.py
import sys
import types
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

from deph.helper import is_stdlib, is_on_pypi, module_classifier
import importlib
import test_samples as S


class DummyResponse:
    def __init__(self, status_code=200):
        self._status = status_code
    def raise_for_status(self):
        if self._status != 200:
            raise Exception("http error")


class TestHelper(unittest.TestCase):
    def test_is_stdlib(self):
        self.assertTrue(is_stdlib("math"))
        # 'builtins' may not be present in stdlib_module_names; keep explicit check
        self.assertFalse(is_stdlib("nonexistent_module_name___"))

    def test_is_on_pypi_with_mock(self):
        import sys as _sys, types as _types
        # Inject a fake requests module into sys.modules
        fake = _types.SimpleNamespace(get=lambda *a, **k: DummyResponse(200))
        orig = _sys.modules.get('requests')
        _sys.modules['requests'] = fake  # type: ignore
        try:
            self.assertTrue(is_on_pypi("requests"))
            fake.get = lambda *a, **k: DummyResponse(404)  # type: ignore
            self.assertFalse(is_on_pypi("__definitely_not_on_pypi__"))
        finally:
            if orig is not None:
                _sys.modules['requests'] = orig
            else:
                del _sys.modules['requests']

    def test_module_classifier_builtin_stdlib_local(self):
        import builtins as bi
        import textwrap as tw
        self.assertEqual(module_classifier(bi), "builtin")
        self.assertEqual(module_classifier(tw), "stdlib")
        # local module defined under tests
        self.assertEqual(module_classifier(S), "local")

    def test_module_classifier_thirdparty_packaging_requests(self):
        # packaging should classify as thirdparty with override mapping
        try:
            import packaging  # type: ignore
        except Exception:
            self.skipTest("packaging not installed")
        self.assertEqual(
            module_classifier(packaging, packages_dists={"packaging": "packaging"}),
            "thirdparty",
        )

        # requests should also classify as thirdparty with override mapping
        try:
            import requests  # type: ignore
        except Exception:
            self.skipTest("requests not installed")
        self.assertEqual(
            module_classifier(requests, packages_dists={"requests": "requests"}),
            "thirdparty",
        )

    def test_module_classifier_pip_negative(self):
        # pip may be bundled as stdlib; ensure it is NOT classified as thirdparty
        try:
            import pip  # type: ignore
        except Exception:
            self.skipTest("pip not importable")
        cls = module_classifier(pip)
        self.assertNotEqual(cls, "thirdparty")


if __name__ == "__main__":
    unittest.main()

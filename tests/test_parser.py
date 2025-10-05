# tests/test_parser.py
import ast
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

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
from deph import parser

# IMPORTANT: import as local module name (no 'tests.' prefix) to keep module __name__ stable
import test_samples as S


class TestParser(unittest.TestCase):

    def test_get_source_from_path(self):
        """
        Test reading source code from a file path.
        """
        # We use the test_samples.py file itself as the source to read.
        sample_path = Path(S.__file__)
        source_code = parser.get_source_from_path(sample_path)
        self.assertIn("def simple_add(a: int, b: int) -> int:", source_code)
        self.assertIn("class C(metaclass=Meta):", source_code)

        # Test for non-existent file
        with self.assertRaises(FileNotFoundError):
            parser.get_source_from_path("non_existent_file.py")

    def test_get_module_source_for_obj(self):
        """
        Test retrieving the source code of the module for a given object.
        """
        src, module = parser.get_module_source_for_obj(S.simple_add)
        self.assertEqual(module, S)
        self.assertIn("def simple_add(a: int, b: int) -> int:", src)
        self.assertIn("import numpy as np", src)

        # Test with a method from a class
        src_class, module_class = parser.get_module_source_for_obj(S.C.m_no_import)
        self.assertEqual(module_class, S)
        self.assertIn("class C(metaclass=Meta):", src_class)

        # Builtins are not supported
        with self.assertRaises(ValueError):
            parser.get_module_source_for_obj(print)

    def test_is_defined_in_source(self):
        """
        Test checking if an object is defined within a source string.
        """
        source_code = """
import math
from os import path as ospath

def my_func():
    return 1

class MyClass:
    pass
"""
        # Helper factory: create a callable and set its __name__ for type-compatibility
        def named_callable(name: str):
            def _f():
                return None
            try:
                _f.__name__ = name  # type: ignore[attr-defined]
            except Exception:
                pass
            return _f

        # Create callables with the desired names
        mock_func = named_callable("my_func")
        mock_class = named_callable("MyClass")
        mock_import = named_callable("math")
        mock_alias = named_callable("ospath")
        mock_not_defined = named_callable("not_defined")

        self.assertTrue(parser.is_defined_in_source(mock_func, source_code))
        self.assertTrue(parser.is_defined_in_source(mock_class, source_code))
        self.assertTrue(parser.is_defined_in_source(mock_import, source_code))
        self.assertTrue(parser.is_defined_in_source(mock_alias, source_code))
        self.assertFalse(parser.is_defined_in_source(mock_not_defined, source_code))

    def test_convert_source_to_ast(self):
        """Test converting source code to an AST node or full module."""
        with self.subTest("single function"):
            func_src = "def hello():\n    return 'world'"
            node = parser.convert_source_to_ast(func_src)
            self.assertIsInstance(node, ast.FunctionDef)
            self.assertEqual(getattr(node, 'name'), "hello")

        with self.subTest("full module"):
            module_src = "import os\n\ndef func1():\n    pass"
            node = parser.convert_source_to_ast(module_src)
            self.assertIsInstance(node, ast.Module)
            self.assertEqual(len(node.body), 2)

        with self.subTest("invalid syntax"):
            with self.assertRaises(SyntaxError):
                parser.convert_source_to_ast("def invalid syntax")

    def test_get_module_ast(self):
        """
        Test retrieving the AST and module object for a given object.
        """
        tree, module = parser.get_module_ast(S.f_stdlib_inside)
        self.assertIsInstance(tree, ast.Module)
        self.assertEqual(module, S)
        # Check if a known function from the sample is in the AST
        func_names = {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}
        self.assertIn("f_stdlib_inside", func_names)

    def test_get_notebook_source_for_obj_raises_outside(self):
        """Outside notebook, get_notebook_source_for_obj should raise."""
        with patch.object(parser, "_IN_NOTEBOOK", return_value=False):
            with self.assertRaises(RuntimeError):
                parser.get_notebook_source_for_obj(S.simple_add)


if __name__ == "__main__":
    unittest.main()

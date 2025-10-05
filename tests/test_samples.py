# tests/test_samples.py
"""
A grab bag of local sample defs/classes/vars that exercise the Isolator+Analyzer.

Notes
-----
- Everything here is LOCAL to this module to satisfy Analyzer's external-entry policy.
- We include a few stdlib imports at module scope so the analyzer can resolve them.
"""

from __future__ import annotations
from typing import Iterable, List

# stdlib imports at module level (to be pulled into isolated snippet when used)
import pprint
import math
import importlib
import re
import textwrap as _tw  # alias used by STDLIB_OBJ
import numpy as np


# --- module-level "dynamic" import (for keep_dynamic_imports tests) ------------
_json = importlib.import_module("json")       # dynamic import (is_dynamic=True)
JSON_OBJ = _json                              # referenced by a function below

# --- module-level variables used by functions ----------------------------------
STDLIB_OBJ = _tw.dedent                       # attribute root use (_tw.dedent)
ABC = "hello"

# bare-name var that points at a local function (to test pulling defs by var)
def f_no_import(x: int) -> int:
    return x + 1

LOCAL_OBJ = f_no_import


# --- simple functions ----------------------------------------------------------
def simple_add(a: int, b: int) -> int:
    return a + b


def f_stdlib_inside(n: float):
    """Function that uses stdlib imported at module level (math)."""
    return (math.isfinite(n), math.sqrt(abs(n)))


def f_numpy_outside(n: float):
    return np.ones(n)


def f_attr_uses_textwrap(s: str) -> str:
    """Uses module var STDLIB_OBJ = _tw.dedent -> should require 'import textwrap as _tw'."""
    return STDLIB_OBJ(s)


def f_comprehension_attr(seq: Iterable[int]) -> List[float]:
    """List comprehension using stdlib `math` imported at module level."""
    return [math.sqrt(abs(x)) for x in seq]


def f_calls_unknown():
    """Intentionally references an unknown global to trigger 'unbound' warning."""
    return not_defined_anywhere(123)  # noqa: F821


def f_dynamic_json_loader() -> str:
    """
    This function does NOT have a top-level dynamic import (that is above).
    It only uses the module-level JSON_OBJ which came from a dynamic import.
    """
    return JSON_OBJ.dumps({"ok": True})


# Function that calls the "bare-name" LOCAL_OBJ = f_no_import
def uses_bare_name(v: int) -> int:
    return LOCAL_OBJ(v)


# An extra function that also references LOCAL_OBJ so the same module var is required twice
def also_uses_bare_name(u: int) -> int:
    return LOCAL_OBJ(u + 10)


# --- nested defs case ----------------------------------------------------------
def outer_with_inner(a: int) -> int:
    def inner(b: int) -> int:
        return b * 2
    return inner(a) + 1


# --- class/methods & decorator/metaclass --------------------------------------
def deco_add_attr(fn):
    def wrapper(*args, **kwargs):
        return fn(*args, **kwargs)
    wrapper._decorated = True
    return wrapper


class Meta(type):
    def __new__(mcls, name, bases, ns):
        ns["_tag"] = "META"
        return super().__new__(mcls, name, bases, ns)


class C(metaclass=Meta):
    """Class with various methods."""

    def m_no_import(self, s: str) -> str:
        return s.upper()

    def m_stdlib_inside(self) -> str:
        from datetime import date
        return date.today().isoformat()

    def m_math_outside(self) -> float:
        # math is a module-level import
        return math.pi

    @deco_add_attr
    def m_with_decorator(self, x: int) -> int:
        return x + 10

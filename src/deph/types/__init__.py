from __future__ import annotations
import ast
from typing import Union
from .dataclasses import DefItem, ImportItem, VarsItem, ModuleCtx
from .attrdefaultdict import AttrDefaultDict


__all__ = [
    'AstDefs',
    'DefItem',
    'ImportItem',
    'VarsItem',
    'ModuleCtx',
    'AttrDefaultDict'
]

AstDefs = Union[ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef]

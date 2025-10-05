import os
import ast
import inspect
import textwrap
from types import ModuleType
from typing import Union, Tuple, Optional, List, Iterable, Callable, cast
from pathlib import Path
from collections.abc import Callable

def _get_ip():
    """Safely retrieve the active IPython shell if present, else None.

    This function avoids caching a global reference so that detection remains
    accurate even if IPython gets imported after this module.
    """
    try:
        from IPython import get_ipython  # type: ignore
        return get_ipython()
    except Exception:
        return None

def _IN_NOTEBOOK() -> bool:
    """Detect if running inside a Jupyter/IPython kernel environment."""
    ip = _get_ip()
    if ip is None:
        return False
    # IPython exposes a `kernel` attribute when running under a Jupyter kernel
    if hasattr(ip, 'kernel'):
        return True
    # Fallback: check known config attribute if present
    cfg = getattr(ip, 'config', None)
    # Some IPython versions provide traitlets.Config; check attribute directly
    return bool(getattr(cfg, 'IPKernelApp', False))
Pathish = Union[str, os.PathLike]
AstObj = Union[ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef]

__all__ = [
    "get_source_from_path",
    "get_notebook_source_for_obj",
    "get_module_source_for_obj",
    "is_defined_in_source",
    "convert_source_to_ast",
    "get_module_ast"
]


def get_source_from_path(path: Pathish) -> str:
    """
    Loads and dedents source code from a given file path.

    Args:
        path: The path to the source file (string or Path-like object).

    Returns:
        The dedented source code as a single string.

    Raises:
        FileNotFoundError: If the provided path does not exist or is not a file.
        IOError: If the file cannot be read due to permissions or other OS-level issues.
    """
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"Provided path is not a file or does not exist: {path}")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return textwrap.dedent(f.read())
    except (IOError, OSError) as e:
        raise IOError(f"Cannot read file at path '{path}': {e}") from e


def get_notebook_source_for_obj(obj: Union[type, Callable]) -> Optional[str]:
    """
    Retrieves the source code of all executed cells in a notebook session
    up to the point where the given object is defined.

    This function is intended for use within an IPython/Jupyter environment.
    It iterates through the session's input history, concatenating the source
    of each cell. It stops searching for the object's definition once found
    but continues to append all subsequent cell sources to provide complete context.

    Args:
        obj: The object (class or function) to find within the notebook history.

    Returns:
        A string containing the concatenated source of all executed cells,
        with each cell annotated with a `CellID`. Returns `None` if the
        object's definition is not found in the history.

    Raises:
        RuntimeError: If not run within a Jupyter/IPython environment.
    """
    if not _IN_NOTEBOOK():
        raise RuntimeError("This function can only be used in a Jupyter environment.")
    defined = False
    src = []
    ip = _get_ip()
    hm = getattr(ip, 'history_manager', None)
    cells: List[str] = []
    if hm is not None:
        parsed = getattr(hm, 'input_hist_parsed', None)
        if isinstance(parsed, list) and parsed:
            # The first element is often an empty entry; include indices for IDs
            cells = list(parsed)
        else:
            # Fallback to HistoryManager.get_range if available
            _gr = getattr(hm, 'get_range', None)
            gr: Optional[Callable[..., Iterable[Tuple[int, int, str]]]] = (
                cast(Callable[..., Iterable[Tuple[int, int, str]]], _gr) if callable(_gr) else None
            )
            if gr is not None:
                try:
                    # Use keyword args to satisfy various IPython versions
                    for _sid, _ln, cell_src in gr(session=0, start=1, stop=None, raw=False, output=False):  # type: ignore[arg-type]
                        cells.append(str(cell_src))
                except Exception:
                    pass

    # Iterate over discovered cells
    for idx, cell_src in enumerate(cells, start=1):
        if not cell_src:
            continue
        cell_src = textwrap.dedent(cell_src)
        try:
            ast.parse(cell_src)
            src.append(f"# CellID[{idx}]\n{cell_src}\n")
            if not defined:
                defined = is_defined_in_source(obj, cell_src)
        except SyntaxError:
            # skip cell containing syntax error
            continue
    return "".join(src) if defined else None


def get_module_source_for_obj(obj: Union[type, Callable]) -> Tuple[str, ModuleType]:
    """
    Retrieves the source code of the module where the given object is defined.

    Args:
        obj: The function, method, or class to find the source module for.

    Returns:
        A tuple containing the source code (str) and the module object (ModuleType).

    Raises:
        ValueError: If the module for the object cannot be found, if the object is
                    a builtin, or if it's in `__main__` but its source cannot be
                    retrieved from the interactive session history.
    """
    module = inspect.getmodule(obj)
    if module is None:
        raise ValueError(f"Could not find the module for the given object: {obj.__name__}")
    if module.__name__ == "__main__":
        src = get_notebook_source_for_obj(obj)
        if not src:
            raise ValueError(f"Object '{obj.__name__}' is in `__main__`, but its source could not be found in the session history.")
    elif module.__name__ == "builtins":
        raise ValueError(f"Object '{obj.__name__}' is a builtin, and its source cannot be extracted.")
    else:
        src = textwrap.dedent(inspect.getsource(module))
    return src, module


def is_defined_in_source(obj: Union[type, Callable], src: str) -> bool:
    """
    Checks if an object's name is defined in the given source code.

    This function parses the source code into an Abstract Syntax Tree (AST)
    and walks through it to find if the object's name appears as a
    class/function definition or as an imported name.

    Args:
        obj: The object (class, function, etc.) to check for.
        src: The source code string to search within.

    Returns:
        True if the object's name is found as a definition or import,
        False otherwise.
    """
    try:
        # Always parse into a full Module for walking, even if convert_source_to_ast might return a single def
        tree = ast.parse(src)
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                name = alias.asname or alias.name
                if name == obj.__name__:
                    return True  # Found as an import
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == obj.__name__:
                return True  # Found as a definition
    return False


def convert_source_to_ast(src: str) -> Union[AstObj, ast.Module]:
    """
    Parses a source code string into an Abstract Syntax Tree (AST) node.

    If the source code contains a single top-level statement (like a function
    or class definition), this function returns that specific AST node.
    Otherwise, it returns the full `ast.Module` object.

    Args:
        src: The Python source code to parse.

    Returns:
        An `AstObj` (FunctionDef, ClassDef, or AsyncFunctionDef) if the source
        contains a single definition, or the top-level `ast.Module` otherwise.

    Raises:
        SyntaxError: If the source code is not valid Python.
    """
    try:
        tree = ast.parse(src)
        if len(tree.body) == 1 and isinstance(tree.body[0], (ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef)):
            return tree.body[0]
        return tree
    except SyntaxError as e:
        raise SyntaxError(f"Failed to parse source code: {e}") from e
    
    
def get_module_ast(obj: Union[type, Callable]) -> Tuple[ast.Module, ModuleType]:
    """
    Retrieves the AST and module object for the module defining the given object.

    This function first locates the source code of the module containing the
    object, then parses it into an `ast.Module` node.

    Args:
        obj: The function, method, or class to find the source module for.

    Returns:
        A tuple containing the parsed `ast.Module` and the module object itself.

    Raises:
        ValueError: If the module for the object cannot be found or its source
                    cannot be retrieved.
        SyntaxError: If the module's source code is not valid Python.
    """
    src, module = get_module_source_for_obj(obj)
    tree = ast.parse(src)
    return tree, module

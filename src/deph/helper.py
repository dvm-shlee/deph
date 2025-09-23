import sys
import inspect
import requests
import importlib.util
import sysconfig
from pathlib import Path
from importlib.metadata import Distribution, distributions
from typing import Dict, Set, Optional
from pathlib import Path
from types import ModuleType
from .parser import _IN_NOTEBOOK


__all__ = [
    "is_on_pypi",
    "is_stdlib",
    "packages_distributions",
    "module_classifier",
    "PACKAGE_DISTRIBUTIONS"
]
    

def is_stdlib(pkg: str) -> bool:
    """
    Checks if a given module name is part of the Python standard library.

    This function relies on `sys.stdlib_module_names` (available in Python 3.10+).
    For earlier versions, it may not be available.

    Args:
        pkg: The name of the module to check (e.g., "os", "math").

    Returns:
        True if the module is part of the standard library, False otherwise.
    """
    return pkg in sys.stdlib_module_names


def is_on_pypi(pkg: str) -> bool:
    """
    Checks if a package exists on the Python Package Index (PyPI).

    This function sends a GET request to the PyPI JSON API for the given package.

    Args:
        pkg: The name of the package to check (e.g., "numpy", "requests").

    Returns:
        True if the package exists on PyPI (HTTP 200 OK), False otherwise.
        Returns False on network errors (e.g., timeout, connection error).
    """
    try:
        r = requests.get(f"https://pypi.org/pypi/{pkg}/json", timeout=5)
        r.raise_for_status()
        return True
    except (requests.exceptions.RequestException, requests.exceptions.HTTPError):
        return False


def packages_distributions() -> Dict[str, list]:
    """
    Creates a mapping from top-level importable module names to their distribution package names.

    Returns
    -------
    Dict[str, str]
        A dictionary where keys are the discovered top-level module names (e.g., "numpy")
        and values are the corresponding distribution names (e.g., "numpy").
    """
    mapping = dict()
    for dist in distributions():
        for mod in _get_toplevel_modules_for_dist(dist):
            mapping[mod] = dist.name
    return mapping


# internal
def _get_toplevel_modules_for_dist(dist: "Distribution") -> Set[str]:
    """
    Extracts top-level importable module names from a distribution.

    It tries to find modules from 'top_level.txt', and if that's not available,
    it infers them from the list of files in the distribution's metadata.

    Args:
        dist: An `importlib.metadata.Distribution` object.

    Returns:
        A set of top-level module names provided by the distribution.
    """
    modules: Set[str] = set()
    # Method 1: Use top_level.txt (most reliable)
    try:
        if top_level_txt := dist.read_text('top_level.txt'):
            modules.update(line.strip() for line in top_level_txt.splitlines() if line.strip() and not line.startswith("_"))
    except (FileNotFoundError, IOError, OSError):
        pass

    # Method 2: Fallback to iterating over files if top_level.txt is missing
    if not modules and dist.files:
        for file_path in dist.files:
            # e.g., 'numpy/version.py' -> 'numpy'
            # e.g., 'scipy.libs/...' -> skip
            # e.g., 'some_package.py' -> 'some_package'
            if file_path.parts:
                top_part = file_path.parts[0]
                if '.dist-info' in top_part or '.egg-info' in top_part:
                    continue
                if top_part.endswith('.py'):
                    module_name = top_part[:-3]
                    modules.add(module_name)
                elif '.' not in top_part: # It's likely a directory-based package
                    modules.add(top_part)
    return modules


def _module_origin_path(mod: ModuleType) -> Optional[Path]:
    """Safely retrieves the resolved file path for a module object."""
    try:
        p = inspect.getsourcefile(mod) or inspect.getfile(mod)
        return Path(p).resolve() if p else None
    except Exception:
        pass
    try:
        spec = importlib.util.find_spec(mod.__name__)
        if spec and spec.origin:
            return Path(spec.origin).resolve()
    except Exception:
        pass
    return None


def module_classifier(
    mod: ModuleType,
    *,
    packages_dists: Optional[Dict[str, str]] = None,
) -> str:
    """
    Classifies a module into a category based on its origin.

    The categories are:
    - 'stdlib': Part of the Python standard library.
    - 'builtin': A built-in module (e.g., 'sys', 'builtins').
    - 'thirdparty': An installed third-party package (in site-packages).
    - 'extension': A compiled C extension module not in stdlib or site-packages.
    - 'local': A user-defined module, typically part of the current project.
    - 'unknown': The module's origin could not be determined.

    Args:
        mod: The module object to classify.
        packages_dists: A pre-computed mapping of top-level module names to
                        distribution package names, used to identify third-party packages.

    Returns:
        A string representing the category of the module.
    """
    if not mod:
        return "unknown"

    name = getattr(mod, "__name__", "")
    if not name:
        return "unknown"

    # Handle special cases first
    if name == "__main__" and _IN_NOTEBOOK():
        return "local"
    if name == "builtins":
        return "builtin"
    if is_stdlib(name):
        return "stdlib"

    origin_path = _module_origin_path(mod)
    if origin_path is None:
        # Likely a frozen or built-in module that `is_stdlib` didn't catch.
        return "builtin"

    # Check against standard library and site-packages paths
    paths = sysconfig.get_paths()
    stdlib_path = Path(paths.get("stdlib", "")).resolve()
    platstdlib_path = Path(paths.get("platstdlib", stdlib_path)).resolve()
    site_packages_paths = {Path(p).resolve() for k, p in paths.items() if k in ("purelib", "platlib") and p}

    try:
        if origin_path.is_relative_to(stdlib_path) or origin_path.is_relative_to(platstdlib_path):
            return "stdlib"
    except (AttributeError, ValueError): # is_relative_to is 3.9+, fallback for older versions
        origin_str = str(origin_path)
        if origin_str.startswith(str(stdlib_path)) or origin_str.startswith(str(platstdlib_path)):
            return "stdlib"

    if any(str(origin_path).startswith(str(p)) for p in site_packages_paths):
         return "thirdparty"

    # packages_distributions mapping
    if packages_dists and name.split(".", 1)[0] in packages_dists:
        return "thirdparty"

    # If it's a compiled extension but not in stdlib or site-packages, classify as 'extension'
    if origin_path.suffix in (".so", ".pyd", ".dll", ".dylib"):
        return "extension"

    return "local"


PACKAGE_DISTRIBUTIONS = packages_distributions()
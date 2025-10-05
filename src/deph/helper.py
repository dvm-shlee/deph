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

    - On Python 3.10+, uses `sys.stdlib_module_names` (authoritative).
    - On older versions, uses a robust path check:
        1) builtins are stdlib
        2) anything under site-packages (purelib/platlib or user site) is NOT stdlib
        3) otherwise, modules under stdlib/platstdlib are stdlib
    """
    # Fast path for 3.10+
    names = getattr(sys, "stdlib_module_names", None)
    if isinstance(names, (set, frozenset)):
        return pkg in names

    # Fallback for <3.10
    try:
        if pkg in sys.builtin_module_names:
            return True

        import importlib.util as _util
        import site as _site

        spec = _util.find_spec(pkg)
        if spec is None:
            return False

        # Resolve a concrete path for the module/package
        origin = getattr(spec, "origin", None)
        locations = getattr(spec, "submodule_search_locations", None)
        if origin and origin != "built-in":
            mod_path = Path(origin).resolve()
        elif locations:
            # Package: use the first location
            try:
                mod_path = Path(list(locations)[0]).resolve()
            except Exception:
                return False
        else:
            return False

        paths = sysconfig.get_paths()
        stdlib_path = Path(paths.get("stdlib", "")).resolve()
        platstdlib_path = Path(paths.get("platstdlib", paths.get("stdlib", ""))).resolve()
        purelib_path = Path(paths.get("purelib", "")).resolve()
        platlib_path = Path(paths.get("platlib", "")).resolve()

        # Include user site-packages as well
        user_site_paths = []
        try:
            user_site = _site.getusersitepackages()
            if user_site:
                user_site_paths.append(Path(user_site).resolve())
        except Exception:
            pass
        try:
            for p in _site.getsitepackages() or []:
                user_site_paths.append(Path(p).resolve())
        except Exception:
            pass

        def _under(base: Path, target: Path) -> bool:
            try:
                return target.is_relative_to(base)
            except Exception:
                return str(target).startswith(str(base))

        # Non-stdlib if under any site-packages
        site_bases = [purelib_path, platlib_path] + user_site_paths
        if any(b.exists() and _under(b, mod_path) for b in site_bases):
            return False

        # Stdlib if under stdlib paths
        return (stdlib_path.exists() and _under(stdlib_path, mod_path)) or (
            platstdlib_path.exists() and _under(platstdlib_path, mod_path)
        )
    except Exception:
        return False


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
    except Exception:
        # Be defensive: if a caller injects a custom response-like object that raises
        # a non-requests exception on raise_for_status, treat as not found.
        return False


def packages_distributions() -> Dict[str, str]:
    """
    Creates a mapping from top-level importable module names to their distribution package names.

    Returns
    -------
    Dict[str, str]
        A dictionary where keys are the discovered top-level module names (e.g., "numpy")
        and values are the corresponding distribution names (e.g., "numpy").
    """
    mapping: Dict[str, str] = {}
    for dist in distributions():
        # Robustly resolve distribution name across Python versions
        dname: Optional[str] = None
        try:
            # Python >=3.10 often provides .metadata mapping
            dname = getattr(dist, 'name', None)
        except Exception:
            dname = None
        if not dname:
            try:
                meta = getattr(dist, 'metadata', None)
                if meta and hasattr(meta, 'get'):
                    dname = meta.get('Name')
            except Exception:
                dname = None
        for mod in _get_toplevel_modules_for_dist(dist):
            if dname:
                mapping[mod] = dname
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

    top = name.split(".", 1)[0]
    # If caller provided an override mapping, honor it first (treat as thirdparty)
    if packages_dists and top in packages_dists:
        return "thirdparty"

    # Standard library by name (3.10+ authoritative), otherwise fall back to path checks later
    if is_stdlib(name):
        return "stdlib"

    origin_path = _module_origin_path(mod)
    if origin_path is None:
        # Likely a frozen or built-in module that `is_stdlib` didn't catch.
        return "builtin"

    # Check against site-packages and stdlib paths
    paths = sysconfig.get_paths()
    stdlib_path = Path(paths.get("stdlib", "")).resolve()
    platstdlib_path = Path(paths.get("platstdlib", paths.get("stdlib", ""))).resolve()
    purelib_path = Path(paths.get("purelib", "")).resolve()
    platlib_path = Path(paths.get("platlib", "")).resolve()

    # Also consider user and global site-packages via 'site'
    site_paths = set()
    try:
        import site as _site
        us = _site.getusersitepackages()
        if us:
            site_paths.add(Path(us).resolve())
        for sp in (_site.getsitepackages() or []):
            site_paths.add(Path(sp).resolve())
    except Exception:
        pass
    for p in (purelib_path, platlib_path):
        if str(p):
            site_paths.add(p)

    def _under(base: Path, target: Path) -> bool:
        try:
            return target.is_relative_to(base)
        except Exception:
            return str(target).startswith(str(base))

    # Prefer classifying as thirdparty if inside site-packages paths
    if any(_under(sp, origin_path) for sp in site_paths if str(sp)):
        # Special case: many CPython builds bundle 'pip' in the distribution.
        # Treat it as stdlib-like (not thirdparty) for classification purposes.
        if top == "pip":
            return "stdlib"
        return "thirdparty"

    # Otherwise, classify as stdlib if under stdlib paths
    if (str(stdlib_path) and _under(stdlib_path, origin_path)) or (str(platstdlib_path) and _under(platstdlib_path, origin_path)):
        return "stdlib"


    # If it's a compiled extension but not in stdlib or site-packages, classify as 'extension'
    if origin_path.suffix in (".so", ".pyd", ".dll", ".dylib"):
        return "extension"

    return "local"


PACKAGE_DISTRIBUTIONS: Dict[str, str] = packages_distributions()

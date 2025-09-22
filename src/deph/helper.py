import sys
import inspect
import requests
import importlib.util
import sysconfig
from pathlib import Path
from importlib.metadata import distributions, PackagePath
from typing import Dict, Set, Optional
from pathlib import Path
from types import ModuleType
from .parser import _IN_NOTEBOOK


__all__ = [
    "is_on_pypi",
    "is_stdlib",
    "packages_distributions",
    "PACKAGE_DISTRIBUTIONS"
]
    

def is_stdlib(pkg: str) -> bool:
    return pkg in sys.stdlib_module_names


def is_on_pypi(pkg: str) -> bool:
    r = requests.get(f"https://pypi.org/pypi/{pkg}/json")
    return r.status_code == 200


def packages_distributions() -> Dict[str, list]:
    """
    Creates a mapping from top-level importable module names to their distribution package names.

    Returns
    -------
    Dict[str, list]
        A dictionary where keys are the discovered top-level module names (e.g., "numpy")
        and values are the corresponding distribution names (e.g., "numpy"). The value is
        a list to align with `importlib.metadata.packages_distributions` but typically
        contains one name.
    """
    mapping = dict()
    for dist in distributions():
        mods: Set[str] = set()
        if (top_level_txt := dist.read_text('top_level.txt')):
            mods.update(line.strip() for line in top_level_txt.splitlines() if line.strip())
        if not mods and dist.files:
            for fpath in dist.files:
                if mod := fpath.parts[0]:
                    if mod.endswith('.py'):
                        mods.add(mod.split('.')[0].replace('-', '_'))
                    if '.' not in mod and not mod.startswith('_'):
                        mods.add(mod)
                    if mod.endswith('.pth'):
                        mods.update(_find_modules(fpath))
        for mod in list(mods):
            mapping[mod] = dist.name
    return mapping


# internal
def _classify_module(path: Path):
    if path.is_dir():
        if any([f for f in path.iterdir() if '__init__.py' in f.name]):
            return path.name
    elif path.is_file():
        if path.name.endswith('.py'):
            return path.name.split('.')[0].replace('-', '_')
    return None


def _find_modules(package_path: PackagePath):
    mods = []
    lines = package_path.read_text().split('\n')
    for l in lines:
        pth = Path(l)
        if pth.is_dir():
            for m in pth.iterdir():
                if mod := _classify_module(m):
                    mods.append(mod)
    return mods


def _module_origin_path(mod: ModuleType) -> Optional[Path]:
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
    packages_dists: Optional[Dict[str, list]] = None,
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

    origin_str = str(origin_path)
    if origin_str.startswith(str(stdlib_path)) or origin_str.startswith(str(platstdlib_path)):
        return "stdlib"
    if any(origin_str.startswith(str(p)) for p in site_packages_paths):
        return "thirdparty"

    # packages_distributions mapping
    if packages_dists:
        top = name.split(".", 1)[0]
        if top in packages_dists:
            return "thirdparty"

    # If it's a compiled extension but not in stdlib or site-packages, classify as 'extension'
    if origin_path.suffix in (".so", ".pyd", ".dll", ".dylib"):
        return "extension"

    return "local"


PACKAGE_DISTRIBUTIONS = packages_distributions()
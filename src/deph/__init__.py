from . import helper
from . import parser
from .analyzer import DependencyAnalyzer
from .isolator import Isolator

__version__ = "0.1.1"

__all__ = [
    "isolate",
    "analyze",
    "helper", 
    "parser"
]

def isolate(*targets):
    """
    Isolate a minimal, self-contained Python code block from one or more entry objects.

    This is a convenience wrapper around `Isolator().isolate()`.

    Parameters
    ----------
    *targets : Any
        One or more function or class objects to be included in the isolated snippet.

    Returns
    -------
    code : str
        The isolated Python source string.
    report : Dict[str, Any]
        The raw analyzer report used to generate the code.
    """
    targets = list(targets)
    code, _, report = Isolator().isolate(targets)
    return code, report

def analyze(*targets):
    """
    Analyze one or more target objects to build a dependency report.

    This is a convenience wrapper around `DependencyAnalyzer().analyze()` or
    `DependencyAnalyzer().analyze_many()`.

    Parameters
    ----------
    *targets : Any
        One or more function or class objects to analyze.

    Returns
    -------
    Dict[str, Any]
        The dependency report, containing information about definitions, imports,
        variables, and unbound names.
    """
    targets = list(targets)
    if len(targets) > 1:
        return DependencyAnalyzer().analyze_many(targets)
    else:
        return DependencyAnalyzer().analyze(targets[0])
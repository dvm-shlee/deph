# isolator.py
"""
Isolate a minimal, self-contained Python code block from one or more entry objects.

The isolator consumes the report produced by `DependencyAnalyzer` and renders:
  1) imports
  2) module-level variables
  3) definitions (classes first, then functions)

Any names that remain unresolved in the analyzer report (`unbound`) are emitted as warnings.

Typical usage
-------------
    from .analyzer import DependencyAnalyzer
    from .isolator import Isolator

    iso = Isolator()
    code, warnings, report = iso.isolate([some_func_or_class])
    print(code)
    for w in warnings:
        print("WARN:", w)
"""
from __future__ import annotations
import ast
import sys
import textwrap
from typing import TYPE_CHECKING
from .analyzer import DependencyAnalyzer
from .types import DefItem, VarsItem, ImportItem
from .helper import is_stdlib, is_on_pypi
from .types import AttrDefaultDict

if TYPE_CHECKING:
    from typing import Any, Dict, Iterable, List, Sequence, Set, Tuple, Optional
    

class Isolator:
    """
    Generate isolated Python source for a set of entry objects by leveraging
    `DependencyAnalyzer`. The output code attempts to be pip/PEP8-friendly
    (imports first, then variables, then definitions).

    Parameters
    ----------
    analyzer : DependencyAnalyzer | None
        If provided, use this analyzer instance; otherwise construct with defaults.
    sort_imports : bool
        Whether to sort imports deterministically (recommended for stable output).
    keep_dynamic_imports : bool
        Whether to include dynamic import snippets (e.g., via importlib) in the output.
    header_comment : bool
        If True, prepend a brief "auto-generated" comment block.
    """

    def __init__(
        self,
        analyzer: Optional[DependencyAnalyzer] = None,
        *,
        sort_imports: bool = True,
        keep_dynamic_imports: bool = True,
        header_comment: bool = True,
    ) -> None:
        self._analyzer = analyzer or DependencyAnalyzer()
        self.sort_imports = sort_imports
        self.keep_dynamic_imports = keep_dynamic_imports
        self.header_comment = header_comment

    # ----------------------------
    # Public API
    # ----------------------------
    def isolate(self, targets: Sequence[Any]) -> Tuple[str, List[str], Dict[str, Any]]:
        """
        Run the analyzer on targets and render isolated code.

        Returns
        -------
        code : str
            The isolated Python source string.
        warnings : List[str]
            Human-readable warnings (e.g., unresolved/unbound names).
        report : Dict[str, Any]
            The raw analyzer report used to generate `code`.
        """
        report = self._analyzer.analyze_many(list(targets))
        code, warnings = self.isolate_from_report(report)
        return code, warnings, report

    def isolate_from_report(self, report: Dict[str, Any]) -> Tuple[str, List[str]]:
        """
        Render isolated code from a precomputed analyzer report.

        Parameters
        ----------
        report : dict
            Output structure from `DependencyAnalyzer.analyze(…)` or `.analyze_many(…)`.

        Returns
        -------
        code : str
            The isolated Python source string.
        warnings : List[str]
            Human-readable warnings (e.g., unresolved/unbound names).
        """
        chunks: List[str] = []

        # 1) Imports
        import_lines = self._collect_import_lines(report.get("imports", {}))
        if import_lines:
            chunks.append("\n".join(import_lines))
        report['requirements'] = _extract_package(report)

        # 2) Module variables
        var_lines = self._collect_vars_lines(report.get("vars", {}))
        if var_lines:
            if chunks:  # keep a blank line between sections
                chunks.append("")
            chunks.append("\n".join(var_lines))

        # 3) Definitions (classes first, then functions)
        def_lines = self._collect_def_lines(report.get("def_items", []))
        if def_lines:
            if chunks:
                chunks.append("")
            chunks.append("\n\n".join(def_lines))

        # Warnings (unbound)
        warnings = self._collect_warnings(report.get("unbound", []))
        
        # Header
        if self.header_comment:
            chunks.insert(0, self._render_header(report))

        return "\n".join(chunks).rstrip() + "\n", warnings

    def _render_header(self, report: AttrDefaultDict) -> str:
        """
        Render the header comment block for the isolated snippet.
        Includes entries, section info, and unresolved warnings if present.
        """
        entries = report.get("entries", [])
        entry_labels = ", ".join(
            f"{e.get('module', '?')}.{e.get('name', '?')}" for e in entries
        ) or "<none>"

        unbound = sorted({str(x) for x in report.get("unbound", []) if x})
        warn_line = ""
        if unbound:
            warn_line = f"# WARNINGS: {', '.join(unbound)}\n"
            
        # ---- Requirements from report['imports'] ----
        requirements = report.get("requirements", {}) or {}
        
        if on_pypi := requirements.get("on_pypi", []) or []:
            on_pypi = [i.package_name for i in on_pypi]
        req_line = _format_pip_install(on_pypi)
        
        if unknown := requirements.get("unknown", []) or []:
            unknown = [i.package_name for i in unknown]
        unknown_line = f"# Unresolvable imports: {', '.join(unknown)}\n" if unknown else ""

        return (
            "# ==============================================\n"
            "# Auto-generated isolated snippet\n"
            f"# Entries: {entry_labels}\n"
            "# Sections: imports, variables, definitions\n"
            f"{req_line}"
            f"{unknown_line}"
            f"{warn_line}"
            "# ==============================================\n"
        )

    def _collect_import_lines(self, imports_by_module: Dict[str, Dict[str, ImportItem]]) -> List[str]:
        """
        Flatten and optionally sort import statements. Deduplicate by exact code line.

        We keep dynamic imports only if `keep_dynamic_imports=True`.
        """
        seen: Set[str] = set()
        lines: List[str] = []

        # Flatten: imports_by_module = { module_name: {alias: ImportItem, ...}, ...}
        items: List[ImportItem] = []
        for _modname, alias_map in imports_by_module.items():
            items.extend(alias_map.values())

        # Optional sorting:
        #   1) non-dynamic imports first
        #   2) then by (module, code) to be deterministic
        if self.sort_imports:
            items.sort(key=lambda it: (getattr(it, "is_dynamic", False), str(getattr(it, "module", "")) or "", str(it.code)))

        for imp in items:
            is_dyn = getattr(imp, "is_dynamic", False)
            if is_dyn and not self.keep_dynamic_imports:
                continue
            line = str(imp.code).rstrip()
            if line and line not in seen:
                lines.append(line)
                seen.add(line)

        return lines

    def _collect_vars_lines(self, vars_by_module: Dict[str, List[VarsItem]]) -> List[str]:
        """
        Gather module-level variables in a deterministic order:
        by module name, preserving the order already present in the report.
        """
        lines: List[str] = []
        for module_name in sorted(vars_by_module.keys()):
            for v in vars_by_module[module_name]:
                code = getattr(v, "code", "").rstrip()
                if code:
                    lines.append(code)
        return lines

    def _collect_def_lines(self, def_items: Iterable[DefItem]) -> List[str]:
        """
        Render definitions. Classes first, then functions.
        If `ast.unparse` fails, fall back to the stored `DefItem.code`.
        """
        classes: List[DefItem] = []
        funcs: List[DefItem] = []
        for d in def_items:
            dtype = getattr(d, "type", "")
            if dtype == "class":
                classes.append(d)
            elif dtype == "function":
                funcs.append(d)
            else:
                # Unknown type: keep at the end as a function-like block
                funcs.append(d)

        lines: List[str] = []
        for di in classes:
            lines.append(_unparse_or_fallback(di))
        for di in funcs:
            # Keep a blank line between class and the first function automatically
            if lines and not lines[-1].endswith("\n"):
                pass
            lines.append(_unparse_or_fallback(di))
        return lines

    def _collect_warnings(self, unbound: Iterable[str]) -> List[str]:
        """
        Produce human-readable warnings for unresolved names.
        Print them to stderr and also return them for programmatic use.
        """
        unbound = sorted({str(x) for x in unbound if x})
        if not unbound:
            return []

        warnings = [
            "Unresolved names were detected. You may need to provide them at runtime or inject stubs:",
            "  - " + "\n  - ".join(unbound),
        ]
        for w in warnings:
            print(w, file=sys.stderr)
        return warnings
    
    
def _extract_package(report):
    on_pypi = []
    stdlib = []
    unknown = []
    
    for imported_in_module in report.imports.values():
        for impitem in imported_in_module.values():
            pname = impitem.package_name
            if not is_stdlib(pname):
                if is_on_pypi(pname):
                    on_pypi.append(impitem)
                else:
                    unknown.append(impitem)
            else:
                stdlib.append(impitem)
    return AttrDefaultDict(
        on_pypi = on_pypi,
        stdlib = stdlib,
        unknown = unknown,
    )
    
    
def _format_pip_install(packages, width: int = 80, indent: str = "    ") -> str:
    """
    Format a 'pip install ...' line. If it exceeds width, split with backslashes
    and indent the continuation lines.
    """
    if not packages:
        return ""

    base = "pip install "
    line = base + " ".join(packages)

    if len(line) <= width:
        return f"# Resolve requirements: `{line}`\n"

    wrapped = textwrap.wrap(
        " ".join(packages),
        width=width - len(base) - 2,  # backslash와 indent 여유
        break_long_words=False,
        break_on_hyphens=False
    )

    lines = [base + wrapped[0] + " \\"]
    for chunk in wrapped[1:]:
        lines.append(indent + chunk + " \\")
    lines[-1] = lines[-1].rstrip(" \\")

    return "# Resolve requirements:\n" + "".join(f"#   ```{ln}```\n" for ln in lines)


def _unparse_or_fallback(di: DefItem) -> str:
    node = getattr(di, "node", None)
    if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
        try:
            return ast.unparse(node)
        except Exception:
            pass
    # fallback: whatever the analyzer stored
    return getattr(di, "code", "").strip() or "# <unparseable definition>"
from __future__ import annotations
import ast
import sys
import textwrap
from typing import TYPE_CHECKING, Any, Dict, Iterable, List, Optional, Sequence, Set

from .analyzer import DependencyAnalyzer
from .helper import is_on_pypi, is_stdlib
from .types import AttrDefaultDict, DefItem, ImportItem

if TYPE_CHECKING:
    # This avoids a circular import at runtime
    from .types import VarsItem


class Isolator:
    """
    Generates minimal, self-contained Python source code from target objects.

    This class consumes a report from `DependencyAnalyzer` to produce a single
    string of Python code containing all necessary imports, module-level
    variables, and definitions. It also generates warnings for any unresolved
    (unbound) names.

    The output is structured to be PEP 8 compliant, with imports first,
    followed by variables and then definitions.
    """

    def __init__(
        self,
        analyzer: Optional[DependencyAnalyzer] = None,
        *,
        sort_imports: bool = True,
        keep_dynamic_imports: bool = True,
    ) -> None:
        """
        Initializes the Isolator.

        Args:
            analyzer: An existing `DependencyAnalyzer` instance. If None, a
                new one is created with default settings.
            sort_imports: If True, imports are sorted deterministically for
                stable output.
            keep_dynamic_imports: If True, dynamic imports (e.g., using
                `importlib`) are included in the output.
            header_comment: If True, a descriptive header is prepended to the
                generated code.
        """
        self._analyzer = analyzer or DependencyAnalyzer()
        self.sort_imports = sort_imports
        self.keep_dynamic_imports = keep_dynamic_imports

    # --------------------------------------------------------------------------
    # Public API
    # --------------------------------------------------------------------------

    def isolate(self, targets: Sequence[Any]) -> AttrDefaultDict:
        """
        Analyzes targets and renders the isolated source code.

        Args:
            targets: A sequence of Python objects (functions, classes) to isolate.

        Returns:
            AttrDefaultDict with keys:
                - 'source' (str): The generated, self-contained Python source code.
                - 'warnings' (List[str]): Warning messages (e.g., unresolved names).
                - 'reqs_pypi' (List[str]): PyPI packages inferred from imports.
                - 'reqs_unknown' (List[str]): Non-stdlib packages that are not on PyPI.
                - 'unbound' (List[str]): Unresolved name identifiers.
        """
        report = self._analyzer.analyze_many(list(targets))
        return self.isolate_from_report(report)
        
    def isolate_from_report(self, report: Dict[str, Any]) -> AttrDefaultDict:
        """
        Renders isolated code from a pre-computed analyzer report.

        Args:
            report: The output dictionary from a `DependencyAnalyzer` run.

        Returns:
            AttrDefaultDict with 'source', 'warnings', 'reqs_pypi', 'reqs_unknown', 'unbound'.
        """
        sections: Dict[str, str] = {}

        # 1. Collect Imports
        import_lines = self._collect_import_lines(report.get("imports", {}))

        # Handle type hints from the report
        typehints = report.get("typehints")
        if typehints:
            # Add future import for annotations at the very top.
            import_lines.insert(0, "from __future__ import annotations")
            # Generate 'from typing import ...' for type aliases.
            typehint_imports = []
            for name, asname in typehints.items():
                if name != asname:
                    typehint_imports.append(f'{name} as {asname}')
                else:
                    typehint_imports.append(name)
            import_lines.append('from typing import TYPE_CHECKING')
            import_lines.append('if TYPE_CHECKING:')
            import_lines.append(textwrap.indent(f'from typing import {", ".join(typehint_imports)}','    '))

        if import_lines:
            sections["imports"] = "\n".join(import_lines)

        # 2. Collect Module Variables
        var_lines = self._collect_vars_lines(report.get("vars", {}))
        if var_lines:
            sections["vars"] = "\n".join(var_lines)

        # 3. Collect Definitions
        def_lines = self._collect_def_lines(report.get("def_items", []))
        if def_lines:
            sections["defs"] = "\n\n".join(def_lines)

        # 4. Generate Header and Warnings
        unbound = report.get("unbound", [])
        warnings = self._collect_warnings(unbound)
        # Ensure a consistent output order: imports, then vars, then defs.
        ordered_sections = [
            sections.get("imports", ""),
            sections.get("vars", ""),
            sections.get("defs", ""),
        ]
        final_code = "\n\n".join(filter(None, ordered_sections))
        requirements = self._extract_package_requirements(report)

        reqs_pypi = {item.package_name for item in requirements.get("on_pypi", [])},
        reqs_unknown = {item.package_name for item in requirements.get("unknown", [])},

        return AttrDefaultDict(
            source = final_code.rstrip() + "\n",
            reqs_pypi = sorted(reqs_pypi),
            reqs_unknown = sorted(reqs_unknown),
            unbound = unbound,
            warnings = warnings
        )

    # --------------------------------------------------------------------------
    # Internal Rendering and Collection Logic
    # --------------------------------------------------------------------------

    def _collect_import_lines(
        self, imports_by_module: Dict[str, Dict[str, ImportItem]]
    ) -> List[str]:
        """
        Flattens and optionally sorts import statements.
        """
        seen: Set[str] = set()
        lines: List[str] = []

        items: List[ImportItem] = [
            item for alias_map in imports_by_module.values() for item in alias_map.values()
        ]

        if self.sort_imports:
            items.sort(
                key=lambda it: (
                    getattr(it, "is_dynamic", False),
                    str(getattr(it, "module", "")) or "",
                    str(it.code),
                )
            )

        for imp in items:
            is_dyn = getattr(imp, "is_dynamic", False)
            if is_dyn and not self.keep_dynamic_imports:
                continue
            line = str(imp.code).rstrip()
            if line and line not in seen:
                lines.append(line)
                seen.add(line)

        return lines

    def _collect_vars_lines(self, vars_by_module: Dict[str, List['VarsItem']]) -> List[str]:
        """
        Gathers module-level variable definitions in a deterministic order.
        """
        lines: List[str] = []
        for module_name in sorted(vars_by_module.keys()):
            for var_item in vars_by_module[module_name]:
                code = getattr(var_item, "code", "").rstrip()
                if code:
                    lines.append(code)
        return lines

    def _collect_def_lines(self, def_items: Iterable[DefItem]) -> List[str]:
        """
        Renders definitions, placing classes before functions.
        """
        classes = [d for d in def_items if getattr(d, "type", "") == "class"]
        funcs = [d for d in def_items if getattr(d, "type", "") != "class"]

        lines: List[str] = [self._unparse_or_fallback(di) for di in classes]
        lines.extend(self._unparse_or_fallback(di) for di in funcs)
        
        return lines

    def _collect_warnings(self, unbound: Iterable[str]) -> List[str]:
        """
        Produces human-readable warnings for unresolved names.
        """
        unbound_names = sorted({str(x) for x in unbound if x})
        if not unbound_names:
            return []

        warning_message = (
            "Unresolved names detected. These may need to be provided at runtime "
            "or via stub definitions:\n - " + "\n - ".join(unbound_names)
        )
        print(warning_message, file=sys.stderr)
        return [warning_message]

    # --------------------------------------------------------------------------
    # Static Utility Methods
    # --------------------------------------------------------------------------

    @staticmethod
    def _extract_package_requirements(report: Dict[str, Any]) -> Dict[str, List[ImportItem]]:
        """
        Categorizes third-party imports from a report into PyPI and unknown packages.
        """
        on_pypi, stdlib, unknown = [], [], []
        
        imports_by_module = report.get("imports", {})
        for imported_in_module in imports_by_module.values():
            for impitem in imported_in_module.values():
                pname = getattr(impitem, "package_name", None)
                if not pname:
                    unknown.append(impitem)
                    continue
                if not is_stdlib(pname):
                    if is_on_pypi(pname):
                        on_pypi.append(impitem)
                    else:
                        unknown.append(impitem)
                else:
                    stdlib.append(impitem)

        return AttrDefaultDict(on_pypi=on_pypi, stdlib=stdlib, unknown=unknown)

    @staticmethod
    def _format_pip_install(
        packages: List[str], width: int = 80, indent: str = "    "
    ) -> str:
        """
        Formats a 'pip install' command, wrapping it if it exceeds line width.
        """
        if not packages:
            return ""

        base = "pip install "
        line = base + " ".join(packages)

        if len(line) <= width:
            return f"# Requirements: `{line}`\n"

        # Wrap the package list for readability
        wrapped = textwrap.wrap(
            " ".join(packages),
            width=width - len(base) - 4,  # Adjust for ` # ` and ` \`
            break_long_words=False,
            break_on_hyphens=False,
        )

        lines = [f"{base}{wrapped[0]} \\"]
        lines.extend(f"{indent}{chunk} \\" for chunk in wrapped[1:])
        lines[-1] = lines[-1].rstrip(" \\")

        formatted_block = "\n".join(f"# {ln}" for ln in lines)
        return f"# Requirements:\n{formatted_block}\n"

    @staticmethod
    def _unparse_or_fallback(di: DefItem) -> str:
        """
        Attempts to unparse a definition's AST node, falling back to stored code.
        """
        # Prefer the pruned AST if available to respect collapse behavior
        node = getattr(di, "pruned", None) or getattr(di, "node", None)
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            try:
                return ast.unparse(node)
            except Exception:
                # Fall through to the stored code if unparsing fails
                pass
        return getattr(di, "code", "").strip() or "# <unparseable definition>"

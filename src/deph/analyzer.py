from __future__ import annotations

import ast
import builtins
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Set, Tuple

# Local imports from the project structure
from .helper import PACKAGE_DISTRIBUTIONS, module_classifier
from .parser import get_module_ast
from .types import AttrDefaultDict, DefItem, ImportItem, ModuleCtx, VarsItem
from .visitors import ImportCollector, LowLevelCollector, NameUsageCollector
from .visitors.usage import roots_in_expr

if TYPE_CHECKING:
    from .types import AstDefs

__all__ = [
    "DependencyAnalyzer",
]


class DependencyAnalyzer:
    """
    Analyzes the dependency graph of Python objects.

    This class recursively resolves dependencies for functions, classes, and
    module-level variables, starting from one or more entry-point objects.
    It produces a report detailing all required definitions, imports,
    variables, and any unbound names that could not be resolved.

    Attributes:
        analyze_nested (str): Strategy for analyzing nested functions/classes.
            Can be "all", "referenced_only", or "none".
        collapse_methods (bool): If True, methods are excluded from the report,
            and only their parent class is included.
        collapse_inner_funcs (bool): If True, nested functions are excluded.
        collapse_non_toplevel (bool): If True, only top-level definitions
            (module-level) are included in the final report.
    """

    # --- Class-level Attribute Type Hinting ---
    # Configuration attributes
    analyze_nested: str
    collapse_methods: bool
    collapse_inner_funcs: bool
    collapse_non_toplevel: bool

    # Per-analysis state attributes
    module_ctxs: Dict[Any, ModuleCtx]
    ctx_by_node_id: Dict[int, ModuleCtx]
    required_defs_by_id: Dict[int, DefItem]
    required_imports_by_module: Dict[str, Dict[str, ImportItem]]
    required_vars_by_module: Dict[str, List[VarsItem]]
    unbound: Set[str]
    visited_def_ids: Set[int]
    visited_var_names_by_module: Dict[str, Set[str]]
    emitted_var_names_by_module: Dict[str, Set[str]]

    def __init__(
        self,
        analyze_nested: str = "all",
        collapse_methods: bool = True,
        collapse_inner_funcs: bool = True,
        collapse_non_toplevel: bool = False,
    ):
        """
        Initializes the DependencyAnalyzer with a given configuration.

        Args:
            analyze_nested: Strategy for descending into nested definitions.
            collapse_methods: Whether to exclude methods from the report.
            collapse_inner_funcs: Whether to exclude nested functions.
            collapse_non_toplevel: Whether to only report top-level definitions.
        """
        self.analyze_nested = analyze_nested
        self.collapse_methods = collapse_methods
        self.collapse_inner_funcs = collapse_inner_funcs
        self.collapse_non_toplevel = collapse_non_toplevel

        self._initialize_analysis_state()

    # --------------------------------------------------------------------------
    # Public API
    # --------------------------------------------------------------------------

    def analyze(self, target: Any) -> AttrDefaultDict:
        """
        Performs a dependency analysis on a single target object.

        This is a convenience wrapper for `analyze_many`.

        Args:
            target: The Python object (function, class, etc.) to analyze.

        Returns:
            An AttrDefaultDict containing the analysis report.
        """
        return self.analyze_many([target])

    def analyze_many(self, targets: List[Any]) -> AttrDefaultDict:
        """
        Performs a dependency analysis on a list of target objects.

        This is the main entry point for the analysis. It resets the analyzer's
        state and recursively resolves all dependencies for the provided targets.

        Args:
            targets: A list of Python objects to analyze.

        Returns:
            An AttrDefaultDict containing the analysis report, structured with
            keys for 'entries', 'def_items', 'imports', 'vars', and 'unbound'.

        Raises:
            ValueError: If `targets` is empty or contains an object from an
                        external (stdlib, third-party) module.
        """
        if not targets:
            raise ValueError("targets must be a non-empty list of objects.")

        self._initialize_analysis_state()
        entries = self._build_module_contexts(targets)

        for tname, mname in entries:
            ctx = next(c for c in self.module_ctxs.values() if c.module_name == mname)
            entry_def = ctx.def_by_name[tname]
            self._resolve_def(entry_def, ctx)

        final_def_items = self._filter_final_defs()

        # Clean up empty entries from the report for clarity
        final_imports = {k: v for k, v in self.required_imports_by_module.items() if v}
        final_vars = {k: v for k, v in self.required_vars_by_module.items() if v}

        entries_report = [{"name": name, "module": module} for (name, module) in entries]
        return AttrDefaultDict(
            entries=entries_report,
            def_items=final_def_items,
            imports=final_imports,
            vars=final_vars,
            unbound=sorted(self.unbound),
        )

    # --------------------------------------------------------------------------
    # Internal Core Logic
    # --------------------------------------------------------------------------

    def _initialize_analysis_state(self) -> None:
        """Resets all state attributes for a fresh analysis run."""
        self.module_ctxs = {}
        self.ctx_by_node_id = {}
        self.required_defs_by_id = {}
        self.required_imports_by_module = {}
        self.required_vars_by_module = {}
        self.unbound = set()
        self.visited_def_ids = set()
        self.visited_var_names_by_module = {}
        self.emitted_var_names_by_module = {}

    def _build_module_contexts(self, targets: List[Any]) -> List[Tuple[str, str]]:
        """
        Builds and indexes ModuleCtx objects for all modules related to the targets.

        This method populates the analyzer's state with context information
        for each module, preventing redundant parsing and analysis.

        Args:
            targets: The list of entry-point objects.

        Returns:
            A list of (name, module_name) tuples for each entry point.
        """
        entries: List[Tuple[str, str]] = []
        for t in targets:
            _, mod = get_module_ast(t)

            kind = module_classifier(mod, packages_dists=PACKAGE_DISTRIBUTIONS)
            if kind in ("stdlib", "thirdparty", "builtin", "extension"):
                tname = getattr(t, "__name__", type(t).__name__)
                mname = getattr(mod, "__name__", repr(mod))
                raise ValueError(
                    f"Entry target '{tname}' belongs to external module '{mname}' ({kind}). "
                    "Pass a symbol from your own module."
                )

            if mod not in self.module_ctxs:
                self.module_ctxs[mod] = self._build_module_ctx_from_target(t)
            ctx = self.module_ctxs[mod]

            tname = getattr(t, "__name__", None)
            if tname is None:
                raise ValueError(f"Target {t} must have a __name__.")

            if tname not in ctx.def_by_name:
                alt = tname.split(".")[-1]
                tname = alt if alt in ctx.def_by_name else tname
                if tname not in ctx.def_by_name:
                     raise ValueError(f"Target '{tname}' not found in module {ctx.module_name}.")
            
            entries.append((tname, ctx.module_name))

        for ctx in self.module_ctxs.values():
            mname = ctx.module_name
            self.visited_var_names_by_module.setdefault(mname, set())
            self.emitted_var_names_by_module.setdefault(mname, set())
            for nid in ctx.def_by_id:
                self.ctx_by_node_id[nid] = ctx

        return entries

    def _resolve_def(self, d: DefItem, ctx: ModuleCtx) -> None:
        """
        Recursively resolves all dependencies for a given `DefItem`.

        Args:
            d: The `DefItem` (a function or class definition) to resolve.
            ctx: The `ModuleCtx` of the module containing the definition.
        """
        did = id(d.node)
        if did in self.visited_def_ids:
            return
        self.visited_def_ids.add(did)
        self.required_defs_by_id[did] = d

        # If 'd' is a method, ensure its parent class is also resolved.
        if self._is_method(d):
            if parent_id := self._parent_id_of(d):
                self._resolve_def(ctx.def_by_id[parent_id], ctx)

        self._ensure_buckets_for_ctx(ctx)

        # Always check for and include __future__ imports from the source module
        # as they affect how type hints are parsed.
        for alias, imp_item in ctx.imported.items():
            if imp_item.module == "__future__":
                self.required_imports_by_module[ctx.module_name][alias] = imp_item

        unbound_names = self._extract_unbound_names(d.node)
        self._resolve_pending_names(unbound_names, ctx)

        self._resolve_nested_defs(d, ctx)
        self.unbound.update(unbound_names)

    def _resolve_var(self, var_name: str, ctx: ModuleCtx) -> None:
        """
        Resolves the Right-Hand Side (RHS) dependencies of a module-level variable.

        Args:
            var_name: The name of the variable to resolve.
            ctx: The `ModuleCtx` of the module containing the variable.
        """
        module = ctx.module_name
        if var_name in self.visited_var_names_by_module[module]:
            return
        self.visited_var_names_by_module[module].add(var_name)

        expr = ctx.module_var_exprs.get(var_name)
        if expr is None:
            return

        roots = roots_in_expr(expr)
        if not roots and isinstance(expr, ast.Name) and isinstance(expr.ctx, ast.Load):
            roots = {expr.id}
        if not roots:
            return

        pending = set(roots)
        self._ensure_buckets_for_ctx(ctx)
        self._resolve_pending_names(pending, ctx)
        self.unbound.update(pending)

    def _resolve_pending_names(self, pending: Set[str], ctx: ModuleCtx) -> None:
        """
        Shared helper to resolve a set of unbound names against the module context.

        It attempts to satisfy names by checking for imports, other module
        variables, or other definitions within the same module. The `pending`
        set is modified in-place.

        Args:
            pending: A set of string names to resolve.
            ctx: The `ModuleCtx` to resolve against.
        """
        module = ctx.module_name
        for name in list(pending):
            # Special case: Ignore names from the 'typing' module that might be
            # picked up from type hints but are not real dependencies.
            # This handles cases like `_GenericAlias` being pulled in.
            if name in getattr(ctx.module_obj, "__dict__", {}):
                obj = getattr(ctx.module_obj, name, None)
                if getattr(obj, "__module__", "") == "typing":
                    pending.discard(name)
                    continue
            if name in ctx.imported:
                self.required_imports_by_module[module][name] = ctx.imported[name]
                pending.discard(name)
            elif name in ctx.module_vars_map:
                self._add_var_and_resolve(name, ctx)
                pending.discard(name)
            elif name in ctx.def_by_name:
                self._resolve_def(ctx.def_by_name[name], ctx)
                pending.discard(name)

    def _add_var_and_resolve(self, name: str, ctx: ModuleCtx) -> None:
        """
        Adds a `VarsItem` to the report and triggers resolution of its dependencies.

        This method ensures a variable is added to the report only once per module.

        Args:
            name: The name of the variable.
            ctx: The relevant `ModuleCtx`.
        """
        module = ctx.module_name
        self._ensure_buckets_for_ctx(ctx)

        if name not in self.emitted_var_names_by_module[module]:
            self.emitted_var_names_by_module[module].add(name)
            vi = ctx.module_vars_map[name]
            self.required_vars_by_module[module].append(vi)
        
        self._resolve_var(name, ctx)

    def _resolve_nested_defs(self, d: DefItem, ctx: ModuleCtx) -> None:
        """
        Handles the resolution of nested definitions based on the `analyze_nested` strategy.

        Args:
            d: The parent `DefItem` containing the nested definitions.
            ctx: The relevant `ModuleCtx`.
        """
        nested_children = list(d.function_defs) + list(d.class_defs)
        if not nested_children:
            return

        to_descend = []
        if self.analyze_nested == "all":
            to_descend = nested_children
        elif self.analyze_nested == "referenced_only":
            parent_loads = self._local_load_names(d.node)
            to_descend = [ch for ch in nested_children if ch.name in parent_loads]

        for ch in to_descend:
            self._resolve_def(ch, ctx)

    def _filter_final_defs(self) -> List[DefItem]:
        """
        Applies post-filters to the resolved `DefItem`s based on configuration.

        Returns:
            A final, filtered list of `DefItem` objects for the report.
        """
        defs = list(self.required_defs_by_id.values())
        if self.collapse_methods:
            defs = [d for d in defs if not self._is_method(d)]
        if self.collapse_inner_funcs:
            defs = [d for d in defs if not self._is_inner_func(d)]
        if self.collapse_non_toplevel:
            defs = [d for d in defs if self._is_toplevel(d)]
        return defs

    # --------------------------------------------------------------------------
    # Internal Helpers & Predicates
    # --------------------------------------------------------------------------

    def _parent_id_of(self, d: DefItem) -> Optional[int]:
        """Retrieves the AST node ID of a `DefItem`'s parent."""
        ctx = self.ctx_by_node_id.get(id(d.node))
        return ctx.parent_of.get(id(d.node)) if ctx else None

    def _is_method(self, d: DefItem) -> bool:
        """Checks if a `DefItem` is a method within a class."""
        pid = self._parent_id_of(d)
        if pid is None: return False
        parent_ctx = self.ctx_by_node_id.get(pid)
        if not parent_ctx: return False
        parent = parent_ctx.def_by_id.get(pid)
        return parent is not None and parent.type == "class" and d.type == "function"

    def _is_inner_func(self, d: DefItem) -> bool:
        """Checks if a `DefItem` is a nested function."""
        pid = self._parent_id_of(d)
        if pid is None: return False
        parent_ctx = self.ctx_by_node_id.get(pid)
        if not parent_ctx: return False
        parent = parent_ctx.def_by_id.get(pid)
        return parent is not None and parent.type == "function" and d.type == "function"

    def _is_toplevel(self, d: DefItem) -> bool:
        """Checks if a `DefItem` is at the top level of a module."""
        return self._parent_id_of(d) is None

    def _ensure_buckets_for_ctx(self, ctx: ModuleCtx) -> None:
        """Ensures that result dictionaries have entries for the given module."""
        mname = ctx.module_name
        self.required_imports_by_module.setdefault(mname, {})
        self.required_vars_by_module.setdefault(mname, [])

    # --------------------------------------------------------------------------
    # Static Utility Methods
    # --------------------------------------------------------------------------

    @staticmethod
    def _build_module_ctx_from_target(target: Any) -> ModuleCtx:
        """Constructs a `ModuleCtx` object from a target object."""
        tree, mod = get_module_ast(target)
        ll = LowLevelCollector(tree)
        imported = ImportCollector(ll.pruned).imported
        def_by_id, parent_of, def_by_name = DependencyAnalyzer._index_defitems_with_parents(ll.defs)
        
        known_def_names = set(def_by_name.keys())
        known_import_aliases = set(imported.keys())
        module_vars_map, module_var_exprs = DependencyAnalyzer._collect_module_vars_with_exprs(
            ll.pruned, known_def_names, known_import_aliases
        )

        return ModuleCtx(
            module_name=getattr(mod, "__name__", repr(mod)),
            module_obj=mod,
            toplevel=ll.pruned,
            def_by_id=def_by_id,
            parent_of=parent_of,
            def_by_name=def_by_name,
            imported=imported,
            module_vars_map=module_vars_map,
            module_var_exprs=module_var_exprs,
        )

    @staticmethod
    def _index_defitems_with_parents(defitems: List[DefItem]) -> Tuple[Dict[int, DefItem], Dict[int, int], Dict[str, DefItem]]:
        """Builds indices for DefItems by id, name, and parent relationship."""
        by_id, parent_of, by_name = {}, {}, {}
        
        def _walk(d: DefItem, parent: Optional[DefItem]):
            nid = id(d.node)
            if nid not in by_id:
                by_id[nid] = d
                # Always overwrite to ensure the last definition seen in the source
                # is the one that gets used, matching Python's behavior.
                by_name[d.name] = d
            if parent:
                parent_of[nid] = id(parent.node)
            for ch in list(d.function_defs) + list(d.class_defs):
                _walk(ch, d)

        for d in defitems:
            _walk(d, None)
        return by_id, parent_of, by_name
    
    @staticmethod
    def _collect_module_vars_with_exprs(
        toplevel: ast.Module, known_def_names: Set[str], known_import_aliases: Set[str]
    ) -> Tuple[Dict[str, VarsItem], Dict[str, Optional[ast.AST]]]:
        """Collects module-level variables not overshadowed by defs or imports."""
        vars_map, var_exprs = {}, {}
        statement_fields = ['body', 'orelse', 'handlers', 'finalbody']

        def handle_assign(name: str, value: Optional[ast.AST], node: ast.AST):
            if name in known_def_names or name in known_import_aliases or name in vars_map:
                return
            vars_map[name] = VarsItem(
                name=name, code=DependencyAnalyzer._safe_unparse(node),
                value_kind=DependencyAnalyzer._value_kind(value),
            )
            var_exprs[name] = value

        def walk_stmts(stmts: List[ast.stmt]):
            for node in stmts:
                if isinstance(node, ast.Assign):
                    for tgt in node.targets:
                        if isinstance(tgt, ast.Name): handle_assign(tgt.id, node.value, node)
                elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                    handle_assign(node.target.id, node.value, node)
                elif isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name):
                    handle_assign(node.target.id, None, node)
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    continue
                
                for field in statement_fields:
                    if content := getattr(node, field, None):
                        if isinstance(content, list):
                            walk_stmts(content)

        walk_stmts(toplevel.body)
        return vars_map, var_exprs

    @staticmethod
    def _extract_unbound_names(def_node: AstDefs) -> Set[str]:
        """Computes the set of unbound root names used in a def/class node."""
        coll = NameUsageCollector()
        if isinstance(def_node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            coll.visit_arguments(def_node.args)
        for n in def_node.body:
            coll.visit(n)

        header_roots = set()
        if isinstance(def_node, ast.ClassDef):
            for b in def_node.bases:
                header_roots.update(NameUsageCollector.root_names_in_expr(b))
            for kw in def_node.keywords or []:
                if kw.arg is not None:
                    header_roots.update(NameUsageCollector.root_names_in_expr(kw.value))
            for dec in def_node.decorator_list:
                header_roots.update(NameUsageCollector.root_names_in_expr(dec))
        
        builtin_names = {n for n in dir(builtins) if not n.startswith("_")}
        ignore_names = {"self", "cls"}
        
        parent_candidates = (coll.loads | coll.attr_roots | header_roots) - (coll.local_stores | coll.params)
        parent_unbound = {n for n in parent_candidates if n not in builtin_names and n not in ignore_names}

        nested_unbound = set()
        for n in def_node.body:
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                nested_unbound.update(DependencyAnalyzer._extract_unbound_names(n))
        
        return parent_unbound | nested_unbound

    @staticmethod
    def _local_load_names(def_node: AstDefs) -> Set[str]:
        """Collects locally loaded bare Name identifiers in the body of a def/class node."""
        loads = set()
        class V(ast.NodeVisitor):
            def visit_Name(self, n: ast.Name):
                if isinstance(n.ctx, ast.Load):
                    loads.add(n.id)
        for n in def_node.body:
            V().visit(n)
        return loads

    @staticmethod
    def _value_kind(expr: Optional[ast.AST]) -> str:
        """Classifies the RHS expression kind for `VarsItem` metadata."""
        if expr is None: return "other"
        if isinstance(expr, (ast.Constant, ast.Tuple, ast.List, ast.Dict, ast.Set)): return "literal"
        if isinstance(expr, ast.Call): return "call"
        if isinstance(expr, ast.Attribute): return "attr"
        if isinstance(expr, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)): return "comprehension"
        return "other"

    @staticmethod
    def _safe_unparse(node: ast.AST) -> str:
        """A wrapper for `ast.unparse` that fails gracefully."""
        try:
            return ast.unparse(node)
        except Exception:
            return "<unparseable>"
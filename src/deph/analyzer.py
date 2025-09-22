from __future__ import annotations
import ast
import builtins
from typing import TYPE_CHECKING
from .parser import get_module_ast
from .visitors import LowLevelCollector, ImportCollector, NameUsageCollector
from .visitors.usage import roots_in_expr
from .helper import module_classifier, PACKAGE_DISTRIBUTIONS
from .types import AttrDefaultDict, ImportItem, DefItem, VarsItem, ModuleCtx

if TYPE_CHECKING:
    from typing import List, Dict, Optional, Set, Any, Tuple
    from .types import AstDefs


__all__ = [
    "DependencyAnalyzer",   
]


# Public API
class DependencyAnalyzer:
    """
    Multi-entry dependency analyzer that resolves, recursively:
      - DefItems
      - ImportItems
      - VarsItems
      - Unbound names

    Parameters
    ----------
    analyze_nested : {"all", "referenced_only", "none"}
        Whether to descend into nested defs.
    collapse_methods : bool
        If True, method defs inside classes are excluded from the final report.
    collapse_inner_funcs : bool
        If True, function defs nested inside functions are excluded.
    collapse_non_toplevel : bool
        If True, only top-level defs are included.
    """
    def __init__(self,
                 analyze_nested: str = "all",
                 collapse_methods: bool = True,
                 collapse_inner_funcs: bool = True,
                 collapse_non_toplevel: bool = False,
                 ):
        self.analyze_nested = analyze_nested
        self.collapse_methods = collapse_methods
        self.collapse_inner_funcs = collapse_inner_funcs
        self.collapse_non_toplevel = collapse_non_toplevel

    def analyze(self, target: Any) -> AttrDefaultDict:
        return self.analyze_many([target])

    def analyze_many(self, targets: List[Any]) -> AttrDefaultDict:
        if not targets:
            raise ValueError("targets must be a non-empty list of objects.")

        # (1) Build/reuse module contexts
        module_ctxs, entries = _build_module_contexts_for_targets(targets)

        # (2) Global accumulators
        required_defs_by_id: Dict[int, "DefItem"] = {}
        required_imports_by_module: Dict[str, Dict[str, "ImportItem"]] = {}
        required_vars_by_module: Dict[str, List[VarsItem]] = {}
        unbound: Set[str] = set()

        # track vars we've already appended to the report, per module
        emitted_var_names_by_module: Dict[str, Set[str]] = {
            ctx.module_name: set() for ctx in module_ctxs.values()
        }

        # node-id -> ctx map
        ctx_by_node_id: Dict[int, ModuleCtx] = {}
        for ctx in module_ctxs.values():
            for nid in ctx.def_by_id.keys():
                ctx_by_node_id[nid] = ctx

        visited_def_ids: Set[int] = set()
        visited_var_names_by_module: Dict[str, Set[str]] = {
            ctx.module_name: set() for ctx in module_ctxs.values()
        }

        # (3) Resolve each entry def
        for tname, mname in entries:
            ctx = next(c for c in module_ctxs.values() if c.module_name == mname)
            entry_def = ctx.def_by_name[tname]
            _resolve_def(
                d=entry_def,
                ctx=ctx,
                analyze_nested=self.analyze_nested,
                required_defs_by_id=required_defs_by_id,
                required_imports_by_module=required_imports_by_module,
                required_vars_by_module=required_vars_by_module,
                unbound=unbound,
                visited_def_ids=visited_def_ids,
                visited_var_names_by_module=visited_var_names_by_module,
                emitted_var_names_by_module=emitted_var_names_by_module,   # <<< FIX
            )

        # (4) Final report: filtering
        final_def_items = _filter_final_defs(
            required_defs_by_id=required_defs_by_id,
            ctx_by_node_id=ctx_by_node_id,
            collapse_methods=self.collapse_methods,
            collapse_inner_funcs=self.collapse_inner_funcs,
            collapse_non_toplevel=self.collapse_non_toplevel,
        )

        entries_report = [{"name": name, "module": module} for (name, module) in entries]

        report = AttrDefaultDict(
            entries = entries_report,
            def_items = final_def_items,
            imports = required_imports_by_module,  # by module: alias -> ImportItem
            vars = required_vars_by_module,        # by module: List[VarsItem]
            unbound = sorted(unbound))
        return report



# internal
def _build_module_contexts_for_targets(
    targets: List[Any],
) -> Tuple[Dict[Any, ModuleCtx], List[Tuple[str, str]]]:
    """
    Build/collect ModuleCtx objects for all target objects and return:
      - module_ctxs: map of module object -> ModuleCtx
      - entries: list of (entry_name, module_name) pairs
    """
    module_ctxs: Dict[Any, ModuleCtx] = {}
    entries: List[Tuple[str, str]] = []

    for t in targets:
        _, mod = get_module_ast(t)
        
        kind = module_classifier(mod, packages_dists=PACKAGE_DISTRIBUTIONS)
        if kind in ("stdlib", "thirdparty", "builtin", "extension"):
            tname = getattr(t, "__name__", type(t).__name__)
            mname = getattr(mod, "__name__", repr(mod))
            msg = (f"Entry target '{tname}' belongs to external module '{mname}' ({kind}). "
                   "Pass a symbol from your own module (e.g., a wrapper def), "
                   "or use a variable-entry API if you expose one.")
            raise ValueError(msg)

        if mod not in module_ctxs:
            module_ctxs[mod] = _build_module_ctx_from_target(t)
        ctx = module_ctxs[mod]

        tname = getattr(t, "__name__", None)
        if tname is None:
            raise ValueError(f"Target {t} must have a __name__.")

        # If fully-qualified __name__ isn't present as a def, try its basename.
        if tname not in ctx.def_by_name:
            alt = tname.split(".")[-1]
            if alt in ctx.def_by_name:
                tname = alt
            else:
                raise ValueError(
                    f"Target '{tname}' not found among DefItems in module {ctx.module_name}."
                )
        entries.append((tname, ctx.module_name))

    return module_ctxs, entries


def _filter_final_defs(
    required_defs_by_id: Dict[int, "DefItem"],
    ctx_by_node_id: Dict[int, ModuleCtx],
    collapse_methods: bool,
    collapse_inner_funcs: bool,
    collapse_non_toplevel: bool,
) -> List["DefItem"]:
    """
    Apply post-filters (collapse methods, inner functions, non-toplevel) to the
    resolved DefItems.
    """
    def _parent_id_of(d: "DefItem") -> Optional[int]:
        ctx = ctx_by_node_id.get(id(d.node))
        if not ctx:
            return None
        return ctx.parent_of.get(id(d.node))

    def _is_method(d: "DefItem") -> bool:
        pid = _parent_id_of(d)
        if pid is None:
            return False
        ctx = ctx_by_node_id[pid]
        parent = ctx.def_by_id[pid]
        return parent.type == "class" and d.type == "function"

    def _is_inner_func(d: "DefItem") -> bool:
        pid = _parent_id_of(d)
        if pid is None:
            return False
        ctx = ctx_by_node_id[pid]
        parent = ctx.def_by_id[pid]
        return parent.type == "function" and d.type == "function"

    def _is_toplevel(d: "DefItem") -> bool:
        return _parent_id_of(d) is None

    final_def_items = list(required_defs_by_id.values())
    if collapse_methods:
        final_def_items = [d for d in final_def_items if not _is_method(d)]
    if collapse_inner_funcs:
        final_def_items = [d for d in final_def_items if not _is_inner_func(d)]
    if collapse_non_toplevel:
        final_def_items = [d for d in final_def_items if _is_toplevel(d)]
    return final_def_items


def _resolve_def(
    d: "DefItem",
    ctx: ModuleCtx,
    analyze_nested: str,
    required_defs_by_id: Dict[int, "DefItem"],
    required_imports_by_module: Dict[str, Dict[str, "ImportItem"]],
    required_vars_by_module: Dict[str, List[VarsItem]],
    unbound: Set[str],
    visited_def_ids: Set[int],
    visited_var_names_by_module: Dict[str, Set[str]],
    emitted_var_names_by_module: Dict[str, Set[str]],
) -> None:
    """
    Resolve a single DefItem `d` within module `ctx`:

    Steps
    -----
    1) Collect unbound names used by `d` (including nested headers).
    2) Satisfy via:
       - module imports,
       - module-level variables (resolve RHS recursively),
       - same-module defs (resolve recursively).
    3) Optionally descend into nested defs/classes based on `analyze_nested`.
    4) Any remaining names are appended to global `unbound`.

    Parameters
    ----------
    analyze_nested : {"all", "referenced_only", "none"}
        Controls whether/which nested defs/classes are traversed.
    emitted_var_names_by_module : Dict[str, Set[str]]
        Names of module variables that have already been appended to the report
        per module, to prevent duplicate VarsItem entries.
    """
    # 0) De-dup: skip if this def node has already been resolved
    did = id(d.node)
    if did in visited_def_ids:
        return
    visited_def_ids.add(did)

    # Track this def as required
    required_defs_by_id[did] = d

    # Ensure buckets exist for this module
    _ensure_import_bucket(required_imports_by_module, ctx)
    _ensure_vars_bucket(required_vars_by_module, ctx)

    # (a) Collect external dependency candidates for this def
    unb = _extract_unbound_names(d.node)

    # (b) Satisfy through module imports
    for name in sorted(list(unb)):
        if name in ctx.imported:
            required_imports_by_module[ctx.module_name][name] = ctx.imported[name]
            unb.discard(name)

    # (c) Satisfy through module-level variables (+ resolve their RHS recursively)
    for name in sorted(list(unb)):
        if name in ctx.module_vars_map:
            _add_var_and_resolve(
                ctx=ctx,
                name=name,
                required_vars_by_module=required_vars_by_module,
                required_imports_by_module=required_imports_by_module,
                visited_var_names_by_module=visited_var_names_by_module,
                unbound=unbound,
                required_defs_by_id=required_defs_by_id,
                visited_def_ids=visited_def_ids,
                analyze_nested=analyze_nested,
                emitted_var_names_by_module=emitted_var_names_by_module,  # keep shared dedup state
            )
            unb.discard(name)

    # (d) Satisfy through same-module defs (recursive)
    for name in sorted(list(unb)):
        if name in ctx.def_by_name:
            _resolve_def(
                d=ctx.def_by_name[name],
                ctx=ctx,
                analyze_nested=analyze_nested,
                required_defs_by_id=required_defs_by_id,
                required_imports_by_module=required_imports_by_module,
                required_vars_by_module=required_vars_by_module,
                unbound=unbound,
                visited_def_ids=visited_def_ids,
                visited_var_names_by_module=visited_var_names_by_module,
                emitted_var_names_by_module=emitted_var_names_by_module,
            )
            unb.discard(name)

    # Remaining unresolved names (so far)
    unresolved_after = set(unb)

    # (e) Optionally descend into nested defs/classes
    nested_children = list(d.function_defs) + list(d.class_defs)
    if nested_children:
        if analyze_nested == "all":
            to_descend = nested_children
        elif analyze_nested == "referenced_only":
            # only descend into nested defs whose names are actually loaded in parent body
            parent_loads = _local_load_names(d.node)
            to_descend = [ch for ch in nested_children if ch.name in parent_loads]
        else:
            to_descend = []

        for ch in to_descend:
            _resolve_def(
                d=ch,
                ctx=ctx,
                analyze_nested=analyze_nested,
                required_defs_by_id=required_defs_by_id,
                required_imports_by_module=required_imports_by_module,
                required_vars_by_module=required_vars_by_module,
                unbound=unbound,
                visited_def_ids=visited_def_ids,
                visited_var_names_by_module=visited_var_names_by_module,
                emitted_var_names_by_module=emitted_var_names_by_module,
            )

    # (f) Add unresolved names to the global set
    unbound.update(unresolved_after)


def _add_var_and_resolve(
    ctx: ModuleCtx,
    name: str,
    required_vars_by_module: Dict[str, List[VarsItem]],
    required_imports_by_module: Dict[str, Dict[str, "ImportItem"]],
    visited_var_names_by_module: Dict[str, Set[str]],
    unbound: Set[str],
    *,
    required_defs_by_id: Dict[int, "DefItem"],
    visited_def_ids: Set[int],
    analyze_nested: str,
    emitted_var_names_by_module: Dict[str, Set[str]],
) -> None:
    """
    Append a module-level variable (VarsItem) to the report (deduped), then
    resolve its RHS dependencies recursively.

    Dedup rules
    -----------
    - Per module, a var name is appended to the report at most once.
    - Even if already appended, we still call `_resolve_var` to ensure
      dependencies are fully traversed.
    """
    module = ctx.module_name
    _ensure_vars_bucket(required_vars_by_module, ctx)

    # If we've already appended this var name for the module, skip appending but still resolve.
    if name in emitted_var_names_by_module.setdefault(module, set()):
        _resolve_var(
            ctx=ctx,
            var_name=name,
            required_vars_by_module=required_vars_by_module,
            required_imports_by_module=required_imports_by_module,
            visited_var_names_by_module=visited_var_names_by_module,
            unbound=unbound,
            required_defs_by_id=required_defs_by_id,
            visited_def_ids=visited_def_ids,
            analyze_nested=analyze_nested,
            emitted_var_names_by_module=emitted_var_names_by_module,
        )
        return

    # Append once
    vi = ctx.module_vars_map[name]
    required_vars_by_module[module].append(vi)
    emitted_var_names_by_module[module].add(name)

    # And resolve its RHS dependencies
    _resolve_var(
        ctx=ctx,
        var_name=name,
        required_vars_by_module=required_vars_by_module,
        required_imports_by_module=required_imports_by_module,
        visited_var_names_by_module=visited_var_names_by_module,
        unbound=unbound,
        required_defs_by_id=required_defs_by_id,
        visited_def_ids=visited_def_ids,
        analyze_nested=analyze_nested,
        emitted_var_names_by_module=emitted_var_names_by_module,
    )


def _resolve_var(
    ctx: ModuleCtx,
    var_name: str,
    required_vars_by_module: Dict[str, List[VarsItem]],
    required_imports_by_module: Dict[str, Dict[str, "ImportItem"]],
    visited_var_names_by_module: Dict[str, Set[str]],
    unbound: Set[str],
    *,
    required_defs_by_id: Dict[int, "DefItem"],
    visited_def_ids: Set[int],
    analyze_nested: str,
    emitted_var_names_by_module: Dict[str, Set[str]],
) -> None:
    """
    Resolve RHS dependencies of a module-level variable:
      1) imports (by alias)
      2) other module vars (recursive)
      3) same-module defs (recursive)
      4) any leftovers -> global `unbound`

    Re-entrancy/loops:
      - Uses `visited_var_names_by_module[module]` to avoid infinite recursion.
    """
    module = ctx.module_name
    visited = visited_var_names_by_module.setdefault(module, set())
    if var_name in visited:
        return
    visited.add(var_name)

    expr = ctx.module_var_exprs.get(var_name)
    if expr is None:
        # e.g., AugAssign or annotated assign without a value
        return

    roots = roots_in_expr(expr)

    # Defensive fallback: if someone changes roots_in_expr and it misses bare Name(load)
    import ast as _ast
    if not roots and isinstance(expr, _ast.Name) and isinstance(expr.ctx, _ast.Load):
        roots = {expr.id}

    if not roots:
        return

    _ensure_import_bucket(required_imports_by_module, ctx)
    _ensure_vars_bucket(required_vars_by_module, ctx)

    pending: Set[str] = set(roots)

    # (1) resolve via module imports
    for r in list(pending):
        if r in ctx.imported:
            required_imports_by_module[module][r] = ctx.imported[r]
            pending.discard(r)

    # (2) resolve via other module-level vars (recursive)
    for r in list(pending):
        if r in ctx.module_vars_map:
            _add_var_and_resolve(
                ctx=ctx,
                name=r,
                required_vars_by_module=required_vars_by_module,
                required_imports_by_module=required_imports_by_module,
                visited_var_names_by_module=visited_var_names_by_module,
                unbound=unbound,
                required_defs_by_id=required_defs_by_id,
                visited_def_ids=visited_def_ids,
                analyze_nested=analyze_nested,
                emitted_var_names_by_module=emitted_var_names_by_module,
            )
            pending.discard(r)

    # (3) resolve via same-module defs (recursive)
    for r in list(pending):
        if r in ctx.def_by_name:
            _resolve_def(
                d=ctx.def_by_name[r],
                ctx=ctx,
                analyze_nested=analyze_nested,
                required_defs_by_id=required_defs_by_id,
                required_imports_by_module=required_imports_by_module,
                required_vars_by_module=required_vars_by_module,
                unbound=unbound,
                visited_def_ids=visited_def_ids,
                visited_var_names_by_module=visited_var_names_by_module,
                emitted_var_names_by_module=emitted_var_names_by_module,
            )
            pending.discard(r)

    # (4) anything left is unbound (e.g., globals not in imports/vars/defs)
    unbound.update(pending)




def _ensure_import_bucket(required_imports_by_module: Dict[str, Dict[str, "ImportItem"]], ctx: ModuleCtx) -> None:
    """Ensure the import bucket exists for a module."""
    if ctx.module_name not in required_imports_by_module:
        required_imports_by_module[ctx.module_name] = {}


def _ensure_vars_bucket(required_vars_by_module: Dict[str, List[VarsItem]], ctx: ModuleCtx) -> None:
    """Ensure the vars bucket exists for a module."""
    if ctx.module_name not in required_vars_by_module:
        required_vars_by_module[ctx.module_name] = []


def _local_load_names(def_node: AstDefs) -> Set[str]:
    """
    Collect locally loaded bare Name identifiers in the body of a def/class node.
    Used to decide which nested children to descend into when analyze_nested="referenced_only".
    """
    loads: Set[str] = set()

    class V(ast.NodeVisitor):
        def visit_Name(self, n: ast.Name):
            if isinstance(n.ctx, ast.Load):
                loads.add(n.id)

    for n in def_node.body:
        V().visit(n)
    return loads


def _extract_unbound_names(def_node: AstDefs) -> Set[str]:
    """
    Compute the set of unbound root names used in a def/class node:
      - Parameters are local.
      - Names assigned locally are excluded.
      - Builtins and common implicit names ('self', 'cls') are excluded.
      - For classes, also consider bases/keywords/decorators.
      - Recurse into nested defs to include their external dependencies.
    """
    coll = NameUsageCollector()

    # 1) Localize parameters
    if isinstance(def_node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        coll.visit_arguments(def_node.args)

    # 2) Traverse body; nested defs/classes only register their names
    for n in def_node.body:
        coll.visit(n)

    # 3) Class header roots (bases, keywords, decorators)
    header_roots: Set[str] = set()
    if isinstance(def_node, ast.ClassDef):
        for b in def_node.bases:
            header_roots |= NameUsageCollector.root_names_in_expr(b)
        for kw in def_node.keywords or []:
            if kw.arg is not None:
                header_roots |= NameUsageCollector.root_names_in_expr(kw.value)
        for dec in def_node.decorator_list:
            header_roots |= NameUsageCollector.root_names_in_expr(dec)

    builtin_names = {n for n in dir(builtins) if not n.startswith("_")}
    ignore_names = {"self", "cls"}

    parent_candidates = (coll.loads | coll.attr_roots | header_roots) - coll.local_stores - coll.params
    parent_unbound = {n for n in parent_candidates if n not in builtin_names and n not in ignore_names}

    # 4) Union with nested defs' external dependencies
    nested_unbound: Set[str] = set()
    for n in def_node.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            nested_unbound |= _extract_unbound_names(n)

    return parent_unbound | nested_unbound


def _collect_module_vars_with_exprs(
    toplevel: ast.Module,
    known_def_names: Set[str],
    known_import_aliases: Set[str],
) -> Tuple[Dict[str, VarsItem], Dict[str, Optional[ast.AST]]]:
    """
    Collect module-level variables (Assign/AnnAssign/AugAssign) that are not
    overshadowed by defs or imports. Return:
      - vars_map: name -> VarsItem
      - var_exprs: name -> value AST (or None for AugAssign)
    """
    vars_map: Dict[str, VarsItem] = {}
    var_exprs: Dict[str, Optional[ast.AST]] = {}

    def handle_assign(name: str, value: Optional[ast.AST], node: ast.AST):
        if name in known_def_names or name in known_import_aliases:
            return
        if name in vars_map:
            return
        vars_map[name] = VarsItem(
            name=name,
            code=_safe_unparse(node),
            value_kind=_value_kind(value),
        )
        var_exprs[name] = value

    def walk_stmts(stmts: List[ast.stmt]):
        for node in stmts:
            if isinstance(node, ast.Assign):
                for tgt in node.targets:
                    if isinstance(tgt, ast.Name):
                        handle_assign(tgt.id, node.value, node)
            elif isinstance(node, ast.AnnAssign):
                if isinstance(node.target, ast.Name):
                    handle_assign(node.target.id, node.value, node)
            elif isinstance(node, ast.AugAssign):
                if isinstance(node.target, ast.Name):
                    handle_assign(node.target.id, None, node)

            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                # Skip bodies of nested defs for module-var collection
                continue

            elif isinstance(node, ast.If):
                walk_stmts(node.body); walk_stmts(node.orelse)
            elif isinstance(node, ast.Try):
                walk_stmts(node.body)
                for h in node.handlers: walk_stmts(h.body)
                walk_stmts(node.orelse); walk_stmts(node.finalbody)
            elif isinstance(node, ast.With):
                walk_stmts(node.body)
            elif isinstance(node, (ast.For, ast.AsyncFor, ast.While)):
                walk_stmts(node.body); walk_stmts(node.orelse)
            # Add more block types as needed

    walk_stmts(toplevel.body)
    return vars_map, var_exprs


def _value_kind(expr: Optional[ast.AST]) -> str:
    """Classify the RHS expression kind for VarsItem metadata."""
    if expr is None:
        return "other"
    if isinstance(expr, (ast.Constant, ast.Tuple, ast.List, ast.Dict, ast.Set)):
        return "literal"
    if isinstance(expr, ast.Call):
        return "call"
    if isinstance(expr, ast.Attribute):
        return "attr"
    if isinstance(expr, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
        return "comprehension"
    return "other"


def _safe_unparse(node: ast.AST) -> str:
    """Best-effort ast.unparse wrapper."""
    try:
        return ast.unparse(node)  # Python 3.9+
    except Exception:
        return "<unparseable>"


def _index_defitems_with_parents(defitems: List["DefItem"]):
    """
    Build indices for DefItems:
      - by_id: node-id -> DefItem
      - parent_of: child node-id -> parent node-id
      - by_name: first occurrence of name -> DefItem
    """
    by_id: Dict[int, "DefItem"] = {}
    parent_of: Dict[int, int] = {}
    by_name: Dict[str, "DefItem"] = {}

    def _walk(d: "DefItem", parent: Optional["DefItem"]):
        nid = id(d.node)
        if nid not in by_id:
            by_id[nid] = d
            by_name.setdefault(d.name, d)
        if parent is not None:
            parent_of[nid] = id(parent.node)
        for ch in d.function_defs:
            _walk(ch, d)
        for ch in d.class_defs:
            _walk(ch, d)

    for d in defitems:
        _walk(d, None)

    return by_id, parent_of, by_name


def _build_module_ctx_from_target(target: Any) -> ModuleCtx:
    """
    Construct a ModuleCtx from an arbitrary object by:
      - Fetching its module AST
      - Collecting top-level defs (pruned)
      - Collecting imports
      - Indexing defs (by id/name, with parent map)
      - Collecting module-level variables and their RHS ASTs
    """
    tree, mod = get_module_ast(target)
    ll = LowLevelCollector(tree)
    toplevel: ast.Module = ll.pruned
    defitems: List["DefItem"] = ll.defs
    imported: Dict[str, "ImportItem"] = ImportCollector(toplevel).imported

    def_by_id, parent_of, def_by_name = _index_defitems_with_parents(defitems)

    known_def_names = set(d.name for d in def_by_name.values())
    known_import_aliases = set(imported.keys())
    module_vars_map, module_var_exprs = _collect_module_vars_with_exprs(
        toplevel=toplevel,
        known_def_names=known_def_names,
        known_import_aliases=known_import_aliases,
    )

    module_name = getattr(mod, "__name__", repr(mod))
    return ModuleCtx(
        module_name=module_name,
        module_obj=mod,
        toplevel=toplevel,
        def_by_id=def_by_id,
        parent_of=parent_of,
        def_by_name=def_by_name,
        imported=imported,
        module_vars_map=module_vars_map,
        module_var_exprs=module_var_exprs,
    )

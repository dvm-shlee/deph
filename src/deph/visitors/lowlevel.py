import ast
from copy import deepcopy
from typing import List, Tuple
from ..types import DefItem, AstDefs

class Pruner(ast.NodeTransformer):
    """
    An AST transformer with two main modes of operation:

    1. 'module_prune': When visiting a module AST, it collects all top-level
       function and class definition nodes (`AstDefs`) and removes them from
       the module's body. The collected nodes are available via the `defnodes`
       property, and the modified AST is available via the `pruned` property.

    2. 'strip_inner': When visiting a function or class AST, it removes all
       nested function and class definitions from its body, keeping only the
       top-level structure of that definition. This is used to generate a
       "signature-only" representation of a definition's code.
    """
    def __init__(self, tree: ast.AST):
        """
        Initializes the Pruner and immediately runs the 'module_prune' operation.
        """
        super().__init__()
        self._original = deepcopy(tree)
        self._defnodes: List[AstDefs] = []
        # mode: None | 'module_prune' | 'strip_inner'
        self._mode: str | None = None
        self._depth: int = 0

        self._mode = 'module_prune'
        self.pruned: ast.AST = self.visit(self._original)
        self._mode = None

    @property
    def defnodes(self) -> List[AstDefs]:
        """Returns the list of top-level definition nodes collected during 'module_prune'."""
        return self._defnodes

    def visit_FunctionDef(self, node: ast.FunctionDef):
        """
        Handles FunctionDef nodes based on the current mode.
        """
        if self._mode == 'module_prune':
            self._defnodes.append(node)
            return None
        elif self._mode == 'strip_inner':
            if self._depth > 0:
                return None
            self._depth += 1
            node = self.generic_visit(node)
            self._depth -= 1
            return node
        return self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):
        """
        Handles AsyncFunctionDef nodes based on the current mode.
        """
        if self._mode == 'module_prune':
            self._defnodes.append(node)
            return None
        elif self._mode == 'strip_inner':
            if self._depth > 0:
                return None
            self._depth += 1
            node = self.generic_visit(node)
            self._depth -= 1
            return node
        return self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef):
        """
        Handles ClassDef nodes based on the current mode.
        """
        if self._mode == 'module_prune':
            self._defnodes.append(node)
            return None
        elif self._mode == 'strip_inner':
            if self._depth > 0:
                return None
            self._depth += 1
            node = self.generic_visit(node)
            self._depth -= 1
            return node
        return self.generic_visit(node)

    def visit_Module(self, node: ast.Module):
        """
        Ensures that visiting children of a Module node correctly rebuilds the body
        while filtering out nodes that were removed (returned as None).
        """
        node.body = [n for n in (self.visit(ch) for ch in list(node.body)) if n is not None]
        return node

    def generic_visit(self, node):
        """
        A modified generic_visit that correctly handles lists of nodes where some items might be removed.
        """
        for field, value in ast.iter_fields(node):
            if isinstance(value, list):
                new_list = []
                for item in value:
                    if isinstance(item, ast.AST):
                        new_item = self.visit(item)
                        if new_item is None:
                            continue
                        new_list.append(new_item)
                    else:
                        new_list.append(item)
                setattr(node, field, new_list)
            elif isinstance(value, ast.AST):
                new_node = self.visit(value)
                if new_node is not None:
                    setattr(node, field, new_node)
        return node

    def strip_inner_defs_keep_root(self, root: AstDefs) -> ast.AST:
        """
        Creates a copy of a definition node and removes all nested definitions from its body.

        This is a public method to run the 'strip_inner' transformation on a given node.
        """
        copy_ = deepcopy(root)
        prev_mode, prev_depth = self._mode, self._depth
        self._mode, self._depth = 'strip_inner', 0
        out = self.visit(copy_)
        self._mode, self._depth = prev_mode, prev_depth
        return out

    
class LowLevelCollector:
    """
    Collects and structures all top-level definitions from a module's AST.

    This class uses a `Pruner` to first separate the top-level function and
    class definitions from the rest of the module's AST. It then recursively
    builds a tree of `DefItem` objects, where each `DefItem` contains the
    original node, a "pruned" version of its code (with inner definitions
    removed), and lists of its own nested `DefItem` children.
    """
    def __init__(self, tree: ast.AST):
        """
        Initializes the collector by pruning the tree and building DefItems.
        """
        pr = Pruner(tree)              # Prune module and collect defnodes
        self.pruned = pr.pruned
        self.original = deepcopy(tree)
        self._defs: List[DefItem] = [self._collect_defitem(pr, n) for n in pr.defnodes]

    @property
    def defs(self) -> List[DefItem]:
        """Returns the list of recursively-structured top-level `DefItem` objects."""
        return self._defs

    @classmethod
    def _collect_defitem(cls, pruner: Pruner, node: AstDefs) -> DefItem:
        """
        Recursively constructs a `DefItem` from an AST definition node.

        For each definition, it generates a pruned code string (without nested
        definitions) and recursively collects `DefItem`s for its children.
        """
        typ = 'function' if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) else 'class'
        code = ast.unparse(pruner.strip_inner_defs_keep_root(node))  # Keep root, remove inner
        pruned_root = pruner.strip_inner_defs_keep_root(node)
        func_defs, class_defs = [], []
        for ch in node.body:
            if isinstance(ch, AstDefs):
                item = cls._collect_defitem(pruner, ch)
                (func_defs if item.type == 'function' else class_defs).append(item)

        return DefItem(name=node.name, type=typ, code=code, node=node, pruned=pruned_root, 
                       function_defs=func_defs, class_defs=class_defs)
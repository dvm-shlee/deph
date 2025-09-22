import ast
from typing import Set, Optional

__all__ = [
    'NameUsageCollector', 
    'roots_in_expr'
]


class NameUsageCollector(ast.NodeVisitor):
    """
    An AST visitor that collects information about name usage within a node.

    It categorizes names into several sets:
    - `loads`: Names that are read (e.g., `print(x)`).
    - `local_stores`: Names that are written to (e.g., `x = 5`, `import y as x`).
    - `params`: Names that are function parameters.
    - `attr_roots`: The root of an attribute access chain (e.g., `np` in `np.array.dtype`).

    This is used to determine which names are "unbound" within a scope, i.e.,
    which names are loaded but not defined locally as a parameter, assignment,
    or local import.

    Note: For nested definitions (functions/classes), it only registers the
    definition's name as a local store and does not visit the body.
    """
    def __init__(self):
        self.loads: Set[str] = set()
        self.local_stores: Set[str] = set()
        self.params: Set[str] = set()
        self.attr_roots: Set[str] = set()

    def visit_arguments(self, node: ast.arguments):
        """Collects all parameter names from a function's signature."""
        for arg in list(node.posonlyargs) + list(node.args) + list(node.kwonlyargs):
            self.params.add(arg.arg)
        if node.vararg: self.params.add(node.vararg.arg)
        if node.kwarg: self.params.add(node.kwarg.arg)

    def visit_Name(self, node: ast.Name):
        """Categorizes a name as a load, store, or deletion."""
        if isinstance(node.ctx, ast.Load):
            self.loads.add(node.id)
        elif isinstance(node.ctx, (ast.Store, ast.Del)):
            self.local_stores.add(node.id)

    def visit_Attribute(self, node: ast.Attribute):
        """Finds the root of an attribute access chain (e.g., `np` in `np.array`)."""
        r = self._root_name(node)
        if r: self.attr_roots.add(r)
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import):
        """Treats imported module names/aliases as local stores."""
        for alias in node.names:
            name = alias.asname or alias.name.split(".", 1)[0]
            self.local_stores.add(name)

    def visit_ImportFrom(self, node: ast.ImportFrom):
        """Treats imported names/aliases from a module as local stores."""
        for alias in node.names:
            if alias.name == "*":
                continue
            name = alias.asname or alias.name
            self.local_stores.add(name)

    def visit_ExceptHandler(self, node: ast.ExceptHandler):
        """Treats the exception alias in an `except` block as a local store."""
        if node.name:
            if isinstance(node.name, str):
                self.local_stores.add(node.name)
            elif isinstance(node.name, ast.Name):
                self.local_stores.add(node.name.id)
        self.generic_visit(node)

    # For nested definitions, only register the name as a local store and do not visit the body.
    def visit_FunctionDef(self, node: ast.FunctionDef):
        """
        Treats a nested function definition as a local store for its name.
        Does not visit the function's body to keep the analysis scoped.
        """
        self.local_stores.add(node.name)
        for dec in node.decorator_list: self.visit(dec)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):
        """
        Treats a nested async function definition as a local store for its name.
        Does not visit the function's body.
        """
        self.local_stores.add(node.name)
        for dec in node.decorator_list: self.visit(dec)

    def visit_ClassDef(self, node: ast.ClassDef):
        """
        Treats a nested class definition as a local store for its name.
        Does not visit the class's body.
        """
        self.local_stores.add(node.name)
        for dec in node.decorator_list: self.visit(dec)

    @staticmethod
    def _root_name(node: ast.AST) -> Optional[str]:
        """
        Traverses an attribute access chain up to its root.
        Returns the root name if it's an `ast.Name`, otherwise None.
        e.g., for `a.b.c`, returns `'a'`.
        """
        cur = node
        while isinstance(cur, ast.Attribute):
            cur = cur.value
        return cur.id if isinstance(cur, ast.Name) else None

    @staticmethod
    def root_names_in_expr(expr: ast.AST) -> Set[str]:
        """
        Convenience static method to call `roots_in_expr` on an expression.
        """
        return roots_in_expr(expr)


def roots_in_expr(expr: ast.AST) -> Set[str]:
    """
    Collect all root identifiers that are loaded within an expression AST.

    This is useful for finding the external dependencies of a single expression,
    such as the right-hand side of an assignment or a decorator.

    e.g., for `(a.b + c.d)`, it returns `{'a', 'c'}`.
    """
    roots: Set[str] = set()
    class V(ast.NodeVisitor):
        def visit_Name(self, n: ast.Name):
            if isinstance(n.ctx, ast.Load):
                roots.add(n.id)

        def visit_Attribute(self, n: ast.Attribute):
            self.generic_visit(n)

        def visit_Call(self, n: ast.Call):
            self.generic_visit(n)

        def visit_Subscript(self, n: ast.Subscript):
            self.generic_visit(n)

        def visit_BinOp(self, n: ast.BinOp):
            self.generic_visit(n)

        def visit_UnaryOp(self, n: ast.UnaryOp):
            self.generic_visit(n)
    V().visit(expr)
    return roots

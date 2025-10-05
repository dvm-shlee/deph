import ast
import textwrap
from typing import List, Tuple, Dict, Optional
from ..types.dataclasses import ImportItem
from ..helper import PACKAGE_DISTRIBUTIONS, is_stdlib


class ImportCollector(ast.NodeVisitor):
    """
    An AST visitor that collects all static and dynamic import statements in a module.

    It traverses an AST and builds a collection of `ImportItem` objects,
    representing `import`, `from ... import`, and dynamic imports via
    `importlib.import_module` or `__import__`.

    The collected items can be accessed via the `imported` property, which
    returns a dictionary mapping the imported alias to its `ImportItem`.
    """
    def __init__(self, node: ast.AST):
        """
        Initializes the collector and immediately traverses the given AST node.

        Parameters
        ----------
        node : ast.AST
            The root of the AST to traverse.
        """
        self._imported_alias: List[str] = []
        self._import_items: List[ImportItem] = []
        self._dynamic_ref: List[str] = []
        self.visit(node)
    
    def visit_Import(self, node: ast.Import):
        """Handles `import a.b.c` and `import a.b.c as d` statements."""
        names = {}
        module = None
        submodule = None
        for i, alias in enumerate(node.names):
            asname, name = self._parse_alias(alias)
            if i == 0:
                module = name.split('.', 1)
                if len(module) > 1:
                    module, submodule = module
                else:
                    module, submodule = module[0], None
            names[asname] = name
        self._import_items.append(ImportItem(names, module,
                                             self._package_name(module),
                                             submodule, ast.unparse(node),
                                             None, False, False))
        
    def visit_ImportFrom(self, node):
        """Handles `from a.b import c` and `from .a import b` statements."""
        names = {}
        if node.module:
            module, submodule = self._parse_submodule(node.module)
        else:
            module, submodule = None, None
        use_star = False
        level = node.level
        for alias in node.names:
            asname, name = self._parse_alias(alias)
            if not use_star and '*' in asname:
                use_star = True
                asname = f'{asname}_{module}.{submodule}'
            names[asname] = name
        self._import_items.append(ImportItem(names, module,
                                             self._package_name(module),
                                             submodule, ast.unparse(node),
                                             level, False, use_star))
    
    def visit_Assign(self, node):
        """
        Detects dynamic imports assigned to a variable.
        
        e.g., `my_json = importlib.import_module('json')`
        """
        # Ensure the assignment is to a single variable name, e.g., `x = ...`
        # and not `x, y = ...` or `x.attr = ...`
        if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
            return

        if not isinstance(node.value, ast.Call):
            return

        if func_node := self._parse_func_name(node.value):
            asname = node.targets[0].id
            
            # Ensure the first argument is a constant string before accessing .value
            first_arg = func_node.args[0]
            if not isinstance(first_arg, ast.Constant) or not isinstance(first_arg.value, str):
                return
            
            name = first_arg.value

            # Initialize module/submodule to prevent UnboundLocalError
            module, submodule = self._parse_submodule(name)

            # Check for keyword arguments like 'package' in import_module
            for kw in func_node.keywords:
                if kw.arg == 'package' and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                    pkg_name = kw.value.value
                    module, submodule = self._parse_submodule(pkg_name)

            code = textwrap.dedent(ast.unparse(node))
            self._imported_alias.append(asname)
            self._import_items.append(ImportItem({asname: name}, module,
                                                 self._package_name(module),
                                                 submodule, code, None, True, False))

    @staticmethod
    def _package_name(module_name: Optional[str]) -> Optional[str]:
        """
        Resolves a top-level module name to its PyPI package distribution name.
        Returns the module name itself if it's a standard library module.
        """
        if module_name is None:
            return None
        if is_stdlib(module_name):
            return module_name
        else:
            return PACKAGE_DISTRIBUTIONS.get(module_name, module_name)


    @property
    def imported(self) -> Dict[str, ImportItem]:
        """
        Returns a dictionary of all collected import items.

        Returns
        -------
        Dict[str, ImportItem]
            A dictionary mapping each imported alias to its corresponding `ImportItem`.
        """
        imported = {}
        for cmd in self._import_items:
            for alias in cmd.names.keys():
                imported[alias] = cmd
        return imported

    # -- internal
    @staticmethod
    def _parse_submodule(module: str) -> Tuple[str, Optional[str]]:
        """
        Splits a module string like 'a.b.c' into a top-level module ('a')
        and the rest ('b.c').
        """
        splitted_mod = module.split('.', 1)
        if len(splitted_mod) > 1:
            return (splitted_mod[0], splitted_mod[1])
        else:
            return module, None
    
    def _parse_alias(self, alias: ast.alias) -> Tuple[str, str]:
        """
        Parses an `ast.alias` node to get the original name and its alias.
        Also tracks names related to dynamic importing.
        """
        _name = alias.name
        _alias = alias.asname or _name
        self._imported_alias.append(_alias)
        if any(x in _name for x in ('importlib', 'import_module')):
            self._dynamic_ref.append(_alias)
        return _alias, _name
    
    def _parse_func_name(self, node: ast.Call) -> Optional[ast.Call]:
        """
        Checks if a call expression is a dynamic import function call.
        
        Returns the `ast.Call` node if it matches, otherwise None.
        """
        if isinstance(node.func, ast.Attribute):
            func_name = node.func.attr
        elif isinstance(node.func, ast.Name):
            func_name = node.func.id
        else:
            return
        if func_name in self._dynamic_ref or any(x in func_name for x in ['__import__', 'import_module']):
            if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                return node

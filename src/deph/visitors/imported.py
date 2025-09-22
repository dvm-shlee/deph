import ast
import textwrap
from collections import defaultdict
from typing import List, Tuple
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
        self._import_cmd = defaultdict(ImportItem)
        self._dynamic_ref: List[str] = []
        self.visit(node)
    
    def visit_Import(self, node: ast.Import):
        """Handles `import a.b.c` and `import a.b.c as d` statements."""
        names = {}
        module = None
        for i, alias in enumerate(node.names):
            asname, name = self._parse_alias(alias)
            if i == 0:
                module = name.split('.', 1)
                if len(module) > 1:
                    module, submodule = module
                else:
                    module, submodule = module[0], None
            names[asname] = name
        self._import_cmd[len(self._import_cmd)] = ImportItem(names, module,
                                                             self._package_name(module),
                                                             submodule, ast.unparse(node),
                                                             None, False, False)
        
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
        self._import_cmd[len(self._import_cmd)] = ImportItem(names, module,
                                                             self._package_name(module),
                                                             submodule, ast.unparse(node),
                                                             level, False, use_star)
    
    
    def visit_Assign(self, node):
        """
        Detects dynamic imports assigned to a variable.
        
        e.g., `my_json = importlib.import_module('json')`
        """
        if isinstance(node.value, ast.Call):
            if func_node := self._parse_func_name(node.value):
                name = func_node.args[0].value
                if len(func_node.args) > 1:
                    for arg in func_node.args:
                        if 'fromlist' in arg.id:
                            module, submodule = self._parse_submodule(name)
                        elif 'package' in arg.id:
                            module, submodule = self._parse_submodule(arg.value)
                else:
                    module, _ = self._parse_submodule(name)
                    submodule = None
                code = textwrap.dedent(ast.unparse(node))
                asname = node.targets[0].id
                self._imported_alias.append(asname)
                self._import_cmd[len(self._import_cmd)] = ImportItem({asname: name}, module, 
                                                                     self._package_name(module),
                                                                     submodule, code, 
                                                                     None, True, False)

    @staticmethod
    def _package_name(module_name: str):
        """
        Resolves a top-level module name to its PyPI package distribution name.
        Returns the module name itself if it's a standard library module.
        """
        if is_stdlib(module_name):
            return module_name
        else:
            return PACKAGE_DISTRIBUTIONS.get(module_name, module_name)

    @property
    def imported(self):
        """
        Returns a dictionary of all collected import items.

        Returns
        -------
        Dict[str, ImportItem]
            A dictionary mapping each imported alias to its corresponding `ImportItem`.
        """
        imported = {}
        for _, cmd in sorted(self._import_cmd.items()):
            for alias in cmd.names.keys():
                imported[alias] = cmd
        return imported

    # -- internal
    @staticmethod
    def _parse_submodule(module: str):
        """
        Splits a module string like 'a.b.c' into a top-level module ('a')
        and the rest ('b.c').
        """
        splitted_mod = module.split('.', 1)
        if len(splitted_mod) > 1:
            return tuple(splitted_mod)
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
    
    def _parse_func_name(self, node: ast.Call):
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
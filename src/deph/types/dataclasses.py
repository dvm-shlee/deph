from __future__ import annotations
from dataclasses import dataclass
from typing import List, Dict, Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from ..types import AstDefs
    from ast import Module, AST


@dataclass
class ImportItem:
    """
    Represents a single import statement found in a module.

    Attributes
    ----------
    names : Dict[str, str]
        A mapping of imported aliases to their original names (e.g., `{'np': 'numpy'}`).
    module : str
        The top-level module being imported (e.g., 'numpy' for `import numpy.linalg`).
    package_name : str
        The resolved PyPI package name for the module.
    """
    names: Dict[str, str]
    module: str
    package_name: str
    submodule: List[str]
    code: str
    level: Optional[int]
    is_dynamic: bool
    use_star: bool


@dataclass
class VarsItem:
    """
    Represents a module-level variable assignment.

    Attributes
    ----------
    name : str
        The name of the variable.
    code : str
        The source code of the assignment statement.
    value_kind : str
        A classification of the assigned value, e.g., 'literal', 'call', 'attr'.
    """
    name: str
    code: str
    value_kind: str  # 'literal' | 'call' | 'attr' | 'comprehension' | 'other'
    
@dataclass
class DefItem:
    """
    Represents a function or class definition, structured hierarchically.

    This contains the definition's name, type ('function' or 'class'), source code,
    and its original and pruned AST nodes. It also holds lists of nested
    function and class definitions (`DefItem`s).
    """
    name: str
    type: str
    code: str
    node: AstDefs
    pruned: AstDefs
    function_defs: List["DefItem"]
    class_defs: List["DefItem"]


@dataclass
class ModuleCtx:
    """
    A container for all information extracted during the analysis of a single module.

    Attributes
    ----------
    module_name : str
        The fully qualified name of the module.
    module_obj : Any
        The actual module object.
    toplevel : ast.Module
        The top-level AST node of the module's source code.
    def_by_id : Dict[int, DefItem]
        A mapping from the id() of a definition's AST node to its `DefItem`.
    parent_of : Dict[int, int]
        A mapping from a child definition's node id to its parent's node id.
    def_by_name : Dict[str, DefItem]
        A mapping from a top-level definition's name to its `DefItem`.
    imported : Dict[str, ImportItem]
        A mapping from an imported alias to its `ImportItem`.
    """
    module_name: str
    module_obj: Any
    toplevel: Module
    def_by_id: Dict[int, "DefItem"]
    parent_of: Dict[int, int]
    def_by_name: Dict[str, "DefItem"]
    imported: Dict[str, "ImportItem"]                 # alias -> ImportItem
    module_vars_map: Dict[str, VarsItem]              # name -> VarsItem
    module_var_exprs: Dict[str, Optional[AST]]  
    
    

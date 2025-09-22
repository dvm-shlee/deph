from __future__ import annotations
from pprint import pformat
import textwrap
from itertools import islice
from typing import Any, Callable, Optional, List


class AttrDefaultDict(dict):
    """
    A dictionary that allows attribute-style access (e.g., `d.key`).

    Features:
    - `default_factory`: A function to create default values for missing keys
      (e.g., list, set, dict, lambda).
    - `auto_nest=True`: Automatically creates nested `AttrDefaultDict` instances
      when accessing a missing key, which is useful for building tree-like structures.
    - Includes a pretty `__repr__`, tab-completion support (`__dir__`), and safe
      implementations of `__getattr__`, `__setattr__`, and `__delattr__`.
    """
    __slots__ = ("_default_factory", "_auto_nest", "_pp_width", "_pp_list_limit")

    def __init__(self, 
                 default_factory: Optional[Callable] = None, 
                 *, auto_nest: bool = False,
                 pp_width: int = 88, 
                 pp_list_limit: int = 0, 
                 **kwargs: Any):
        """
        Args:
            default_factory: Function to generate default values for missing keys.
            auto_nest: If True, automatically create nested AttrDefaultDicts.
            pp_width: Line width for pretty printing.
            pp_list_limit: For long lists, show this many items from the start
                           and summarize the rest as '... (+N more)'. 0 means no limit.
        """
        super().__init__()
        object.__setattr__(self, "_default_factory", default_factory)
        object.__setattr__(self, "_auto_nest", auto_nest)
        object.__setattr__(self, "_pp_width", pp_width)
        object.__setattr__(self, "_pp_list_limit", pp_list_limit)

        if kwargs:
            self.update(kwargs)

    # ── attribute access ────────────────────────────────────────────────────────
    def __getattr__(self, name: str) -> Any:
        """
        Provide attribute-style access to dictionary keys.
        If a key is not found, it invokes `__missing__` to create a default value.
        """
        # Do not delegate internal attributes/special names to the dict.
        if name.startswith("_"):
            raise AttributeError(name)
        try:
            return self[name]
        except KeyError:
            # On accessing a missing attribute, create it like defaultdict.
            return self.__missing__(name)

    def __setattr__(self, name: str, value: Any) -> None:
        """
        Set a dictionary key using attribute assignment syntax.
        Internal attributes (starting with '_') are set on the object itself.
        """
        # Store internal attributes/special names as real attributes.
        if name.startswith("_"):
            object.__setattr__(self, name, value)
        else:
            self[name] = value

    def __delattr__(self, name: str) -> None:
        """
        Delete a dictionary key using attribute deletion syntax.
        Internal attributes (starting with '_') are deleted from the object itself.
        """
        # Handle internal attributes.
        if name.startswith("_"):
            try:
                object.__delattr__(self, name)
            except AttributeError:
                raise AttributeError(name) from None
        else:
            # Handle dict keys.
            try:
                del self[name]
            except KeyError:
                raise AttributeError(name) from None

    def __missing__(self, key: str) -> Any:
        """
        Handle missing keys, called by `__getitem__` (and `__getattr__`).

        If `auto_nest` is True, it creates and returns a new `AttrDefaultDict`.
        Otherwise, it uses the `default_factory` if provided.
        """
        # If auto_nest is enabled, create a nested dict of the same type.
        if self._auto_nest:
            value = AttrDefaultDict(
                self._default_factory,
                auto_nest=True,
                pp_width=self._pp_width,
                pp_list_limit=self._pp_list_limit
            )
        elif self._default_factory is not None:
            value = self._default_factory()
        else:
            raise KeyError(key)

        dict.__setitem__(self, key, value)
        return value

    def _summarize_ast(self, node: Any) -> Any:
        """
        Convert an `ast.AST` object to a one-line summary string for pretty printing.
        
        Returns the original object if it's not an AST node or if `ast` can't be imported.
        """
        try:
            import ast
        except Exception:
            ast = None
        if ast and isinstance(node, ast.AST):
            typ = node.__class__.__name__
            ln = getattr(node, "lineno", None)
            cn = getattr(node, "col_offset", None)
            if ln is not None and cn is not None:
                return f"{typ}(lineno={ln}, col={cn})"
            elif ln is not None:
                return f"{typ}(lineno={ln})"
            return typ
        return node  # If not an ast node, return as is.

    def _convert(self, x: Any) -> Any:
        """
        Recursively convert values for pretty printing before `__repr__`.

        This method summarizes `ast.AST` nodes and truncates long lists.
        """
        # First, summarize ast nodes.
        x = self._summarize_ast(x)

        if isinstance(x, AttrDefaultDict):
            return {k: self._convert(v) for k, v in x.items()}
        elif isinstance(x, dict):
            return {k: self._convert(v) for k, v in x.items()}
        elif isinstance(x, list):
            if self._pp_list_limit and len(x) > self._pp_list_limit:
                head = [self._convert(v) for v in islice(x, self._pp_list_limit)]
                rest = len(x) - self._pp_list_limit
                head.append(f"... (+{rest} more)")
                return head
            return [self._convert(v) for v in x]
        elif isinstance(x, tuple):
            return tuple(self._convert(v) for v in x)
        elif isinstance(x, set):
            return {self._convert(v) for v in x}
        else:
            return x
        
    def __repr__(self) -> str:
        """Return a developer-friendly, pretty-printed representation of the dictionary."""
        body = pformat(
            self.to_dict(),
            width=self._pp_width,
            compact=False,  # Use line breaks actively.
            sort_dicts=True,
        )
        flags = []
        if self._default_factory:
            ff = getattr(self._default_factory, "__name__", repr(self._default_factory))
            flags.append(f"default_factory={ff}")
        if self._auto_nest:
            flags.append("auto_nest=True")
        suffix = f"  # {', '.join(flags)}" if flags else ""

        # Key: Wrap the outer representation in multiple lines with indentation.
        return "AttrDefaultDict(\n" + textwrap.indent(body, "  ") + f"\n){suffix}"

    def __dir__(self) -> List[str]:
        """
        Include dictionary keys in `dir()` output to support tab-completion
        in interactive environments like IPython/Jupyter.
        """
        return sorted(set(list(super().__dir__()) + [k for k in self.keys() if isinstance(k, str)]))

    def to_dict(self) -> dict:
        """
        Recursively convert the `AttrDefaultDict` and its nested `AttrDefaultDict`
        children into regular Python dictionaries.
        """
        return {k: self._convert(v) for k, v in self.items()}

    def copy(self) -> AttrDefaultDict:
        """
        Create a shallow copy of the dictionary, preserving its factory settings.
        """
        new = AttrDefaultDict(
            self._default_factory,
            auto_nest=self._auto_nest,
            pp_width=self._pp_width,
            pp_list_limit=self._pp_list_limit
        )
        for k, v in self.items():
            dict.__setitem__(new, k, v)
        return new
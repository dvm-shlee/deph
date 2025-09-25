from __future__ import annotations
import re
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from typing import Any, Iterable, Optional


def calc_num_char(input_var: Any) -> int:
    """Calculates the number of characters in the string representation of a variable."""
    return len(str(input_var))

def calc_max_char(input_list: Iterable[Any]) -> int:
    """Finds the maximum character length among items in an iterable, returning 0 for empty iterables."""
    if not input_list:
        return 0
    return max((calc_num_char(i) for i in input_list), default=0)

def line_of_char(char: str, num_char: int) -> str:
    """Creates a string by repeating a character a specified number of times."""
    return char * num_char

def camel_to_snake(name: str) -> str:
    """Converts a CamelCase string to snake_case.
    
    Example: "MyAwesomeString" -> "my_awesome_string"
    """
    s1 = re.sub('(.)([A-Z][a-z]+)', r'\1_\2', name)
    return re.sub('([a-z0-9])([A-Z])', r'\1_\2', s1).lower()

def snake_to_camel(name: str, upper: bool = True) -> str:
    """Converts a snake_case string to CamelCase.
    
    Args:
        name: The snake_case string.
        upper: If True, converts to UpperCamelCase. Otherwise, lowerCamelCase.
    
    Example: "my_awesome_string" -> "MyAwesomeString" (upper=True)
             "my_awesome_string" -> "myAwesomeString" (upper=False)
    """
    components = name.split('_')
    if upper:
        return "".join(x.capitalize() for x in components)
    return components[0] + "".join(x.capitalize() for x in components[1:])

def truncate(text: str, length: int, ellipsis: str = "...") -> str:
    """Truncates a string if it exceeds the specified length, appending an ellipsis."""
    if len(text) <= length:
        return text
    return text[:length - len(ellipsis)] + ellipsis

def is_blank(text: Optional[str]) -> bool:
    """Checks if a string is None, empty, or consists only of whitespace."""
    return text is None or not text.strip()

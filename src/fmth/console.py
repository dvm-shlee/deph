from __future__ import annotations
import sys
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from typing import Union, Literal, Tuple, TextIO, List, Optional


_COLOR_CODES = {
    # Styles
    'bold': '1', 'underline': '4',
    # Colors
    'black': '30', 'red': '31', 'green': '32', 'yellow': '33',
    'blue': '34', 'magenta': '35', 'cyan': '36', 'white': '37',
}
_RESET_CODE = '\033[0m'


def ask_yes_or_no(question: str) -> bool:
    """
    Asks a yes/no question via input() and returns the answer as a boolean.

    Args:
        question: The question to display to the user.

    Returns:
        True for 'yes' or 'y', False for 'no' or 'n'.
    """
    while True:
        reply = input(f"{question} (y/n): ").lower().strip()
        if reply.startswith('y'):
            return True
        if reply.startswith('n'):
            return False
        
        sys.stderr.write("  Invalid input. Please enter 'y' or 'n'.\n")
        sys.stderr.flush()

@staticmethod
def print_internal_error(io_handler: TextIO = sys.stderr) -> None:
    """
    Prints the current exception traceback to the specified I/O stream.

    Args:
        io_handler: The I/O stream to write to (defaults to sys.stderr).
    """
    import traceback
    traceback.print_exception(*sys.exc_info(),
                            file=io_handler)


def colored(
    string: str,
    color: Union[str, Tuple[int, int, int]] = 'red',
    style: Optional[Union[str, List[str]]] = None
) -> str:
    """
    Applies ANSI color and style codes to a string.

    Args:
        string: The input string.
        color: Color name (e.g., 'red', 'green') or an RGB tuple (r, g, b).
        style: Style name ('bold', 'underline') or a list of styles.

    Returns:
        The formatted string with ANSI escape codes.
    """
    codes = []

    # Handle styles
    if style:
        style_list = [style] if isinstance(style, str) else style
        for s in style_list:
            if s.lower() in _COLOR_CODES:
                codes.append(_COLOR_CODES[s.lower()])

    # Handle colors
    if isinstance(color, tuple) and len(color) == 3:
        r, g, b = color
        codes.append(f'38;2;{r};{g};{b}')
    elif isinstance(color, str) and color.lower() in _COLOR_CODES:
        codes.append(_COLOR_CODES[color.lower()])
    else:
        # Default to red if color is invalid
        codes.append(_COLOR_CODES['red'])

    if not codes:
        return string

    return f'\033[{";".join(codes)}m{string}{_RESET_CODE}'


def message(string: str, 
            io: Union[Literal['stdout', 'stderr'], TextIO] = 'stdout'):
    """
    Writes a message to a specified output stream and flushes it.

    Args:
        string: The message to write.
        io: 'stdout', 'stderr', or a file-like object (defaults to 'stdout').
    """
    stream = io
    if io == 'stdout':
        stream = sys.stdout
    elif io == 'stderr':
        stream = sys.stderr
    
    if hasattr(stream, 'write') and hasattr(stream, 'flush'):
        stream.write(string)
        stream.flush()

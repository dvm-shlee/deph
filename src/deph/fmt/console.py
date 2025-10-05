from __future__ import annotations
import traceback
from typing import TYPE_CHECKING
from ..utils import log

if TYPE_CHECKING:
    from typing import Union, Literal, Tuple, List, Optional
    
    
_COLOR_CODES = {
    # Styles
    'bold': '1', 'underline': '4',
    # Colors
    'black': '30', 'red': '31', 'green': '32', 'yellow': '33',
    'blue': '34', 'magenta': '35', 'cyan': '36', 'white': '37',
}
_RESET_CODE = '\033[0m'


def init(*args, **kwargs):
    """Initializes the logging system.

    This is a convenience wrapper around `devh.log.init`. It accepts all the
    same arguments for configuring console and file logging.

    See Also
    --------
    devh.log.init
        The underlying function with detailed documentation on all available
        parameters like `level`, `log_file`, `use_console`, etc.
    """
    log.init(*args, **kwargs)


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
        log.emit("Invalid input. Please enter 'y' or 'n'.", level='warning')


def print_internal_error() -> None:
    """
    Logs the current exception traceback at the 'error' level.
    """
    # Capture the full traceback as a string and log it.
    # The `end=''` prevents an extra newline since format_exc includes one.
    exc_info = traceback.format_exc()
    log.emit(exc_info, level='error', end='')


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
            level: Literal['debug', 'info', 'warning', 'error', 'critical'] = 'info',
            end: str = '\n'):
    """
    Logs a message using the configured logger.

    Args:
        string: The message to write.
        level: The logging level to use (defaults to 'info').
    """
    log.emit(string, level=level, end=end)

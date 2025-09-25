"""
devh.log
========

A simple and flexible logging setup utility.

This module provides a straightforward way to configure Python's root logger
for console and/or file output, or to use a detailed dictionary configuration.
It also includes a `log()` function that acts as a `print()` replacement with
logging levels, making it easy to switch from printing to structured logging.

Key Functions
-------------
- **init()**
  Initializes the root logger. It can be called with simple arguments like
  `level`, `log_file`, and `use_console` for quick setup, or with a full
  `dictConfig` dictionary for advanced control over formatters, handlers,
  and loggers.
- **emit()**
  A wrapper that mimics the `print()` function but directs its output to the
  configured logger. It accepts multiple arguments and a `level` keyword
  (e.g., 'info', 'debug', 'warning').

Quick start
-----------
```python
from devh import log
import logging

# Basic console logging at INFO level
log.init()
log.emit("This is an info message.")
# Debug logging to both console and a file
log.init(level=logging.DEBUG, log_file="app.log")
log.log("This is a debug message.", level="debug")

# Using a custom dictionary configuration
my_config = {
    "version": 1,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "WARNING"},
}
log.init(config=my_config)
log.log("This warning will appear.", level="warning")
```
"""
import logging
import logging.config
import sys
from pathlib import Path
from typing import Union, Optional, Dict, Any


class _MaxLevelFilter(logging.Filter):
    """
    Filters log records to allow only those with a level *below* a certain threshold.
    e.g., _MaxLevelFilter(logging.WARNING) allows DEBUG and INFO records.
    """

    def __init__(self, max_level):
        super().__init__()
        self.max_level = max_level

    def filter(self, record):
        return record.levelno < self.max_level

def init(
    config: Optional[Dict[str, Any]] = None,
    level: int = logging.INFO,
    console_level: Optional[int] = None,
    file_level: Optional[int] = None,
    log_file: Optional[Union[str, Path]] = None,
    use_console: bool = True,
    use_file: bool = False,
):
    """
    Initializes the root logger with a flexible configuration.

    This function sets up logging based on either a provided dictionary
    configuration or simple parameters for level, console, and file output.

    When `use_console` is True, it sets up two handlers:
    - One for `stdout` that handles logs below `WARNING` level.
    - One for `stderr` that handles logs from `WARNING` level and up.

    Parameters
    ----------
    config : dict, optional
        A dictionary conforming to `logging.config.dictConfig` schema.
        If provided, all other parameters are ignored.
    level : int, optional
        The base logging level for the root logger. To ensure all messages are passed
        to handlers for their own filtering, this should be the lowest level
        among all specified handler levels. Defaults to `logging.INFO`.
    console_level, file_level : int, optional
        The minimum logging level for the basic configuration (e.g., `logging.INFO`,
        `logging.DEBUG`). Default is `logging.INFO`.
    log_file : str or Path, optional
        Path to the log file. If provided, `use_file` is implicitly True.
    use_console : bool, optional
        If True (default), logs will be sent to `sys.stdout`.
    use_file : bool, optional
        If True, logs will be sent to the file specified by `log_file`.
        If `log_file` is given, this is automatically set to True.

    """
    if config:
        logging.config.dictConfig(config)
        return

    if log_file:
        use_file = True

    # Determine the most verbose level to set on the root logger
    all_levels = [lvl for lvl in (level, console_level, file_level) if lvl is not None]
    root_level = min(all_levels) if all_levels else logging.WARNING

    handlers = []
    if use_console:
        # stdout handler: for levels below WARNING (DEBUG, INFO)
        stdout_handler = logging.StreamHandler(sys.stdout)
        stdout_handler.setLevel(console_level if console_level is not None else level)
        stdout_handler.addFilter(_MaxLevelFilter(logging.WARNING))
        stdout_handler.setFormatter(logging.Formatter("%(message)s"))
        handlers.append(stdout_handler)

        # stderr handler: for levels WARNING and above (WARNING, ERROR, CRITICAL)
        stderr_handler = logging.StreamHandler(sys.stderr)
        # Ensure the stderr handler is at least at WARNING level
        stderr_level = max(console_level if console_level is not None else level, logging.WARNING) # type: ignore
        stderr_handler.setLevel(stderr_level)
        stderr_handler.setFormatter(
            logging.Formatter("[%(levelname)-8s] %(message)s"))
        handlers.append(stderr_handler)

    if use_file:
        if not log_file:
            raise ValueError("`log_file` must be specified when `use_file` is True.")
        
        file_handler = logging.FileHandler(log_file, mode='a', encoding='utf-8')
        # Use specific file_level, or fall back to the general level
        file_handler.setLevel(file_level if file_level is not None else level)
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)-8s] %(message)s")
        )
        handlers.append(file_handler)

    # To prevent duplicate logs or conflicting configurations from previous `init`
    # calls or other libraries, remove all existing handlers from the root logger.
    if logging.root.handlers:
        for handler in logging.root.handlers[:]:
            logging.root.removeHandler(handler)

    if handlers:
        logging.basicConfig(level=root_level, handlers=handlers)
    else:
        # No handlers configured, use a basic default to avoid "No handlers could be found"
        logging.basicConfig(level=root_level, format="%(message)s")
        logging.warning("logh.init() called with no output configured (console or file).")


def emit(*args: Any, sep: str = ' ', end: str = '\n', level: str = 'info'):
    """
    A flexible logger that acts like `print()` but with logging levels.

    It takes any number of arguments, converts them to strings, joins them
    with `sep`, and appends `end`. The resulting message is then logged
    at the specified level.

    Parameters
    ----------
    *args : Any
        Objects to be logged. They will be converted to strings.
    sep : str, optional
        Separator between arguments. Default is a space.
    end : str, optional
        String to append at the end of the message. Default is a newline.
    level : str, optional
        The logging level to use. Can be 'debug', 'info', 'warning', 'error',
        or 'critical'. Default is 'info'.
    """
    message = sep.join(map(str, args)) + end.rstrip('\n')
    logger = logging.getLogger()
    log_func = getattr(logger, level.lower(), logger.info)
    log_func(message)
    
__all__ = [
    "init",
    "emit",
]
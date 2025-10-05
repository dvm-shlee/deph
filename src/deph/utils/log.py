"""
deph.utils.log
================

Simple, flexible logging setup utility compatible with Python 3.9–3.13.

- init(): Configure the root logger via simple flags or a dictConfig.
- emit(): print-like convenience that logs at a given level.

Example
-------
    from deph.utils import log
    import logging

    log.init(level=logging.INFO)
    log.emit("Hello", level="info")
    log.init(level=logging.DEBUG, log_file="app.log")
    log.emit("Debug details", level="debug")
"""
from __future__ import annotations
import inspect
import logging
import logging.config
import sys
from pathlib import Path
from typing import Union, Optional, Dict, Any, Callable, IO


class _MaxLevelFilter(logging.Filter):
    """
    Filters log records to allow only those with a level *below* a certain threshold.
    e.g., _MaxLevelFilter(logging.WARNING) allows DEBUG and INFO records.
    """

    def __init__(self, max_level: int) -> None:
        super().__init__()
        self.max_level = max_level

    def filter(self, record: logging.LogRecord) -> bool:  # pragma: no cover - trivial
        return record.levelno < self.max_level


class _DynamicStreamHandler(logging.Handler):
    """A logging handler that resolves its stream at emit time.

    This allows capturing via redirect_stdout/redirect_stderr after handlers
    have been initialized, which is useful in tests and notebooks.
    """

    def __init__(self, stream_getter: Callable[[], IO[str]], level: int = logging.NOTSET):
        super().__init__(level)
        self._get_stream = stream_getter
        self.terminator = "\n"

    def emit(self, record: logging.LogRecord) -> None:  # pragma: no cover - trivial plumbing
        try:
            msg = self.format(record)
            stream = self._get_stream()
            stream.write(msg + self.terminator)
            try:
                stream.flush()
            except Exception:
                pass
        except Exception:
            self.handleError(record)

def init(
    config: Optional[Dict[str, Any]] = None,
    level: int = logging.INFO,
    *,
    console_level: Optional[int] = None,
    file_level: Optional[int] = None,
    log_file: Optional[Union[str, Path]] = None,
    use_console: bool = True,
    use_file: bool = False,
) -> None:
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
    # Root should be at least as low as the lowest handler so handlers can filter.
    all_levels = [lvl for lvl in (level, console_level, file_level) if lvl is not None]
    root_level = min(all_levels) if all_levels else logging.INFO

    eff_console_level = console_level if console_level is not None else level

    handlers = []
    if use_console:
        # Conditionally add stdout handlers based on effective console level
        if eff_console_level <= logging.DEBUG:
            debug_handler = _DynamicStreamHandler(lambda: sys.stdout, level=logging.DEBUG)
            debug_handler.addFilter(_MaxLevelFilter(logging.INFO))
            debug_handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
            handlers.append(debug_handler)

        if eff_console_level <= logging.INFO:
            info_handler = _DynamicStreamHandler(lambda: sys.stdout, level=logging.INFO)
            info_handler.addFilter(_MaxLevelFilter(logging.WARNING))
            info_handler.setFormatter(logging.Formatter("%(message)s"))
            handlers.append(info_handler)

        # stderr handler: WARNING and above
        stderr_level = eff_console_level if eff_console_level >= logging.WARNING else logging.WARNING
        stderr_handler = _DynamicStreamHandler(lambda: sys.stderr, level=stderr_level)
        stderr_handler.setFormatter(logging.Formatter("[%(levelname)-8s] %(name)s: %(message)s"))
        handlers.append(stderr_handler)

    if use_file:
        if not log_file:
            raise ValueError("`log_file` must be specified when `use_file` is True.")
        
        file_handler = logging.FileHandler(log_file, mode='a', encoding='utf-8')
        # Use specific file_level, or fall back to the general level
        file_handler.setLevel(file_level if file_level is not None else level)
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)-8s]  %(name)s: %(message)s")
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
        logging.getLogger(__name__).warning(
            "log.init() called with no output configured (console or file)."
        )


def emit(*args: Any, sep: str = ' ', end: str = '\n', level: str = 'info') -> None:
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
    message = sep.join(map(str, args))
    # Avoid trailing newlines in logging records; logging adds its own formatting.
    if end and end != '\n':
        message = f"{message}{end}"

    # Safely resolve caller module for logger name across 3.9-3.13
    caller_module = "__main__"
    frame = inspect.currentframe()
    try:
        caller = frame.f_back if frame is not None else None
        if caller is not None:
            mod = caller.f_globals.get("__name__")
            if isinstance(mod, str) and mod:
                caller_module = mod
    finally:
        # Prevent reference cycles
        del frame

    logger = logging.getLogger(caller_module)
    method = getattr(logger, level.lower(), None)
    if not callable(method):
        method = logger.info
    method(message)

# Backwards-friendly alias sometimes referenced in docs
log = emit
    
__all__ = [
    "init",
    "emit",
    "log",
]

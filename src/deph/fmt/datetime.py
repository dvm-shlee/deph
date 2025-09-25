from __future__ import annotations
import datetime as dt
import re
import warnings
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from typing import Union, List, Any


class DateTime:
    """
    A wrapper for parsing and formatting various datetime string formats.
    """
    def __init__(self, datetime_obj: dt.datetime, tzcode: str | None = None):
        """
        Initializes the DateTime object.

        Args:
            datetime_obj: A standard datetime object.
            tzcode: An optional timezone offset string (e.g., "+09:00").
        """
        self.datetime = datetime_obj
        self.tzcode = tzcode

    @classmethod
    def from_mixed_format(cls, dt_input: Union[int, str, List[Any]]) -> "DateTime | None":
        """
        Parses a variety of input formats into a DateTime object.

        Args:
            dt_input: The input to parse. Can be:
                - int: A Unix timestamp.
                - str: A datetime string.
                - list: A list containing a string/timestamp and a residual string.

        Returns:
            A DateTime object if parsing is successful, otherwise None.
        """
        datetime_obj = None
        residual_str = None

        if isinstance(dt_input, int):
            datetime_obj = cls.unix_timestamp_to_datetime(dt_input)
        elif isinstance(dt_input, str):
            datetime_obj = cls.string_to_datetime(dt_input)
        elif isinstance(dt_input, list) and dt_input:
            if isinstance(dt_input[0], str):
                datetime_obj = cls.string_to_datetime(dt_input[0])
                if len(dt_input) > 1:
                    residual_str = dt_input[1]
            elif isinstance(dt_input[0], int):
                datetime_obj = cls.unix_timestamp_to_datetime(dt_input[0])
                if len(dt_input) > 1:
                    residual_str = ''.join(map(str, dt_input[1:]))

        if datetime_obj is None:
            return None

        instance = cls(datetime_obj)
        if residual_str:
            instance.apply_residuals(residual_str)
        
        return instance

    def apply_residuals(self, residual_str: str):
        """
        Applies microsecond and timezone information from a residual string.
        Example residual string: "123+0900" (123ms, +9 hours offset).
        """
        ptrn = r'^(\d{3})([+-]{1})(\d{3,4})$'
        if matched := re.match(ptrn, residual_str):
            ms, side, tz = matched.groups()
            # .replace() is not in-place, so we must reassign.
            self.datetime = self.datetime.replace(microsecond=int(ms) * 1000)
            
            if len(tz) == 3:
                minutes_total = int(tz)
                hours, minutes = divmod(minutes_total, 60)
                tzcode = f"{hours:02d}:{minutes:02d}"
            else:
                tzcode = tz[:2] + ":" + tz[2:]
            self.tzcode = f'{side}{tzcode}'

    def get(self):
        """Returns the formatted datetime string."""
        datetime = self.datetime.strftime('%Y-%m-%d %H:%M:%S')
        return f"{datetime} UTC{self.tzcode}" if self.tzcode else datetime 
        
    @staticmethod
    def string_to_datetime(datetime_str: str):
        """
        Convert a datetime string into a datetime object.

        Args:
            datetime_str (str): The datetime string to convert. Supports two patterns:
                1. "HH:MM:SS dd Mon YYYY" (e.g., "12:34:56 1 Jan 2021")
                2. "YYYY-MM-DDTHH:MM:SS" (e.g., "2021-01-01T12:34:56")
        Returns:
            A `datetime.datetime` object if successful, otherwise None.
        """
        # Pattern 1: "12:34:56 01 Jan 2021"
        try:
            return dt.datetime.strptime(datetime_str, '%H:%M:%S %d %b %Y')
        except ValueError:
            pass
        # Pattern 2: "2021-01-01T12:34:56"
        try:
            return dt.datetime.strptime(datetime_str, '%Y-%m-%dT%H:%M:%S')
        except ValueError:
            pass
            
        warnings.warn(f"Cannot find a matching pattern for the provided datetime string: {datetime_str}")
        return None

    @staticmethod
    def unix_timestamp_to_datetime(unix_timestamp: int) -> dt.datetime:
        """Converts a Unix timestamp to a datetime object."""
        return dt.datetime.fromtimestamp(unix_timestamp)

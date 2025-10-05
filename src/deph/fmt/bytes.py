from __future__ import annotations
import os
import math
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from typing import Tuple


class Bytes:
    """A utility class for byte size conversions and formatting."""

    UNITS: Tuple[str, ...] = ("B", "KB", "MB", "GB", "TB", "PB", "EB")

    @staticmethod
    def _format_size(size_in_bytes: int) -> Tuple[float, str]:
        """
        Formats a size in bytes into a human-readable format (KB, MB, etc.).

        Args:
            size_in_bytes: The size in bytes.

        Returns:
            A tuple containing the converted size and the appropriate unit string.
        """
        if size_in_bytes == 0:
            return 0.0, "B"
        
        # Determine the appropriate unit by taking the logarithm
        unit_index = int(math.floor(math.log(size_in_bytes, 1024)))
        
        # Ensure the unit_index is within the bounds of our UNITS tuple
        unit_index = min(unit_index, len(Bytes.UNITS) - 1)

        converted_size = Bytes.convert_unit(size_in_bytes, unit_index)
        unit_str = Bytes.UNITS[unit_index]
        
        return converted_size, unit_str

    @staticmethod
    def convert_unit(size_in_bytes: int, unit_index: int) -> float:
        """
        Convert the size from bytes to a specified unit.

        Args:
            size_in_bytes: The size in bytes.
            unit_index: The index of the target unit in the UNITS tuple (0=B, 1=KB, etc.).

        Returns:
            The size converted to the target unit.
        """
        if unit_index <= 0:
            return float(size_in_bytes)
        return size_in_bytes / (1024 ** unit_index)

    @staticmethod
    def get_dirsize(dir_path: str) -> Tuple[float, str]:
        """
        Calculates the total size of a directory and returns it in a human-readable format.

        Args:
            dir_path: The path to the directory.

        Returns:
            A tuple containing the total size and the corresponding unit string.
        """
        dir_size = 0
        try:
            for root, _, files in os.walk(dir_path):
                for f in files:
                    fp = os.path.join(root, f)
                    if not os.path.islink(fp):
                        dir_size += os.path.getsize(fp)
        except OSError:
            return 0.0, "B" # Return 0 if path is inaccessible
        return Bytes._format_size(dir_size)

    @staticmethod
    def get_filesize(file_path: str) -> Tuple[float, str]:
        """Calculates the size of a file and returns it in a human-readable format."""
        file_size = os.path.getsize(file_path)
        return Bytes._format_size(file_size)

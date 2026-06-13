"""Data export service."""
from __future__ import annotations

import io
import os
import tempfile
from pathlib import Path
from typing import Any, Iterator

import pandas as pd

# Try xlsxwriter (much faster for writing large files)
try:
    import xlsxwriter  # noqa: F401
    _WRITE_ENGINE = "xlsxwriter"
except ImportError:
    _WRITE_ENGINE = "openpyxl"

# Threshold for using temp-file streaming instead of in-memory BytesIO
# Files over this size (in bytes) will be streamed via a temp file
_STREAM_THRESHOLD = 10 * 1024 * 1024  # 10 MB


def export_to_excel(
    dataframes: dict[str, pd.DataFrame],
    filename: str = "export.xlsx",
) -> bytes:
    """
    Export one or more DataFrames to an Excel file (in-memory bytes).

    Uses xlsxwriter engine when available (40-60% faster than openpyxl).
    For very large exports, the caller should use export_to_excel_streamed() instead.
    """
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine=_WRITE_ENGINE) as writer:
        for sheet_name, df in dataframes.items():
            safe_name = sheet_name[:31]
            df.to_excel(writer, sheet_name=safe_name, index=False)
    output.seek(0)
    return output.getvalue()


def export_to_excel_streamed(
    dataframes: dict[str, pd.DataFrame],
) -> tuple[Iterator[bytes], str]:
    """
    Export DataFrames to Excel by writing to a temp file, then yielding chunks.

    Returns (iterator, temp_file_path).
    Caller MUST delete the temp file after consuming the iterator.
    """
    fd, tmp_path = tempfile.mkstemp(suffix=".xlsx", prefix="export_")
    os.close(fd)

    with pd.ExcelWriter(tmp_path, engine=_WRITE_ENGINE) as writer:
        for sheet_name, df in dataframes.items():
            safe_name = sheet_name[:31]
            df.to_excel(writer, sheet_name=safe_name, index=False)

    def _chunk_reader():
        try:
            with open(tmp_path, "rb") as f:
                while True:
                    chunk = f.read(64 * 1024)  # 64KB chunks
                    if not chunk:
                        break
                    yield chunk
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    return _chunk_reader(), tmp_path


def estimate_excel_size(df: pd.DataFrame) -> int:
    """Rough estimate of the output Excel file size.

    Used to decide whether to use in-memory or streamed export.
    """
    num_cells = len(df) * len(df.columns)
    # Rough: ~10 bytes per cell for xlsxwriter, ~5 for openpyxl
    bytes_per_cell = 12 if _WRITE_ENGINE == "xlsxwriter" else 6
    return num_cells * bytes_per_cell

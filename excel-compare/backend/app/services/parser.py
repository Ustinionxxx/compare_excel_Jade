"""Excel file parsing service."""
from __future__ import annotations

import os
import shutil
import uuid
from pathlib import Path
from typing import Any
from typing import BinaryIO

import pandas as pd

UPLOAD_DIR = Path(__file__).resolve().parent.parent.parent / "uploads"

# Prefer calamine engine for .xlsx (5-10x faster than openpyxl)
try:
    import python_calamine  # noqa: F401
    _HAS_CALAMINE = True
except ImportError:
    _HAS_CALAMINE = False

# Chunk size for streaming file I/O (64KB)
_STREAM_CHUNK = 64 * 1024


def _get_engine(ext: str) -> str:
    """Return the best available Excel engine for the given extension."""
    if ext == ".xls":
        return "xlrd"
    # .xlsx: prefer calamine, fall back to openpyxl
    return "calamine" if _HAS_CALAMINE else "openpyxl"


def _ensure_upload_dir():
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


def save_upload(file_bytes: bytes, original_name: str) -> tuple[str, str]:
    """Save uploaded file (as bytes), return (file_id, saved_path)."""
    _ensure_upload_dir()
    file_id = uuid.uuid4().hex[:12]
    ext = Path(original_name).suffix or ".xlsx"
    filename = f"{file_id}{ext}"
    dest = UPLOAD_DIR / filename
    dest.write_bytes(file_bytes)
    return file_id, str(dest)


def save_upload_streamed(
    source: BinaryIO,
    original_name: str,
) -> tuple[str, str]:
    """Save uploaded file by streaming chunks to disk.

    Uses 64KB chunks to avoid loading the entire file into memory.
    Returns (file_id, saved_path).
    """
    _ensure_upload_dir()
    file_id = uuid.uuid4().hex[:12]
    ext = Path(original_name).suffix or ".xlsx"
    filename = f"{file_id}{ext}"
    dest = UPLOAD_DIR / filename

    with open(dest, "wb") as f:
        shutil.copyfileobj(source, f, length=_STREAM_CHUNK)

    return file_id, str(dest)


def remove_file(file_path: str | None):
    """Remove a file from disk."""
    if file_path and Path(file_path).exists():
        Path(file_path).unlink(missing_ok=True)


def parse_excel(file_path: str) -> tuple[list[str], dict[str, pd.DataFrame]]:
    """
    Parse an Excel file.
    Returns (sheet_names, {sheet_name: dataframe}).
    All column names are converted to strings.
    """
    ext = Path(file_path).suffix.lower()
    engine = _get_engine(ext)

    xls = pd.ExcelFile(file_path, engine=engine)
    sheet_names = xls.sheet_names

    dataframes: dict[str, pd.DataFrame] = {}
    for name in sheet_names:
        df = pd.read_excel(file_path, sheet_name=name, engine=engine, dtype=str, keep_default_na=False)
        df.columns = [str(c).strip() for c in df.columns]
        df.replace({"nan": "", "NaN": "", "None": "", "NaT": "", "<NA>": ""}, inplace=True)
        dataframes[name] = df

    return sheet_names, dataframes


def get_preview(
    df: pd.DataFrame, max_rows: int = 5
) -> tuple[list[str], list[dict[str, Any]], int]:
    """Return (column_names, preview_rows, total_rows)."""
    columns = list(df.columns)
    preview = df.head(max_rows).fillna("").to_dict(orient="records")
    total = len(df)
    return columns, preview, total


def load_dataframe(file_path: str) -> pd.DataFrame:
    """Load an Excel file into a DataFrame, reading all columns as strings."""
    ext = Path(file_path).suffix.lower()
    engine = _get_engine(ext)
    df = pd.read_excel(file_path, engine=engine, dtype=str, keep_default_na=False)
    df.columns = [str(c).strip() for c in df.columns]
    df.replace({"nan": "", "NaN": "", "None": "", "NaT": "", "<NA>": ""}, inplace=True)
    return df

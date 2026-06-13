"""Excel / CSV file parsing service."""
from __future__ import annotations

import csv
import io
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

# Encodings to try for CSV files (in priority order)
_CSV_ENCODINGS = ["utf-8", "utf-8-sig", "gbk", "gb2312", "gb18030", "latin-1"]

# Delimiters to try for CSV detection (in priority order)
_CSV_DELIMITERS = [",", "\t", ";", "|"]


def _get_engine(ext: str) -> str:
    """Return the best available Excel engine for the given extension."""
    if ext == ".xls":
        return "xlrd"
    # .xlsx: prefer calamine, fall back to openpyxl
    return "calamine" if _HAS_CALAMINE else "openpyxl"


def _detect_csv_params(file_path: str) -> tuple[str, str]:
    """Detect encoding and delimiter for a CSV file.

    Strategy:
      1. Try each encoding in priority order (read a sample).
      2. For each encoding, try each delimiter — pick the one that yields
         the most consistent column count across the first 10 lines.
    Returns (encoding, delimiter).
    """
    # Read raw bytes sample (first 64KB is enough for detection)
    with open(file_path, "rb") as f:
        raw = f.read(64 * 1024)

    best_encoding = "utf-8"
    best_delimiter = ","
    best_score = -1

    for enc in _CSV_ENCODINGS:
        try:
            text = raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue

        lines = text.splitlines()
        if len(lines) < 2:
            continue
        # Use first 10 non-empty lines for detection
        sample = [l for l in lines[:20] if l.strip()][:10]
        if len(sample) < 2:
            continue

        for delim in _CSV_DELIMITERS:
            try:
                reader = csv.reader(io.StringIO("\n".join(sample)), delimiter=delim)
                rows = list(reader)
                if len(rows) < 2:
                    continue
                col_counts = [len(r) for r in rows if r]
                if len(col_counts) < 2:
                    continue
                # Score: prefer consistent column count + more columns (≥ 2)
                unique_counts = len(set(col_counts))
                avg_cols = sum(col_counts) / len(col_counts)
                if avg_cols < 2:
                    continue
                # Higher score = more consistent (fewer unique counts) + more columns
                score = (1.0 / (unique_counts + 1)) * avg_cols
                if score > best_score:
                    best_score = score
                    best_encoding = enc
                    best_delimiter = delim
            except Exception:
                continue

    return best_encoding, best_delimiter


def _parse_csv(file_path: str) -> pd.DataFrame:
    """Parse a CSV file with auto-detected encoding and delimiter.

    Returns a single DataFrame (CSV has no concept of sheets).
    All columns are read as strings, NaN/empty values normalized.
    """
    encoding, delimiter = _detect_csv_params(file_path)
    df = pd.read_csv(
        file_path,
        encoding=encoding,
        sep=delimiter,
        dtype=str,
        keep_default_na=False,
        engine="python",  # more robust for non-standard CSVs
    )
    df.columns = [str(c).strip() for c in df.columns]
    df.replace({"nan": "", "NaN": "", "None": "", "NaT": "", "<NA>": ""}, inplace=True)
    return df


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
    Parse an Excel or CSV file.
    Returns (sheet_names, {sheet_name: dataframe}).
    For CSV files, sheet_names contains a single entry derived from the filename.
    All column names are converted to strings.
    """
    ext = Path(file_path).suffix.lower()

    # ── CSV path ──
    if ext == ".csv":
        df = _parse_csv(file_path)
        stem = Path(file_path).stem
        sheet_name = stem if stem else "Sheet1"
        return [sheet_name], {sheet_name: df}

    # ── Excel path ──
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
    """Load an Excel or CSV file into a DataFrame, reading all columns as strings."""
    ext = Path(file_path).suffix.lower()
    if ext == ".csv":
        return _parse_csv(file_path)
    engine = _get_engine(ext)
    df = pd.read_excel(file_path, engine=engine, dtype=str, keep_default_na=False)
    df.columns = [str(c).strip() for c in df.columns]
    df.replace({"nan": "", "NaN": "", "None": "", "NaT": "", "<NA>": ""}, inplace=True)
    return df

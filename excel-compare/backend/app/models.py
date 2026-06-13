"""Pydantic models for request/response schemas."""
from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ── Upload ──

class UploadResponse(BaseModel):
    session_id: str
    file_id: str
    file_name: str
    sheet_names: list[str]
    columns: list[str]
    preview_rows: list[dict[str, Any]]
    total_rows: int


class RemoveFileRequest(BaseModel):
    session_id: str
    file_id: str


class SetFileAliasRequest(BaseModel):
    session_id: str
    file_id: str
    alias: str


# ── Column Mapping ──

class ColumnMapping(BaseModel):
    file_id: str
    key_columns: list[str] = Field(min_length=1)


class SetColumnMappingRequest(BaseModel):
    session_id: str
    mappings: list[ColumnMapping]


# ── Compare ──

class CompareMode(str, Enum):
    all = "all"
    ab = "ab"
    bc = "bc"
    ac = "ac"


class CompareRequest(BaseModel):
    session_id: str
    compare_columns: list[str] = Field(min_length=1)
    mode: CompareMode = CompareMode.all


class CompareResponse(BaseModel):
    session_id: str
    total_keys: int
    matched: int  # exists in all compared tables
    only_a: int = 0
    only_b: int = 0
    only_c: int | None = 0
    column_stats: list[dict[str, Any]]  # [{column, same, diff}]


# ── Detail Query ──

class MatchFilter(str, Enum):
    all = "all"
    matched = "matched"
    only_a = "only_a"
    only_b = "only_b"
    only_c = "only_c"


class DiffFilter(str, Enum):
    all = "all"
    same = "same"
    different = "different"


class DetailQueryRequest(BaseModel):
    session_id: str
    match_filter: MatchFilter = MatchFilter.all
    diff_filter: DiffFilter = DiffFilter.all
    diff_column: str | None = None  # filter by specific column diff
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=50, ge=1, le=500)
    sort_column: str | None = None
    sort_order: str | None = None  # "asc" | "desc"


class DetailRow(BaseModel):
    row_id: str
    match_type: str
    key_values: dict[str, str]  # {"姓名": "张三"}
    data: dict[str, Any]  # {"A_年龄": 25, "B_年龄": 26, "diff_年龄": True}
    diff_columns: list[str]  # columns that differ


class DetailQueryResponse(BaseModel):
    total: int
    page: int
    page_size: int
    rows: list[DetailRow]


# ── Export ──

class ExportRequest(BaseModel):
    session_id: str
    match_filter: MatchFilter = MatchFilter.all
    diff_filter: DiffFilter = DiffFilter.all
    diff_column: str | None = None
    file_ids: list[str] | None = None  # export raw source files
    export_type: str = "filtered"  # "filtered" | "raw_a" | "raw_b" | "raw_c"
    include_diff_columns: bool = False  # 需求 5：是否导出差异列
    file_aliases: dict[str, str] | None = None  # file_id -> alias for column prefix

"""Multi-table match & diff engine."""
from __future__ import annotations

import logging
import os
import pickle as _pickle
import threading
import time
import uuid
from collections import OrderedDict
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

_logger = logging.getLogger("excel_compare.matcher")


# Match type labels matching the frontend table display
MATCH_TYPE_LABELS: dict[str, str] = {
    "matched": "✓ 匹配",
    "only_a": "仅 A",
    "only_b": "仅 B",
    "only_c": "仅 C",
    "only_a_b": "A+B（缺C）",
    "only_a_c": "A+C（缺B）",
    "only_b_c": "B+C（缺A）",
}

# Diff category labels for export display
DIFF_CATEGORY_LABELS: dict[str, str] = {
    "value_match": "三表一致",
    "t1_diff": "A表独异",
    "t2_diff": "B表独异",
    "t3_diff": "C表独异",
    "all_diff": "三方互异",
}


def _normalize_keys(df: pd.DataFrame, key_cols: list[str]) -> pd.DataFrame:
    """Normalize key columns to string, fill NaN, return with a composite key."""
    wk = df.copy()
    for col in key_cols:
        if col in wk.columns:
            wk[col] = wk[col].astype(str).fillna("__NULL__").str.strip()
    wk["__composite_key"] = wk[key_cols].astype(str).agg("||".join, axis=1)
    return wk


def _prefix_columns(columns: list[str], prefix: str, key_cols: list[str]) -> list[str]:
    """Prefix non-key columns with table indicator."""
    result = []
    for c in columns:
        if c in key_cols:
            result.append(c)
        else:
            result.append(f"{prefix}_{c}")
    return result


class DiffEngine:
    """Holds parsed data and computed diff results for a session."""

    def __init__(self):
        self.files: dict[str, dict[str, Any]] = {}  # file_id -> metadata
        self.key_mappings: dict[str, list[str]] = {}  # file_id -> key_columns
        self.compare_columns: list[str] = []
        self.result_df: pd.DataFrame | None = None  # joined + diff result
        self.column_stats: list[dict] = []
        self.total_keys: int = 0
        self.matched: int = 0
        self.only_a: int = 0
        self.only_b: int = 0
        self.only_c: int | None = 0
        self.compare_mode: str = "all"

    def register_file(self, file_id: str, file_name: str, path: str, sheet: str, df: pd.DataFrame):
        # Default alias: filename without extension
        default_alias = Path(file_name).stem if file_name else file_name
        self.files[file_id] = {
            "file_name": file_name,
            "file_alias": default_alias,
            "alias_set_by_user": False,
            "path": path,
            "sheet": sheet,
            "df": df,
            "columns": list(df.columns),
        }

    def set_file_alias(self, file_id: str, alias: str):
        if file_id in self.files:
            self.files[file_id]["file_alias"] = alias
            self.files[file_id]["alias_set_by_user"] = True

    def set_key_mapping(self, mappings: dict[str, list[str]]):
        self.key_mappings = mappings

    def compute(
        self,
        compare_columns: list[str],
        mode: str = "all",
        on_progress: "callable | None" = None,
    ):
        """Execute the full diff computation.

        Args:
            compare_columns: Columns to diff.
            mode: "all" | "ab" | "ac" | "bc".
            on_progress: Optional callback(stage, progress, message) for real-time
                         progress reporting during long-running comparisons.
        """
        self.compare_columns = compare_columns
        self.compare_mode = mode

        file_ids = list(self.files.keys())
        if len(file_ids) < 2:
            raise ValueError("Need at least 2 files to compare")

        # Determine which files to compare
        if mode == "all":
            compare_ids = file_ids
        elif mode == "ab":
            compare_ids = file_ids[:2]
        elif mode == "ac":
            compare_ids = [file_ids[0], file_ids[2]] if len(file_ids) > 2 else file_ids[:2]
        elif mode == "bc":
            compare_ids = [file_ids[1], file_ids[2]] if len(file_ids) > 2 else file_ids[-2:]
        else:
            compare_ids = file_ids

        # Validate that all files being compared have key mappings
        for fid in compare_ids:
            if fid not in self.key_mappings:
                raise ValueError(f"Key mapping not configured for file {self.files.get(fid, {}).get('file_name', fid)}")

        labels = {fid: chr(65 + i) for i, fid in enumerate(file_ids)}  # A, B, C
        labels_in_use = {fid: labels[fid] for fid in compare_ids}

        # Phase 1: Prepare normalized dataframes
        if on_progress:
            on_progress("preparing", 20, "正在规范化数据...")
        prepared: dict[str, pd.DataFrame] = {}
        for fid in compare_ids:
            df = self.files[fid]["df"]
            keys = self.key_mappings[fid]
            prepared[fid] = _normalize_keys(df, keys)

        # Merge with outer join to find match status.
        # Add boolean presence markers BEFORE merging — each marker is a single
        # column, avoiding the expensive all-column iteration of _has_data().
        for fid in compare_ids:
            prepared[fid]["__present"] = True

        # Phase 2: Execute outer join merges
        if on_progress:
            on_progress("merging", 35, "正在执行表外连接...")
        merge_keys = ["__composite_key"]
        merged = None
        for i, fid in enumerate(compare_ids):
            pk = prepared[fid][merge_keys + prepared[fid].columns.difference(merge_keys).tolist()]
            suffix_label = labels_in_use[fid]
            pk = pk.add_suffix(f"__{suffix_label}")
            pk.rename(columns={f"__composite_key__{suffix_label}": "__composite_key"}, inplace=True)

            if merged is None:
                merged = pk
            else:
                is_last = (i == len(compare_ids) - 1)
                merged = pd.merge(
                    merged, pk,
                    on="__composite_key",
                    how="outer",
                    indicator=is_last,  # only last merge gets indicator
                )
            if on_progress:
                on_progress("merging", 35 + (i + 1) * (25 // len(compare_ids)),
                           f"正在执行表外连接 ({i+1}/{len(compare_ids)})...")

        # Phase 3: Build match type column
        if on_progress:
            on_progress("matching", 60, "正在计算匹配类型...")
        if len(compare_ids) == 2:
            merged["__match_type"] = merged["_merge"].map({
                "both": "matched",
                "left_only": f"only_{labels_in_use[compare_ids[0]].lower()}",
                "right_only": f"only_{labels_in_use[compare_ids[1]].lower()}",
            })
            merged.drop(columns=["_merge"], inplace=True)
            self.only_a = int((merged["__match_type"] == f"only_{labels_in_use[compare_ids[0]].lower()}").sum())
            self.only_b = int((merged["__match_type"] == f"only_{labels_in_use[compare_ids[1]].lower()}").sum())
            self.only_c = 0
        elif len(compare_ids) == 3:
            merged.drop(columns=["_merge"], inplace=True)
            self._compute_3way_match_type(merged, compare_ids, labels_in_use)

        self.matched = int((merged["__match_type"] == "matched").sum())
        self.total_keys = len(merged)

        # Phase 4: Fill NaN in all data columns
        if on_progress:
            on_progress("filling", 75, "正在填充缺失值...")
        data_cols = [c for c in merged.columns if not c.startswith("__")]
        # Handle categorical columns first (rare), then fill remaining in one shot
        cat_cols = [c for c in data_cols if isinstance(merged[c].dtype, pd.CategoricalDtype)]
        for col in cat_cols:
            merged[col] = merged[col].cat.add_categories("").fillna("")
        non_cat_cols = [c for c in data_cols if c not in cat_cols]
        if non_cat_cols:
            merged[non_cat_cols] = merged[non_cat_cols].fillna("")

        # Phase 5: Compute column diffs — 5-category classification
        if on_progress:
            on_progress("diffing", 85, "正在计算列差异（五类分类）...")
        diff_cols = []
        for col in compare_columns:
            col_values: list[str] = []
            for fid in compare_ids:
                label = labels_in_use[fid]
                col_values.append(f"{col}__{label}")
            if all(c in merged.columns for c in col_values):
                diff_col = f"__diff_{col}"
                vals = [merged[c].astype(str) for c in col_values]

                # Default: all values equal
                diff_cat = pd.Series("value_match", index=merged.index)

                if len(vals) == 2:
                    # 2-way: only value_match or t1_diff
                    diff_cat[vals[0] != vals[1]] = "t1_diff"
                else:
                    # 3-way: full 5-category classification
                    v0, v1, v2 = vals
                    eq01 = v0 == v1
                    eq02 = v0 == v2
                    eq12 = v1 == v2

                    # Order matters: more-specific masks override earlier ones
                    # T2=T3 but T1≠T2 → T1 is the outlier
                    diff_cat[eq12 & ~eq01] = "t1_diff"
                    # T1=T3 but T1≠T2 → T2 is the outlier
                    diff_cat[eq02 & ~eq01] = "t2_diff"
                    # T1=T2 but T1≠T3 → T3 is the outlier
                    diff_cat[eq01 & ~eq02] = "t3_diff"
                    # All three differ from each other
                    diff_cat[~eq01 & ~eq02 & ~eq12] = "all_diff"

                # Non-matched rows → empty string (no meaningful comparison)
                matched_mask = merged["__match_type"] == "matched"
                diff_cat[~matched_mask] = ""
                merged[diff_col] = diff_cat
                diff_cols.append(diff_col)

        # Phase 6: Extract key column values (from already-filled data)
        if on_progress:
            on_progress("extracting", 92, "正在提取主键值...")
        key_cols = self.key_mappings[compare_ids[0]]
        first_label = labels_in_use[compare_ids[0]]
        for col in key_cols:
            src = f"{col}__{first_label}"
            if src in merged.columns:
                merged[f"__key_{col}"] = merged[src].astype(str)
            else:
                merged[f"__key_{col}"] = ""

        # Phase 7: Compute column stats — 5-category breakdown (matched rows only)
        if on_progress:
            on_progress("stats", 96, "正在生成统计摘要...")
        matched_mask = merged["__match_type"] == "matched"
        self.column_stats = []
        for col, diff_col in zip(compare_columns, diff_cols):
            if diff_col in merged.columns:
                cat_series = merged.loc[matched_mask, diff_col]
                self.column_stats.append({
                    "column": col,
                    "value_match": int((cat_series == "value_match").sum()),
                    "t1_diff": int((cat_series == "t1_diff").sum()),
                    "t2_diff": int((cat_series == "t2_diff").sum()),
                    "t3_diff": int((cat_series == "t3_diff").sum()),
                    "all_diff": int((cat_series == "all_diff").sum()),
                })

        self.result_df = merged
        return self._make_summary()

    def _compute_3way_match_type(self, df: pd.DataFrame, compare_ids: list[str], labels: dict):
        """Determine match types for 3-way outer join — fully vectorized.

        Assigns __match_type per row:
          'matched'            — present in ALL 3 tables
          'only_a' / 'only_b' / 'only_c'  — present in exactly ONE table
          'only_a_b' / 'only_a_c' / 'only_b_c' — present in exactly TWO tables

        Uses boolean presence marker columns (__present__A, __present__B, …)
        added before the outer join.  After an outer join, only rows that
        actually came from a table have __present__X == True; rows that
        were solely contributed by other tables have __present__X == NaN.

        This avoids iterating over every data column (50-150+ columns per
        table) to detect presence, which was the #1 bottleneck for large
        multi-table comparisons (16 MB × 3).
        """
        a_label = labels[compare_ids[0]].lower()
        b_label = labels[compare_ids[1]].lower()
        c_label = labels[compare_ids[2]].lower()

        # Presence markers: True where the row came from that table.
        # After an outer join, the __present__ column is True for rows
        # that actually came from that table and NaN for rows that were
        # contributed by other tables only.  Use .eq(True) to avoid
        # fillna-downcast FutureWarnings (pandas 2.x → 3.x migration).
        a_col = f"__present__{labels[compare_ids[0]]}"
        b_col = f"__present__{labels[compare_ids[1]]}"
        c_col = f"__present__{labels[compare_ids[2]]}"
        present_a = df[a_col].eq(True) if a_col in df.columns else pd.Series(False, index=df.index)
        present_b = df[b_col].eq(True) if b_col in df.columns else pd.Series(False, index=df.index)
        present_c = df[c_col].eq(True) if c_col in df.columns else pd.Series(False, index=df.index)

        # Count presence (vectorized integer sum)
        presence_count = present_a.astype(int) + present_b.astype(int) + present_c.astype(int)

        # Build match_type using vectorized boolean indexing.
        match_type = pd.Series("unknown", index=df.index)

        # 3 tables present → matched
        match_type[presence_count == 3] = "matched"

        # 2 tables present → only_X_Y
        match_type[(presence_count == 2) & present_a & present_b] = f"only_{a_label}_{b_label}"
        match_type[(presence_count == 2) & present_a & present_c] = f"only_{a_label}_{c_label}"
        match_type[(presence_count == 2) & present_b & present_c] = f"only_{b_label}_{c_label}"

        # 1 table present → only_X
        match_type[(presence_count == 1) & present_a] = f"only_{a_label}"
        match_type[(presence_count == 1) & present_b] = f"only_{b_label}"
        match_type[(presence_count == 1) & present_c] = f"only_{c_label}"

        df["__match_type"] = match_type

        # Drop presence marker columns now that match types are computed
        present_cols = [
            c for c in df.columns
            if c.startswith("__present__")
        ]
        if present_cols:
            df.drop(columns=present_cols, inplace=True)

        self.only_a = int((match_type == f"only_{a_label}").sum())
        self.only_b = int((match_type == f"only_{b_label}").sum())
        self.only_c = int((match_type == f"only_{c_label}").sum())

    def _build_match_type_labels(self) -> dict[str, str]:
        """Build display labels for match types using file aliases.

        Generates a mapping from internal match_type keys ("only_a", "only_b", …)
        to human-readable labels that use the user-set file aliases.

        Examples (2 files):
          "only_a" → "仅 采购单 存在"
          "only_b" → "仅 销售单 存在"
        Examples (3 files):
          "only_a_b" → "采购单+销售单（缺库存单）"
        """
        file_ids = list(self.files.keys())
        label_letters = {fid: chr(65 + i) for i, fid in enumerate(file_ids)}  # A, B, C

        # Resolve display alias per file: user alias > filename stem > letter
        aliases: dict[str, str] = {}
        for fid, letter in label_letters.items():
            info = self.files.get(fid, {})
            alias = info.get("file_alias", "")
            if not alias:
                fname = info.get("file_name", "")
                alias = Path(fname).stem if fname else f"{letter}表"
            aliases[letter.lower()] = alias

        labels: dict[str, str] = {"matched": "✓ 匹配"}

        # Single-file-only labels
        for letter_lower, alias in aliases.items():
            labels[f"only_{letter_lower}"] = f"仅 {alias} 存在"

        # 2-of-3 labels (only when 3 files uploaded)
        if len(file_ids) >= 3:
            combos = [("a", "b", "c"), ("a", "c", "b"), ("b", "c", "a")]
            for x, y, z in combos:
                labels[f"only_{x}_{y}"] = (
                    f"{aliases.get(x, x.upper())}+{aliases.get(y, y.upper())}"
                    f"（缺{aliases.get(z, z.upper())}）"
                )

        return labels

    def _make_summary(self) -> dict:
        return {
            "total_keys": self.total_keys,
            "matched": self.matched,
            "only_a": self.only_a,
            "only_b": self.only_b,
            "only_c": self.only_c,
            "column_stats": self.column_stats,
            "match_type_labels": self._build_match_type_labels(),
        }

    def get_detail_page(
        self,
        match_filter: str = "all",
        diff_filter: str = "all",
        diff_column: str | None = None,
        page: int = 1,
        page_size: int = 50,
        sort_column: str | None = None,
        sort_order: str | None = None,
    ) -> dict:
        """Get paginated detail rows (vectorized, no iterrows)."""
        if self.result_df is None:
            return {"total": 0, "page": page, "page_size": page_size, "rows": []}

        # Build filter mask first, then apply it once
        mask = pd.Series(True, index=self.result_df.index)

        if match_filter != "all":
            mask &= self.result_df["__match_type"] == match_filter

        if diff_filter != "all" or diff_column:
            diff_cols_list = [c for c in self.result_df.columns if c.startswith("__diff_")]
            if diff_column:
                target = f"__diff_{diff_column}"
                if target in self.result_df.columns:
                    vals = self.result_df[target].fillna("")
                    if diff_filter == "same":
                        mask &= vals == "value_match"
                    elif diff_filter == "different":
                        mask &= (vals != "") & (vals != "value_match")
                    elif diff_filter in ("value_match", "t1_diff", "t2_diff", "t3_diff", "all_diff"):
                        mask &= vals == diff_filter
            else:
                if diff_filter == "same":
                    for dc in diff_cols_list:
                        mask &= self.result_df[dc].fillna("") == "value_match"
                elif diff_filter == "different":
                    any_diff_mask = pd.Series(False, index=self.result_df.index)
                    for dc in diff_cols_list:
                        vals = self.result_df[dc].fillna("")
                        any_diff_mask |= (vals != "") & (vals != "value_match")
                    mask &= any_diff_mask
                elif diff_filter in ("value_match", "t1_diff", "t2_diff", "t3_diff", "all_diff"):
                    cat_mask = pd.Series(False, index=self.result_df.index)
                    for dc in diff_cols_list:
                        cat_mask |= self.result_df[dc].fillna("") == diff_filter
                    mask &= cat_mask

        df = self.result_df.loc[mask]
        total = len(df)

        # Sort
        if sort_column and sort_order and sort_column in df.columns:
            df = df.sort_values(by=sort_column, ascending=(sort_order == "asc"))

        # Paginate
        offset = (page - 1) * page_size
        page_df = df.iloc[offset: offset + page_size]

        if len(page_df) == 0:
            rows = []
        else:
            file_ids = list(self.files.keys())
            labels = {fid: chr(65 + i) for i, fid in enumerate(file_ids)}
            diff_col_names = [c for c in page_df.columns if c.startswith("__diff_")]
            key_cols = self.key_mappings.get(file_ids[0], [])
            label_set = set(labels.values())

            # Convert to list of dicts once (avoids iterrows overhead)
            records = page_df.to_dict(orient="records")

            rows_list = []
            for idx, rec in enumerate(records):
                # Key values
                key_values = {}
                for kc in key_cols:
                    val = ""
                    for src in [f"__key_{kc}"] + [f"{kc}__{l}" for l in label_set] + [kc]:
                        v = rec.get(src)
                        if v is not None and v != "" and not pd.isna(v):
                            val = str(v)
                            break
                    key_values[kc] = val

                # Data organized by table (clean keys: "A_Name", "B_Age")
                data = {}
                diff_columns = []
                for fid in file_ids:
                    label = labels[fid]
                    file_info = self.files[fid]
                    for col in file_info["columns"]:
                        src_col = f"{col}__{label}"
                        val = rec.get(src_col, "")
                        data[f"{label}_{col}"] = str(val) if val is not None else ""

                # Diff flags — store category string, not boolean
                for dc in diff_col_names:
                    col_name = dc.replace("__diff_", "")
                    diff_cat = str(rec.get(dc, ""))
                    data[dc] = diff_cat
                    # Include in diff_columns if any kind of difference
                    if diff_cat not in ("", "value_match"):
                        diff_columns.append(col_name)

                rows_list.append({
                    "row_id": str(idx + offset),
                    "match_type": str(rec.get("__match_type", "")),
                    "key_values": key_values,
                    "data": data,
                    "diff_columns": diff_columns,
                })

        return {
            "total": total,
            "page": page,
            "page_size": page_size,
            "rows": rows_list if len(page_df) > 0 else [],
            "match_type_labels": self._build_match_type_labels(),
        }

    def get_export_df(
        self,
        match_filter: str = "all",
        diff_filter: str = "all",
        diff_column: str | None = None,
        file_ids: list[str] | None = None,
        export_type: str = "filtered",
        *,
        include_diff_columns: bool = False,
        file_aliases: dict[str, str] | None = None,
    ) -> pd.DataFrame:
        """Get filtered DataFrame for export.

        Columns match the detail table display:
          - 匹配类型 (Chinese label)
          - 主键：{key_name} for each key column
          - {label}_{col} for each data column per table
          - 差异_{col} for each compared column (when include_diff_columns=True)

        When match_filter is only_a / only_b / only_c:
          - Only columns from that table are included
          - No diff columns
        """
        if export_type.startswith("raw_"):
            # Export single source table — use original column names
            target_label = export_type[-1].upper()
            for fid, info in self.files.items():
                label = chr(65 + list(self.files.keys()).index(fid))
                if label == target_label:
                    return info["df"].rename(columns=info["df"].columns)
            return pd.DataFrame()

        if self.result_df is None:
            return pd.DataFrame()

        df = self.result_df.copy()

        # ── Apply filters (same logic as get_detail_page) ──
        mask = pd.Series(True, index=df.index)

        if match_filter != "all":
            mask &= df["__match_type"] == match_filter

        if diff_filter != "all" or diff_column:
            diff_cols_list = [c for c in df.columns if c.startswith("__diff_")]
            if diff_column:
                target = f"__diff_{diff_column}"
                if target in df.columns:
                    vals = df[target].fillna("")
                    if diff_filter == "same":
                        mask &= vals == "value_match"
                    elif diff_filter == "different":
                        mask &= (vals != "") & (vals != "value_match")
                    elif diff_filter in ("value_match", "t1_diff", "t2_diff", "t3_diff", "all_diff"):
                        mask &= vals == diff_filter
            else:
                if diff_filter == "same":
                    for dc in diff_cols_list:
                        mask &= df[dc].fillna("") == "value_match"
                elif diff_filter == "different":
                    any_diff_mask = pd.Series(False, index=df.index)
                    for dc in diff_cols_list:
                        vals = df[dc].fillna("")
                        any_diff_mask |= (vals != "") & (vals != "value_match")
                    mask &= any_diff_mask
                elif diff_filter in ("value_match", "t1_diff", "t2_diff", "t3_diff", "all_diff"):
                    cat_mask = pd.Series(False, index=df.index)
                    for dc in diff_cols_list:
                        cat_mask |= df[dc].fillna("") == diff_filter
                    mask &= cat_mask

        df = df.loc[mask]

        if df.empty:
            return df

        # ── 差异明细格式 (long format, one row per differing column) ──
        if export_type == "diff_detail":
            return self._get_diff_detail_export_df(df, file_aliases)

        # ── Build export columns (wide format) matching table display ──
        file_ids = list(self.files.keys())
        labels = {fid: chr(65 + i) for i, fid in enumerate(file_ids)}
        aliases = file_aliases or {}
        first_fid = file_ids[0]

        # Determine if this is a single-table export (only_a/b/c)
        is_single_table = match_filter in ("only_a", "only_b", "only_c")
        single_label = match_filter[-1].upper() if is_single_table else None

        result: OrderedDict[str, pd.Series] = OrderedDict()

        # 1. Match type — dynamic labels using file aliases
        _export_labels = self._build_match_type_labels()
        result["匹配类型"] = df["__match_type"].map(
            lambda x: _export_labels.get(x, x)
        )

        # 2. Key columns
        key_cols = self.key_mappings.get(first_fid, [])
        for col in key_cols:
            src = f"__key_{col}"
            if src in df.columns:
                result[f"主键：{col}"] = df[src].astype(str)
            elif col in df.columns:
                result[f"主键：{col}"] = df[col].astype(str)
            else:
                result[f"主键：{col}"] = ""

        # 3. Data columns — use alias as prefix where available
        for fid in file_ids:
            label = labels[fid]

            # 需求 4: only_a/b/c — skip other tables' columns
            if is_single_table and label != single_label:
                continue

            prefix = (
                aliases.get(fid)
                or self.files.get(fid, {}).get("file_alias")
                or label
            )
            file_info = self.files[fid]
            for col in file_info["columns"]:
                src = f"{col}__{label}"
                if src in df.columns:
                    result[f"{prefix}_{col}"] = df[src].astype(str)

        # 4. Diff columns — only if requested and not a single-table export
        if include_diff_columns and not is_single_table:
            for col in self.compare_columns:
                diff_col = f"__diff_{col}"
                if diff_col in df.columns:
                    result[f"差异_{col}"] = df[diff_col].map(
                        DIFF_CATEGORY_LABELS
                    ).fillna("")

        return pd.DataFrame(result)

    def _get_diff_detail_export_df(
        self,
        df: pd.DataFrame,
        file_aliases: dict[str, str] | None = None,
    ) -> pd.DataFrame:
        """Build long-format diff detail — one row per differing column.

        Columns for 2-file: 主键：{key1}, ..., 差异的字段名, {alias_A}, {alias_B}
        Columns for 3-file: 主键：{key1}, ..., 差异的字段名, {alias_A}, {alias_B}, {alias_C}

        Column names for table values use file aliases (user-set or filename stem).
        Each matched record with N differing columns produces N rows.

        Fully vectorized — no iterrows(), ~10-50x faster on large DataFrames.
        """
        file_ids = list(self.files.keys())
        labels = {fid: chr(65 + i) for i, fid in enumerate(file_ids)}
        key_cols = self.key_mappings.get(file_ids[0], [])

        # Resolve aliases for table value column names
        aliases = file_aliases or {}

        def _alias(fid: str, fallback_label: str) -> str:
            if fid in aliases:
                return aliases[fid]
            finfo = self.files.get(fid, {})
            if finfo.get("alias_set_by_user"):
                return finfo.get("file_alias", fallback_label)
            return f"{fallback_label}表"

        # Resolve column names for each table
        table_col_names: list[str] = []
        table_src_templates: list[str | None] = []
        for i, fid in enumerate(file_ids):
            label = labels[fid]
            table_col_names.append(_alias(fid, label))
            table_src_templates.append(f"{{}}__{label}")

        # Build diff column mapping: __diff_{col} → col name
        diff_col_map = {f"__diff_{c}": c for c in self.compare_columns}
        diff_col_names = [d for d in diff_col_map if d in df.columns]
        if not diff_col_names:
            return pd.DataFrame()

        # Only keep rows that have at least one diff (non-empty, non-value_match)
        has_any_diff = pd.Series(False, index=df.index)
        for dc in diff_col_names:
            if dc in df.columns:
                vals = df[dc].fillna("")
                has_any_diff |= (vals != "") & (vals != "value_match")
        diff_df = df.loc[has_any_diff]
        if len(diff_df) == 0:
            return pd.DataFrame()

        frames: list[pd.DataFrame] = []
        for diff_col, cmp_col in diff_col_map.items():
            if diff_col not in diff_df.columns:
                continue

            # Select rows where this specific column differs
            col_series = diff_df[diff_col].fillna("")
            col_mask = (col_series != "") & (col_series != "value_match")
            subset = diff_df.loc[col_mask]
            if len(subset) == 0:
                continue

            # Build the output subset as a DataFrame (vectorized)
            result_cols: OrderedDict[str, pd.Series] = OrderedDict()

            # Key columns
            for col in key_cols:
                key_src = f"__key_{col}"
                val = subset.get(key_src, pd.Series("", index=subset.index))
                result_cols[f"主键：{col}"] = val.fillna("").astype(str)

            # Column name
            result_cols["差异的字段名"] = pd.Series(cmp_col, index=subset.index)

            # Table values (A, B, ... for 2-way; A, B, C for 3-way)
            for table_idx, src_tpl in enumerate(table_src_templates):
                col_name = table_col_names[table_idx]
                src = src_tpl.format(cmp_col)
                val = subset.get(src, pd.Series("", index=subset.index))
                result_cols[col_name] = val.fillna("").astype(str)

            frames.append(pd.DataFrame(result_cols))

        if not frames:
            return pd.DataFrame()

        result = pd.concat(frames, ignore_index=True)
        columns = [f"主键：{c}" for c in key_cols] + ["差异的字段名"] + table_col_names
        return result[columns]


# In-memory session store with TTL-based expiry and filesystem fallback.
# Filesystem storage is critical for multi-worker (gunicorn) deployments:
# a session created on Worker A is pickled to disk, and Worker B can
# load it from disk when it receives a request for that session.

# Default session TTL: 30 minutes of inactivity
SESSION_TTL_SECONDS = 30 * 60
# Cleanup interval: run garbage collection every 5 minutes
_CLEANUP_INTERVAL = 5 * 60

_SESSIONS_DIR = Path(__file__).resolve().parent.parent.parent / "uploads" / "sessions"

_sessions: dict[str, DiffEngine] = {}
_session_last_access: dict[str, float] = {}  # session_id -> last access timestamp
_session_cached_at: dict[str, float] = {}  # session_id -> file mtime at cache time (for staleness check)
_session_cached_size: dict[str, int] = {}  # session_id -> file size at cache time (extra staleness check)
_sessions_lock = threading.Lock()
_cleanup_started = False


def _session_file(session_id: str) -> Path:
    """Return the pickle path for a session."""
    safe_id = session_id.replace("/", "_").replace("\\", "_")
    return _SESSIONS_DIR / f"{safe_id}.pickle"


def _save_session_to_disk(session_id: str, engine: DiffEngine) -> None:
    """Persist a session to disk so other workers can access it."""
    try:
        _SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        tmp = _session_file(session_id).with_suffix(".tmp")
        with open(tmp, "wb") as f:
            _pickle.dump(engine, f, protocol=_pickle.HIGHEST_PROTOCOL)
        os.replace(tmp, _session_file(session_id))
    except Exception as e:
        _logger.warning("Failed to persist session %s to disk: %s", session_id, e)


def _load_session_from_disk(session_id: str) -> DiffEngine | None:
    """Try to load a session from disk (cross-worker fallback).

    Also touches the file's mtime so that cleanup threads on other workers
    can see this session is still alive — prevents premature deletion of
    the shared pickle file.
    """
    try:
        path = _session_file(session_id)
        if path.exists():
            # Update mtime to signal "this session is in use" to other workers'
            # cleanup threads (cheap metadata-only operation).
            os.utime(path, None)
            with open(path, "rb") as f:
                return _pickle.load(f)
    except Exception:
        pass
    return None


def _delete_session_from_disk(session_id: str) -> None:
    """Remove a session pickle file."""
    try:
        path = _session_file(session_id)
        if path.exists():
            path.unlink()
    except Exception:
        pass


def _touch_session(session_id: str):
    """Update the last-access timestamp for a session."""
    with _sessions_lock:
        if session_id in _sessions:
            _session_last_access[session_id] = time.time()


def _cleanup_expired_sessions():
    """Remove sessions that have exceeded the TTL.

    Checks BOTH the in-memory last_access time AND the pickle file's mtime
    on disk.  The disk check is critical: Worker A must not delete a session
    pickle file that Worker B is still actively using (and updating via
    persist_session).  Only when BOTH indicators show expiry is it safe to
    delete the shared file.
    """
    now = time.time()
    with _sessions_lock:
        expired = [
            sid for sid, last_access in _session_last_access.items()
            if now - last_access > SESSION_TTL_SECONDS
        ]
        for sid in expired:
            # Before deleting the shared pickle file, check whether another
            # worker recently touched it (via persist_session).  If the file
            # mtime indicates recent activity, skip cleanup — another worker
            # is still using this session.
            try:
                pf = _session_file(sid)
                if pf.exists():
                    file_age = now - pf.stat().st_mtime
                    if file_age < SESSION_TTL_SECONDS:
                        # Another worker touched this file recently — do NOT
                        # delete it.  Just remove our stale local cache entry.
                        _sessions.pop(sid, None)
                        _session_last_access.pop(sid, None)
                        _session_cached_at.pop(sid, None)
                        _session_cached_size.pop(sid, None)
                        continue
            except Exception:
                pass

            engine = _sessions.pop(sid, None)
            _session_last_access.pop(sid, None)
            _session_cached_at.pop(sid, None)
            _session_cached_size.pop(sid, None)
            # Clean up uploaded files on disk
            if engine:
                for info in engine.files.values():
                    path = info.get("path", "")
                    if path and Path(path).exists():
                        try:
                            Path(path).unlink(missing_ok=True)
                        except OSError:
                            pass
            # Clean up session pickle
            _delete_session_from_disk(sid)


def _start_cleanup_timer():
    """Start a background thread that periodically cleans up expired sessions."""
    global _cleanup_started
    with _sessions_lock:
        if _cleanup_started:
            return
        _cleanup_started = True

    def _cleanup_loop():
        while True:
            time.sleep(_CLEANUP_INTERVAL)
            try:
                _cleanup_expired_sessions()
            except Exception:
                pass

    t = threading.Thread(target=_cleanup_loop, daemon=True, name="session-cleanup")
    t.start()


def get_session(session_id: str) -> DiffEngine | None:
    """Get a session, falling back to disk for cross-worker access.

    CRITICAL: on cache hit, checks whether the pickle file on disk was
    modified since we cached the session.  Uses BOTH mtime AND file size
    — on filesystems with coarse mtime granularity (WSL2, NFS, ...) the
    mtime may not change for rapid writes, but the file size will differ
    because the pickle payload changes (e.g. when a file is added).
    """
    _start_cleanup_timer()
    engine = _sessions.get(session_id)
    if engine is not None:
        now = time.time()
        cached_mtime = _session_cached_at.get(session_id)
        cached_size = _session_cached_size.get(session_id)
        if cached_mtime is not None:
            try:
                pf = _session_file(session_id)
                if pf.exists():
                    st = pf.stat()
                    # Check BOTH mtime and size — either changing means
                    # another worker persisted a new version.
                    mtime_changed = st.st_mtime != cached_mtime
                    size_changed = cached_size is not None and st.st_size != cached_size
                    if mtime_changed or size_changed:
                        _logger.debug("Session %s cache is stale (mtime:%s size:%s), reloading",
                                      session_id,
                                      "changed" if mtime_changed else "same",
                                      "changed" if size_changed else "same")
                        fresh = _load_session_from_disk(session_id)
                        if fresh is not None:
                            with _sessions_lock:
                                _sessions[session_id] = fresh
                                _session_last_access[session_id] = now
                                _session_cached_at[session_id] = st.st_mtime
                                _session_cached_size[session_id] = st.st_size
                            return fresh
            except Exception:
                pass  # If we can't check, trust the cache

        _touch_session(session_id)
        return engine

    # Cache miss — try loading from disk (may have been created by another worker)
    engine = _load_session_from_disk(session_id)
    if engine is not None:
        now = time.time()
        with _sessions_lock:
            if session_id not in _sessions:
                _sessions[session_id] = engine
                _session_last_access[session_id] = now
                # Record the file metadata we loaded
                try:
                    pf = _session_file(session_id)
                    if pf.exists():
                        st = pf.stat()
                        _session_cached_at[session_id] = st.st_mtime
                        _session_cached_size[session_id] = st.st_size
                except Exception:
                    _session_cached_at[session_id] = now
        return engine
    return None


def create_session() -> str:
    _start_cleanup_timer()
    session_id = uuid.uuid4().hex[:16]
    engine = DiffEngine()
    now = time.time()
    with _sessions_lock:
        _sessions[session_id] = engine
        _session_last_access[session_id] = now
    # Persist to disk so other workers can find this session
    _save_session_to_disk(session_id, engine)
    # Record the file's actual metadata after writing
    try:
        pf = _session_file(session_id)
        if pf.exists():
            st = pf.stat()
            with _sessions_lock:
                _session_cached_at[session_id] = st.st_mtime
                _session_cached_size[session_id] = st.st_size
    except Exception:
        with _sessions_lock:
            _session_cached_at[session_id] = now
    return session_id


def persist_session(session_id: str) -> None:
    """If the session is in local cache, flush it to disk.

    Call this after any mutation to the session (key mappings, file add/remove)
    so that other gunicorn workers can pick up the latest state.
    """
    with _sessions_lock:
        engine = _sessions.get(session_id)
    if engine is not None:
        _save_session_to_disk(session_id, engine)
        # Record the file's actual metadata so we can detect external changes
        try:
            pf = _session_file(session_id)
            if pf.exists():
                st = pf.stat()
                with _sessions_lock:
                    _session_cached_at[session_id] = st.st_mtime
                    _session_cached_size[session_id] = st.st_size
        except Exception:
            pass


def remove_session(session_id: str):
    with _sessions_lock:
        _sessions.pop(session_id, None)
        _session_last_access.pop(session_id, None)
        _session_cached_at.pop(session_id, None)
        _session_cached_size.pop(session_id, None)
    _delete_session_from_disk(session_id)

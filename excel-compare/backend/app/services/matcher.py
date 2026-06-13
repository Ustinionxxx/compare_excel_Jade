"""Multi-table match & diff engine."""
from __future__ import annotations

import threading
import time
import uuid
from collections import OrderedDict
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


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

    def compute(self, compare_columns: list[str], mode: str = "all"):
        """Execute the full diff computation."""
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

        # Prepare normalized dataframes
        prepared: dict[str, pd.DataFrame] = {}
        for fid in compare_ids:
            df = self.files[fid]["df"]
            keys = self.key_mappings[fid]
            prepared[fid] = _normalize_keys(df, keys)

        # Merge with outer join to find match status
        # Use indicator only on the last merge to avoid column name conflicts
        merged = None
        merge_keys = ["__composite_key"]
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

        # Build match type column based on indicator (2-way) or null analysis (3-way)
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

        # ── Step 1: fill NaN in all data columns (safe, handles all dtypes) ──
        data_cols = [c for c in merged.columns if not c.startswith("__")]
        for col in data_cols:
            col_dtype = merged[col].dtype
            if isinstance(col_dtype, pd.CategoricalDtype):
                merged[col] = merged[col].cat.add_categories("").fillna("")
            else:
                merged[col] = merged[col].fillna("")

        # ── Step 2: compute column diffs (cast to str for safe comparison) ──
        diff_cols = []
        for col in compare_columns:
            col_values: list[str] = []
            for fid in compare_ids:
                label = labels_in_use[fid]
                col_values.append(f"{col}__{label}")
            if all(c in merged.columns for c in col_values):
                diff_col = f"__diff_{col}"
                vals = [merged[c].astype(str) for c in col_values]
                if len(vals) == 2:
                    merged[diff_col] = vals[0] != vals[1]
                else:
                    merged[diff_col] = (vals[0] != vals[1]) | (vals[1] != vals[2]) | (vals[0] != vals[2])
                diff_cols.append(diff_col)

        # ── Step 3: extract key column values (from already-filled data) ──
        key_cols = self.key_mappings[compare_ids[0]]
        first_label = labels_in_use[compare_ids[0]]
        for col in key_cols:
            src = f"{col}__{first_label}"
            if src in merged.columns:
                merged[f"__key_{col}"] = merged[src].astype(str)
            else:
                merged[f"__key_{col}"] = ""

        # ── Step 4: compute column stats (vectorized, no per-row loop) ──
        matched_mask = merged["__match_type"] == "matched"
        self.column_stats = []
        for col, diff_col in zip(compare_columns, diff_cols):
            if diff_col in merged.columns:
                same = int((matched_mask & ~merged[diff_col].fillna(False)).sum())
                diff = int((matched_mask & merged[diff_col].fillna(False)).sum())
                self.column_stats.append({"column": col, "same": same, "diff": diff})

        self.result_df = merged
        return self._make_summary()

    def _compute_3way_match_type(self, df: pd.DataFrame, compare_ids: list[str], labels: dict):
        """Determine match types for 3-way outer join — fully vectorized.

        Assigns __match_type per row:
          'matched'            — present in ALL 3 tables
          'only_a' / 'only_b' / 'only_c'  — present in exactly ONE table
          'only_a_b' / 'only_a_c' / 'only_b_c' — present in exactly TWO tables

        Uses vectorized column operations instead of df.apply(axis=1),
        which is ~50-100x faster for large DataFrames.
        """
        a_label = labels[compare_ids[0]].lower()
        b_label = labels[compare_ids[1]].lower()
        c_label = labels[compare_ids[2]].lower()

        def _has_data(label: str) -> "pd.Series":
            """Return boolean Series: True where at least one non-empty value exists."""
            cols = [
                c for c in df.columns
                if c.endswith(f"__{label.upper()}") and not c.startswith("__")
            ]
            if not cols:
                return pd.Series(False, index=df.index)
            # Vectorized: a row is "present" if any of its columns is non-empty
            mask = pd.Series(False, index=df.index)
            for col in cols:
                # Treat empty string, NaN, "nan", "None" as missing
                col_vals = df[col].fillna("").astype(str)
                mask |= (col_vals != "") & (col_vals != "nan") & (col_vals != "None")
            return mask

        present_a = _has_data(a_label)
        present_b = _has_data(b_label)
        present_c = _has_data(c_label)

        # Count presence (vectorized integer sum)
        presence_count = present_a.astype(int) + present_b.astype(int) + present_c.astype(int)

        # Build match_type using vectorized numpy.select
        # Start with default "unknown"
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
            is_3way = self.compare_mode == "all" and len(self.files) >= 3
            if is_3way and match_filter in ("only_a", "only_b", "only_c"):
                mask &= self.result_df["__match_type"].str.startswith(match_filter)
            else:
                mask &= self.result_df["__match_type"] == match_filter

        if diff_filter != "all" or diff_column:
            diff_cols_list = [c for c in self.result_df.columns if c.startswith("__diff_")]
            if diff_column:
                target = f"__diff_{diff_column}"
                if target in self.result_df.columns:
                    if diff_filter == "same":
                        mask &= ~self.result_df[target].fillna(False)
                    elif diff_filter == "different":
                        mask &= self.result_df[target].fillna(False)
            else:
                if diff_filter == "same":
                    mask &= ~self.result_df[diff_cols_list].fillna(False).any(axis=1)
                elif diff_filter == "different":
                    mask &= self.result_df[diff_cols_list].fillna(False).any(axis=1)

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

                # Diff flags
                for dc in diff_col_names:
                    col_name = dc.replace("__diff_", "")
                    is_diff = bool(rec.get(dc, False))
                    data[dc] = is_diff
                    if is_diff:
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
            is_3way = self.compare_mode == "all" and len(self.files) >= 3
            if is_3way and match_filter in ("only_a", "only_b", "only_c"):
                mask &= df["__match_type"].str.startswith(match_filter)
            else:
                mask &= df["__match_type"] == match_filter

        if diff_filter != "all" or diff_column:
            diff_cols_list = [c for c in df.columns if c.startswith("__diff_")]
            if diff_column:
                target = f"__diff_{diff_column}"
                if target in df.columns:
                    if diff_filter == "same":
                        mask &= ~df[target].fillna(False)
                    elif diff_filter == "different":
                        mask &= df[target].fillna(False)
            else:
                if diff_filter == "same":
                    mask &= ~df[diff_cols_list].fillna(False).any(axis=1)
                elif diff_filter == "different":
                    mask &= df[diff_cols_list].fillna(False).any(axis=1)

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
                        {True: "不同", False: "相同"}
                    ).fillna("相同")

        return pd.DataFrame(result)

    def _get_diff_detail_export_df(
        self,
        df: pd.DataFrame,
        file_aliases: dict[str, str] | None = None,
    ) -> pd.DataFrame:
        """Build long-format diff detail — one row per differing column.

        Columns: 主键：{key1}, 主键：{key2}, ..., 差异的字段名, {alias_A}, {alias_B}

        Column names for A/B values use file aliases (user-set or filename stem).
        Each matched record with N differing columns produces N rows.

        Fully vectorized — no iterrows(), ~10-50x faster on large DataFrames.
        """
        file_ids = list(self.files.keys())
        labels = {fid: chr(65 + i) for i, fid in enumerate(file_ids)}
        key_cols = self.key_mappings.get(file_ids[0], [])

        # Resolve aliases for A/B value column names
        aliases = file_aliases or {}

        def _alias(fid: str, fallback_label: str) -> str:
            if fid in aliases:
                return aliases[fid]
            finfo = self.files.get(fid, {})
            if finfo.get("alias_set_by_user"):
                return finfo.get("file_alias", fallback_label)
            return f"{fallback_label}表"

        col_name_a = _alias(file_ids[0], labels[file_ids[0]])
        col_name_b = (
            _alias(file_ids[1], labels[file_ids[1]])
            if len(file_ids) > 1 else "B表值"
        )

        # Build diff column mapping: __diff_{col} → col name
        diff_col_map = {f"__diff_{c}": c for c in self.compare_columns}
        diff_col_names = [d for d in diff_col_map if d in df.columns]
        if not diff_col_names:
            return pd.DataFrame()

        # Only keep rows that have at least one diff=True
        has_any_diff = df[diff_col_names].fillna(False).any(axis=1)
        diff_df = df.loc[has_any_diff]
        if len(diff_df) == 0:
            return pd.DataFrame()

        # A/B source columns
        a_src_col = f"{{}}__{labels[file_ids[0]]}"
        b_src_col = (
            f"{{}}__{labels[file_ids[1]]}"
            if len(file_ids) > 1 else None
        )

        frames: list[pd.DataFrame] = []
        for diff_col, cmp_col in diff_col_map.items():
            if diff_col not in diff_df.columns:
                continue

            # Select rows where this specific column differs
            col_mask = diff_df[diff_col].fillna(False).astype(bool)
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

            # A value
            a_src = a_src_col.format(cmp_col)
            a_val = subset.get(a_src, pd.Series("", index=subset.index))
            result_cols[col_name_a] = a_val.fillna("").astype(str)

            # B value
            if b_src_col:
                b_src = b_src_col.format(cmp_col)
                b_val = subset.get(b_src, pd.Series("", index=subset.index))
                result_cols[col_name_b] = b_val.fillna("").astype(str)
            else:
                result_cols[col_name_b] = pd.Series("", index=subset.index)

            frames.append(pd.DataFrame(result_cols))

        if not frames:
            return pd.DataFrame()

        result = pd.concat(frames, ignore_index=True)
        columns = [f"主键：{c}" for c in key_cols] + ["差异的字段名", col_name_a, col_name_b]
        return result[columns]


# In-memory session store with TTL-based expiry

# Default session TTL: 30 minutes of inactivity
SESSION_TTL_SECONDS = 30 * 60
# Cleanup interval: run garbage collection every 5 minutes
_CLEANUP_INTERVAL = 5 * 60

_sessions: dict[str, DiffEngine] = {}
_session_last_access: dict[str, float] = {}  # session_id -> last access timestamp
_sessions_lock = threading.Lock()
_cleanup_started = False


def _touch_session(session_id: str):
    """Update the last-access timestamp for a session."""
    with _sessions_lock:
        if session_id in _sessions:
            _session_last_access[session_id] = time.time()


def _cleanup_expired_sessions():
    """Remove sessions that have exceeded the TTL."""
    now = time.time()
    with _sessions_lock:
        expired = [
            sid for sid, last_access in _session_last_access.items()
            if now - last_access > SESSION_TTL_SECONDS
        ]
        for sid in expired:
            engine = _sessions.pop(sid, None)
            _session_last_access.pop(sid, None)
            # Clean up uploaded files on disk
            if engine:
                for info in engine.files.values():
                    path = info.get("path", "")
                    if path and Path(path).exists():
                        try:
                            Path(path).unlink(missing_ok=True)
                        except OSError:
                            pass


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
    _start_cleanup_timer()
    engine = _sessions.get(session_id)
    if engine:
        _touch_session(session_id)
    return engine


def create_session() -> str:
    _start_cleanup_timer()
    session_id = uuid.uuid4().hex[:16]
    with _sessions_lock:
        _sessions[session_id] = DiffEngine()
        _session_last_access[session_id] = time.time()
    return session_id


def remove_session(session_id: str):
    with _sessions_lock:
        _sessions.pop(session_id, None)
        _session_last_access.pop(session_id, None)

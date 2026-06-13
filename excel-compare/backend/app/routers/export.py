"""Export routes."""
from __future__ import annotations

import re

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response, StreamingResponse

from app.models import ExportRequest
from app.services.matcher import get_session
from app.services.exporter import (
    export_to_excel,
    export_to_excel_streamed,
    estimate_excel_size,
)

router = APIRouter(prefix="/api/export", tags=["export"])

# Threshold for streaming: 10 MB estimated file size → switch from in-memory to temp-file streaming
_STREAM_THRESHOLD = 10 * 1024 * 1024


def _safe_filename(name: str) -> str:
    """Strip non-ASCII chars for Content-Disposition header, use ASCII subset."""
    safe = name.encode("ascii", errors="replace").decode("ascii")
    safe = re.sub(r"[^\w\.\-]", "_", safe)
    return safe or "export"


def _content_disposition(filename: str) -> str:
    """Build Content-Disposition header ASCII-only filename."""
    safe = _safe_filename(filename)
    return f'attachment; filename="{safe}"'


def _build_filename(parts: list[str]) -> str:
    """Build an ASCII-safe download filename."""
    filename_parts = ["筛选结果"]
    if parts[1:]:
        for p in parts[1:]:
            filename_parts.append(re.sub(r"[^\w]", "_", p))
    return "_".join(filename_parts) + ".xlsx"


def _export_raw_source(engine, labels, req) -> Response:
    """Export a raw source file — returns the original DataFrame as Excel."""
    target_label = req.export_type[-1].upper()
    target_fid = None
    for fid, label in labels.items():
        if label == target_label:
            target_fid = fid
            break
    if not target_fid or target_fid not in engine.files:
        raise HTTPException(400, "未找到对应表的原始数据")

    df = engine.files[target_fid]["df"]
    name = engine.files[target_fid]["file_name"]
    safe_name = name.replace(".xlsx", "").replace(".xls", "")

    # For large raw exports, stream via temp file
    estimated = estimate_excel_size(df)
    if estimated > 10 * 1024 * 1024:  # > 10MB estimated → stream
        iterator, _ = export_to_excel_streamed({f"原始数据_{safe_name[:20]}": df})
        return StreamingResponse(
            iterator,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={
                "Content-Disposition": _content_disposition(f"原始数据_{safe_name}.xlsx"),
            },
        )

    data = {f"原始数据_{safe_name[:20]}": df}
    bytes_io = export_to_excel(data)
    return Response(
        content=bytes_io,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": _content_disposition(f"原始数据_{safe_name}.xlsx"),
        },
    )


@router.post("/excel")
def export_excel(req: ExportRequest):
    """Export data as Excel file."""
    engine = get_session(req.session_id)
    if not engine:
        raise HTTPException(404, "Session not found")

    try:
        file_ids = list(engine.files.keys())
        labels = {fid: chr(65 + i) for i, fid in enumerate(file_ids)}

        # ── Export raw source data ──
        if req.export_type.startswith("raw_"):
            return _export_raw_source(engine, labels, req)

        # ── Export filtered comparison results ──
        match_filter_value = req.match_filter.value
        diff_filter_value = req.diff_filter.value

        df = engine.get_export_df(
            match_filter=match_filter_value,
            diff_filter=diff_filter_value,
            diff_column=req.diff_column,
            export_type=req.export_type,
            include_diff_columns=req.include_diff_columns,
            file_aliases=req.file_aliases,
        )

        if df.empty:
            raise HTTPException(400, "当前筛选条件下没有可导出的数据，请调整筛选条件后重试")

        # Build a descriptive sheet name
        if req.export_type == "diff_detail":
            parts = ["差异明细"]
            if match_filter_value != "all":
                parts.append("匹配成功")
            if diff_filter_value != "all":
                parts.append("值不同")
        else:
            parts = ["比对结果"]
            if match_filter_value != "all":
                from app.services.matcher import MATCH_TYPE_LABELS
                parts.append(MATCH_TYPE_LABELS.get(match_filter_value, match_filter_value))
            if diff_filter_value != "all":
                parts.append("值不同" if diff_filter_value == "different" else "值相同")
        if req.diff_column:
            parts.append(f"列：{req.diff_column}")
        sheet_name = "_".join(parts)[:31]  # Excel 31-char limit

        dataframes = {sheet_name: df}
        filename = _build_filename(parts)

        # Choose streaming vs in-memory based on estimated size
        estimated = estimate_excel_size(df)
        if estimated > _STREAM_THRESHOLD:
            iterator, _ = export_to_excel_streamed(dataframes)
            return StreamingResponse(
                iterator,
                media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                headers={
                    "Content-Disposition": _content_disposition(filename),
                },
            )

        bytes_io = export_to_excel(dataframes)
        return Response(
            content=bytes_io,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={
                "Content-Disposition": _content_disposition(filename),
            },
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"导出内部错误: {type(e).__name__}: {e}")



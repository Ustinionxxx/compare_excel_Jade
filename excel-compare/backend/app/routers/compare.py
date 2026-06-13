"""Comparison routes."""
from __future__ import annotations

import traceback

from fastapi import APIRouter, HTTPException

from app.models import (
    SetColumnMappingRequest,
    CompareRequest,
    CompareResponse,
    DetailQueryRequest,
    DetailQueryResponse,
)
from app.services.matcher import get_session

router = APIRouter(prefix="/api/compare", tags=["compare"])


@router.post("/mapping")
def set_column_mapping(req: SetColumnMappingRequest):
    """Set primary key column mappings for each file."""
    engine = get_session(req.session_id)
    if not engine:
        raise HTTPException(404, "Session not found")

    for m in req.mappings:
        if m.file_id not in engine.files:
            raise HTTPException(400, f"File {m.file_id} not in session")
        engine.key_mappings[m.file_id] = m.key_columns

    return {"status": "ok"}


@router.post("/execute", response_model=CompareResponse)
def execute_compare(req: CompareRequest):
    """Execute the comparison and return statistics."""
    engine = get_session(req.session_id)
    if not engine:
        raise HTTPException(404, "Session not found")

    if len(engine.key_mappings) < 2:
        raise HTTPException(400, "Key mappings not configured for all files")

    try:
        summary = engine.compute(req.compare_columns, req.mode.value)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"比对引擎内部错误: {type(e).__name__}: {e}\n{traceback.format_exc()}")

    return CompareResponse(
        session_id=req.session_id,
        total_keys=summary["total_keys"],
        matched=summary["matched"],
        only_a=summary["only_a"],
        only_b=summary["only_b"],
        only_c=summary.get("only_c", 0),
        column_stats=summary["column_stats"],
    )


@router.post("/detail", response_model=DetailQueryResponse)
def query_detail(req: DetailQueryRequest):
    """Query paginated detail rows with filters."""
    engine = get_session(req.session_id)
    if not engine:
        raise HTTPException(404, "Session not found")

    try:
        result = engine.get_detail_page(
            match_filter=req.match_filter.value,
            diff_filter=req.diff_filter.value,
            diff_column=req.diff_column,
            page=req.page,
            page_size=req.page_size,
            sort_column=req.sort_column,
            sort_order=req.sort_order,
        )
        return DetailQueryResponse(**result)
    except Exception as e:
        raise HTTPException(500, f"明细查询内部错误: {type(e).__name__}: {e}")

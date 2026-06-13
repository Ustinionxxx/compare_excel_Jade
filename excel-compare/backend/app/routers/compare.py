"""Comparison routes."""
from __future__ import annotations

import threading
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

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

# ── Async Compare: Thread pool + progress tracking ──
# Use a dedicated thread pool for CPU-heavy comparison tasks.
# Thread-based (not process-based) to keep shared in-memory sessions accessible.
# Limit to 2 concurrent comparisons to avoid thrashing under multi-tenant load.
_compare_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="compare-")


@dataclass
class CompareProgress:
    stage: str = "pending"
    progress: int = 0
    message: str = ""
    result: dict | None = None
    error: str | None = None


_compare_progress: dict[str, CompareProgress] = {}
_compare_lock = threading.Lock()


def _set_compare_progress(task_id: str, **kwargs):
    with _compare_lock:
        p = _compare_progress.get(task_id)
        if p:
            for k, v in kwargs.items():
                setattr(p, k, v)


def _run_compare_background(
    task_id: str,
    session_id: str,
    compare_columns: list[str],
    mode: str,
):
    """Background task: execute comparison and update progress at each stage."""
    try:
        engine = get_session(session_id)
        if engine is None:
            _set_compare_progress(
                task_id, stage="error", message="会话已过期", error="Session not found"
            )
            return

        if len(engine.key_mappings) < 2:
            _set_compare_progress(
                task_id, stage="error", message="请先配置各表的匹配键列",
                error="Key mappings not configured"
            )
            return

        _set_compare_progress(task_id, stage="merging", progress=30, message="正在合并数据表...")

        try:
            summary = engine.compute(compare_columns, mode)
        except ValueError as e:
            _set_compare_progress(task_id, stage="error", message=str(e), error=str(e))
            return
        except Exception as e:
            _set_compare_progress(
                task_id, stage="error",
                message=f"比对引擎内部错误: {type(e).__name__}: {e}",
                error=traceback.format_exc(),
            )
            return

        _set_compare_progress(
            task_id,
            stage="complete", progress=100, message="比对完成",
            result=summary,
        )
    except Exception as e:
        _set_compare_progress(
            task_id, stage="error",
            message=f"比对失败: {e}",
            error=traceback.format_exc(),
        )


# ── Endpoints ──


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
    """Execute the comparison synchronously (legacy, for small files)."""
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


@router.post("/execute-async")
def execute_compare_async(req: CompareRequest):
    """Submit a comparison task and return immediately.

    The comparison runs in background; poll /api/compare/status/{task_id}
    for progress and completion.

    This is the recommended endpoint for large files or multi-user scenarios —
    it prevents long-running comparisons from blocking other requests.
    """
    engine = get_session(req.session_id)
    if not engine:
        raise HTTPException(404, "Session not found")

    if len(engine.key_mappings) < 2:
        raise HTTPException(400, "Key mappings not configured for all files")

    # Check if there's already a running comparison for this session
    for tid, p in list(_compare_progress.items()):
        if tid.startswith(req.session_id) and p.stage not in ("complete", "error"):
            raise HTTPException(
                409,
                "该会话已有比对任务正在执行中，请等待完成后再发起新比对"
            )

    task_id = f"{req.session_id}:{uuid.uuid4().hex[:8]}"

    progress = CompareProgress(
        stage="started", progress=5, message="比对任务已提交..."
    )
    with _compare_lock:
        _compare_progress[task_id] = progress

    _compare_executor.submit(
        _run_compare_background,
        task_id,
        req.session_id,
        req.compare_columns,
        req.mode.value,
    )

    # Clean up old completed/errored tasks for this session (keep only the new one)
    with _compare_lock:
        stale = [
            tid for tid, p in _compare_progress.items()
            if tid.startswith(req.session_id) and tid != task_id
            and p.stage in ("complete", "error")
        ]
        for tid in stale:
            del _compare_progress[tid]

    return {
        "task_id": task_id,
        "session_id": req.session_id,
        "status": "started",
    }


@router.get("/status/{task_id}")
def get_compare_status(task_id: str):
    """Poll comparison progress by task_id."""
    with _compare_lock:
        progress = _compare_progress.get(task_id)

    if not progress:
        raise HTTPException(404, "Compare task not found")

    resp: dict[str, Any] = {
        "task_id": task_id,
        "status": progress.stage,
        "stage": progress.stage,
        "progress": progress.progress,
        "message": progress.message,
    }

    if progress.stage == "complete" and progress.result:
        resp["status"] = "complete"
        resp["result"] = progress.result

    if progress.stage == "error":
        resp["status"] = "error"
        resp["error"] = progress.error

    return resp


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

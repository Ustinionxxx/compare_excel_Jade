"""Comparison routes."""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

from app.models import (
    SetColumnMappingRequest,
    CompareRequest,
    CompareResponse,
    DetailQueryRequest,
    DetailQueryResponse,
)
from app.services.matcher import get_session, persist_session

_logger = logging.getLogger("excel_compare.compare")

router = APIRouter(prefix="/api/compare", tags=["compare"])

# ── Async Compare: Thread pool + progress tracking ──
# Use a dedicated thread pool for CPU-heavy comparison tasks.
# Thread-based (not process-based) to keep shared in-memory sessions accessible.
# Limit to 2 concurrent comparisons to avoid thrashing under multi-tenant load.
_compare_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="compare-")

# Filesystem-based task storage — survives gunicorn multi-worker routing.
# Each worker process reads/writes the same directory, so a task created on
# Worker A can be polled from Worker B without "Compare task not found".
_COMPARE_TASKS_DIR = Path(__file__).resolve().parent.parent.parent / "uploads" / "compare_tasks"

# Maximum age for a task file before cleanup (1 hour).
_TASK_MAX_AGE_SECONDS = 3600


def _ensure_tasks_dir() -> None:
    """Create the compare-tasks directory if it doesn't exist."""
    _COMPARE_TASKS_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class CompareProgress:
    stage: str = "pending"
    progress: int = 0
    message: str = ""
    result: dict | None = None
    error: str | None = None

    def to_dict(self) -> dict:
        """Serialize to a JSON-safe dict (drop non-serializable result internals if any)."""
        try:
            return {
                "stage": self.stage,
                "progress": self.progress,
                "message": self.message,
                "result": self.result,
                "error": self.error,
            }
        except Exception:
            return {
                "stage": self.stage,
                "progress": self.progress,
                "message": self.message,
                "result": None,
                "error": self.error,
            }

    @staticmethod
    def from_dict(d: dict) -> "CompareProgress":
        """Deserialize from a dict."""
        return CompareProgress(
            stage=d.get("stage", "pending"),
            progress=d.get("progress", 0),
            message=d.get("message", ""),
            result=d.get("result"),
            error=d.get("error"),
        )


def _task_file(task_id: str) -> Path:
    """Return the path for a task's JSON file."""
    # Sanitize task_id to prevent path traversal
    safe_id = task_id.replace("/", "_").replace("\\", "_")
    return _COMPARE_TASKS_DIR / f"{safe_id}.json"


# In-memory cache + lock to reduce filesystem I/O during polling.
# Each worker process still caches briefly, but the authoritative source
# is the filesystem — so a cache miss falls back to disk.
_compare_cache: dict[str, CompareProgress] = {}
_compare_lock = threading.Lock()


def _save_task(task_id: str, progress: CompareProgress) -> None:
    """Persist task progress to filesystem (authoritative store)."""
    _ensure_tasks_dir()
    try:
        data = json.dumps(progress.to_dict(), ensure_ascii=False)
        tmp_path = _task_file(task_id).with_suffix(".tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(data)
        os.replace(tmp_path, _task_file(task_id))  # atomic rename
    except Exception as e:
        _logger.warning("Failed to persist compare task %s: %s", task_id, e)


def _load_task(task_id: str) -> CompareProgress | None:
    """Load task progress, trying cache first then filesystem."""
    with _compare_lock:
        cached = _compare_cache.get(task_id)
        if cached is not None:
            return cached

    # Cache miss — try filesystem
    try:
        path = _task_file(task_id)
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            progress = CompareProgress.from_dict(data)
            with _compare_lock:
                # Only populate cache if not already set (avoid race)
                if task_id not in _compare_cache:
                    _compare_cache[task_id] = progress
            return progress
    except Exception:
        pass
    return None


def _set_compare_progress(task_id: str, **kwargs):
    """Update task progress in-memory AND persist to filesystem."""
    with _compare_lock:
        p = _compare_cache.get(task_id)
        if p:
            for k, v in kwargs.items():
                setattr(p, k, v)
            _save_task(task_id, p)


def _delete_task(task_id: str) -> None:
    """Remove a task from cache and disk."""
    with _compare_lock:
        _compare_cache.pop(task_id, None)
    try:
        path = _task_file(task_id)
        if path.exists():
            path.unlink()
    except Exception:
        pass


def _cleanup_old_tasks() -> None:
    """Delete task files older than _TASK_MAX_AGE_SECONDS."""
    try:
        _ensure_tasks_dir()
        now = time.time()
        for path in _COMPARE_TASKS_DIR.glob("*.json"):
            if now - path.stat().st_mtime > _TASK_MAX_AGE_SECONDS:
                try:
                    path.unlink()
                except Exception:
                    pass
    except Exception:
        pass


def _list_session_tasks(session_id: str) -> list[str]:
    """Return task_ids that belong to a session (from cache + disk)."""
    task_ids: list[str] = []
    with _compare_lock:
        for tid in _compare_cache:
            if tid.startswith(session_id):
                task_ids.append(tid)
    # Also check filesystem for tasks not in cache
    try:
        _ensure_tasks_dir()
        for path in _COMPARE_TASKS_DIR.glob("*.json"):
            tid = path.stem
            if tid.startswith(session_id) and tid not in task_ids:
                task_ids.append(tid)
    except Exception:
        pass
    return task_ids


def _run_compare_background(
    task_id: str,
    session_id: str,
    compare_columns: list[str],
    mode: str,
):
    """Background task: execute comparison and update progress at each stage.

    Progress is persisted to the filesystem after each stage so that any
    gunicorn worker can serve status polls, not just the worker that
    received the original execute-async request.
    """
    _logger.info("Compare thread started: task=%s session=%s", task_id, session_id)
    try:
        engine = get_session(session_id)
        if engine is None:
            _logger.warning("Compare thread: session not found session=%s", session_id)
            _set_compare_progress(
                task_id, stage="error", message="会话已过期", error="Session not found"
            )
            return

        if len(engine.key_mappings) < 2:
            _logger.warning("Compare thread: key mappings not configured session=%s", session_id)
            _set_compare_progress(
                task_id, stage="error", message="请先配置各表的匹配键列",
                error="Key mappings not configured"
            )
            return

        _set_compare_progress(task_id, stage="merging", progress=15, message="正在准备比对数据...")

        try:
            _logger.info("Compare thread: calling engine.compute session=%s", session_id)

            # Pass progress callback so the frontend sees granular updates
            def _on_progress(stage: str, progress: int, message: str):
                _set_compare_progress(task_id, stage=stage, progress=progress, message=message)

            summary = engine.compute(compare_columns, mode, on_progress=_on_progress)
            _logger.info("Compare thread: compute done session=%s keys=%d", session_id, summary.get("total_keys", 0))
        except ValueError as e:
            _logger.error("Compare thread: ValueError session=%s error=%s", session_id, e)
            _set_compare_progress(task_id, stage="error", message=str(e), error=str(e))
            return
        except Exception as e:
            _logger.error("Compare thread: engine error session=%s error=%s\n%s",
                           session_id, e, traceback.format_exc())
            _set_compare_progress(
                task_id, stage="error",
                message=f"比对引擎内部错误: {type(e).__name__}: {e}",
                error=traceback.format_exc(),
            )
            return

        # CRITICAL: persist the session AFTER compute so that result_df is
        # available to detail queries on other gunicorn workers.
        # Without this, cross-worker detail queries see result_df=None → 0 rows.
        _logger.info("Compare thread: persisting session with result_df session=%s", session_id)
        persist_session(session_id)

        _set_compare_progress(
            task_id,
            stage="complete", progress=100, message="比对完成",
            result=summary,
        )
        _logger.info("Compare thread completed OK: task=%s", task_id)
    except Exception as e:
        _logger.error("Compare thread failed: task=%s error=%s\n%s",
                       task_id, e, traceback.format_exc())
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

    # Validate all file_ids exist in session
    missing = [m.file_id for m in req.mappings if m.file_id not in engine.files]
    if missing:
        # Stale cache possible on multi-worker setups — force a fresh load
        # from disk and retry once before giving up.
        from app.services.matcher import _load_session_from_disk, _session_file
        fresh = _load_session_from_disk(req.session_id)
        if fresh is not None:
            engine = fresh
        # Re-check after force-reload
        still_missing = [fid for fid in missing if fid not in engine.files]
        if still_missing:
            raise HTTPException(
                400,
                f"文件 {still_missing[0]} 未在会话中找到，"
                f"请返回上一步确认文件已成功上传解析"
            )

    for m in req.mappings:
        engine.key_mappings[m.file_id] = m.key_columns

    # Persist to disk for cross-worker visibility
    persist_session(req.session_id)

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
        match_type_labels=summary.get("match_type_labels", {}),
    )


@router.post("/execute-async")
def execute_compare_async(req: CompareRequest):
    """Submit a comparison task and return immediately.

    The comparison runs in background; poll /api/compare/status/{task_id}
    for progress and completion.

    Task progress is stored on the filesystem (uploads/compare_tasks/) so
    that any gunicorn worker can serve status polls, even when the backend
    runs with multiple workers.

    This is the recommended endpoint for large files or multi-user scenarios —
    it prevents long-running comparisons from blocking other requests.
    """
    engine = get_session(req.session_id)
    if not engine:
        raise HTTPException(404, "Session not found")

    if len(engine.key_mappings) < 2:
        raise HTTPException(400, "Key mappings not configured for all files")

    # Check if there's already a running comparison for this session
    for tid in _list_session_tasks(req.session_id):
        p = _load_task(tid)
        if p and p.stage not in ("complete", "error"):
            raise HTTPException(
                409,
                "该会话已有比对任务正在执行中，请等待完成后再发起新比对"
            )

    task_id = f"{req.session_id}:{uuid.uuid4().hex[:8]}"

    progress = CompareProgress(
        stage="started", progress=5, message="比对任务已提交..."
    )
    with _compare_lock:
        _compare_cache[task_id] = progress
    _save_task(task_id, progress)

    _compare_executor.submit(
        _run_compare_background,
        task_id,
        req.session_id,
        req.compare_columns,
        req.mode.value,
    )

    # Clean up old completed/errored tasks for this session (keep only the new one)
    for tid in _list_session_tasks(req.session_id):
        if tid == task_id:
            continue
        p = _load_task(tid)
        if p and p.stage in ("complete", "error"):
            _delete_task(tid)

    # Periodic cleanup of stale task files from all sessions
    _cleanup_old_tasks()

    return {
        "task_id": task_id,
        "session_id": req.session_id,
        "status": "started",
    }


@router.get("/status/{task_id}")
def get_compare_status(task_id: str):
    """Poll comparison progress by task_id.

    Tries the in-memory cache first, then falls back to the filesystem.
    This ensures the task is visible to all gunicorn workers, not just
    the one that created it.
    """
    progress = _load_task(task_id)

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

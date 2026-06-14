"""File upload and management routes."""
from __future__ import annotations

import logging
import os
import threading
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, UploadFile, HTTPException
from pydantic import BaseModel

from app.models import UploadResponse, RemoveFileRequest, SetFileAliasRequest
from app.services import parser
from app.services.matcher import create_session, get_session, remove_session, persist_session

_logger = logging.getLogger("excel_compare.files")

router = APIRouter(prefix="/api/files", tags=["files"])

# ── Parse Progress Tracking ──

@dataclass
class ParseProgress:
    stage: str = "pending"
    progress: int = 0
    message: str = ""
    result: dict | None = None
    error: str | None = None


_parse_progress: dict[tuple[str, str], ParseProgress] = {}
_parse_lock = threading.Lock()


def _set_progress(session_id: str, file_id: str, **kwargs):
    """Thread-safe update to a parse progress entry."""
    with _parse_lock:
        p = _parse_progress.get((session_id, file_id))
        if p:
            for k, v in kwargs.items():
                setattr(p, k, v)


def _cleanup_progress(session_id: str, file_id: str):
    with _parse_lock:
        _parse_progress.pop((session_id, file_id), None)


def _parse_file_background(session_id: str, file_id: str, saved_path: str, file_name: str):
    """Background task: parse Excel file and update progress at each stage.

    Progress is reported through _parse_progress (in-memory) and the session is
    persisted to disk on completion/error so that cross-worker polling works.
    """
    _logger.info("Parse thread started: session=%s file=%s path=%s", session_id, file_id, saved_path)
    try:
        # Check if the file was removed while we were waiting to start
        engine = get_session(session_id)
        if engine is None or file_id not in engine.files:
            _logger.warning("Parse thread: session or file gone session=%s file=%s", session_id, file_id)
            _set_progress(session_id, file_id, stage="error", message="文件已被移除", error="File removed")
            parser.remove_file(saved_path)
            return

        _set_progress(session_id, file_id, stage="reading_file", progress=20, message="正在读取文件...")

        # The heavy work: parse_excel reads all sheets
        _set_progress(session_id, file_id, stage="parsing_rows", progress=50, message="正在解析数据行...")
        _logger.info("Parse thread: calling parse_excel for %s", saved_path)
        sheet_names, dataframes = parser.parse_excel(saved_path)
        _logger.info("Parse thread: parse_excel done, %d sheets", len(sheet_names))

        _set_progress(session_id, file_id, stage="validating_columns", progress=80, message="校验列名...")
        first_sheet = sheet_names[0]
        df = dataframes[first_sheet]
        columns, preview, total = parser.get_preview(df)

        # Update the placeholder entry in engine with real data
        if engine and file_id in engine.files:
            # Preserve the alias that may have been set by the user
            existing_alias = engine.files[file_id].get("file_alias")
            engine.files[file_id].update({
                "sheet": first_sheet,
                "df": df,
                "columns": columns,
                "parsing": False,
            })
            if existing_alias:
                engine.files[file_id]["file_alias"] = existing_alias
            # Persist updated session to disk for cross-worker visibility
            _logger.info("Parse thread: persisting session %s", session_id)
            persist_session(session_id)

        _set_progress(
            session_id, file_id,
            stage="complete", progress=100, message="解析完成",
            result={
                "file_name": file_name,
                "sheet_names": sheet_names,
                "columns": columns,
                "preview_rows": preview,
                "total_rows": total,
            },
        )
        _logger.info("Parse thread completed OK: session=%s file=%s rows=%d", session_id, file_id, total)
    except Exception as e:
        _logger.error("Parse thread failed: session=%s file=%s error=%s\n%s",
                       session_id, file_id, e, traceback.format_exc())
        _set_progress(session_id, file_id, stage="error", message=f"解析失败: {e}", error=str(e))
        # Clean up engine entry and disk file on failure
        engine = get_session(session_id)
        if engine:
            engine.files.pop(file_id, None)
            # CRITICAL: persist session after removing file so other workers
            # see the updated state (file removed) instead of stale parsing:True
            persist_session(session_id)
        parser.remove_file(saved_path)


# ── Endpoints ──

@router.get("/new-session")
def new_session():
    session_id = create_session()
    return {"session_id": session_id}


@router.post("/upload")
async def upload_file(
    file: UploadFile = File(...),
    session_id: str | None = None,
):
    """Upload an Excel file and return immediately.

    The file is parsed in background; poll /parse-status/{session_id}/{file_id}
    for progress and completion.
    """
    if not file.filename:
        raise HTTPException(400, "No file provided")

    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in (".xlsx", ".xls", ".csv"):
        raise HTTPException(400, f"Unsupported file type: {ext}. Only .xlsx, .xls and .csv are supported.")

    # 文件大小校验：最大 100 MB（与 nginx client_max_body_size 和前端限制保持一致）
    MAX_UPLOAD_SIZE = 100 * 1024 * 1024  # 100 MB
    if file.size is not None and file.size > MAX_UPLOAD_SIZE:
        size_mb = file.size / (1024 * 1024)
        raise HTTPException(413, f"文件大小 {size_mb:.1f} MB 超过 {MAX_UPLOAD_SIZE // (1024*1024)} MB 上限，请拆分文件后重新上传。")

    # Create or get session
    if not session_id:
        session_id = create_session()
    else:
        if get_session(session_id) is None:
            session_id = create_session()

    engine = get_session(session_id)
    if engine is None:
        raise HTTPException(500, "Failed to create session")

    # Check limit
    if len(engine.files) >= 3:
        raise HTTPException(400, "Maximum 3 files allowed per session. Remove a file first.")

    # Stream file directly to disk (avoids loading entire file into memory)
    file_id, saved_path = parser.save_upload_streamed(file.file, file.filename)

    # Pre-register file in engine (placeholder — actual df added after background parse)
    # This ensures remove-file works even while parsing is in progress.
    default_alias = Path(file.filename).stem
    engine.files[file_id] = {
        "file_name": file.filename,
        "file_alias": default_alias,
        "path": saved_path,
        "sheet": "",
        "df": None,
        "columns": [],
        "parsing": True,
    }

    # Persist to disk for cross-worker visibility
    persist_session(session_id)

    # Setup parse progress and start background parsing
    progress = ParseProgress(stage="file_saved", progress=5, message="文件已保存，开始解析...")
    with _parse_lock:
        _parse_progress[(session_id, file_id)] = progress

    thread = threading.Thread(
        target=_parse_file_background,
        args=(session_id, file_id, saved_path, file.filename),
        daemon=True,
    )
    thread.start()

    return {
        "session_id": session_id,
        "file_id": file_id,
        "file_name": file.filename,
        "status": "parsing",
    }


@router.get("/parse-status/{session_id}/{file_id}")
def get_parse_status(session_id: str, file_id: str):
    """Poll parsing progress for a file.

    Tries in-memory progress first, then falls back to the session's file
    record. The filesystem-backed session store ensures this works across
    gunicorn workers — a parse started on Worker A can be polled from
    Worker B.
    """
    with _parse_lock:
        progress = _parse_progress.get((session_id, file_id))

    if not progress:
        # Progress not in this worker's memory — may be on another worker,
        # or the session was restored after a restart. Check the session.
        engine = get_session(session_id)
        if engine and file_id in engine.files:
            info = engine.files[file_id]
            if not info.get("parsing", False):
                # Fully parsed
                return {
                    "file_id": file_id,
                    "status": "complete",
                    "stage": "complete",
                    "progress": 100,
                    "message": "解析完成",
                    "file_name": info["file_name"],
                    "columns": info["columns"],
                    "total_rows": len(info.get("df") or []),
                }
            else:
                # Still parsing on another worker — return a synthetic
                # "parsing" status so the frontend keeps polling instead
                # of treating this as an error.
                return {
                    "file_id": file_id,
                    "status": "parsing",
                    "stage": "reading_file",
                    "progress": 25,
                    "message": "正在解析中...",
                }
        raise HTTPException(404, "Parse progress not found for this file")

    resp: dict[str, Any] = {
        "file_id": file_id,
        "status": "parsing",
        "stage": progress.stage,
        "progress": progress.progress,
        "message": progress.message,
    }

    if progress.stage == "complete" and progress.result:
        resp["status"] = "complete"
        resp.update(progress.result)

    if progress.stage == "error":
        resp["status"] = "error"
        resp["error"] = progress.error

    return resp


@router.post("/remove")
def remove_file(req: RemoveFileRequest):
    """Remove a file from the session."""
    engine = get_session(req.session_id)
    if not engine:
        raise HTTPException(404, "Session not found")

    # Clean up parse progress in case file is still being parsed
    _cleanup_progress(req.session_id, req.file_id)

    info = engine.files.pop(req.file_id, None)
    if not info:
        raise HTTPException(404, "File not found in session")

    # Only remove the file from disk if it exists (placeholder may not have a real path)
    if info.get("path") and os.path.exists(info["path"]):
        parser.remove_file(info.get("path"))
    # Also clean up the key mapping for this file ID
    engine.key_mappings.pop(req.file_id, None)
    # Persist to disk for cross-worker visibility
    persist_session(req.session_id)
    return {"status": "removed", "file_id": req.file_id}


@router.get("/session/{session_id}")
def get_session_info(session_id: str):
    """Get current session state: uploaded files and columns."""
    engine = get_session(session_id)
    if not engine:
        raise HTTPException(404, "Session not found")

    files_info = []
    for fid, info in engine.files.items():
        # Skip files that are still being parsed (no df yet)
        if info.get("parsing", False):
            continue
        files_info.append({
            "file_id": fid,
            "file_name": info["file_name"],
            "alias": info.get("file_alias", Path(info["file_name"]).stem),
            "columns": info["columns"],
            "sheet": info["sheet"],
        })

    return {
        "session_id": session_id,
        "files": files_info,
        "key_mappings": engine.key_mappings,
    }


@router.post("/alias")
def set_file_alias(req: SetFileAliasRequest):
    """Update the display alias for a file."""
    engine = get_session(req.session_id)
    if not engine:
        raise HTTPException(404, "Session not found")
    if req.file_id not in engine.files:
        raise HTTPException(404, "File not found in session")
    if not req.alias or not req.alias.strip():
        raise HTTPException(400, "Alias cannot be empty")
    engine.set_file_alias(req.file_id, req.alias.strip())
    # Persist to disk for cross-worker visibility
    persist_session(req.session_id)
    return {"status": "ok", "file_id": req.file_id, "alias": req.alias.strip()}

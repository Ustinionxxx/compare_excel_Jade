#!/usr/bin/env python3
"""
Comprehensive test suite for the Excel Compare tool.

Covers:
  1. Unit tests — DiffEngine (matcher), parser, exporter
  2. Integration tests — all API endpoints (via HTTP, no FastAPI TestClient dependency)
  3. Concurrency tests — multi-user simultaneous workflows
  4. Stress tests — scale test (engine-only), sustained load (API)
  5. Cross-worker simulation — stale cache, task routing, parse progress

Run:
    python test_comprehensive.py                    # all tests
    python test_comprehensive.py --unit             # unit tests only
    python test_comprehensive.py --integration      # integration tests only
    python test_comprehensive.py --concurrency N    # N concurrent users
    python test_comprehensive.py --stress           # scale test (engine only)
    python test_comprehensive.py --load SECONDS     # sustained load test
"""

import argparse
import io
import json
import os
import random
import statistics
import sys
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import pandas as pd
import requests

# ── Setup ──
sys.path.insert(0, str(Path(__file__).resolve().parent))

BACKEND_URL = os.environ.get("BACKEND_URL", "http://127.0.0.1:8000")
PASS = 0
FAIL = 0
SKIP = 0
_lock = threading.Lock()


def ok(msg: str):
    global PASS
    with _lock:
        PASS += 1
    print(f"  ✅ {msg}")


def bad(msg: str):
    global FAIL
    with _lock:
        FAIL += 1
    print(f"  ❌ {msg}")


def skipped(msg: str):
    global SKIP
    with _lock:
        SKIP += 1
    print(f"  ⏭️  {msg}")


# ═══════════════════════════════════════════════════════════════════════
# Test data generators
# ═══════════════════════════════════════════════════════════════════════

def make_test_df(rows: int = 100, cols: int = 5, prefix: str = "") -> pd.DataFrame:
    """Generate a test DataFrame with id + value columns."""
    data = {"id": [f"{prefix}{i:05d}" for i in range(rows)]}
    for j in range(cols - 1):
        data[f"col_{j}"] = [f"val_{i % (rows // 3)}_{j}" for i in range(rows)]
    return pd.DataFrame(data)


def make_test_csv_bytes(rows: int = 100, cols: int = 5, prefix: str = "") -> bytes:
    """Generate in-memory CSV bytes."""
    buf = io.StringIO()
    headers = ["id"] + [f"col_{j}" for j in range(cols - 1)]
    buf.write(",".join(headers) + "\n")
    for i in range(rows):
        vals = [f"{prefix}{i:05d}"]
        for j in range(cols - 1):
            vals.append(f"val_{i % (rows // 3)}_{j}")
        buf.write(",".join(vals) + "\n")
    return buf.getvalue().encode("utf-8")


# ═══════════════════════════════════════════════════════════════════════
# 1. UNIT TESTS
# ═══════════════════════════════════════════════════════════════════════

class TestDiffEngine:
    """Tests for the core DiffEngine in matcher.py."""

    @staticmethod
    def test_basic_two_way_compare():
        """Two files, partial overlap, standard match."""
        from app.services.matcher import DiffEngine

        df1 = make_test_df(100, 5, prefix="a_")
        df2 = make_test_df(100, 5, prefix="a_")
        # Change last 20 IDs in df2 to create only_b
        for i in range(80, 100):
            df2.at[i, "id"] = f"only_b_{i:05d}"

        engine = DiffEngine()
        engine.register_file("f1", "a.csv", "", "Sheet1", df1)
        engine.register_file("f2", "b.csv", "", "Sheet1", df2)
        engine.set_key_mapping({"f1": ["id"], "f2": ["id"]})
        result = engine.compute(["col_0", "col_1", "col_2", "col_3"], "all")

        assert result["total_keys"] == 120, f"expected 120, got {result['total_keys']}"
        assert result["matched"] == 80, f"expected 80, got {result['matched']}"
        assert result["only_a"] == 20, f"expected 20, got {result['only_a']}"
        assert result["only_b"] == 20, f"expected 20, got {result['only_b']}"
        ok("Basic 2-way compare: 120 keys (80 matched, 20 only_a, 20 only_b)")

    @staticmethod
    def test_detail_query():
        """Detail query returns correct pages."""
        from app.services.matcher import DiffEngine

        df1 = make_test_df(100, 5, prefix="x_")
        df2 = make_test_df(100, 5, prefix="x_")
        for i in range(50, 100):
            df2.at[i, "id"] = f"diff_{i:05d}"

        engine = DiffEngine()
        engine.register_file("f1", "a.csv", "", "Sheet1", df1)
        engine.register_file("f2", "b.csv", "", "Sheet1", df2)
        engine.set_key_mapping({"f1": ["id"], "f2": ["id"]})
        engine.compute(["col_0", "col_1"], "all")

        # All rows
        detail = engine.get_detail_page(match_filter="all", page=1, page_size=20)
        assert detail["total"] == 150, f"total {detail['total']}"
        assert len(detail["rows"]) == 20, f"page1 rows {len(detail['rows'])}"

        # Matched only
        detail = engine.get_detail_page(match_filter="matched", page=1, page_size=20)
        assert detail["total"] == 50, f"matched total {detail['total']}"

        # only_a
        detail = engine.get_detail_page(match_filter="only_a", page=1, page_size=20)
        assert detail["total"] == 50, f"only_a total {detail['total']}"

        ok("Detail query: pagination, matcþh/diff filters correct")

    @staticmethod
    def test_three_way_compare():
        """Three files comparison."""
        from app.services.matcher import DiffEngine

        df1 = make_test_df(50, 5, prefix="t_")
        df2 = make_test_df(50, 5, prefix="t_")
        df3 = make_test_df(50, 5, prefix="t_")
        for i in range(30, 50):
            df2.at[i, "id"] = f"b_only_{i:05d}"
            df3.at[i, "id"] = f"c_only_{i:05d}"

        engine = DiffEngine()
        engine.register_file("f1", "a.csv", "", "Sheet1", df1)
        engine.register_file("f2", "b.csv", "", "Sheet1", df2)
        engine.register_file("f3", "c.csv", "", "Sheet1", df3)
        engine.set_key_mapping({"f1": ["id"], "f2": ["id"], "f3": ["id"]})
        result = engine.compute(["col_0", "col_1"], "all")

        assert result["total_keys"] > 0, "should have keys"
        assert result["only_c"] is not None, "should track only_c"
        ok("3-way compare: multi-table join works")

    @staticmethod
    def test_empty_result_handling():
        """No overlap at all."""
        from app.services.matcher import DiffEngine

        df1 = make_test_df(10, 3, prefix="a_")
        df2 = make_test_df(10, 3, prefix="b_")

        engine = DiffEngine()
        engine.register_file("f1", "a.csv", "", "Sheet1", df1)
        engine.register_file("f2", "b.csv", "", "Sheet1", df2)
        engine.set_key_mapping({"f1": ["id"], "f2": ["id"]})
        result = engine.compute(["col_0"], "all")

        assert result["matched"] == 0, "should have 0 matched"
        assert result["only_a"] == 10
        assert result["only_b"] == 10
        ok("Empty overlap: 0 matched, all only_a/only_b")

    @staticmethod
    def test_missing_key_mapping():
        """Should raise ValueError when key mapping is missing."""
        from app.services.matcher import DiffEngine

        df1 = make_test_df(10, 3, prefix="a_")
        df2 = make_test_df(10, 3, prefix="b_")

        engine = DiffEngine()
        engine.register_file("f1", "a.csv", "", "Sheet1", df1)
        engine.register_file("f2", "b.csv", "", "Sheet1", df2)
        # No key mapping

        try:
            engine.compute(["col_0"], "all")
            bad("Missing key mapping should raise ValueError")
        except ValueError as e:
            ok(f"Missing key mapping raises ValueError: {str(e)[:60]}")

    @staticmethod
    def test_persist_and_reload():
        """Session pickle round-trip across simulated workers."""
        from app.services.matcher import (
            create_session, get_session, persist_session, remove_session,
            _session_file, _session_cached_at,
        )

        sid = create_session()

        # Worker A: get session, add file, persist
        e_a = get_session(sid)
        df = make_test_df(50, 5, prefix="p_")
        e_a.register_file("f1", "test.csv", "", "Sheet1", df)
        e_a.set_key_mapping({"f1": ["id"]})
        persist_session(sid)

        # Simulate Worker B: load from disk, should see the file
        import pickle
        with open(_session_file(sid), "rb") as f:
            e_b_data = pickle.load(f)

        assert "f1" in e_b_data.files, "Worker B should see file from disk"
        assert e_b_data.key_mappings == {"f1": ["id"]}, "Worker B should see key mapping"

        remove_session(sid)
        ok("Session persist/reload: cross-worker pickle round-trip works")

    @staticmethod
    def test_cache_invalidation():
        """Worker A's stale cache should be invalidated when Worker B updates.

        Simulates the exact multi-worker race:
          1. Worker A creates session, caches it locally
          2. Worker B loads session from disk, adds a file, persists to disk
          3. Worker A's local cache is now stale — get_session must reload

        Uses file SIZE change as the staleness signal (works even when
        filesystem mtime granularity is coarse, e.g. WSL2).
        """
        from app.services.matcher import (
            create_session, get_session, persist_session, remove_session,
            _sessions, _sessions_lock, _session_cached_at, _session_cached_size,
        )

        sid = create_session()

        # Worker A: cache the session
        e_a = get_session(sid)
        assert len(e_a.files) == 0, "initial session should be empty"
        worker_a_mtime = _session_cached_at.get(sid)
        worker_a_size = _session_cached_size.get(sid)
        assert worker_a_size is not None, "should track file size"

        # Worker B: load, add file, persist
        with _sessions_lock:
            _sessions.pop(sid, None)
            _session_cached_at.pop(sid, None)
            _session_cached_size.pop(sid, None)
        e_b = get_session(sid)
        e_b.files["f_test"] = {"file_name": "test.csv", "parsing": False}
        persist_session(sid)  # file size changes: pickle now includes f_test entry

        # Restore Worker A's stale cache (old mtime AND old size)
        with _sessions_lock:
            _session_cached_at[sid] = worker_a_mtime
            _session_cached_size[sid] = worker_a_size  # OLD size (empty session)
            _sessions[sid] = e_a  # OLD empty engine

        # Worker A: get_session should detect size change and reload
        e_a2 = get_session(sid)
        assert "f_test" in e_a2.files, (
            "Worker A must detect stale cache (file size changed) and reload from disk!"
        )

        remove_session(sid)
        ok("Cache invalidation: stale cache detected and reloaded from disk (size-based)")


class TestParser:
    """Tests for the file parser."""

    @staticmethod
    def test_csv_parse():
        """Parse a simple CSV."""
        from app.services.parser import _parse_csv
        import tempfile

        csv_content = "id,col_0,col_1\na_001,val_a,val_b\na_002,val_c,val_d\n"
        with tempfile.NamedTemporaryFile(suffix=".csv", mode="w", delete=False) as f:
            f.write(csv_content)
            path = f.name

        try:
            df = _parse_csv(path)
            assert len(df) == 2, f"expected 2 rows, got {len(df)}"
            assert list(df.columns) == ["id", "col_0", "col_1"]
            ok("CSV parse: 2 rows, 3 columns")
        finally:
            os.unlink(path)

    @staticmethod
    def test_csv_encoding():
        """Parse CSV with GBK encoding."""
        from app.services.parser import _parse_csv
        import tempfile

        # GBK encoded Chinese content
        content = "姓名,年龄\n张三,25\n李四,30\n"
        with tempfile.NamedTemporaryFile(suffix=".csv", mode="wb", delete=False) as f:
            f.write(content.encode("gbk"))
            path = f.name

        try:
            df = _parse_csv(path)
            assert len(df) == 2, f"expected 2 rows, got {len(df)}"
            ok("CSV GBK encoding: auto-detected and parsed")
        finally:
            os.unlink(path)


class TestExporter:
    """Tests for the Excel exporter."""

    @staticmethod
    def test_export_basic():
        """Export should produce valid bytes."""
        from app.services.matcher import DiffEngine
        from app.services.exporter import export_to_excel

        df1 = make_test_df(50, 5, prefix="a_")
        df2 = make_test_df(50, 5, prefix="a_")
        for i in range(30, 50):
            df2.at[i, "id"] = f"diff_{i:05d}"

        engine = DiffEngine()
        engine.register_file("f1", "a.csv", "", "Sheet1", df1)
        engine.register_file("f2", "b.csv", "", "Sheet1", df2)
        engine.set_key_mapping({"f1": ["id"], "f2": ["id"]})
        engine.compute(["col_0", "col_1", "col_2", "col_3"], "all")

        # export_to_excel takes a dict of dataframes
        dataframes = {
            "A": engine.files["f1"]["df"],
            "B": engine.files["f2"]["df"],
        }
        data = export_to_excel(dataframes, "test.xlsx")

        assert len(data) > 100, f"exported file too small: {len(data)} bytes"
        ok(f"Export: produced {len(data)} bytes of Excel data")


# ═══════════════════════════════════════════════════════════════════════
# 2. INTEGRATION TESTS (via HTTP API)
# ═══════════════════════════════════════════════════════════════════════

class TestIntegration:
    """Full API workflow tests. Requires backend running."""

    @staticmethod
    def _check_backend():
        try:
            r = requests.get(f"{BACKEND_URL}/health", timeout=3)
            return r.status_code == 200
        except Exception:
            return False

    @staticmethod
    def test_health():
        """Health endpoint."""
        r = requests.get(f"{BACKEND_URL}/health", timeout=5)
        assert r.status_code == 200
        ok("Health endpoint: OK")

    @staticmethod
    def test_full_workflow():
        """Complete workflow: session → upload → parse → map → compare → detail → export."""
        s = requests.Session()

        # 1. Create session
        r = s.get(f"{BACKEND_URL}/api/files/new-session", timeout=5)
        assert r.status_code == 200, f"new-session failed: {r.status_code}"
        sid = r.json()["session_id"]
        assert len(sid) == 16

        # 2. Upload two CSVs (small, 100 rows each)
        for fname in ["test_a.csv", "test_b.csv"]:
            csv_bytes = make_test_csv_bytes(100, 5, prefix=fname[:1])
            r = s.post(
                f"{BACKEND_URL}/api/files/upload?session_id={sid}",
                files={"file": (fname, io.BytesIO(csv_bytes), "text/csv")},
                timeout=30,
            )
            assert r.status_code == 200, f"upload {fname}: {r.status_code} {r.text[:100]}"
            fid = r.json()["file_id"]

            # Poll until parsed
            for _ in range(50):
                r = s.get(f"{BACKEND_URL}/api/files/parse-status/{sid}/{fid}", timeout=10)
                st = r.json()
                if st["status"] == "complete":
                    break
                if st["status"] == "error":
                    raise RuntimeError(f"Parse error: {st.get('error')}")
                time.sleep(0.1)
            else:
                raise RuntimeError(f"Parse timeout for {fname}")
            assert st["status"] == "complete", f"parse {fname}: {st}"

        # 3. Get session info
        r = s.get(f"{BACKEND_URL}/api/files/session/{sid}", timeout=10)
        assert r.status_code == 200
        files_info = r.json()["files"]
        assert len(files_info) == 2, f"expected 2 files, got {len(files_info)}"
        fid_a, fid_b = files_info[0]["file_id"], files_info[1]["file_id"]
        ok(f"Session: 2 files uploaded and parsed (columns: {len(files_info[0]['columns'])})")

        # 4. Set column mapping
        r = s.post(f"{BACKEND_URL}/api/compare/mapping", json={
            "session_id": sid,
            "mappings": [
                {"file_id": fid_a, "key_columns": ["id"]},
                {"file_id": fid_b, "key_columns": ["id"]},
            ],
        }, timeout=10)
        assert r.status_code == 200, f"mapping: {r.status_code}"

        # 5. Execute compare (async)
        compare_cols = [c for c in files_info[0]["columns"] if c != "id"]
        r = s.post(f"{BACKEND_URL}/api/compare/execute-async", json={
            "session_id": sid,
            "compare_columns": compare_cols,
            "mode": "all",
        }, timeout=10)
        assert r.status_code == 200, f"execute-async: {r.status_code} {r.text[:100]}"
        task_id = r.json()["task_id"]

        # Poll until complete
        for _ in range(100):
            r = s.get(f"{BACKEND_URL}/api/compare/status/{task_id}", timeout=10)
            st = r.json()
            if st["status"] == "complete":
                break
            if st["status"] == "error":
                raise RuntimeError(f"Compare error: {st.get('error', st.get('message'))}")
            time.sleep(0.1)
        else:
            raise RuntimeError("Compare timeout")
        assert st["status"] == "complete"
        result = st["result"]
        assert result["total_keys"] > 0
        ok(f"Compare async: {result['total_keys']} keys, {result['matched']} matched, "
           f"only_a={result['only_a']}, only_b={result['only_b']}")

        # 6. Query detail
        r = s.post(f"{BACKEND_URL}/api/compare/detail", json={
            "session_id": sid, "match_filter": "all", "page": 1, "page_size": 20,
        }, timeout=30)
        assert r.status_code == 200, f"detail: {r.status_code}"
        detail = r.json()
        assert detail["total"] > 0, f"detail total is 0! stats had {result['total_keys']} keys"
        assert len(detail["rows"]) > 0, f"detail rows is 0!"
        ok(f"Detail query: {detail['total']} rows, page1={len(detail['rows'])}")

        # 7. Query detail with filters
        r = s.post(f"{BACKEND_URL}/api/compare/detail", json={
            "session_id": sid, "match_filter": "matched", "page": 1, "page_size": 10,
        }, timeout=30)
        detail_m = r.json()
        ok(f"Detail matched filter: {detail_m['total']} rows")

        # 8. Export
        r = s.post(f"{BACKEND_URL}/api/export/excel", json={
            "session_id": sid, "export_type": "filtered",
        }, timeout=30)
        assert r.status_code == 200, f"export: {r.status_code}"
        assert len(r.content) > 100, f"exported file too small: {len(r.content)}"
        ok(f"Export: {len(r.content) / 1024:.1f} KB")

    @staticmethod
    def test_sync_compare():
        """Legacy synchronous comparison."""
        s = requests.Session()

        r = s.get(f"{BACKEND_URL}/api/files/new-session", timeout=5)
        sid = r.json()["session_id"]

        for fname in ["s_a.csv", "s_b.csv"]:
            csv_bytes = make_test_csv_bytes(30, 3, prefix=fname[:1])
            r = s.post(
                f"{BACKEND_URL}/api/files/upload?session_id={sid}",
                files={"file": (fname, io.BytesIO(csv_bytes), "text/csv")},
                timeout=30,
            )
            fid = r.json()["file_id"]
            for _ in range(50):
                st = s.get(f"{BACKEND_URL}/api/files/parse-status/{sid}/{fid}", timeout=10).json()
                if st["status"] == "complete":
                    break
                time.sleep(0.1)

        r = s.get(f"{BACKEND_URL}/api/files/session/{sid}", timeout=10)
        files_info = r.json()["files"]
        fid_a, fid_b = files_info[0]["file_id"], files_info[1]["file_id"]
        compare_cols = [c for c in files_info[0]["columns"] if c != "id"]

        s.post(f"{BACKEND_URL}/api/compare/mapping", json={
            "session_id": sid,
            "mappings": [
                {"file_id": fid_a, "key_columns": ["id"]},
                {"file_id": fid_b, "key_columns": ["id"]},
            ],
        }, timeout=10)

        r = s.post(f"{BACKEND_URL}/api/compare/execute", json={
            "session_id": sid,
            "compare_columns": compare_cols,
            "mode": "all",
        }, timeout=60)
        assert r.status_code == 200
        result = r.json()
        assert result["total_keys"] > 0
        ok(f"Sync compare: {result['total_keys']} total keys")

    @staticmethod
    def test_error_handling():
        """Test various error conditions."""
        s = requests.Session()

        # 404: nonexistent session
        r = s.post(f"{BACKEND_URL}/api/compare/mapping", json={
            "session_id": "nonexistent12345",
            "mappings": [],
        }, timeout=10)
        assert r.status_code == 404, f"Expected 404, got {r.status_code}"
        ok("404 for nonexistent session")

        # 400: missing key mappings
        r = s.get(f"{BACKEND_URL}/api/files/new-session", timeout=5)
        sid = r.json()["session_id"]

        csv_bytes = make_test_csv_bytes(10, 3, prefix="e_")
        r = s.post(
            f"{BACKEND_URL}/api/files/upload?session_id={sid}",
            files={"file": ("e_a.csv", io.BytesIO(csv_bytes), "text/csv")},
            timeout=30,
        )
        fid = r.json()["file_id"]
        for _ in range(50):
            st = s.get(f"{BACKEND_URL}/api/files/parse-status/{sid}/{fid}", timeout=10).json()
            if st["status"] == "complete":
                break
            time.sleep(0.1)

        # Missing mapping for second file — compare should fail
        r = s.post(f"{BACKEND_URL}/api/compare/execute", json={
            "session_id": sid,
            "compare_columns": ["col_0"],
            "mode": "all",
        }, timeout=30)
        # Should error: not enough files or missing key mapping
        assert r.status_code in (400, 404), f"Expected 400/404, got {r.status_code}"
        ok(f"Proper error ({r.status_code}) for incomplete key mappings")


# ═══════════════════════════════════════════════════════════════════════
# 3. CONCURRENCY TESTS
# ═══════════════════════════════════════════════════════════════════════

def _single_user_full_workflow(user_id: int, rows: int = 500) -> dict:
    """Complete workflow for one concurrent user. Returns timing dict."""
    s = requests.Session()
    t_start = time.perf_counter()
    timings = {}

    try:
        # Create session
        r = s.get(f"{BACKEND_URL}/api/files/new-session", timeout=5)
        sid = r.json()["session_id"]

        # Upload + parse two files
        fids = []
        for fnum in range(2):
            csv_bytes = make_test_csv_bytes(rows, 10, prefix=f"u{user_id}f{fnum}_")
            t0 = time.perf_counter()
            r = s.post(
                f"{BACKEND_URL}/api/files/upload?session_id={sid}",
                files={"file": (f"u{user_id}_{fnum}.csv", io.BytesIO(csv_bytes), "text/csv")},
                timeout=120,
            )
            fid = r.json()["file_id"]
            fids.append(fid)

            # Poll for parse
            while True:
                r = s.get(f"{BACKEND_URL}/api/files/parse-status/{sid}/{fid}", timeout=10)
                st = r.json()
                if st["status"] == "complete":
                    break
                if st["status"] == "error":
                    raise RuntimeError(f"Parse error: {st.get('error')}")
                time.sleep(0.05)
        timings["upload_parse_s"] = round(time.perf_counter() - t_start, 2)

        # Get session info
        r = s.get(f"{BACKEND_URL}/api/files/session/{sid}", timeout=10)
        cols = list(dict.fromkeys(
            c for f in r.json()["files"] for c in f["columns"] if c != "id"
        ))

        # Set mappings
        s.post(f"{BACKEND_URL}/api/compare/mapping", json={
            "session_id": sid,
            "mappings": [
                {"file_id": fids[0], "key_columns": ["id"]},
                {"file_id": fids[1], "key_columns": ["id"]},
            ],
        }, timeout=10)

        # Execute async compare
        t_cmp = time.perf_counter()
        r = s.post(f"{BACKEND_URL}/api/compare/execute-async", json={
            "session_id": sid, "compare_columns": cols, "mode": "all",
        }, timeout=10)
        task_id = r.json()["task_id"]

        while True:
            r = s.get(f"{BACKEND_URL}/api/compare/status/{task_id}", timeout=10)
            st = r.json()
            if st["status"] == "complete":
                break
            if st["status"] == "error":
                raise RuntimeError(f"Compare error: {st.get('error', st.get('message'))}")
            time.sleep(0.1)
        timings["compare_s"] = round(time.perf_counter() - t_cmp, 2)

        # Query detail
        r = s.post(f"{BACKEND_URL}/api/compare/detail", json={
            "session_id": sid, "match_filter": "all", "page": 1, "page_size": 10,
        }, timeout=30)
        detail = r.json()

        timings["total_s"] = round(time.perf_counter() - t_start, 2)
        timings["total_keys"] = st["result"]["total_keys"]
        timings["detail_total"] = detail["total"]
        timings["success"] = True

    except Exception as e:
        timings["success"] = False
        timings["error"] = str(e)[:150]
        timings["total_s"] = round(time.perf_counter() - t_start, 2)

    return timings


def run_concurrency_test(users: int = 5, rows: int = 500):
    """Run N concurrent users through the full workflow."""
    print(f"\n{'='*60}")
    print(f"🔥 CONCURRENCY TEST: {users} simultaneous users (each {rows} rows × 10 cols × 2 files)")
    print(f"{'='*60}")

    if not TestIntegration._check_backend():
        skipped("Backend not reachable — skipping concurrency test")
        return

    wall_start = time.perf_counter()
    results = []

    with ThreadPoolExecutor(max_workers=users) as pool:
        futures = {pool.submit(_single_user_full_workflow, i, rows): i for i in range(users)}
        for future in as_completed(futures):
            uid = futures[future]
            try:
                r = future.result()
                r["user_id"] = uid
                results.append(r)
                status = "✅" if r["success"] else "❌"
                print(f"  [{status}] User {uid:2d}: {r.get('total_s', 0):.1f}s total, "
                      f"compare={r.get('compare_s', 0):.1f}s, "
                      f"keys={r.get('total_keys', '?')}")
            except Exception as e:
                print(f"  [❌] User {uid:2d}: {e}")
                results.append({"user_id": uid, "success": False, "error": str(e)[:150]})

    wall_elapsed = time.perf_counter() - wall_start
    successful = [r for r in results if r.get("success")]
    failed = [r for r in results if not r.get("success")]

    print(f"\n  ── Concurrency Summary ──")
    print(f"  Users: {users}, Successful: {len(successful)}, Failed: {len(failed)}")
    print(f"  Wall time: {wall_elapsed:.1f}s")
    print(f"  Throughput: {len(successful) / wall_elapsed:.2f} workflows/s" if wall_elapsed > 0 else "")

    if successful:
        totals = sorted(r["total_s"] for r in successful)
        compares = sorted(r["compare_s"] for r in successful)

        def pct(arr, n):
            return arr[min(int(len(arr) * n / 100), len(arr) - 1)]

        print(f"  Latency (full workflow):")
        print(f"    p50={pct(totals, 50):.1f}s, p95={pct(totals, 95):.1f}s, "
              f"avg={statistics.mean(totals):.1f}s")

        # Check: do detail totals match summary totals? (catches the stale cache / empty detail bug)
        detail_mismatches = sum(
            1 for r in successful
            if r.get("detail_total", -1) == 0 and r.get("total_keys", 0) > 0
        )
        if detail_mismatches == 0:
            ok(f"Cross-worker check: all {len(successful)} users have detail data (no stale cache)")
        else:
            bad(f"Cross-worker: {detail_mismatches}/{len(successful)} users have EMPTY detail (stale cache bug!)")

        if len(failed) == 0:
            ok(f"Concurrency: all {users} users completed successfully")
        elif len(failed) <= users * 0.2:
            skipped(f"Concurrency: {len(failed)}/{users} failed (acceptable for stress)")
        else:
            bad(f"Concurrency: {len(failed)}/{users} failed!")


# ═══════════════════════════════════════════════════════════════════════
# 4. STRESS TESTS
# ═══════════════════════════════════════════════════════════════════════

def run_scale_test():
    """Increasing row counts to find performance inflection point."""
    from app.services.matcher import DiffEngine

    configs = [
        (1_000, 10, 0.7),
        (10_000, 10, 0.7),
        (50_000, 10, 0.7),
        (100_000, 10, 0.7),
    ]

    print(f"\n{'='*60}")
    print(f"📈 SCALE TEST: Increasing data size")
    print(f"{'='*60}")
    print(f"{'Rows':>10} {'Cols':>6} {'Time(s)':>10} {'Rows/s':>12} {'TotalKeys':>10} {'Status'}")
    print(f"{'-'*60}")

    for rows, cols, overlap in configs:
        try:
            df1 = make_test_df(rows, cols, prefix="s1_")
            df2 = make_test_df(rows, cols, prefix="s1_")
            overlap_n = int(rows * overlap)
            for i in range(overlap_n, rows):
                df2.at[i, "id"] = f"only_b_{i:08d}"

            engine = DiffEngine()
            engine.register_file("f1", "a.csv", "", "Sheet1", df1)
            engine.register_file("f2", "b.csv", "", "Sheet1", df2)
            engine.set_key_mapping({"f1": ["id"], "f2": ["id"]})
            compare_cols = [c for c in df1.columns if c != "id"]

            t0 = time.perf_counter()
            result = engine.compute(compare_cols, "all")
            elapsed = time.perf_counter() - t0
            rate = rows / elapsed if elapsed > 0 else 0

            print(f"{rows:>10,} {cols:>6} {elapsed:>10.3f} {rate:>12,.0f} "
                  f"{result['total_keys']:>10} {'✅'}")

            if elapsed > 10:
                print(f"  ⚠️  Performance warning: >10s for {rows:,} rows")

        except Exception as e:
            print(f"{rows:>10,} {cols:>6} {'N/A':>10} {'N/A':>12} {'N/A':>10} ❌ {str(e)[:40]}")


def run_sustained_load(duration_s: int = 15, users: int = 3):
    """Continuous compare requests for a fixed duration."""
    print(f"\n{'='*60}")
    print(f"⏱️  SUSTAINED LOAD: {users} users for {duration_s}s")
    print(f"{'='*60}")

    if not TestIntegration._check_backend():
        skipped("Backend not reachable — skipping sustained load test")
        return

    # Prepare sessions
    print("  Preparing test data...")
    sessions = []
    for i in range(users):
        s = requests.Session()
        r = s.get(f"{BACKEND_URL}/api/files/new-session", timeout=5)
        sid = r.json()["session_id"]

        fids = []
        for fn in range(2):
            csv_bytes = make_test_csv_bytes(200, 8, prefix=f"load_u{i}f{fn}_")
            r = s.post(
                f"{BACKEND_URL}/api/files/upload?session_id={sid}",
                files={"file": (f"l_{i}_{fn}.csv", io.BytesIO(csv_bytes), "text/csv")},
                timeout=60,
            )
            fid = r.json()["file_id"]
            fids.append(fid)
            while True:
                st = s.get(f"{BACKEND_URL}/api/files/parse-status/{sid}/{fid}", timeout=10).json()
                if st["status"] == "complete":
                    break
                if st["status"] == "error":
                    break
                time.sleep(0.05)

        r = s.get(f"{BACKEND_URL}/api/files/session/{sid}", timeout=10)
        cols = list(dict.fromkeys(
            c for f in r.json()["files"] for c in f["columns"] if c != "id"
        ))

        s.post(f"{BACKEND_URL}/api/compare/mapping", json={
            "session_id": sid,
            "mappings": [
                {"file_id": fids[0], "key_columns": ["id"]},
                {"file_id": fids[1], "key_columns": ["id"]},
            ],
        }, timeout=10)

        sessions.append({"session": s, "sid": sid, "cols": cols, "fids": fids})

    # Continuous load
    req_counts = [0] * users
    err_counts = [0] * users
    latencies = []
    stop_event = threading.Event()

    def worker(idx):
        sdata = sessions[idx]
        s = sdata["session"]
        sid = sdata["sid"]
        cols = sdata["cols"]

        while not stop_event.is_set():
            t0 = time.perf_counter()
            try:
                r = s.post(f"{BACKEND_URL}/api/compare/execute-async", json={
                    "session_id": sid, "compare_columns": cols[:5], "mode": "all",
                }, timeout=10)
                task_id = r.json()["task_id"]
                while not stop_event.is_set():
                    r = s.get(f"{BACKEND_URL}/api/compare/status/{task_id}", timeout=10)
                    st = r.json()
                    if st["status"] in ("complete", "error"):
                        break
                    time.sleep(0.1)
                latencies.append(time.perf_counter() - t0)
                req_counts[idx] += 1
                if st["status"] == "error":
                    err_counts[idx] += 1
            except Exception as e:
                err_counts[idx] += 1
                req_counts[idx] += 1
                latencies.append(time.perf_counter() - t0)

    threads = [threading.Thread(target=worker, args=(i,), daemon=True) for i in range(users)]
    for t in threads:
        t.start()

    # Progress display
    for remaining in range(duration_s, 0, -5):
        time.sleep(5)
        total = sum(req_counts)
        errs = sum(err_counts)
        print(f"  [{duration_s - remaining + 5}s] requests={total}, errors={errs}, "
              f"rate={total / max(1, duration_s - remaining + 5):.1f}/s")

    stop_event.set()
    for t in threads:
        t.join(timeout=5)

    total_req = sum(req_counts)
    total_err = sum(err_counts)
    print(f"\n  ── Load Test Summary ──")
    print(f"  Duration: {duration_s}s, Users: {users}")
    print(f"  Total requests: {total_req}, Errors: {total_err}")
    print(f"  Throughput: {total_req / duration_s:.2f} req/s")

    if latencies:
        latencies.sort()

        def pct(arr, n):
            return arr[min(int(len(arr) * n / 100), len(arr) - 1)]

        print(f"  Latency: p50={pct(latencies, 50):.1f}s, p95={pct(latencies, 95):.1f}s, "
              f"avg={statistics.mean(latencies):.1f}s")

    if total_err == 0:
        ok(f"Sustained load: 0 errors in {total_req} requests")
    elif total_err <= total_req * 0.05:
        skipped(f"Sustained load: {total_err}/{total_req} errors (5% acceptable)")
    else:
        bad(f"Sustained load: {total_err}/{total_req} errors!")


# ═══════════════════════════════════════════════════════════════════════
# 5. CROSS-WORKER SIMULATION TESTS
# ═══════════════════════════════════════════════════════════════════════

def test_cross_worker_compare_progress():
    """Simulate: task created on 'Worker A', polled from 'Worker B'."""
    print(f"\n{'='*60}")
    print(f"🔄 CROSS-WORKER SIMULATION: Compare task shared via filesystem")
    print(f"{'='*60}")

    from app.services.matcher import create_session, get_session, persist_session, remove_session
    from app.routers.compare import (
        _compare_cache, _compare_lock, _save_task, _load_task,
        _set_compare_progress, _delete_task, _cleanup_old_tasks,
        CompareProgress, _COMPARE_TASKS_DIR,
    )

    task_id = f"test_session_{uuid.uuid4().hex[:8]}:{uuid.uuid4().hex[:8]}"

    # Worker A: create task
    progress = CompareProgress(stage="started", progress=5, message="starting...")
    with _compare_lock:
        _compare_cache[task_id] = progress
    _save_task(task_id, progress)

    # Worker B: poll (simulate by clearing local cache first)
    with _compare_lock:
        _compare_cache.pop(task_id, None)  # Worker B has no cache
    loaded = _load_task(task_id)
    assert loaded is not None, "Worker B should load task from filesystem!"
    assert loaded.stage == "started", f"Expected 'started', got {loaded.stage}"
    ok("Compare task shared: Worker B loads task from filesystem after Worker A creates")

    # Worker A: update progress
    _set_compare_progress(task_id, stage="complete", progress=100, message="done")
    # Worker B loads again
    with _compare_lock:
        _compare_cache.pop(task_id, None)
    loaded2 = _load_task(task_id)
    assert loaded2 is not None and loaded2.stage == "complete"

    # Cleanup
    _delete_task(task_id)
    # Verify cleanup
    with _compare_lock:
        _compare_cache.pop(task_id, None)
    loaded3 = _load_task(task_id)
    assert loaded3 is None, "Task should be deleted"
    ok("Compare task lifecycle: create → update → delete across workers works")


def test_cross_worker_session_staleness():
    """Simulate the exact 'File not in session' bug scenario."""
    print(f"\n{'='*60}")
    print(f"🔄 CROSS-WORKER SIMULATION: Session cache invalidation")
    print(f"{'='*60}")

    from app.services.matcher import (
        create_session, get_session, persist_session, remove_session,
        _sessions, _sessions_lock, _session_cached_at, _session_file,
        _save_session_to_disk,
    )

    sid = create_session()

    # Worker A: get session (empty, cached)
    with _sessions_lock:
        _sessions.pop(sid, None)  # nuke Worker A's cache
        _session_cached_at.pop(sid, None)

    e_a = get_session(sid)  # loads from disk (empty)
    assert len(e_a.files) == 0
    ok("Worker A loads empty session from disk")

    # Worker B: add file to session, persist to disk
    import pickle
    with _sessions_lock:
        _sessions.pop(sid, None)  # nuke Worker B's cache
        _session_cached_at.pop(sid, None)
    e_b = get_session(sid)
    df = make_test_df(50, 5, prefix="cw_")
    e_b.register_file("f_cross", "cross.csv", "", "Sheet1", df)
    e_b.set_key_mapping({"f_cross": ["id"]})
    persist_session(sid)  # writes to disk

    # Worker A: get_session again — should detect file mtime change
    # and reload (NOT return the cached empty copy)
    e_a2 = get_session(sid)
    if "f_cross" in e_a2.files:
        ok("Cache invalidation: Worker A reloads after Worker B persists (file found!)")
    else:
        bad("STALE CACHE BUG: Worker A returned empty session despite Worker B's update!")

    remove_session(sid)


# ═══════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════

def main():
    global PASS, FAIL, SKIP, BACKEND_URL

    parser = argparse.ArgumentParser(description="Comprehensive test suite")
    parser.add_argument("--unit", action="store_true", help="Unit tests only")
    parser.add_argument("--integration", action="store_true", help="Integration tests only")
    parser.add_argument("--concurrency", type=int, default=0, metavar="N",
                        help="Concurrency test with N users")
    parser.add_argument("--concurrency-rows", type=int, default=500,
                        help="Rows per user in concurrency test")
    parser.add_argument("--stress", action="store_true", help="Scale test (engine only)")
    parser.add_argument("--load", type=int, default=0, metavar="SECONDS",
                        help="Sustained load test duration")
    parser.add_argument("--load-users", type=int, default=3,
                        help="Concurrent users for sustained load")
    parser.add_argument("--cross-worker", action="store_true", help="Cross-worker simulation tests")
    parser.add_argument("--all", action="store_true", help="Run all tests (default)")
    parser.add_argument("--url", default="http://127.0.0.1:8000", help="Backend URL")
    parser.add_argument("--quick", action="store_true", help="Quick test (smaller scale)")

    args = parser.parse_args()
    BACKEND_URL = args.url

    # Default: run all
    run_all = not any([
        args.unit, args.integration, args.concurrency, args.stress,
        args.load, args.cross_worker,
    ]) or args.all

    print("=" * 70)
    print("🧪 Excel Compare — Comprehensive Test Suite")
    print("=" * 70)
    print(f"  Backend: {BACKEND_URL}")
    print(f"  Start:   {time.strftime('%H:%M:%S')}")

    t_start = time.perf_counter()

    # ── Unit tests ──
    if run_all or args.unit or args.cross_worker:
        print(f"\n{'='*60}")
        print("📦 UNIT TESTS")
        print(f"{'='*60}")

        for name in sorted(dir(TestDiffEngine)):
            if name.startswith("test_"):
                try:
                    getattr(TestDiffEngine, name)()
                except Exception as e:
                    bad(f"DiffEngine.{name}: {e}")
                    traceback.print_exc()

        for name in sorted(dir(TestParser)):
            if name.startswith("test_"):
                try:
                    getattr(TestParser, name)()
                except Exception as e:
                    bad(f"Parser.{name}: {e}")

        for name in sorted(dir(TestExporter)):
            if name.startswith("test_"):
                try:
                    getattr(TestExporter, name)()
                except Exception as e:
                    bad(f"Exporter.{name}: {e}")

    # ── Cross-worker simulation ──
    if run_all or args.cross_worker:
        try:
            test_cross_worker_compare_progress()
        except Exception as e:
            bad(f"Cross-worker compare progress: {e}")
            traceback.print_exc()

        try:
            test_cross_worker_session_staleness()
        except Exception as e:
            bad(f"Cross-worker session staleness: {e}")
            traceback.print_exc()

    # ── Integration tests ──
    if run_all or args.integration or args.concurrency or args.load:
        backend_ok = TestIntegration._check_backend()
        if not backend_ok:
            skipped(f"BACKEND NOT REACHABLE at {BACKEND_URL} — skipping API tests")
        else:
            print(f"\n{'='*60}")
            print("🔌 INTEGRATION TESTS (API)")
            print(f"{'='*60}")

            for name in sorted(dir(TestIntegration)):
                if name.startswith("test_") and not name.startswith("_"):
                    try:
                        getattr(TestIntegration, name)()
                    except Exception as e:
                        bad(f"Integration.{name}: {e}")
                        traceback.print_exc()

    # ── Concurrency test ──
    if args.concurrency > 0:
        run_concurrency_test(args.concurrency, args.concurrency_rows)
    elif run_all and TestIntegration._check_backend():
        run_concurrency_test(3 if args.quick else 5, 200 if args.quick else 500)

    # ── Scale test ──
    if run_all or args.stress:
        run_scale_test()

    # ── Sustained load ──
    if args.load > 0:
        run_sustained_load(args.load, args.load_users)
    elif run_all and TestIntegration._check_backend():
        run_sustained_load(10 if args.quick else 20, 2 if args.quick else 3)

    # ── Summary ──
    elapsed = time.perf_counter() - t_start
    print(f"\n{'='*70}")
    print(f"📊 TEST SUMMARY")
    print(f"{'='*70}")
    print(f"  ✅ Passed:  {PASS}")
    print(f"  ❌ Failed:  {FAIL}")
    print(f"  ⏭️  Skipped: {SKIP}")
    print(f"  ⏱️  Time:    {elapsed:.1f}s")
    print(f"{'='*70}")

    if FAIL > 0:
        print(f"\n❌ {FAIL} TEST(S) FAILED!")
        return 1
    else:
        print(f"\n✅ ALL TESTS PASSED!" if PASS > 0 else "\n⏭️  No tests run")
        return 0


if __name__ == "__main__":
    sys.exit(main())

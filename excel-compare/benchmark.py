#!/usr/bin/env python3
"""
压力测试脚本 —— 测试智能多表比对工具的数据处理能力。

使用方法：
    # 启动后端后：
    python benchmark.py --rows 10000 --cols 20

    # 仅测试引擎（不启动后端）：
    python benchmark.py --engine-only --rows 50000 --cols 30

    # 递增压力测试：
    python benchmark.py --scale
"""

import argparse
import io
import sys
import time
import tempfile
import os
from pathlib import Path

import pandas as pd

# 确保能找到 app 模块
sys.path.insert(0, str(Path(__file__).resolve().parent / "backend"))

HERE = Path(__file__).resolve().parent


def generate_data(rows: int, cols: int, id_prefix: str = "") -> pd.DataFrame:
    """生成指定行数列数的测试 DataFrame。"""
    import random
    import string

    data = {
        "id": [f"{id_prefix}{i:010d}" for i in range(rows)],
    }
    for j in range(cols - 1):
        col_name = f"col_{j:03d}"
        if j % 3 == 0:
            # 文本列
            data[col_name] = [f"val_{random.randint(0, rows//2):06d}" for _ in range(rows)]
        elif j % 3 == 1:
            # 数字列
            data[col_name] = [random.randint(0, 1000000) for _ in range(rows)]
        else:
            # 日期列
            data[col_name] = [f"2024-{random.randint(1,12):02d}-{random.randint(1,28):02d}" for _ in range(rows)]
    return pd.DataFrame(data)


class Benchmark:
    def __init__(self, rows: int, cols: int, overlap: float = 0.7):
        self.rows = rows
        self.cols = cols
        self.overlap = overlap
        self.results = {}

    def _save_excel(self, df: pd.DataFrame, path: str):
        """保存 DataFrame 到 Excel，返回文件大小。"""
        df.to_excel(path, index=False)
        return os.path.getsize(path)

    def engine_only(self):
        """仅测试后端引擎（不经过 HTTP）。"""
        print(f"\n{'='*60}")
        print(f"📊 引擎压力测试: {self.rows} 行 × {self.cols} 列 × 2 表")
        print(f"{'='*60}")

        from app.services.matcher import DiffEngine

        # 生成数据：两表有 overlap% 的重叠
        overlap_rows = int(self.rows * self.overlap)
        unique_rows = self.rows - overlap_rows

        df1 = generate_data(self.rows, self.cols, id_prefix="")
        df2 = generate_data(self.rows, self.cols, id_prefix="")

        # 让后 unique_rows 条记录的 id 不同，剩余 overlap_rows 条相同
        for i in range(overlap_rows, self.rows):
            df2.at[i, "id"] = f"only_b_{i:010d}"

        compare_cols = [c for c in df1.columns if c != "id"]

        engine = DiffEngine()
        engine.register_file("f1", "table_a.xlsx", "", "Sheet1", df1)
        engine.register_file("f2", "table_b.xlsx", "", "Sheet1", df2)
        engine.set_key_mapping({"f1": ["id"], "f2": ["id"]})

        # 计时
        t0 = time.perf_counter()
        result = engine.compute(compare_cols, "all")
        t1 = time.perf_counter()

        elapsed = t1 - t0
        rate = self.rows / elapsed if elapsed > 0 else 0

        print(f"  结果: total={result['total_keys']}, matched={result['matched']}, "
              f"only_a={result['only_a']}, only_b={result['only_b']}")
        print(f"  耗时: {elapsed:.2f}s")
        print(f"  速率: {rate:,.0f} 行/秒")

        # 测试分页查询
        t2 = time.perf_counter()
        detail = engine.get_detail_page(match_filter="all", page=1, page_size=100)
        t3 = time.perf_counter()
        print(f"  分页查询(100条): {t3-t2:.4f}s, 总条数: {detail['total']}")

        # 测试带过滤的分页
        t4 = time.perf_counter()
        detail2 = engine.get_detail_page(match_filter="matched", diff_filter="different", page=1, page_size=50)
        t5 = time.perf_counter()
        print(f"  过滤分页(matched+diff): {t5-t4:.4f}s, 条数: {detail2['total']}")

        self.results["engine"] = {
            "rows": self.rows,
            "cols": self.cols,
            "total_keys": result["total_keys"],
            "elapsed_s": round(elapsed, 2),
            "rows_per_sec": round(rate, 0),
            "detail_query_s": round(t3 - t2, 4),
        }
        return self.results["engine"]

    def api_test(self, base_url: str = "http://127.0.0.1:8000"):
        """通过 HTTP API 测试（需先启动后端）。"""
        import requests

        print(f"\n{'='*60}")
        print(f"🌐 API 压力测试: {self.rows} 行 × {self.cols} 列")
        print(f"{'='*60}")

        # 生成文件
        tmp_dir = tempfile.mkdtemp()
        f1_path = os.path.join(tmp_dir, "table_a.xlsx")
        f2_path = os.path.join(tmp_dir, "table_b.xlsx")

        overlap_rows = int(self.rows * self.overlap)
        df1 = generate_data(self.rows, self.cols, id_prefix="")
        df2 = generate_data(self.rows, self.cols, id_prefix="")
        for i in range(overlap_rows, self.rows):
            df2.at[i, "id"] = f"only_b_{i:010d}"

        size1 = self._save_excel(df1, f1_path)
        size2 = self._save_excel(df2, f2_path)
        print(f"  文件大小: A={size1/1024:.0f}KB, B={size2/1024:.0f}KB")
        print(f"  总数据量: {(size1+size2)/1024/1024:.1f}MB")

        try:
            # 创建会话
            s = requests.Session()
            r = s.get(f"{base_url}/api/files/new-session", timeout=5)
            sid = r.json()["session_id"]

            # 上传
            t0 = time.perf_counter()
            r1 = s.post(f"{base_url}/api/files/upload?session_id={sid}",
                        files={"file": open(f1_path, "rb")}, timeout=300)
            fid_a = r1.json()["file_id"]
            t1 = time.perf_counter()
            print(f"  上传 A: {t1-t0:.1f}s")

            r2 = s.post(f"{base_url}/api/files/upload?session_id={sid}",
                        files={"file": open(f2_path, "rb")}, timeout=300)
            fid_b = r2.json()["file_id"]
            t2 = time.perf_counter()
            print(f"  上传 B: {t2-t1:.1f}s")

            # 设置映射
            r3 = s.post(f"{base_url}/api/compare/mapping", json={
                "session_id": sid,
                "mappings": [
                    {"file_id": fid_a, "key_columns": ["id"]},
                    {"file_id": fid_b, "key_columns": ["id"]},
                ],
            }, timeout=10)
            assert r3.status_code == 200

            # 比对
            compare_cols = [c for c in df1.columns if c != "id"]
            t3 = time.perf_counter()
            r4 = s.post(f"{base_url}/api/compare/execute", json={
                "session_id": sid,
                "compare_columns": compare_cols,
                "mode": "all",
            }, timeout=600)
            t4 = time.perf_counter()
            result = r4.json()
            print(f"  比对执行: {t4-t3:.2f}s")
            print(f"  结果: total={result['total_keys']}, matched={result['matched']}")

            # 分页查询
            t5 = time.perf_counter()
            r5 = s.post(f"{base_url}/api/compare/detail", json={
                "session_id": sid, "match_filter": "all", "page": 1, "page_size": 50,
            }, timeout=30)
            t6 = time.perf_counter()
            print(f"  分页查询: {t6-t5:.4f}s")

            # 导出
            t7 = time.perf_counter()
            r6 = s.post(f"{base_url}/api/export/excel", json={
                "session_id": sid, "export_type": "filtered",
            }, timeout=60)
            t8 = time.perf_counter()
            print(f"  导出: {t8-t7:.2f}s, {len(r6.content)/1024/1024:.1f}MB")

            self.results["api"] = {
                "rows": self.rows,
                "cols": self.cols,
                "file_size_mb": round((size1 + size2) / 1024 / 1024, 1),
                "upload_s": round(t2 - t0, 1),
                "compare_s": round(t4 - t3, 2),
                "detail_s": round(t6 - t5, 4),
                "export_s": round(t8 - t7, 2),
            }

        except Exception as e:
            print(f"  ❌ 错误: {e}")
        finally:
            os.unlink(f1_path)
            os.unlink(f2_path)
            os.rmdir(tmp_dir)

        return self.results.get("api")


# ── Concurrency / multi-user stress test ──

import statistics
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed


def _build_test_csv(rows: int, cols: int, prefix: str = "") -> bytes:
    """Build an in-memory CSV file for upload testing."""
    import random
    buf = io.StringIO()
    headers = ["id"] + [f"col_{j:03d}" for j in range(cols - 1)]
    buf.write(",".join(headers) + "\n")
    for i in range(rows):
        vals = [f"{prefix}{i:010d}"]
        for j in range(cols - 1):
            if j % 3 == 0:
                vals.append(f"val_{random.randint(0, rows // 2):06d}")
            elif j % 3 == 1:
                vals.append(str(random.randint(0, 1_000_000)))
            else:
                vals.append(f"2024-{random.randint(1,12):02d}-{random.randint(1,28):02d}")
        buf.write(",".join(vals) + "\n")
    return buf.getvalue().encode("utf-8")


def _single_user_workflow(
    base_url: str,
    rows: int,
    cols: int,
    user_id: int,
    use_async: bool = True,
) -> dict:
    """Simulate one user's complete workflow: upload → parse → compare → detail.

    Returns timing breakdown.
    """
    import requests

    s = requests.Session()
    timings = {}
    t_start = time.perf_counter()

    try:
        # 1. Create session
        r = s.get(f"{base_url}/api/files/new-session", timeout=5)
        sid = r.json()["session_id"]

        # 2. Upload file A
        csv_a = _build_test_csv(rows, cols, prefix=f"u{user_id}_a_")
        t0 = time.perf_counter()
        r = s.post(
            f"{base_url}/api/files/upload?session_id={sid}",
            files={"file": (f"user{user_id}_a.csv", io.BytesIO(csv_a), "text/csv")},
            timeout=300,
        )
        upload_resp = r.json()
        fid_a = upload_resp["file_id"]
        t1 = time.perf_counter()
        timings["upload_a_s"] = round(t1 - t0, 2)

        # 3. Poll until file A parsed
        polls = 0
        while True:
            r = s.get(f"{base_url}/api/files/parse-status/{sid}/{fid_a}", timeout=10)
            st = r.json()
            polls += 1
            if st["status"] == "complete":
                break
            if st["status"] == "error":
                raise RuntimeError(f"Parse error: {st.get('error')}")
            time.sleep(0.1)
        timings["parse_a_s"] = round(time.perf_counter() - t1, 2)
        timings["parse_a_polls"] = polls

        # 4. Upload file B
        csv_b = _build_test_csv(rows, cols, prefix=f"u{user_id}_b_")
        t2 = time.perf_counter()
        r = s.post(
            f"{base_url}/api/files/upload?session_id={sid}",
            files={"file": (f"user{user_id}_b.csv", io.BytesIO(csv_b), "text/csv")},
            timeout=300,
        )
        fid_b = r.json()["file_id"]
        t3 = time.perf_counter()
        timings["upload_b_s"] = round(t3 - t2, 2)

        # 5. Poll until file B parsed
        polls = 0
        while True:
            r = s.get(f"{base_url}/api/files/parse-status/{sid}/{fid_b}", timeout=10)
            st = r.json()
            polls += 1
            if st["status"] == "complete":
                break
            if st["status"] == "error":
                raise RuntimeError(f"Parse error: {st.get('error')}")
            time.sleep(0.1)
        timings["parse_b_s"] = round(time.perf_counter() - t3, 2)
        timings["parse_b_polls"] = polls

        # 6. Read columns from session
        r = s.get(f"{base_url}/api/files/session/{sid}", timeout=10)
        session_info = r.json()
        compare_cols = [
            c for f in session_info["files"]
            for c in f["columns"]
            if c != "id"
        ]
        # Deduplicate while preserving order
        seen = set()
        compare_cols_dedup = []
        for c in compare_cols:
            if c not in seen:
                seen.add(c)
                compare_cols_dedup.append(c)

        # 7. Set mappings
        s.post(f"{base_url}/api/compare/mapping", json={
            "session_id": sid,
            "mappings": [
                {"file_id": fid_a, "key_columns": ["id"]},
                {"file_id": fid_b, "key_columns": ["id"]},
            ],
        }, timeout=10)

        # 8. Execute comparison (async or sync)
        t4 = time.perf_counter()
        if use_async:
            r = s.post(f"{base_url}/api/compare/execute-async", json={
                "session_id": sid,
                "compare_columns": compare_cols_dedup,
                "mode": "all",
            }, timeout=10)
            task_id = r.json()["task_id"]

            # Poll until complete
            while True:
                r = s.get(f"{base_url}/api/compare/status/{task_id}", timeout=10)
                st = r.json()
                if st["status"] == "complete":
                    break
                if st["status"] == "error":
                    raise RuntimeError(f"Compare error: {st.get('error')}")
                time.sleep(0.1)
            compare_result = st["result"]
        else:
            r = s.post(f"{base_url}/api/compare/execute", json={
                "session_id": sid,
                "compare_columns": compare_cols_dedup,
                "mode": "all",
            }, timeout=600)
            compare_result = r.json()
        t5 = time.perf_counter()
        timings["compare_s"] = round(t5 - t4, 2)

        # 9. Query detail page
        r = s.post(f"{base_url}/api/compare/detail", json={
            "session_id": sid, "match_filter": "all", "page": 1, "page_size": 20,
        }, timeout=30)
        t6 = time.perf_counter()
        timings["detail_s"] = round(t6 - t5, 4)

        timings["total_s"] = round(time.perf_counter() - t_start, 2)
        timings["total_keys"] = compare_result.get("total_keys", 0)
        timings["matched"] = compare_result.get("matched", 0)
        timings["success"] = True

    except Exception as e:
        timings["success"] = False
        timings["error"] = str(e)[:120]
        timings["total_s"] = round(time.perf_counter() - t_start, 2)

    return timings


def run_concurrent_test(
    base_url: str = "http://127.0.0.1:8000",
    users: int = 5,
    rows: int = 5000,
    cols: int = 20,
    use_async: bool = True,
):
    """Run N simultaneous users through the full workflow.

    Key metrics:
      - Throughput: completed workflows / wall-clock time
      - Latency distribution: p50, p95, p99 for total workflow time
      - Success rate
    """
    import requests

    # Quick health check
    try:
        r = requests.get(f"{base_url}/health", timeout=5)
        if r.status_code != 200:
            print(f"❌ Backend not healthy: {r.status_code}")
            return
    except Exception as e:
        print(f"❌ Cannot reach backend at {base_url}: {e}")
        print("   Please start the backend first: cd excel-compare && docker compose up -d")
        return

    print(f"\n{'='*70}")
    print(f"🔥 并发压力测试: {users} 用户同时操作")
    print(f"   每用户数据: {rows:,} 行 × {cols} 列 × 2 表")
    print(f"   比对模式: {'async (异步)' if use_async else 'sync (同步)'}")
    print(f"{'='*70}")

    wall_start = time.perf_counter()

    # Launch all users in parallel
    results = []
    with ThreadPoolExecutor(max_workers=users, thread_name_prefix="bench-user") as pool:
        futures = {
            pool.submit(
                _single_user_workflow, base_url, rows, cols, i, use_async
            ): i
            for i in range(users)
        }
        for future in as_completed(futures):
            user_id = futures[future]
            try:
                result = future.result()
                result["user_id"] = user_id
                results.append(result)
                status = "✅" if result.get("success") else "❌"
                print(f"  [{status}] 用户 {user_id:2d}: "
                      f"总耗时 {result.get('total_s', 0):.1f}s, "
                      f"比对 {result.get('compare_s', 0):.1f}s, "
                      f"keys={result.get('total_keys', '?')}")
            except Exception as e:
                print(f"  [❌] 用户 {user_id:2d}: {e}")
                results.append({"user_id": user_id, "success": False, "error": str(e)[:120]})

    wall_elapsed = time.perf_counter() - wall_start

    # ── Statistics ──
    successful = [r for r in results if r.get("success")]
    failed = [r for r in results if not r.get("success")]

    print(f"\n{'─'*70}")
    print(f"📊 汇总统计")
    print(f"{'─'*70}")
    print(f"  总用户数:       {users}")
    print(f"  成功:           {len(successful)}")
    print(f"  失败:           {len(failed)}")
    print(f"  总墙钟时间:     {wall_elapsed:.1f}s")
    print(f"  吞吐量:         {len(successful) / wall_elapsed:.2f} workflows/s")

    if successful:
        totals = [r["total_s"] for r in successful]
        compares = [r["compare_s"] for r in successful]
        uploads = [r.get("upload_a_s", 0) + r.get("upload_b_s", 0) for r in successful]
        parses = [r.get("parse_a_s", 0) + r.get("parse_b_s", 0) for r in successful]

        totals.sort()
        compares.sort()

        def p(arr, n):
            idx = int(len(arr) * n / 100)
            return arr[min(idx, len(arr) - 1)]

        print(f"\n  端到端延迟 (含上传+解析+比对+查询):")
        print(f"    p50:  {p(totals, 50):.1f}s")
        print(f"    p95:  {p(totals, 95):.1f}s")
        print(f"    p99:  {p(totals, 99):.1f}s")
        print(f"    avg:  {statistics.mean(totals):.1f}s")
        print(f"    min:  {min(totals):.1f}s")
        print(f"    max:  {max(totals):.1f}s")

        print(f"\n  比对延迟:")
        print(f"    p50:  {p(compares, 50):.1f}s")
        print(f"    p95:  {p(compares, 95):.1f}s")
        print(f"    avg:  {statistics.mean(compares):.1f}s")

        print(f"\n  上传+解析延迟:")
        up_parse = [u + p for u, p in zip(uploads, parses)]
        up_parse.sort()
        print(f"    avg:  {statistics.mean(up_parse):.1f}s")

        # Concurrency efficiency: ideal vs actual
        if len(successful) >= 2 and compares:
            sum_compare = sum(compares)
            ideal = sum_compare / max(2, users)  # with 2 workers, best case
            actual_wall = wall_elapsed
            efficiency = (sum_compare / actual_wall) * 100 if actual_wall > 0 else 0
            print(f"\n  并发效率:")
            print(f"    比对总CPU时间: {sum_compare:.1f}s")
            print(f"    墙钟时间:      {actual_wall:.1f}s")
            print(f"    并行效率:      {efficiency:.0f}% (vs ideal {users}×)")

        print(f"\n  结论: ", end="")
        if len(failed) == 0 and p(totals, 95) < 30:
            print("✅ 并发性能良好，{users} 用户同时操作无异常")
        elif len(failed) <= users * 0.1:
            print(f"⚠️  部分用户失败 ({len(failed)}/{users})，需进一步排查")
        else:
            print(f"❌ 大量用户失败 ({len(failed)}/{users})，存在严重并发问题")

    if failed:
        print(f"\n  失败详情:")
        for f in failed:
            print(f"    用户 {f.get('user_id')}: {f.get('error', 'unknown')[:150]}")


def run_load_test(
    base_url: str = "http://127.0.0.1:8000",
    duration_s: int = 30,
    users: int = 5,
):
    """Sustained-load test: keep submitting compare requests for a fixed duration.

    Measures sustained throughput and error rate under continuous load.
    """
    import requests

    print(f"\n{'='*70}")
    print(f"⏱️  持续负载测试: {users} 用户, {duration_s}s")
    print(f"{'='*70}")

    # First, prepare shared sessions with uploaded files
    print("  准备测试数据...")
    sessions = []
    for i in range(users):
        s = requests.Session()
        r = s.get(f"{base_url}/api/files/new-session", timeout=5)
        sid = r.json()["session_id"]

        csv_a = _build_test_csv(3000, 15, prefix=f"load_a_{i}_")
        csv_b = _build_test_csv(3000, 15, prefix=f"load_b_{i}_")

        r = s.post(f"{base_url}/api/files/upload?session_id={sid}",
                   files={"file": (f"la_{i}.csv", io.BytesIO(csv_a), "text/csv")}, timeout=300)
        fid_a = r.json()["file_id"]

        # Wait for parse
        while True:
            st = s.get(f"{base_url}/api/files/parse-status/{sid}/{fid_a}", timeout=10).json()
            if st["status"] == "complete":
                break
            if st["status"] == "error":
                break
            time.sleep(0.1)

        r = s.post(f"{base_url}/api/files/upload?session_id={sid}",
                   files={"file": (f"lb_{i}.csv", io.BytesIO(csv_b), "text/csv")}, timeout=300)
        fid_b = r.json()["file_id"]

        while True:
            st = s.get(f"{base_url}/api/files/parse-status/{sid}/{fid_b}", timeout=10).json()
            if st["status"] == "complete":
                break
            if st["status"] == "error":
                break
            time.sleep(0.1)

        r = s.get(f"{base_url}/api/files/session/{sid}", timeout=10)
        cols = list(dict.fromkeys(
            c for f in r.json()["files"] for c in f["columns"] if c != "id"
        ))

        s.post(f"{base_url}/api/compare/mapping", json={
            "session_id": sid,
            "mappings": [
                {"file_id": fid_a, "key_columns": ["id"]},
                {"file_id": fid_b, "key_columns": ["id"]},
            ],
        }, timeout=10)

        sessions.append({"session": s, "sid": sid, "cols": cols})
        print(f"    用户 {i}: session={sid}, cols={len(cols)}")

    print(f"  准备完成，开始持续负载...")

    # Track metrics
    req_count = [0] * users
    err_count = [0] * users
    latencies: list[float] = []
    stop_event = threading.Event()

    def worker(user_idx: int):
        sdata = sessions[user_idx]
        s = sdata["session"]
        sid = sdata["sid"]
        cols = sdata["cols"]

        while not stop_event.is_set():
            t0 = time.perf_counter()
            try:
                r = s.post(f"{base_url}/api/compare/execute-async", json={
                    "session_id": sid,
                    "compare_columns": cols[:10],  # compare first 10 cols
                    "mode": "all",
                }, timeout=10)
                task_id = r.json()["task_id"]
                # Poll for completion
                while not stop_event.is_set():
                    r = s.get(f"{base_url}/api/compare/status/{task_id}", timeout=10)
                    st = r.json()
                    if st["status"] in ("complete", "error"):
                        break
                    time.sleep(0.2)
                latencies.append(time.perf_counter() - t0)
                req_count[user_idx] += 1
                if st["status"] == "error":
                    err_count[user_idx] += 1
            except Exception:
                err_count[user_idx] += 1
                req_count[user_idx] += 1
                latencies.append(time.perf_counter() - t0)

    threads = [threading.Thread(target=worker, args=(i,), daemon=True) for i in range(users)]
    for t in threads:
        t.start()

    time.sleep(duration_s)
    stop_event.set()
    for t in threads:
        t.join(timeout=5)

    total_req = sum(req_count)
    total_err = sum(err_count)
    print(f"\n  结果:")
    print(f"    总请求数:   {total_req}")
    print(f"    成功:       {total_req - total_err}")
    print(f"    失败:       {total_err}")
    print(f"    吞吐量:     {total_req / duration_s:.2f} req/s")

    if latencies:
        latencies.sort()
        def p(arr, n):
            return arr[min(int(len(arr) * n / 100), len(arr) - 1)]
        print(f"    延迟 p50:   {p(latencies, 50):.1f}s")
        print(f"    延迟 p95:   {p(latencies, 95):.1f}s")
        print(f"    延迟 p99:   {p(latencies, 99):.1f}s")
        print(f"    延迟 avg:   {statistics.mean(latencies):.1f}s")


def run_scale_test():
    """递增压力测试，找出性能拐点。"""
    configs = [
        (1000, 5),
        (10000, 10),
        (50000, 15),
        (100000, 20),
        (500000, 30),
        (1000000, 30),
    ]

    print("=" * 60)
    print("📈 递增压力测试")
    print("=" * 60)
    print(f"{'行数':>10} {'列数':>5} {'耗时(s)':>10} {'行/秒':>10} {'状态':>8}")
    print("-" * 60)

    for rows, cols in configs:
        bm = Benchmark(rows, cols, overlap=0.8)
        try:
            result = bm.engine_only()
            status = "✅"
        except Exception as e:
            result = {"elapsed_s": 0, "rows_per_sec": 0}
            status = f"❌{str(e)[:20]}"
            if rows >= 100000:
                print(f"{rows:>10,} {cols:>5} {'N/A':>10} {'N/A':>10} {status}")
                break
        print(f"{result['rows']:>10,} {result['cols']:>5} "
              f"{result.get('elapsed_s', 0):>10.2f} "
              f"{result.get('rows_per_sec', 0):>10,.0f} {status}")


def main():
    parser = argparse.ArgumentParser(description="多表比对工具压力测试")
    parser.add_argument("--rows", type=int, default=10000, help="行数")
    parser.add_argument("--cols", type=int, default=10, help="列数")
    parser.add_argument("--overlap", type=float, default=0.7, help="两表重叠比例 (0-1)")
    parser.add_argument("--engine-only", action="store_true", help="仅测试引擎（不启动后端）")
    parser.add_argument("--api", action="store_true", help="通过 API 测试（需先启动后端）")
    parser.add_argument("--api-url", default="http://127.0.0.1:8000", help="后端地址")
    parser.add_argument("--scale", action="store_true", help="递增压力测试")
    parser.add_argument("--memory", action="store_true", help="测试后打印内存占用")

    # Concurrency tests
    parser.add_argument("--concurrent", type=int, default=0, metavar="N",
                        help="并发测试: N 个用户同时执行完整工作流")
    parser.add_argument("--sync", action="store_true",
                        help="并发测试使用同步比对 (默认异步)")
    parser.add_argument("--load", type=int, default=0, metavar="SECONDS",
                        help="持续负载测试: 指定持续时间（秒）")
    parser.add_argument("--load-users", type=int, default=5,
                        help="持续负载测试的并发用户数")

    args = parser.parse_args()

    # ── Concurrency tests (priority — skip other modes) ──
    if args.concurrent > 0:
        run_concurrent_test(
            base_url=args.api_url,
            users=args.concurrent,
            rows=args.rows,
            cols=args.cols,
            use_async=not args.sync,
        )
        return

    if args.load > 0:
        run_load_test(
            base_url=args.api_url,
            duration_s=args.load,
            users=args.load_users,
        )
        return

    if args.scale:
        run_scale_test()
        return

    bm = Benchmark(args.rows, args.cols, args.overlap)

    if args.engine_only or not args.api:
        bm.engine_only()

    if args.api:
        bm.api_test(args.api_url)

    if args.memory:
        try:
            import psutil
            proc = psutil.Process(os.getpid())
            mem = proc.memory_info()
            print(f"\n💾 当前进程内存: RSS={mem.rss/1024/1024:.0f}MB, VMS={mem.vms/1024/1024:.0f}MB")
        except ImportError:
            print("  (安装 psutil 可查看内存占用: pip install psutil)")


if __name__ == "__main__":
    main()

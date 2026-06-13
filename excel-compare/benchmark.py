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

    args = parser.parse_args()

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

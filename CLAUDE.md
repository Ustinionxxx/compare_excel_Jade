# CLAUDE.md — 智能多表比对分析工具 (Excel Compare)

This is a zero-code, visual multi-Excel sheet comparison and diff analysis platform. The primary developer is Ustinionxxx; this file provides context for Claude when assisting with code in this repo.

## Project Overview

Users upload 2–3 Excel (`.xlsx`/`.xls`) or CSV files, select primary-key columns for record matching, and the system performs an outer-join comparison across all columns. Results are displayed as a statistics dashboard with drill-down detail and exportable Excel reports.

**Live deployment:** Server-side Docker Compose, code hosted in a git repo. Deployed via `docker compose up -d`.

## Tech Stack

| Layer | Technology |
|---|---|
| Frontend | React 18 + TypeScript + Vite 5 + Ant Design 5 + AG Grid Community 31 |
| Backend | Python 3.10+ / FastAPI + pandas + openpyxl + xlrd |
| Async Server | Gunicorn + Uvicorn workers (multi-process, default `WORKERS=2`) |
| Containerization | Docker (multi-stage Node build → Nginx for frontend, Python 3.12-slim for backend) |
| Storage | Filesystem only — pickle for sessions, JSON for async task progress, temp dirs for uploads. **No database.** |

## Project Structure

```
excel-compare/
├── docker-compose.yml
├── benchmark.py                   # Standalone performance/stress test
├── backend/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── test_comprehensive.py      # Full test suite
│   └── app/
│       ├── main.py                # FastAPI entry point, CORS, router registration
│       ├── models.py              # Pydantic request/response models
│       ├── routers/
│       │   ├── files.py           # Upload, parse-status polling, session mgmt, aliases
│       │   ├── compare.py         # Key mapping, sync/async compare, detail queries, task status
│       │   └── export.py          # Excel export (in-memory or streamed for large files)
│       └── services/
│           ├── parser.py          # Excel/CSV parsing (calamine engine preferred, fallback to openpyxl/xlrd)
│           ├── matcher.py         # Core DiffEngine: outer-join merge, vectorized diff, column stats
│           └── exporter.py        # Excel file generation (xlsxwriter preferred, fallback to openpyxl)
├── frontend/
│   ├── Dockerfile                 # Multi-stage: Node 20 build → Nginx serve
│   ├── nginx.conf                 # Reverse proxy /api → backend:8000, 100MB upload limit
│   ├── vite.config.ts             # Dev proxy /api → 127.0.0.1:8000
│   └── src/
│       ├── App.tsx                # Step-based wizard state machine
│       ├── api/index.ts           # Axios client with upload progress, parse polling, async compare
│       ├── types/index.ts         # TypeScript interfaces matching backend Pydantic models
│       └── components/
│           ├── FileUpload/        # Ant Design Dragger + streaming upload + background parse polling
│           ├── KeyMapping/        # Multi-column primary key selection per file
│           ├── ColumnSelect/      # Compare column selection with 3-way mode options
│           ├── StatsBoard/        # Summary stats cards + per-column diff table
│           ├── DiffTable/         # Paginated detail view with match/diff filters
│           └── ExportBar/         # Export dropdown (filtered wide, diff detail long, raw source)
```

## How to Build / Run / Test

### Local Development

```bash
# Backend (terminal 1)
cd excel-compare/backend
pip install -r requirements.txt
python -m uvicorn app.main:app --reload --port 8000

# Frontend (terminal 2)
cd excel-compare/frontend
npm install
npm run dev
# → http://localhost:5173
```

### Docker (matches production)

```bash
cd excel-compare
docker compose up -d
# → http://localhost:5173
# WORKERS env var controls gunicorn worker count (default 2)
```

### Testing

All tests are in `backend/test_comprehensive.py`. Run from the `backend/` directory:

```bash
python test_comprehensive.py --unit              # Engine unit tests only
python test_comprehensive.py --integration       # Requires running backend
python test_comprehensive.py --concurrency 5     # Concurrency test (5 clients, 500 rows each)
python test_comprehensive.py --cross-worker      # Simulates multi-worker race conditions
python test_comprehensive.py --stress            # Engine stress test (no API)
python test_comprehensive.py --load 30           # API load test (30 concurrent)
python test_comprehensive.py --quick             # Fast sanity check
python test_comprehensive.py                     # All tests
```

Benchmark: `python benchmark.py --engine-only --rows 50000 --cols 30`

## Architecture & Key Patterns

### Session-Based 4-Step Wizard

1. **Upload** → Files streamed to disk in 64KB chunks (max 100MB each). Parsing runs in a background daemon thread; frontend polls `/api/files/parse-status/{session_id}/{file_id}` with graduated intervals (800ms → 3s).
2. **Key Mapping** → User selects composite primary keys per file. Auto-suggests first common column name.
3. **Column Select** → Choose columns to diff. For 3-file scenarios, select compare mode (AB, AC, BC, all).
4. **Results** → Stats dashboard + paginated diff detail table + Excel export.

### Multi-Worker Architecture (CRITICAL)

The backend runs under **Gunicorn with multiple Uvicorn workers**. This is the single most important architectural constraint:

- **No shared memory between workers.** `threading.Lock` protects within a process but does NOT protect across processes.
- **Sessions are persisted to disk** (`uploads/sessions/*.pickle`) as the source of truth. The `get_session()` function checks BOTH `mtime` AND file size to detect cross-worker updates.
- **Async compare tasks** use a dedicated thread pool (max 2 concurrent) with progress tracked via JSON files on disk (`uploads/compare_tasks/*.json`), so any worker can poll any task.
- **Session cleanup** runs on a background timer (30 min TTL), checking both in-memory timestamps and disk file mtime to avoid races.
- **Always call `persist_session()` after modifying session state** — otherwise other workers will see stale data.

### Core Diff Engine (`services/matcher.py`)

- `DiffEngine.compute()` uses **pandas vectorized operations exclusively** (no `iterrows()`) — ~50-100x faster than row-wise loops.
- Performs an outer-join merge on the composite primary key, then compares each common column element-wise.
- Column statistics are computed in one pass; paginated detail queries slice from the merged DataFrame.

### CSV Auto-Detection (`services/parser.py`)

- Tries multiple encodings: utf-8, gbk, gb2312, gb18030, latin-1, etc.
- Tries multiple delimiters: comma, tab, semicolon, pipe.
- Uses a scoring algorithm to pick the best combination.

### Optional Performance Dependencies

- `python_calamine` — Rust-based Excel reader, 5-10x faster than openpyxl. Falls back gracefully.
- `xlsxwriter` — Faster Excel writer. Falls back to openpyxl.

## Code Conventions

- **Language:** User-facing text (API error messages, UI labels, README, comments explaining business logic) is in **Chinese**. Code symbols (variable names, function names, class names, docstrings) are in **English**.
- **Backend naming:** `snake_case` for Python; `camelCase` for TypeScript/React.
- **File upload limit:** 100MB, enforced at three levels (Nginx `client_max_body_size`, FastAPI explicit check, React frontend validation).
- **Session IDs:** 16-char hex; file IDs: 12-char hex.
- **Error handling:** Backend catches by type (`ValueError` → user error, `Exception` → internal error), wraps in `HTTPException` with Chinese `detail`. Frontend reads `e.response?.data?.detail`.
- **Development workflow:** Implement first, test after. Tests are comprehensive integration/regression tests, not TDD unit tests.

## Known Pitfalls

1. **Multi-worker data loss** — The most common bug class. If you modify a session object and forget to call `persist_session()`, other workers won't see the change, leading to "file not found" or stale data errors. The recent fix (commit `f2c34b9`) added size-based staleness detection as an extra safeguard.
2. **No database** — All state is on the filesystem. High-concurrency deployments should consider Redis; the README explicitly notes this limitation.
3. **Optional native deps** — `python_calamine` and `xlsxwriter` are not in `requirements.txt`. Install them separately for performance: `pip install python_calamine xlsxwriter`.
4. **Docker registry mirrors** — The docker-compose.yml comments reference Chinese mirror URLs. If deploying outside China, ignore those comments.
5. **npm registry** — Frontend `.npmrc` points to `npmmirror.com`. Remove or change this if building outside China.
6. **Uploads volume** — Docker mounts `uploads` as a named volume. Backups/cleanup must account for this.

## API Quick Reference

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/api/files/new-session` | Create session |
| POST | `/api/files/upload` | Upload file (returns immediately, parses in background) |
| GET | `/api/files/parse-status/{session_id}/{file_id}` | Poll parse progress |
| POST | `/api/files/remove` | Remove uploaded file |
| POST | `/api/files/alias` | Set file display alias |
| GET | `/api/files/session/{id}` | Get full session state |
| POST | `/api/compare/mapping` | Set primary key column mappings |
| POST | `/api/compare/execute` | Sync compare (legacy, small files only) |
| POST | `/api/compare/execute-async` | Async compare (recommended for all files) |
| GET | `/api/compare/status/{task_id}` | Poll async compare progress |
| POST | `/api/compare/detail` | Paginated detail query with match/diff filters |
| POST | `/api/export/excel` | Export results as Excel |

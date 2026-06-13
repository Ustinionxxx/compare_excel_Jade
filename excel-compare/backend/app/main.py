"""FastAPI main entry point."""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers import files, compare, export

app = FastAPI(
    title="智能多表比对分析工具",
    description="Excel 多表比对与差异分析平台 API",
    version="1.0.0",
)

# CORS — allow frontend dev server
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register routers
app.include_router(files.router)
app.include_router(compare.router)
app.include_router(export.router)


@app.get("/")
def root():
    return {"service": "excel-compare-api", "version": "1.0.0"}


@app.get("/health")
def health():
    return {"status": "ok"}

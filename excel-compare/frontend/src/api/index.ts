/**
 * API client for the Excel Compare backend.
 */
import axios from 'axios';
import type {
  UploadResponse,
  ColumnMapping,
  CompareResponse,
  CompareSubmitResponse,
  CompareStatusResponse,
  DetailQueryResponse,
  SessionInfo,
} from '../types';

const http = axios.create({
  baseURL: '/api',
  timeout: 600_000, // 10 min default (for large 3-way comparisons)
});

// ── Session ──

export async function newSession(): Promise<string> {
  const res = await http.get('/files/new-session');
  return res.data.session_id;
}

// ── Files ──

export async function uploadFile(
  file: File,
  sessionId?: string,
  onProgress?: (percent: number) => void,
): Promise<UploadResponse> {
  const form = new FormData();
  form.append('file', file);
  const params: Record<string, string> = {};
  if (sessionId) params.session_id = sessionId;
  const res = await http.post('/files/upload', form, {
    params,
    timeout: 0, // no timeout — large files can take many minutes to upload
    onUploadProgress: onProgress ? (e) => {
      if (e.total) onProgress(Math.round((e.loaded / e.total) * 100));
    } : undefined,
  });
  return res.data;
}

export async function removeFile(sessionId: string, fileId: string) {
  await http.post('/files/remove', { session_id: sessionId, file_id: fileId });
}

export interface ParseStatusResponse {
  file_id: string;
  status: 'parsing' | 'complete' | 'error';
  stage: string;
  progress: number;
  message: string;
  // Present when complete (via result or already-registered file)
  file_name?: string;
  sheet_names?: string[];
  columns?: string[];
  preview_rows?: Record<string, unknown>[];
  total_rows?: number;
  // Present on error
  error?: string;
}

export async function getParseStatus(
  sessionId: string,
  fileId: string,
): Promise<ParseStatusResponse> {
  const res = await http.get(`/files/parse-status/${sessionId}/${fileId}`);
  return res.data;
}

export async function getSessionInfo(sessionId: string): Promise<SessionInfo> {
  const res = await http.get(`/files/session/${sessionId}`);
  return res.data;
}

export async function setFileAlias(
  sessionId: string,
  fileId: string,
  alias: string,
): Promise<void> {
  await http.post('/files/alias', {
    session_id: sessionId,
    file_id: fileId,
    alias,
  });
}

// ── Compare ──

export async function setColumnMapping(
  sessionId: string,
  mappings: ColumnMapping[],
) {
  await http.post('/compare/mapping', { session_id: sessionId, mappings });
}

export async function executeCompare(
  sessionId: string,
  compareColumns: string[],
  mode: string = 'all',
): Promise<CompareResponse> {
  const res = await http.post('/compare/execute', {
    session_id: sessionId,
    compare_columns: compareColumns,
    mode,
  });
  return res.data;
}

// ── Async Compare (recommended for large files / multi-user scenarios) ──

export async function executeCompareAsync(
  sessionId: string,
  compareColumns: string[],
  mode: string = 'all',
): Promise<CompareSubmitResponse> {
  const res = await http.post('/compare/execute-async', {
    session_id: sessionId,
    compare_columns: compareColumns,
    mode,
  });
  return res.data;
}

export async function getCompareStatus(
  taskId: string,
): Promise<CompareStatusResponse> {
  const res = await http.get(`/compare/status/${taskId}`);
  return res.data;
}

export async function queryDetail(
  sessionId: string,
  params: {
    match_filter?: string;
    diff_filter?: string;
    diff_column?: string | null;
    page?: number;
    page_size?: number;
    sort_column?: string | null;
    sort_order?: string | null;
  },
): Promise<DetailQueryResponse> {
  const res = await http.post('/compare/detail', {
    session_id: sessionId,
    match_filter: params.match_filter ?? 'all',
    diff_filter: params.diff_filter ?? 'all',
    diff_column: params.diff_column ?? null,
    page: params.page ?? 1,
    page_size: params.page_size ?? 50,
    sort_column: params.sort_column ?? null,
    sort_order: params.sort_order ?? null,
  });
  return res.data;
}

// ── Export ──

export async function exportExcel(
  sessionId: string,
  params: {
    match_filter?: string;
    diff_filter?: string;
    diff_column?: string | null;
    export_type?: string;
    file_ids?: string[];
    include_diff_columns?: boolean;
    file_aliases?: Record<string, string>;
  },
): Promise<Blob> {
  const res = await http.post('/export/excel', {
    session_id: sessionId,
    match_filter: params.match_filter ?? 'all',
    diff_filter: params.diff_filter ?? 'all',
    diff_column: params.diff_column ?? null,
    export_type: params.export_type ?? 'filtered',
    file_ids: params.file_ids ?? null,
    include_diff_columns: params.include_diff_columns ?? false,
    file_aliases: params.file_aliases ?? null,
  }, { responseType: 'blob' });
  return res.data;
}

export function downloadBlob(blob: Blob, filename: string) {
  const url = window.URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  window.URL.revokeObjectURL(url);
}

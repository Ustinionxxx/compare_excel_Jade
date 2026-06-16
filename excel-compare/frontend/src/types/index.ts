/** Shared TypeScript types matching backend models. */

export interface FileInfo {
  file_id: string;
  file_name: string;
  alias?: string;
  columns: string[];
  sheet: string;
}

export interface UploadResponse {
  session_id: string;
  file_id: string;
  file_name: string;
  status: 'parsing' | 'complete';
  // Only populated when complete:
  sheet_names?: string[];
  columns?: string[];
  preview_rows?: Record<string, unknown>[];
  total_rows?: number;
}

export interface ColumnMapping {
  file_id: string;
  key_columns: string[];
}

export interface ColumnStat {
  column: string;
  value_match: number;
  t1_diff: number;
  t2_diff: number;
  t3_diff: number;
  all_diff: number;
}

export interface CompareResponse {
  session_id: string;
  total_keys: number;
  matched: number;
  only_a: number;
  only_b: number;
  only_c: number | null;
  column_stats: ColumnStat[];
  /** "only_a" → "仅 采购单 存在", "only_b" → "仅 销售单 存在", ... */
  match_type_labels: Record<string, string>;
}

export type MatchType = 'matched' | 'only_a' | 'only_b' | 'only_c'
  | 'only_a_b' | 'only_a_c' | 'only_b_c';
export type DiffFilterValue = 'all' | 'same' | 'different'
  | 'value_match' | 't1_diff' | 't2_diff' | 't3_diff' | 'all_diff';

export interface DetailRow {
  row_id: string;
  match_type: string;
  key_values: Record<string, string>;
  data: Record<string, unknown>;
  diff_columns: string[];
}

export interface DetailQueryResponse {
  total: number;
  page: number;
  page_size: number;
  rows: DetailRow[];
}

export interface SessionInfo {
  session_id: string;
  files: (FileInfo & { alias?: string })[];
  key_mappings: Record<string, string[]>;
}

// ── Async Compare ──

export interface CompareSubmitResponse {
  task_id: string;
  session_id: string;
  status: string;
}

export interface CompareStatusResponse {
  task_id: string;
  status: string;        // "started" | "merging" | "complete" | "error"
  stage: string;
  progress: number;       // 0-100
  message: string;
  result?: CompareResponse;  // populated when status === "complete"
  error?: string;            // populated when status === "error"
}

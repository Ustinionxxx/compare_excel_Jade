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
  same: number;
  diff: number;
}

export interface CompareResponse {
  session_id: string;
  total_keys: number;
  matched: number;
  only_a: number;
  only_b: number;
  only_c: number | null;
  column_stats: ColumnStat[];
}

export type MatchType = 'matched' | 'only_a' | 'only_b' | 'only_c';
export type DiffType = 'same' | 'different';

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

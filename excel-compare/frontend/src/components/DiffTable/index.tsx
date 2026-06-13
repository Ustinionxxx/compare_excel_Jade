import { useState, useEffect, useCallback } from 'react';
import {
  Card, Select, Space, Typography, Tag, Badge, Pagination, Spin,
} from 'antd';
import type { CompareResponse, DetailRow, DetailQueryResponse } from '../../types';
import { queryDetail } from '../../api';

const { Title, Text } = Typography;

interface Props {
  sessionId: string;
  compareResult: CompareResponse;
  compareColumns: string[];
  // Controlled filter state (lifted to App for ExportBar sync)
  matchFilter: string;
  diffFilter: string;
  diffColumn: string | null;
  onFilterChange: (filters: {
    match_filter?: string;
    diff_filter?: string;
    diff_column?: string | null;
  }) => void;
  dismissedColumns?: string[];
  /** 文件别名映射: label (A/B/C) -> alias name */
  labelAliases?: Record<string, string>;
  /** 匹配类型展示文案: "only_a" → "仅 采购单 存在", ... */
  matchTypeLabels: Record<string, string>;
}

const DIFF_FILTERS = [
  { label: '全部', value: 'all' },
  { label: '值相同', value: 'same' },
  { label: '值不同', value: 'different' },
];

/** Build match filter options from dynamic labels (fallback to 仅A/仅B/仅C) */
function buildMatchFilters(labels: Record<string, string>) {
  const filters = [
    { label: '全部', value: 'all' },
    { label: labels.matched || '匹配成功', value: 'matched' },
  ];
  if (labels.only_a) filters.push({ label: labels.only_a, value: 'only_a' });
  if (labels.only_b) filters.push({ label: labels.only_b, value: 'only_b' });
  if (labels.only_c) filters.push({ label: labels.only_c, value: 'only_c' });
  return filters;
}

export default function DiffTable({
  sessionId, compareColumns, compareResult,
  matchFilter, diffFilter, diffColumn, onFilterChange,
  dismissedColumns = [],
  labelAliases,
  matchTypeLabels,
}: Props) {
  const [loading, setLoading] = useState(false);
  const [data, setData] = useState<DetailQueryResponse>({ total: 0, page: 1, page_size: 50, rows: [] });
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);

  // Filter out dismissed columns
  const activeCompareColumns = compareColumns.filter(c => !dismissedColumns.includes(c));

  // Focused view: when filtering "matched" + "different",
  // only show match type + key columns + compare column A/B values
  const isFocused = matchFilter === 'matched' && diffFilter === 'different';
  const focusedCols = diffColumn
    ? (dismissedColumns.includes(diffColumn) ? [] : [diffColumn])
    : activeCompareColumns;

  // Determine table labels from data (2-way A/B, 3-way A/B/C)
  const tableLabels: string[] = [];
  if (data.rows.length > 0) {
    const keys = Object.keys(data.rows[0].data);
    const prefixes = new Set<string>();
    for (const k of keys) {
      if (!k.startsWith('__diff_') && k.length >= 2 && k[1] === '_') {
        prefixes.add(k[0]);
      }
    }
    for (const p of ['A', 'B', 'C']) {
      if (prefixes.has(p)) tableLabels.push(p);
    }
  }
  if (tableLabels.length === 0) {
    tableLabels.push('A', 'B');
    if (compareResult.only_c !== null) tableLabels.push('C');
  }

  const fetchData = useCallback(async () => {
    setLoading(true);
    try {
      const result = await queryDetail(sessionId, {
        match_filter: matchFilter,
        diff_filter: diffFilter,
        diff_column: diffColumn,
        page,
        page_size: pageSize,
      });
      setData(result);
    } catch (e) {
      console.error('Failed to fetch detail', e);
    } finally {
      setLoading(false);
    }
  }, [sessionId, matchFilter, diffFilter, diffColumn, page, pageSize]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  // Reset to page 1 when filters change
  useEffect(() => {
    setPage(1);
  }, [matchFilter, diffFilter, diffColumn]);

  // Determine which table prefix to filter for (only_a → "A", only_b → "B", etc.)
  const activeTablePrefix = matchFilter === 'only_a' ? 'A'
    : matchFilter === 'only_b' ? 'B'
    : matchFilter === 'only_c' ? 'C'
    : null;

  // Filter data keys to only the active table's columns (only_a/b/c) or all
  const filterDataKeys = (keys: string[]) => {
    if (!activeTablePrefix) return keys;
    return keys.filter(k => k.startsWith(activeTablePrefix));
  };

  /** Map a data key like "A_Name" to use alias prefix like "采购单_Name" */
  const aliasDataKey = (key: string) => {
    if (key.length >= 2 && key[1] === '_' && labelAliases) {
      const prefix = key.charAt(0);
      const alias = labelAliases[prefix];
      if (alias) return `${alias}_${key.substring(2)}`;
    }
    return key;
  };

  // Build header info from first row
  const header = data.rows.length > 0 ? (() => {
    const firstRow = data.rows[0];
    const keyCols = Object.keys(firstRow.key_values || {});
    const keyColSet = new Set(keyCols);
    const dataKeys = filterDataKeys(Object.keys(firstRow.data).filter(k => !k.startsWith('__diff_')))
      // 需求 3：匹配成功时主键不重复 — 过滤掉已在 keyCols 中显示的列
      .filter(k => {
        if (k.length >= 2 && k[1] === '_') {
          const colName = k.substring(2);
          if (keyColSet.has(colName)) return false;
        }
        return true;
      });
    const tablePrefixes = [...new Set(
      dataKeys
        .map(k => k.charAt(0))
        .filter(k => k >= 'A' && k <= 'C')
    )].sort();
    return { keyCols, tablePrefixes, dataKeys };
  })() : null;

  // Match type helpers
  const matchTypeColor = (mt: string) => {
    switch (mt) {
      case 'matched': return 'success';
      case 'only_a': return 'warning';
      case 'only_b': return 'warning';
      case 'only_c': return 'warning';
      default: return 'default';
    }
  };

  const matchTypeLabel = (mt: string) => matchTypeLabels[mt] || mt;

  const thStyle: React.CSSProperties = {
    padding: '8px 12px',
    textAlign: 'left',
    borderBottom: '2px solid #f0f0f0',
    whiteSpace: 'nowrap',
    fontWeight: 600,
    fontSize: 12,
    color: '#666',
  };

  const tdStyle: React.CSSProperties = {
    padding: '6px 12px',
    whiteSpace: 'nowrap',
    maxWidth: 250,
    overflow: 'hidden',
    textOverflow: 'ellipsis',
  };

  return (
    <Card
      title={
        <Space>
          <Title level={5} style={{ margin: 0 }}>明细数据</Title>
          <Badge count={data.total} showZero color="#1677ff" />
        </Space>
      }
      size="small"
      extra={
        <Space wrap>
          <Select
            size="small"
            value={matchFilter}
            onChange={v => onFilterChange({ match_filter: v })}
            options={buildMatchFilters(matchTypeLabels)}
            style={{ width: 130 }}
          />
          <Select
            size="small"
            value={diffFilter}
            onChange={v => onFilterChange({ diff_filter: v })}
            options={DIFF_FILTERS}
            style={{ width: 110 }}
          />
          {diffFilter === 'different' && (
            <Select
              size="small"
              value={diffColumn || undefined}
              onChange={v => onFilterChange({ diff_column: v || null })}
              placeholder="按列筛选"
              options={activeCompareColumns.map(c => ({ label: c, value: c }))}
              style={{ width: 140 }}
              allowClear
            />
          )}
        </Space>
      }
    >
      <Spin spinning={loading}>
        {data.rows.length === 0 ? (
          <div style={{ textAlign: 'center', padding: '40px 0', color: '#999' }}>
            <Text type="secondary">暂无匹配数据</Text>
          </div>
        ) : (
          <>
            <div style={{ overflowX: 'auto', marginBottom: 12 }}>
              <table style={{
                width: '100%', borderCollapse: 'collapse', fontSize: 13,
                border: '1px solid #f0f0f0',
              }}>
                <thead>
                  {isFocused ? (
                    /* ── Focused view header (matched + different) ── */
                    <tr style={{ background: '#fafafa' }}>
                      <th style={thStyle}>匹配类型</th>
                      {header?.keyCols.map(kc => (
                        <th key={kc} style={thStyle}>{kc}（主键）</th>
                      ))}
                      {focusedCols.map(col => (
                        tableLabels.map(label => {
                          const alias = (labelAliases && labelAliases[label]) || label;
                          return (
                            <th key={`${label}_${col}`} style={{
                              ...thStyle,
                              background: '#f6f8ff',
                            }}>
                              {col}_{alias}
                            </th>
                          );
                        })
                      ))}
                    </tr>
                  ) : (
                    /* ── Normal view header (filtered by match type) ── */
                    <tr style={{ background: '#fafafa' }}>
                      <th style={thStyle}>匹配类型</th>
                      {header?.keyCols.map(kc => (
                        <th key={kc} style={thStyle}>{kc}（主键）</th>
                      ))}
                      {header?.dataKeys.map(k => (
                        <th key={k} style={thStyle}>{aliasDataKey(k)}</th>
                      ))}
                    </tr>
                  )}
                </thead>
                <tbody>
                  {data.rows.map((row) => {
                    const diffCols = new Set(row.diff_columns);
                    return (
                      <tr key={row.row_id} style={{ borderBottom: '1px solid #f0f0f0' }}>
                        <td style={tdStyle}>
                          <Tag color={matchTypeColor(row.match_type)}>
                            {matchTypeLabel(row.match_type)}
                          </Tag>
                        </td>
                        {header?.keyCols.map(kc => (
                          <td key={kc} style={{ ...tdStyle, fontWeight: 500 }}>
                            {row.key_values[kc] || ''}
                          </td>
                        ))}
                        {isFocused ? (
                          /* ── Focused view cells ── */
                          focusedCols.map(col =>
                            tableLabels.map(label => {
                              const val = row.data[`${label}_${col}`];
                              const isDiff = diffCols.has(col);
                              return (
                                <td key={`${label}_${col}`} style={{
                                  ...tdStyle,
                                  backgroundColor: isDiff ? '#ffccc7' : '#f6ffed',
                                }}>
                                  {val != null ? String(val) : ''}
                                </td>
                              );
                            })
                          )
                        ) : (
                          /* ── Normal view cells (filtered by match type) ── */
                          header?.dataKeys.map(k => {
                            const v = row.data[k];
                            const colName = k.length >= 2 && k[1] === '_' ? k.substring(2) : k;
                            const isDiff = diffCols.has(colName);
                            return (
                              <td key={k} style={{
                                ...tdStyle,
                                backgroundColor: isDiff ? '#ffccc7' : undefined,
                              }}>
                                {v != null ? String(v) : ''}
                              </td>
                            );
                          })
                        )}
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>

            {isFocused && (
              <div style={{ fontSize: 12, color: '#999', marginBottom: 8, padding: '0 4px' }}>
                <Text type="secondary">
                  聚焦模式：仅展示匹配成功且值不同的对比列
                  {focusedCols.length > 0
                    ? `（${focusedCols.join(', ')}${tableLabels.map(l => ` ${l}表值`)}）`
                    : '（无有效对比列，请在逐列对比结果中恢复已剔除的列）'
                  }
                  {dismissedColumns.length > 0 && ` | 已剔除 ${dismissedColumns.length} 列`}
                </Text>
              </div>
            )}

            <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
              <Pagination
                current={data.page}
                total={data.total}
                pageSize={data.page_size}
                showSizeChanger
                showTotal={(total) => `共 ${total} 条`}
                onChange={(p, ps) => { setPage(p); setPageSize(ps); }}
              />
            </div>
          </>
        )}
      </Spin>
    </Card>
  );
}
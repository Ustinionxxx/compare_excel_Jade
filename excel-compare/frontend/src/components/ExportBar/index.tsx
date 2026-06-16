import { useState } from 'react';
import { Dropdown, Button, Space, message, Switch, Typography } from 'antd';
import { DownloadOutlined, FileExcelOutlined, TableOutlined } from '@ant-design/icons';
import type { MenuProps } from 'antd';
import { exportExcel, downloadBlob } from '../../api';
import type { CompareResponse, FileInfo } from '../../types';

const { Text } = Typography;

interface Props {
  sessionId: string;
  compareResult: CompareResponse;
  files: FileInfo[];
  matchFilter: string;
  diffFilter: string;
  diffColumn: string | null;
  matchTypeLabels: Record<string, string>;
}

/** Diff category display labels for export filenames / filter display */
const DIFF_LABEL_MAP: Record<string, string> = {
  different: '值不同',
  same: '值相同',
  value_match: '三表一致',
  t1_diff: 'A表独异',
  t2_diff: 'B表独异',
  t3_diff: 'C表独异',
  all_diff: '三方互异',
};

/** Build a human-readable label for the current filter combination */
function filterLabel(
  matchFilter: string,
  diffFilter: string,
  diffColumn: string | null,
  matchTypeLabels: Record<string, string>,
): string {
  const parts: string[] = [];
  if (matchFilter !== 'all') {
    parts.push(matchTypeLabels[matchFilter] || matchFilter);
  }
  if (diffFilter !== 'all') {
    parts.push(DIFF_LABEL_MAP[diffFilter] || diffFilter);
  }
  if (diffColumn) {
    parts.push(`列:${diffColumn}`);
  }
  return parts.length > 0 ? `（${parts.join(' · ')}）` : '';
}

export default function ExportBar({ sessionId, files, matchFilter, diffFilter, diffColumn, matchTypeLabels }: Props) {
  const [exporting, setExporting] = useState(false);
  const [includeDiffCols, setIncludeDiffCols] = useState(false);

  // Build file_aliases from files — only include user-set aliases.
  // When alias is unset, backend uses "A表"/"B表" (diff_detail) or filename stem (wide).
  const fileAliases = Object.fromEntries(
    files.filter(f => f.alias).map(f => [f.file_id, f.alias as string]),
  );

  const doExport = async (exportType: string, filename: string) => {
    setExporting(true);
    try {
      const blob = await exportExcel(sessionId, {
        match_filter: matchFilter,
        diff_filter: diffFilter,
        diff_column: diffColumn,
        export_type: exportType,
        include_diff_columns: includeDiffCols,
        file_aliases: fileAliases,
      });
      downloadBlob(blob, filename);
      message.success('导出成功');
    } catch (e: any) {
      const errMsg = e.response?.data?.detail || e.message;
      if (errMsg.includes('没有可导出的数据')) {
        message.warning('当前筛选条件下没有数据，无法导出。请调整筛选条件。');
      } else {
        message.error('导出失败: ' + errMsg);
      }
    } finally {
      setExporting(false);
    }
  };

  const filteredFilename = () => {
    const parts = ['筛选结果'];
    if (matchFilter !== 'all') {
      parts.push(matchTypeLabels[matchFilter] || matchFilter);
    }
    if (diffFilter !== 'all') {
      parts.push(DIFF_LABEL_MAP[diffFilter] || diffFilter);
    }
    if (diffColumn) parts.push(diffColumn);
    return `${parts.join('_')}_${Date.now()}.xlsx`;
  };

  // 差异列导出开关 — 仅非 only_a/b/c 及 2-of-3 类型时显示
  const SINGLE_TABLE_FILTERS = ['only_a', 'only_b', 'only_c', 'only_a_b', 'only_a_c', 'only_b_c'];
  const showDiffToggle = !SINGLE_TABLE_FILTERS.includes(matchFilter);

  // "差异明细格式" — matched + any specific diff category
  const isDiffCategory = diffFilter !== 'all' && diffFilter !== 'same' && diffFilter !== 'value_match';
  const showDiffDetail = matchFilter === 'matched' && isDiffCategory;

  // Build menu items imperatively to avoid JSX spread issues
  const items: MenuProps['items'] = [];

  // ── 宽表格式（原有的筛选结果导出） ──
  if (showDiffDetail) {
    items.push({
      key: 'filtered',
      label: (
        <Space>
          <TableOutlined />
          <span>宽表格式 <span style={{ fontSize: 11, color: '#999' }}>（字段并列展示）</span></span>
        </Space>
      ),
    });
    // ── 差异明细格式（新增） ──
    items.push({
      key: 'diff_detail',
      label: (
        <Space>
          <FileExcelOutlined />
          <span>差异明细格式</span>
          <span style={{ fontSize: 11, color: '#999' }}>（一行一个差异字段）</span>
        </Space>
      ),
    });
  } else {
    items.push({
      key: 'filtered',
      label: (
        <Space>
          <TableOutlined />
          <span>
            导出当前筛选结果
            <span style={{ fontSize: 11, color: '#999', marginLeft: 6 }}>
              {filterLabel(matchFilter, diffFilter, diffColumn, matchTypeLabels)}
            </span>
          </span>
        </Space>
      ),
    });
  }

  // 需求 5: Diff columns toggle
  if (showDiffToggle) {
    items.push({
      key: 'diff_toggle',
      label: (
        // eslint-disable-next-line jsx-a11y/click-events-have-key-events, jsx-a11y/no-static-element-interactions
        <div
          style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', width: 180 }}
          onClick={() => setIncludeDiffCols(!includeDiffCols)}
        >
          <Text style={{ fontSize: 12 }}>包含差异列</Text>
          <Switch size="small" checked={includeDiffCols} />
        </div>
      ),
      disabled: true,
      style: { cursor: 'default', padding: '4px 12px' },
    });
  }

  items.push(
    { type: 'divider' },
    { key: 'raw_header', label: '导出原始数据', disabled: true, style: { cursor: 'default', color: '#999', fontSize: 12 } },
    ...files.map((f, i) => ({
      key: `raw_${String.fromCharCode(97 + i)}`,
      label: (
        <Space>
          <FileExcelOutlined />
          <span>{f.file_name}</span>
        </Space>
      ),
    })),
  );

  return (
    <Dropdown
      trigger={['click']}
      menu={{
        items,
        onClick: ({ key }) => {
          if (key === 'diff_toggle') return;
          if (key === 'filtered') {
            doExport('filtered', filteredFilename());
          } else if (key === 'diff_detail') {
            doExport('diff_detail', `差异明细_${Date.now()}.xlsx`);
          } else {
            doExport(key, `原始数据_${key}_${Date.now()}.xlsx`);
          }
        },
      }}
    >
      <Button
        type="primary"
        icon={<DownloadOutlined />}
        loading={exporting}
      >
        导出
      </Button>
    </Dropdown>
  );
}
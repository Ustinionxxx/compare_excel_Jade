import { useState, useMemo } from 'react';
import { Card, Row, Col, Statistic, Table, Tag, Typography, Space, Button, Tooltip, Switch } from 'antd';
import {
  CheckCircleOutlined, CloseCircleOutlined, CloseOutlined, ReloadOutlined,
  FileOutlined, TeamOutlined, DeleteOutlined, UndoOutlined, FilterOutlined,
} from '@ant-design/icons';
import type { CompareResponse, ColumnStat } from '../../types';

const { Title, Text } = Typography;

interface Props {
  result: CompareResponse;
  dismissedColumns: string[];
  onDismissColumn: (column: string) => void;
  onRestoreColumn: (column: string) => void;
  onDrill: (filter: { match_filter?: string; diff_filter?: string; diff_column?: string | null }) => void;
  /** "only_a" → "仅 采购单 存在", ... */
  matchTypeLabels: Record<string, string>;
}

export default function StatsBoard({ result, dismissedColumns, onDismissColumn, onRestoreColumn, onDrill, matchTypeLabels }: Props) {
  const [showOnlyDiffs, setShowOnlyDiffs] = useState(false);

  const hasC = result.only_c !== null && result.only_c !== undefined;
  // 3-way comparison: matchTypeLabels includes 2-of-3 types like "only_a_b"
  const is3Way = 'only_a_b' in (result.match_type_labels || {});

  /** Resolve a friendly label for a match_type key, falling back to 仅A/仅B/仅C */
  const label = (key: string, fallback: string) => matchTypeLabels[key] || fallback;

  /** Check if any of the 4 diff categories is non-zero */
  const hasAnyDiff = (s: ColumnStat) =>
    s.t1_diff > 0 || s.t2_diff > 0 || s.t3_diff > 0 || s.all_diff > 0;

  // Count columns with actual differences
  const diffColumnCount = useMemo(
    () => result.column_stats.filter(hasAnyDiff).length,
    [result.column_stats],
  );

  const statCards = [
    {
      title: '总唯一主键数',
      value: result.total_keys,
      color: '#1677ff',
      icon: <TeamOutlined />,
    },
    {
      title: '匹配成功',
      value: result.matched,
      color: '#52c41a',
      icon: <CheckCircleOutlined />,
      onClick: () => onDrill({ match_filter: 'matched' }),
      style: { cursor: 'pointer' },
    },
    {
      title: label('only_a', '仅 A 存在'),
      value: result.only_a,
      color: '#faad14',
      icon: <FileOutlined />,
      onClick: () => onDrill({ match_filter: 'only_a' }),
      style: { cursor: 'pointer' },
    },
    {
      title: label('only_b', '仅 B 存在'),
      value: result.only_b,
      color: '#faad14',
      icon: <FileOutlined />,
      onClick: () => onDrill({ match_filter: 'only_b' }),
      style: { cursor: 'pointer' },
    },
  ];

  if (hasC) {
    statCards.push({
      title: label('only_c', '仅 C 存在'),
      value: result.only_c || 0,
      color: '#faad14',
      icon: <FileOutlined />,
      onClick: () => onDrill({ match_filter: 'only_c' }),
      style: { cursor: 'pointer' },
    });
  }

  // Build diff category columns; 3-way-only columns (T2/T3) hidden for 2-way
  const diffCategoryCols: any[] = [
    {
      title: '三表一致', dataIndex: 'value_match', key: 'value_match',
      render: (v: number, record: ColumnStat) => (
        <Tag
          icon={<CheckCircleOutlined />}
          color="success"
          style={{ cursor: 'pointer' }}
          onClick={() => onDrill({ diff_filter: 'value_match', diff_column: record.column })}
        >
          {v.toLocaleString()}
        </Tag>
      ),
      sorter: (a: ColumnStat, b: ColumnStat) => a.value_match - b.value_match,
    },
    {
      title: 'A表独异', dataIndex: 't1_diff', key: 't1_diff',
      render: (v: number, record: ColumnStat) => (
        <Tag
          icon={<CloseCircleOutlined />}
          color="warning"
          style={{ cursor: 'pointer' }}
          onClick={() => onDrill({ diff_filter: 't1_diff', diff_column: record.column })}
        >
          {v.toLocaleString()}
        </Tag>
      ),
      sorter: (a: ColumnStat, b: ColumnStat) => a.t1_diff - b.t1_diff,
    },
  ];
  // 3-way only: show T2/T3/all_diff columns
  if (is3Way) {
    diffCategoryCols.push(
      {
        title: 'B表独异', dataIndex: 't2_diff', key: 't2_diff',
        render: (v: number, record: ColumnStat) => (
          <Tag
            icon={<CloseCircleOutlined />}
            color="processing"
            style={{ cursor: 'pointer' }}
            onClick={() => onDrill({ diff_filter: 't2_diff', diff_column: record.column })}
          >
            {v.toLocaleString()}
          </Tag>
        ),
        sorter: (a: ColumnStat, b: ColumnStat) => a.t2_diff - b.t2_diff,
      },
      {
        title: 'C表独异', dataIndex: 't3_diff', key: 't3_diff',
        render: (v: number, record: ColumnStat) => (
          <Tag
            icon={<CloseCircleOutlined />}
            color="cyan"
            style={{ cursor: 'pointer' }}
            onClick={() => onDrill({ diff_filter: 't3_diff', diff_column: record.column })}
          >
            {v.toLocaleString()}
          </Tag>
        ),
        sorter: (a: ColumnStat, b: ColumnStat) => a.t3_diff - b.t3_diff,
      },
      {
        title: '三方互异', dataIndex: 'all_diff', key: 'all_diff',
        render: (v: number, record: ColumnStat) => (
          <Tag
            icon={<CloseCircleOutlined />}
            color="error"
            style={{ cursor: 'pointer' }}
            onClick={() => onDrill({ diff_filter: 'all_diff', diff_column: record.column })}
          >
            {v.toLocaleString()}
          </Tag>
        ),
        sorter: (a: ColumnStat, b: ColumnStat) => a.all_diff - b.all_diff,
      },
    );
  }

  const colStatsColumns: any[] = [
    { title: '对比列', dataIndex: 'column', key: 'column', fixed: 'left' as const },
    ...diffCategoryCols,
    {
      title: '差异率', key: 'rate',
      render: (_: unknown, record: ColumnStat) => {
        const diffTotal = record.t1_diff + record.t2_diff + record.t3_diff + record.all_diff;
        const total = record.value_match + diffTotal;
        if (total === 0) return '-';
        const rate = ((diffTotal / total) * 100).toFixed(1);
        return <span style={{ color: diffTotal > record.value_match ? '#ff4d4f' : '#52c41a' }}>{rate}%</span>;
      },
    },
    {
      title: '操作', key: 'action',
      render: (_: unknown, record: ColumnStat) => {
        const isDismissed = dismissedColumns.includes(record.column);
        return isDismissed ? (
          <Tooltip title="恢复此列">
            <Button
              type="link"
              size="small"
              icon={<UndoOutlined />}
              onClick={() => onRestoreColumn(record.column)}
            >
              恢复
            </Button>
          </Tooltip>
        ) : (
          <Tooltip title="剔除此列，明细中不再显示">
            <Button
              type="link"
              size="small"
              danger
              icon={<DeleteOutlined />}
              onClick={() => onDismissColumn(record.column)}
            >
              剔除
            </Button>
          </Tooltip>
        );
      },
    },
  ];

  // Check if all 4 diff categories are zero
  const allDiffsZero = (s: ColumnStat) =>
    s.t1_diff === 0 && s.t2_diff === 0 && s.t3_diff === 0 && s.all_diff === 0;

  // Filter data source
  const rawStats = [...result.column_stats];
  const filteredStats = showOnlyDiffs
    ? rawStats.filter(s => !allDiffsZero(s))
    : rawStats;

  // Sorted: dismissed at bottom
  const sortedStats = [...filteredStats].sort((a, b) => {
    const aDismissed = dismissedColumns.includes(a.column) ? 1 : 0;
    const bDismissed = dismissedColumns.includes(b.column) ? 1 : 0;
    return aDismissed - bDismissed;
  });

  // Check if there are any columns eligible for batch dismiss (no diffs at all)
  const dismissibleAll = result.column_stats.filter(
    s => allDiffsZero(s) && !dismissedColumns.includes(s.column)
  );
  const hasDismissed = dismissedColumns.length > 0;

  return (
    <div>
      <Title level={5}>比对统计概览</Title>
      <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
        {statCards.map(s => (
          <Col span={4} key={s.title}>
            <Card
              hoverable={!!s.onClick}
              size="small"
              onClick={s.onClick}
              style={s.style || {}}
            >
              <Statistic
                title={s.title}
                value={s.value}
                valueStyle={{ color: s.color }}
                prefix={s.icon}
              />
            </Card>
          </Col>
        ))}
      </Row>

      <Card
        title={
          <Space>
            <span>逐列对比结果</span>
            {dismissibleAll.length > 0 && (
              <Button
                size="small"
                icon={<DeleteOutlined />}
                onClick={() => dismissibleAll.forEach(s => onDismissColumn(s.column))}
              >
                一键剔除 {dismissibleAll.length} 列无差异列
              </Button>
            )}
            {hasDismissed && (
              <Button
                size="small"
                icon={<UndoOutlined />}
                onClick={() => result.column_stats
                  .filter(s => dismissedColumns.includes(s.column))
                  .forEach(s => onRestoreColumn(s.column))
                }
              >
                恢复全部已剔除列
              </Button>
            )}
          </Space>
        }
        size="small"
        extra={
          <Space size="middle">
            <Text type="secondary" style={{ fontSize: 13 }}>
              共有 <Text strong style={{ color: '#ff4d4f' }}>{diffColumnCount}</Text> 个字段存在值不同
            </Text>
            <Space size={4}>
              <FilterOutlined style={{ color: '#999', fontSize: 12 }} />
              <Switch
                size="small"
                checked={showOnlyDiffs}
                onChange={setShowOnlyDiffs}
              />
              <Text style={{ fontSize: 12, color: showOnlyDiffs ? '#1677ff' : '#999' }}>
                仅显示有差异的列
              </Text>
            </Space>
          </Space>
        }
      >
        {hasDismissed && (
          <div style={{ marginBottom: 8 }}>
            <Text type="secondary" style={{ fontSize: 12 }}>
              已剔除 {dismissedColumns.length} 列，明细数据中将不再显示
            </Text>
          </div>
        )}

        {showOnlyDiffs && filteredStats.length === 0 ? (
          <div style={{ textAlign: 'center', padding: '24px 0', color: '#999' }}>
            <Text type="secondary">所有字段值均相同</Text>
          </div>
        ) : (
          <Table
            dataSource={sortedStats}
            columns={colStatsColumns}
            rowKey="column"
            pagination={false}
            size="small"
            rowClassName={(record) =>
              dismissedColumns.includes(record.column) ? 'stats-row-dismissed' : ''
            }
          />
        )}
        <style>{`
          .stats-row-dismissed {
            opacity: 0.45;
            background: #fafafa;
            text-decoration: line-through;
          }
          .stats-row-dismissed td {
            color: #999 !important;
          }
        `}</style>
      </Card>
    </div>
  );
}
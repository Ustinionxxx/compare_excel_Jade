import { useState, useMemo } from 'react';
import {
  Card, Checkbox, Typography, Button, Space, Alert, Tag, Radio, Tooltip,
} from 'antd';
import { SwapOutlined, SelectOutlined, ClearOutlined } from '@ant-design/icons';
import type { FileInfo, ColumnMapping } from '../../types';

const { Title, Text } = Typography;

interface Props {
  files: FileInfo[];
  keyMappings: ColumnMapping[];
  onCompare: (columns: string[], mode: string) => void;
  onBack: () => void;
}

export default function ColumnSelect({ files, keyMappings, onCompare, onBack }: Props) {
  const [selectedColumns, setSelectedColumns] = useState<string[]>([]);
  const [compareMode, setCompareMode] = useState<string>('all');

  // Find common columns across all files (excluding key columns)
  const commonColumns = useMemo(() => {
    const keyCols = new Set(keyMappings.flatMap(m => m.key_columns));
    const allColumns = files.map(f => f.columns.filter(c => !keyCols.has(c)));
    const common = allColumns.reduce((acc, cols) =>
      acc.filter(c => cols.includes(c)),
    );
    return common;
  }, [files, keyMappings]);

  const isThreeWay = files.length >= 3;

  // Mode options: only show for 3+ files
  const modeOptions = useMemo(() => {
    if (!isThreeWay) return [];
    const fileLabels = files.map((f, i) => String.fromCharCode(65 + i));
    return [
      { label: '全量比对（三表两两比对）', value: 'all' },
      { label: `仅比 ${fileLabels[0]} vs ${fileLabels[1]}（忽略 ${fileLabels[2]}）`, value: 'ab' },
      { label: `仅比 ${fileLabels[0]} vs ${fileLabels[2]}（忽略 ${fileLabels[1]}）`, value: 'ac' },
      { label: `仅比 ${fileLabels[1]} vs ${fileLabels[2]}（忽略 ${fileLabels[0]}）`, value: 'bc' },
    ];
  }, [isThreeWay, files]);

  const handleSelectAll = () => {
    setSelectedColumns([...commonColumns]);
  };

  const handleClearAll = () => {
    setSelectedColumns([]);
  };

  return (
    <div>
      <Title level={5}>选择对比列{isThreeWay ? '与比对模式' : ''}</Title>
      <Text type="secondary" style={{ display: 'block', marginBottom: 16 }}>
        从所有表共有的列中选择需要比对差异的列
        {isThreeWay ? '，并选择比对模式' : '（两表比对，默认全量）'}。
      </Text>

      <Alert
        message={
          <Space>
            <span>共有的可对比列：共 {commonColumns.length} 列</span>
            {commonColumns.length > 0 && (
              <>
                <Button size="small" type="link" icon={<SelectOutlined />} onClick={handleSelectAll}>
                  全选
                </Button>
                <Button size="small" type="link" icon={<ClearOutlined />} onClick={handleClearAll}>
                  清空
                </Button>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  已选择 {selectedColumns.length} / {commonColumns.length} 列
                </Text>
              </>
            )}
          </Space>
        }
        description={
          commonColumns.length > 0 ? (
            <Space size={4} wrap style={{ marginTop: 4 }}>
              {commonColumns.map(c => (
                <Tag
                  key={c}
                  color={selectedColumns.includes(c) ? 'blue' : 'default'}
                  style={{ cursor: 'pointer', fontSize: 12 }}
                  onClick={() => {
                    setSelectedColumns(prev =>
                      prev.includes(c) ? prev.filter(x => x !== c) : [...prev, c]
                    );
                  }}
                >
                  {c}
                </Tag>
              ))}
            </Space>
          ) : (
            <Text type="danger" style={{ marginTop: 4 }}>未找到共有列</Text>
          )
        }
        type="info"
        showIcon
        style={{ marginBottom: 16 }}
      />

      <Card title={
        <Space>
          <span>选择对比列</span>
          {commonColumns.length > 0 && (
            <Space size={4}>
              <Button size="small" type="link" icon={<SelectOutlined />} onClick={handleSelectAll}>
                全选
              </Button>
              <Button size="small" type="link" icon={<ClearOutlined />} onClick={handleClearAll}>
                清空
              </Button>
              <Text type="secondary" style={{ fontSize: 12 }}>
                已选择 {selectedColumns.length} / {commonColumns.length} 列
              </Text>
            </Space>
          )}
        </Space>
      } size="small" style={{ marginBottom: 16 }}>
        {commonColumns.length === 0 ? (
          <Alert message="没有可用于对比的共有列" type="warning" showIcon />
        ) : (
          <Checkbox.Group
            value={selectedColumns}
            onChange={(vals) => setSelectedColumns(vals as string[])}
          >
            <Space direction="vertical" style={{ maxHeight: 300, overflow: 'auto', width: '100%' }}>
              {commonColumns.map(col => (
                <Checkbox key={col} value={col} style={{ marginLeft: 0 }}>{col}</Checkbox>
              ))}
            </Space>
          </Checkbox.Group>
        )}
      </Card>

      {/* Compare mode: only show for 3+ files */}
      {isThreeWay && (
        <Card title="比对模式" size="small" style={{ marginBottom: 16 }}>
          <Radio.Group
            value={compareMode}
            onChange={e => setCompareMode(e.target.value)}
          >
            <Space direction="vertical">
              {modeOptions.map(opt => (
                <Radio key={opt.value} value={opt.value}>{opt.label}</Radio>
              ))}
            </Space>
          </Radio.Group>
        </Card>
      )}

      <div style={{ textAlign: 'center', marginTop: 24 }}>
        <Space>
          <Button onClick={onBack}>上一步</Button>
          <Tooltip title={selectedColumns.length === 0 ? '请先选择至少一列' : undefined}>
            <Button
              type="primary"
              size="large"
              icon={<SwapOutlined />}
              disabled={selectedColumns.length === 0}
              onClick={() => onCompare(selectedColumns, compareMode)}
            >
              开始比对
            </Button>
          </Tooltip>
        </Space>
      </div>
    </div>
  );
}

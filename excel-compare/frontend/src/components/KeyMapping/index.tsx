import { useState, useEffect } from 'react';
import {
  Card, Select, Typography, Button, Space, Alert, Tag, Table,
} from 'antd';
import { SwapOutlined, ArrowRightOutlined } from '@ant-design/icons';
import type { FileInfo, ColumnMapping } from '../../types';

const { Title, Text } = Typography;

interface Props {
  files: FileInfo[];
  initialMappings: ColumnMapping[];
  onNext: (mappings: ColumnMapping[]) => void;
  onBack: () => void;
}

export default function KeyMapping({ files, initialMappings, onNext, onBack }: Props) {
  const [mappings, setMappings] = useState<ColumnMapping[]>(initialMappings);

  useEffect(() => {
    setMappings(initialMappings);
  }, [initialMappings]);

  const updateKeys = (fileId: string, keys: string[]) => {
    setMappings(prev =>
      prev.map(m => m.file_id === fileId ? { ...m, key_columns: keys } : m)
    );
  };

  const allConfigured = mappings.every(m => m.key_columns.length > 0);

  const columns = [
    {
      title: '文件', dataIndex: 'file_name', key: 'file',
      render: (_: string, record: FileInfo) => (
        <Text strong>{record.file_name}</Text>
      ),
    },
    {
      title: '可选列', key: 'all_cols',
      render: (_: unknown, record: FileInfo) => (
        <Space size={4} wrap>
          {record.columns.map(c => (
            <Tag key={c} style={{ cursor: 'pointer', userSelect: 'none' }}
              onClick={() => {
                const cur = mappings.find(m => m.file_id === record.file_id);
                if (cur && !cur.key_columns.includes(c)) {
                  updateKeys(record.file_id, [...cur.key_columns, c]);
                }
              }
            }>{c}</Tag>
          ))}
        </Space>
      ),
    },
    {
      title: '主键列（选择联合主键）', key: 'keys', width: 300,
      render: (_: unknown, record: FileInfo) => {
        const mapping = mappings.find(m => m.file_id === record.file_id);
        return (
          <Select
            mode="multiple"
            value={mapping?.key_columns || []}
            onChange={(vals) => updateKeys(record.file_id, vals)}
            options={record.columns.map(c => ({ label: c, value: c }))}
            placeholder="选择主键列"
            style={{ width: '100%' }}
          />
        );
      },
    },
  ];

  // Determine if mappings look correct: same count of keys per table
  const keySetSizes = mappings.map(m => m.key_columns.length);
  const consistentKeys = new Set(keySetSizes).size === 1;

  return (
    <div>
      <Title level={5}>设置联合主键</Title>
      <Text type="secondary" style={{ display: 'block', marginBottom: 16 }}>
        为每个表选择用于记录匹配的主键列。可选择多列组成联合主键，各表主键列数量应一致。
      </Text>

      <Alert
        message="匹配规则说明"
        description={
          <ul style={{ margin: 0, paddingLeft: 20 }}>
            <li>系统根据选定的主键值进行记录匹配（全外连接）</li>
            <li>主键值将统一转为字符串进行比较（处理类型差异）</li>
            <li>建议选择具有唯一标识意义的列（如 ID、编号等）</li>
            <li>若组合主键不唯一，仅保留第一条匹配</li>
          </ul>
        }
        type="info"
        showIcon
        style={{ marginBottom: 16 }}
      />

      <Table
        dataSource={files}
        columns={columns}
        rowKey="file_id"
        pagination={false}
        size="middle"
      />

      {!consistentKeys && allConfigured && (
        <Alert
          message="各表主键列数不一致，请确保选择相同数量的主键列"
          type="warning"
          showIcon
          style={{ marginTop: 12 }}
        />
      )}

      <div style={{ textAlign: 'center', marginTop: 24 }}>
        <Space>
          <Button onClick={onBack}>上一步</Button>
          <Button
            type="primary"
            size="large"
            icon={<ArrowRightOutlined />}
            disabled={!allConfigured || !consistentKeys}
            onClick={() => onNext(mappings)}
          >
            下一步：选择对比列
          </Button>
        </Space>
      </div>
    </div>
  );
}

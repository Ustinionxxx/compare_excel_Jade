import { useState, useCallback, useEffect } from 'react';
import {
  Layout, Typography, Steps, Button, Space, message, Spin,
  ConfigProvider, theme, Alert,
} from 'antd';
import { ReloadOutlined, DownloadOutlined } from '@ant-design/icons';
import FileUpload from './components/FileUpload';
import KeyMapping from './components/KeyMapping';
import ColumnSelect from './components/ColumnSelect';
import StatsBoard from './components/StatsBoard';
import DiffTable from './components/DiffTable';
import ExportBar from './components/ExportBar';
import { newSession, executeCompare, setColumnMapping } from './api';
import type {
  FileInfo, ColumnMapping as ColumnMappingType,
  CompareResponse, DetailRow,
} from './types';

const { Header, Content } = Layout;
const { Title } = Typography;

const STEP_TITLES = ['上传文件', '设置主键', '选择对比列', '查看结果'];

export default function App() {
  const [sessionId, setSessionId] = useState<string>('');
  const [currentStep, setCurrentStep] = useState(0);
  const [files, setFiles] = useState<FileInfo[]>([]);
  const [keyMappings, setKeyMappings] = useState<ColumnMappingType[]>([]);
  const [compareColumns, setCompareColumns] = useState<string[]>([]);
  const [compareResult, setCompareResult] = useState<CompareResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // ── Detail drill-down / filter state (lifted for ExportBar sync) ──
  const [matchFilter, setMatchFilter] = useState<string>('all');
  const [diffFilter, setDiffFilter] = useState<string>('all');
  const [diffColumn, setDiffColumn] = useState<string | null>(null);

  // Handle drill-down from StatsBoard
  const handleDrill = useCallback((filter: {
    match_filter?: string;
    diff_filter?: string;
    diff_column?: string | null;
  }) => {
    if (filter.match_filter) setMatchFilter(filter.match_filter);
    if (filter.diff_filter) setDiffFilter(filter.diff_filter);
    if (filter.diff_column !== undefined) setDiffColumn(filter.diff_column ?? null);
  }, []);

  // Handle filter changes from DiffTable
  const handleFilterChange = useCallback((filters: {
    match_filter?: string;
    diff_filter?: string;
    diff_column?: string | null;
  }) => {
    if (filters.match_filter !== undefined) setMatchFilter(filters.match_filter);
    if (filters.diff_filter !== undefined) setDiffFilter(filters.diff_filter);
    if (filters.diff_column !== undefined) setDiffColumn(filters.diff_column);
  }, []);

  // Dismissed columns (hidden from detail diff dropdown)
  const [dismissedColumns, setDismissedColumns] = useState<string[]>([]);

  // Initialize session
  useEffect(() => {
    newSession().then(setSessionId).catch(e => setError('初始化会话失败: ' + e.message));
  }, []);

  const onFilesReady = useCallback((uploadedFiles: FileInfo[]) => {
    setFiles(uploadedFiles);
    // Auto-infer key mappings: find common columns
    if (uploadedFiles.length >= 2) {
      const commonCols = uploadedFiles.reduce((acc, f) =>
        acc.length ? acc.filter(c => f.columns.includes(c)) : [...f.columns],
        [] as string[],
      );
      const mappings = uploadedFiles.map(f => ({
        file_id: f.file_id,
        key_columns: commonCols.length > 0 ? [commonCols[0]] : [],
      }));
      setKeyMappings(mappings);
    }
    setCurrentStep(1);
  }, []);

  const onKeyMappingsReady = useCallback((mappings: ColumnMappingType[]) => {
    setKeyMappings(mappings);
    setCurrentStep(2);
  }, []);

  const runCompare = useCallback(async (columns: string[], mode: string = 'all') => {
    setLoading(true);
    setError(null);
    setDismissedColumns([]);
    setMatchFilter('all');
    setDiffFilter('all');
    setDiffColumn(null);
    try {
      // Ensure mappings are in sync with actual uploaded files
      const validFileIds = new Set(files.map(f => f.file_id));
      let mappingsToSend = keyMappings.filter(m => validFileIds.has(m.file_id));

      // Auto-add missing mappings for files that don't have one yet
      for (const f of files) {
        if (!mappingsToSend.some(m => m.file_id === f.file_id)) {
          const commonCols = files.length >= 2
            ? files.reduce((acc, ff) =>
                acc.length ? acc.filter(c => ff.columns.includes(c)) : [...ff.columns],
                [] as string[],
              )
            : [];
          mappingsToSend.push({
            file_id: f.file_id,
            key_columns: commonCols.length > 0 ? [commonCols[0]] : [],
          });
        }
      }

      await setColumnMapping(sessionId, mappingsToSend);
      const result = await executeCompare(sessionId, columns, mode);
      setCompareColumns(columns);
      setCompareResult(result);
      setCurrentStep(3);
    } catch (e: any) {
      setError('比对执行失败: ' + (e.response?.data?.detail || e.message));
    } finally {
      setLoading(false);
    }
  }, [sessionId, keyMappings, files]);

  const handleReset = useCallback(() => {
    setFiles([]);
    setKeyMappings([]);
    setCompareColumns([]);
    setCompareResult(null);
    setMatchFilter('all');
    setDiffFilter('all');
    setDiffColumn(null);
    setDismissedColumns([]);
    setError(null);
    // 必须先创建新 session，再切换到步骤 0，
    // 否则 FileUpload 会用旧 sessionId 恢复旧文件导致 session/文件不一致
    newSession().then(newId => {
      setSessionId(newId);
      setCurrentStep(0);
    });
  }, []);

  const hasResult = compareResult !== null;

  // Build label → alias mapping for display (A → "采购单", B → "采购单交期", etc.)
  const labelAliases = hasResult && files.length >= 2
    ? Object.fromEntries(
        files.map((f, i) => [String.fromCharCode(65 + i), f.alias || f.file_name.replace(/\.(xlsx|xls)$/i, '')]),
      )
    : undefined;

  return (
    <ConfigProvider theme={{ algorithm: theme.defaultAlgorithm }}>
      <Layout style={{ minHeight: '100vh' }}>
        <Header style={{
          background: '#fff',
          borderBottom: '1px solid #f0f0f0',
          padding: '0 24px',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          height: 56,
        }}>
          <Title level={4} style={{ margin: 0 }}>智能多表比对分析工具</Title>
          <Space>
            {hasResult && (
              <ExportBar
                sessionId={sessionId}
                compareResult={compareResult}
                files={files}
                matchFilter={matchFilter}
                diffFilter={diffFilter}
                diffColumn={diffColumn}
              />
            )}
            <Button icon={<ReloadOutlined />} onClick={handleReset}>重新开始</Button>
          </Space>
        </Header>
        <Content style={{ padding: '24px', maxWidth: 1400, margin: '0 auto', width: '100%' }}>
          {error && (
            <Alert
              message={error}
              type="error"
              closable
              onClose={() => setError(null)}
              style={{ marginBottom: 16 }}
            />
          )}

          <Steps
            current={currentStep}
            items={STEP_TITLES.map(t => ({ title: t }))}
            style={{ marginBottom: 24 }}
          />

          <Spin spinning={loading} tip="正在比对中...">
            {currentStep === 0 && (
              <FileUpload
                sessionId={sessionId}
                onReady={onFilesReady}
              />
            )}
            {currentStep === 1 && (
              <KeyMapping
                files={files}
                initialMappings={keyMappings}
                onNext={onKeyMappingsReady}
                onBack={() => setCurrentStep(0)}
              />
            )}
            {currentStep === 2 && (
              <ColumnSelect
                files={files}
                keyMappings={keyMappings}
                onCompare={runCompare}
                onBack={() => setCurrentStep(1)}
              />
            )}
            {currentStep === 3 && compareResult && (
              <StatsBoard
                result={compareResult}
                onDrill={handleDrill}
                dismissedColumns={dismissedColumns}
                onDismissColumn={(col) => setDismissedColumns(prev => [...prev, col])}
                onRestoreColumn={(col) => setDismissedColumns(prev => prev.filter(c => c !== col))}
              />
            )}
          </Spin>

          {hasResult && (
            <div style={{ marginTop: 16 }}>
              <DiffTable
                sessionId={sessionId}
                compareResult={compareResult}
                compareColumns={compareColumns}
                matchFilter={matchFilter}
                diffFilter={diffFilter}
                diffColumn={diffColumn}
                onFilterChange={handleFilterChange}
                dismissedColumns={dismissedColumns}
                labelAliases={labelAliases}
              />
            </div>
          )}
        </Content>
      </Layout>
    </ConfigProvider>
  );
}

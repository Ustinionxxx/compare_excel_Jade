import { useState, useRef, useEffect, useCallback } from 'react';
import {
  Upload, Card, Table, Typography, message, Space, Tag, Button, Alert, Progress, Input,
} from 'antd';
import { InboxOutlined, DeleteOutlined, FileExcelOutlined, LoadingOutlined } from '@ant-design/icons';
import type { UploadFile, UploadProps } from 'antd';
import { uploadFile, removeFile, getSessionInfo, getParseStatus, setFileAlias } from '../../api';
import type { ParseStatusResponse } from '../../api';
import type { FileInfo } from '../../types';

const { Dragger } = Upload;
const { Text, Title } = Typography;

// 最大文件上传大小：100 MB（与 nginx client_max_body_size 和后端限制保持一致）
const MAX_FILE_SIZE = 100 * 1024 * 1024; // 100 MB in bytes

interface Props {
  sessionId: string;
  onReady: (files: FileInfo[]) => void;
}

/** Stage description map for friendly display */
const STAGE_LABELS: Record<string, string> = {
  file_saved: '文件已保存，开始解析...',
  reading_file: '正在读取文件...',
  parsing_rows: '正在解析数据行...',
  validating_columns: '校验列名...',
  complete: '解析完成',
};

export default function FileUpload({ sessionId, onReady }: Props) {
  const [files, setFiles] = useState<FileInfo[]>([]);
  const [uploading, setUploading] = useState(false);
  const [uploadStatus, setUploadStatus] = useState<{
    type: 'uploading' | 'done' | 'error';
    message: string;
  } | null>(null);
  const uploadCount = useRef(0);
  const [uploadProgress, setUploadProgress] = useState(0);

  // ── Parsing progress state ──
  const [parsing, setParsing] = useState(false);
  const [parsingProgress, setParsingProgress] = useState(0);
  const [parsingMessage, setParsingMessage] = useState('');
  const pollingRef = useRef<Map<string, number>>(new Map());  // fileId → timeoutId
  const currentUploadFileRef = useRef<File | null>(null);      // track current file name for error messages
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      // Clear all polling timeouts on unmount
      pollingRef.current.forEach((id) => clearTimeout(id));
      pollingRef.current.clear();
    };
  }, []);

  // Restore file list from session on mount (handles back-navigation)
  // NOTE: does NOT auto-advance — user stays at step 0 to edit aliases if desired
  useEffect(() => {
    getSessionInfo(sessionId).then(info => {
      if (info.files.length > 0 && mountedRef.current) {
        setFiles(info.files);
      }
    }).catch(() => {/* silent - session may be empty */});
  }, [sessionId]);

  /** Start polling parse status for a newly uploaded file.
   *
   * Uses setTimeout with graduated delay:
   *  - first ~30s: 800ms intervals (fast feedback for small files)
   *  - then up to ~6min: 3s intervals (reduce overhead for large files)
   */
  const startPolling = useCallback((fileId: string, fileName: string) => {
    // Clear any existing poll for this file
    const existing = pollingRef.current.get(fileId);
    if (existing) { clearInterval(existing); pollingRef.current.delete(fileId); }

    const FAST_MS = 800;
    const SLOW_MS = 3000;
    const FAST_LIMIT = 38;   // 38 * 0.8s ≈ 30s
    const MAX_RETRIES = 138; // 38 + 100 = 30s + 300s = 330s total

    let retryCount = 0;

    const poll = async () => {
      if (!mountedRef.current) return;

      retryCount += 1;

      if (retryCount > MAX_RETRIES) {
        pollingRef.current.delete(fileId);
        setParsing(false);
        setParsingProgress(0);
        setParsingMessage('');
        setUploadStatus({
          type: 'error',
          message: `❌ ${fileName} 解析超时（超过5分钟），请重试或上传较小的文件`,
        });
        return;
      }

      try {
        const status = await getParseStatus(sessionId, fileId);

        if (status.status === 'parsing') {
          setParsingProgress(status.progress);
          setParsingMessage(
            STAGE_LABELS[status.stage] || status.message || '正在解析...',
          );
          // Schedule next poll with graduated delay
          const delay = retryCount <= FAST_LIMIT ? FAST_MS : SLOW_MS;
          const tid = window.setTimeout(poll, delay);
          pollingRef.current.set(fileId, tid);
        } else if (status.status === 'complete') {
          pollingRef.current.delete(fileId);
          setParsingProgress(100);
          setParsingMessage('解析完成');

          // Small delay so user sees 100% before transition
          setTimeout(() => {
            if (!mountedRef.current) return;
            setParsing(false);
            setParsingProgress(0);
            setParsingMessage('');

            const defaultAlias = (status.file_name || fileName).replace(/\.(xlsx|xls|csv)$/i, '');
            const newFile: FileInfo = {
              file_id: fileId,
              file_name: status.file_name || fileName,
              alias: defaultAlias,
              columns: status.columns || [],
              sheet: (status.sheet_names && status.sheet_names[0]) || '',
            };

            // 不自动跳转，用户可在步骤 0 编辑别名后再手动点击"下一步"
            setFiles(prev => {
              if (prev.some(f => f.file_id === fileId)) return prev;
              return [...prev, newFile];
            });

            setUploadStatus({
              type: 'done',
              message: `✅ ${status.file_name || fileName} 上传成功，共解析 ${(status.total_rows || 0).toLocaleString()} 行`,
            });
          }, 400);
        } else if (status.status === 'error') {
          pollingRef.current.delete(fileId);
          setParsing(false);
          setParsingProgress(0);
          setParsingMessage('');

          setUploadStatus({
            type: 'error',
            message: `❌ ${fileName} 解析失败: ${status.error || '未知错误'}`,
          });
        }
      } catch (e) {
        // Polling connection error — retry with graduated delay
        const delay = retryCount <= FAST_LIMIT ? FAST_MS : SLOW_MS;
        const tid = window.setTimeout(poll, delay);
        pollingRef.current.set(fileId, tid);
      }
    };

    // Kick off first poll immediately (delay 0)
    const tid = window.setTimeout(poll, 0);
    pollingRef.current.set(fileId, tid);
  }, [sessionId, onReady]);

  const handleUpload: UploadProps['customRequest'] = async (options) => {
    const { file, onSuccess, onError } = options;
    const uploadFileObj = file as File;

    // 提前校验文件大小，避免上传被 nginx/后端拦截
    if (uploadFileObj.size > MAX_FILE_SIZE) {
      const sizeMB = (uploadFileObj.size / (1024 * 1024)).toFixed(1);
      const maxMB = (MAX_FILE_SIZE / (1024 * 1024)).toFixed(0);
      setUploadStatus({
        type: 'error',
        message: `❌ ${uploadFileObj.name} 文件大小为 ${sizeMB} MB，超过 ${maxMB} MB 的上限。\n请拆分文件后重新上传。`,
      });
      onError?.(new Error(`文件超过 ${maxMB} MB 上限`));
      return;
    }

    setUploading(true);
    uploadCount.current += 1;
    const current = uploadCount.current;
    currentUploadFileRef.current = uploadFileObj;

    try {
      setUploadProgress(0);
      setParsing(false);
      setParsingProgress(0);
      setParsingMessage('');
      setUploadStatus({
        type: 'uploading',
        message: `正在上传文件 ${current}/${files.length + 1}...`,
      });

      // Phase 1: upload bytes (shows real network progress)
      const result = await uploadFile(
        uploadFileObj,
        sessionId,
        (pct) => {
          setUploadProgress(pct);
          setUploadStatus({
            type: 'uploading',
            message: `正在上传文件 ${current}/${files.length + 1}... ${pct}%`,
          });
        },
      );

      setUploadProgress(100);

      // Phase 2: file saved, now parsing in background
      setParsing(true);
      setParsingProgress(5);
      setParsingMessage('文件已保存，开始解析...');
      setUploadStatus({
        type: 'uploading',
        message: `正在解析 ${uploadFileObj.name}...`,
      });

      // Call onSuccess so Ant Upload's internal state is resolved
      onSuccess?.(result);

      // Start polling for parse status
      startPolling(result.file_id, uploadFileObj.name);

    } catch (e: any) {
      const errMsg = e.response?.data?.detail || e.message;
      const displayMsg = errMsg.includes('Maximum 3 files')
        ? `已达到最大文件数限制（3个）。如需换文件，请先移除已有文件再上传。`
        : errMsg;
      setUploadStatus({
        type: 'error',
        message: `❌ ${uploadFileObj.name} 上传失败: ${displayMsg}`,
      });
      setParsing(false);
      setParsingProgress(0);
      setParsingMessage('');
      onError?.(e);
    } finally {
      setUploading(false);
    }
  };

  const handleRemove = async (fileId: string) => {
    // Stop any active polling for this file
    const pollId = pollingRef.current.get(fileId);
    if (pollId) {
      clearTimeout(pollId);
      pollingRef.current.delete(fileId);
    }

    try {
      await removeFile(sessionId, fileId);
      setFiles(prev => prev.filter(f => f.file_id !== fileId));
      setUploadStatus(null);
      message.success('文件已移除');
    } catch (e: any) {
      message.error('移除失败: ' + (e.response?.data?.detail || e.message));
    }
  };

  const [editingAlias, setEditingAlias] = useState<string | null>(null);
  const [aliasInput, setAliasInput] = useState('');

  const handleAliasSave = async (fileId: string) => {
    if (!aliasInput.trim()) return;
    try {
      await setFileAlias(sessionId, fileId, aliasInput.trim());
      setFiles(prev => prev.map(f =>
        f.file_id === fileId ? { ...f, alias: aliasInput.trim() } : f,
      ));
      setEditingAlias(null);
      setAliasInput('');
    } catch (e: any) {
      message.error('别名更新失败: ' + (e.response?.data?.detail || e.message));
    }
  };

  const maxReached = files.length >= 3;
  const isProcessing = uploading || parsing;

  const fileColumns = [
    { title: '文件名', dataIndex: 'file_name', key: 'name' },
    {
      title: '别名', key: 'alias',
      width: 200,
      render: (_: unknown, record: FileInfo) => {
        const currentAlias = record.alias || record.file_name.replace(/\.(xlsx|xls|csv)$/i, '');
        if (editingAlias === record.file_id) {
          return (
            <Input
              size="small"
              value={aliasInput}
              onChange={e => setAliasInput(e.target.value)}
              onPressEnter={() => handleAliasSave(record.file_id)}
              onBlur={() => handleAliasSave(record.file_id)}
              autoFocus
              style={{ width: 160 }}
            />
          );
        }
        return (
          <Button
            type="link"
            size="small"
            onClick={() => {
              setEditingAlias(record.file_id);
              setAliasInput(currentAlias);
            }}
            style={{ padding: 0 }}
          >
            {currentAlias}
          </Button>
        );
      },
    },
    {
      title: '列数', dataIndex: 'columns', key: 'cols',
      render: (cols: string[]) => cols.length,
    },
    {
      title: '列名预览', dataIndex: 'columns', key: 'cols_list',
      width: 300,
      render: (cols: string[]) => (
        <Space size={4} wrap>
          {cols.slice(0, 10).map(c => <Tag key={c} style={{ fontSize: 12 }}>{c}</Tag>)}
          {cols.length > 10 && <Tag>+{cols.length - 10}</Tag>}
        </Space>
      ),
    },
    {
      title: '操作', key: 'action',
      render: (_: unknown, record: FileInfo) => (
        <Button
          type="text"
          danger
          icon={<DeleteOutlined />}
          disabled={isProcessing}
          onClick={() => handleRemove(record.file_id)}
        >
          移除
        </Button>
      ),
    },
  ];

  return (
    <div>
      <Title level={5} style={{ marginBottom: 16 }}>
        上传 Excel / CSV 文件（支持 .xlsx / .xls / .csv，最多 3 个文件）
      </Title>

      {/* Status banner */}
      {uploadStatus && (
        <Alert
          type={uploadStatus.type === 'error' ? 'error' : uploadStatus.type === 'done' ? 'success' : 'info'}
          message={
            <Space>
              {(uploadStatus.type === 'uploading' || parsing) && <LoadingOutlined />}
              {parsing ? parsingMessage : uploadStatus.message}
            </Space>
          }
          showIcon={uploadStatus.type !== 'uploading'}
          style={{ marginBottom: 12 }}
          closable={uploadStatus.type !== 'uploading'}
          onClose={() => setUploadStatus(null)}
        />
      )}

      {/* Progress bar: shows upload progress OR parsing progress */}
      {(uploading || parsing) && (
        <Progress
          percent={parsing ? (parsingProgress || 1) : (uploadProgress || 1)}
          status={parsing ? 'active' : 'active'}
          strokeColor={parsing ? '#52c41a' : '#1677ff'}
          showInfo={false}
          style={{ marginBottom: 12 }}
        />
      )}

      <Dragger
        customRequest={handleUpload}
        showUploadList={false}
        accept=".xlsx,.xls,.csv"
        disabled={maxReached || isProcessing}
      >
        <p className="ant-upload-drag-icon"><InboxOutlined /></p>
        <p className="ant-upload-text">点击或拖拽 Excel 文件到此区域上传</p>
        <p className="ant-upload-hint">
          支持 .xlsx / .xls / .csv 格式，单个文件最大 100 MB
          {files.length > 0 && `（已上传 ${files.length}/3 个）`}
        </p>
      </Dragger>

      {files.length > 0 && (
        <Card
          size="small"
          title={<Space><FileExcelOutlined /> 已上传文件</Space>}
          style={{ marginTop: 16 }}
        >
          <Table
            dataSource={files}
            columns={fileColumns}
            rowKey="file_id"
            pagination={false}
            size="small"
          />
        </Card>
      )}

      {files.length >= 2 && !isProcessing && (
        <div style={{ textAlign: 'center', marginTop: 16 }}>
          <Text type="secondary">
            已上传 {files.length} 个文件，可继续上传或
            <Button type="link" onClick={() => onReady(files)}>下一步设置主键</Button>
          </Text>
        </div>
      )}

      {files.length >= 2 && isProcessing && (
        <div style={{ textAlign: 'center', marginTop: 16 }}>
          <Text type="secondary">
            正在处理文件，请稍候...
          </Text>
        </div>
      )}
    </div>
  );
}
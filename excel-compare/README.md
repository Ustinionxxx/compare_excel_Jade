# 智能多表比对分析工具

零代码、可视化的多 Excel/CSV 表比对与差异分析平台。上传 2~3 个文件，灵活选择联合主键进行记录匹配，逐列对比数据是否一致，支持差异高亮、交互式钻取与多格式数据导出。

**🌐 在线体验：** [http://1.14.195.251:5173](http://1.14.195.251:5173)

---

## 目录

- [功能特性](#功能特性)
- [操作流程](#操作流程)
- [技术栈](#技术栈)
- [快速启动](#快速启动)
- [项目结构](#项目结构)
- [API 参考](#api-参考)
- [测试](#测试)
- [性能基准](#性能基准)
- [架构说明](#架构说明)
- [配置与环境变量](#配置与环境变量)
- [注意事项](#注意事项)

---

## 功能特性

### 📁 文件管理
- 支持 `.xlsx` / `.xls` / `.csv` 三种格式
- 拖拽上传，64KB 分块流式传输，最大 100MB/文件
- 自动识别表头列名，支持最多 3 个文件同时比对
- 上传后后台自动解析，前端轮询显示解析进度
- 支持为每个文件设置别名（如 "采购单"、"采购单交期"），方便结果阅读
- 可随时删除已上传文件，重新上传

### 🔑 主键映射
- 为每个文件独立选择主键列（支持多列联合主键）
- 自动推荐所有文件共有的列名作为默认主键
- 灵活适配不同列名但含义相同的场景

### 🔗 智能比对引擎
- 基于 pandas 向量化操作的全外连接匹配，性能远超逐行遍历
- 支持 2 表比对和 3 表比对
- 3 表场景支持 4 种比对模式：全量两两比对（A-B、A-C、B-C）、仅 A-B、仅 A-C、仅 B-C
- 匹配状态标记：匹配成功 / 仅 A 存在 / 仅 B 存在 / 仅 C 存在 / A+B（缺C）/ A+C（缺B）/ B+C（缺A）

### 📊 统计看板
- 关键指标卡片：总唯一主键数、匹配成功数、仅各表存在数
- 逐列差异统计表，展示每列：匹配数、差异数、仅 A 存在数、仅 B 存在数
- 点击统计卡片直接钻取到明细数据
- 可收起无差异列，聚焦真正有差异的数据

### 🔍 明细钻取
- 基于 AG Grid 的高性能虚拟滚动表格，轻松处理万级行数
- 按匹配状态筛选（全部 / 匹配成功 / 仅某表存在）
- 按差异列筛选（全部列 / 值相同 / 值不同）
- 差异单元格红色高亮，一目了然
- 支持分页查询，服务端排序

### 📥 灵活导出
- **筛选结果宽表** — 导出当前筛选条件下的宽表视图（各表数据并排对比）
- **差异明细长表** — 导出差异明细行式视图（每行一条差异记录）
- **原始数据表** — 逐个导出各文件的原始数据
- 大数据集（>10MB 预估）自动切换为流式导出，避免内存溢出

---

## 操作流程

```
上传文件  →  设置主键  →  选择对比列  →  查看结果
(Step 1)    (Step 2)     (Step 3)       (Step 4)
```

1. **上传文件** — 拖拽或点击上传 2~3 个 Excel/CSV 文件，设置别名后系统自动解析
2. **设置主键** — 为每个文件选择用于记录匹配的键列（如 "订单号"、"SKU" 等）
3. **选择对比列** — 勾选需要比对的列，3 表场景可选择比对模式
4. **查看结果** — 统计看板 + 明细表格 + 一键导出

点击顶部 **"重新开始"** 按钮可随时开始新的比对任务。

---

## 技术栈

| 层级 | 技术 |
| ------ | ------------------------------ |
| 前端 | React 18 + TypeScript + Vite 5 |
| UI 组件 | Ant Design 5 + @ant-design/icons |
| 表格 | AG Grid Community 31（虚拟滚动） |
| HTTP 客户端 | Axios |
| 后端 | Python 3.10+ / FastAPI |
| 服务器 | Gunicorn + Uvicorn Workers（默认 2 进程） |
| 数据处理 | pandas + numpy（全向量化操作） |
| Excel 解析 | python-calamine（首选）/ openpyxl / xlrd |
| Excel 导出 | xlsxwriter（首选）/ openpyxl |
| CSV 解析 | 多编码 + 多分隔符自动检测 |
| 存储 | 文件系统（pickle 会话 + JSON 任务进度） |
| 容器化 | Docker + Docker Compose + Nginx |

---

## 快速启动

### 方式一：本地开发

**后端：**

```bash
cd backend
pip install -r requirements.txt

# 可选：安装性能加速依赖
pip install python-calamine xlsxwriter

# 启动后端
python -m uvicorn app.main:app --reload --port 8000
```

**前端：**

```bash
cd frontend
npm install
npm run dev
```

访问 [http://localhost:5173](http://localhost:5173)

### 方式二：Docker（推荐）

```bash
# 启动（后端 8000 端口 + 前端 5173 端口）
docker compose up -d

# 指定 Worker 数量（默认 2）
WORKERS=4 docker compose up -d
```

访问 [http://localhost:5173](http://localhost:5173)

### 一键完整部署（生产环境）

```bash
# 1. 克隆仓库
git clone <your-repo-url>
cd excel-compare

# 2. 启动服务
docker compose up -d

# 3. 检查健康状态
curl http://localhost:8000/health
```

> **注意：** 如果在国内部署遇到 Docker 镜像拉取慢的问题，请参考 `docker-compose.yml` 中的镜像加速器配置注释。前端 `.npmrc` 默认使用 npmmirror.com，海外部署请移除或修改。

---

## 项目结构

```
excel-compare/
├── docker-compose.yml              # Docker 编排配置
├── benchmark.py                    # 性能压测工具
├── backend/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── test_comprehensive.py       # 综合测试套件
│   └── app/
│       ├── main.py                 # FastAPI 入口，CORS，路由注册
│       ├── models.py               # Pydantic 请求/响应模型
│       ├── routers/
│       │   ├── files.py            # 文件上传、解析状态轮询、会话管理、别名设置
│       │   ├── compare.py          # 主键映射、同步/异步比对、明细查询、任务状态轮询
│       │   └── export.py           # Excel 导出（内存模式 / 流式模式自动切换）
│       └── services/
│           ├── parser.py           # Excel/CSV 解析（calamine 首选，openpyxl/xlrd 备用）
│           ├── matcher.py          # 核心比对引擎：外连接合并、向量化差异计算、列统计
│           └── exporter.py         # Excel 文件生成（xlsxwriter 首选，openpyxl 备用）
├── frontend/
│   ├── Dockerfile                  # 多阶段构建：Node 20 构建 → Nginx 提供静态文件
│   ├── nginx.conf                  # 反向代理 /api → backend:8000，100MB 上传限制
│   ├── vite.config.ts              # 开发代理 /api → 127.0.0.1:8000
│   └── src/
│       ├── App.tsx                 # 步骤向导状态机，全局状态管理
│       ├── api/index.ts            # Axios 客户端，上传进度、解析轮询、异步比对
│       ├── types/index.ts          # TypeScript 接口定义
│       └── components/
│           ├── FileUpload/         # 拖拽上传 + 流式传输 + 后台解析轮询 + 别名设置
│           ├── KeyMapping/         # 多列主键选择，自动推荐
│           ├── ColumnSelect/       # 对比列勾选 + 3 表比对模式选择
│           ├── StatsBoard/         # 统计卡片 + 逐列差异表 + 钻取交互
│           ├── DiffTable/          # 分页明细表格 + 匹配/差异过滤器 + 差异高亮
│           └── ExportBar/          # 导出下拉菜单（宽表/长表/原始数据）
└── README.md
```

---

## API 参考

### 文件与会话

| 方法 | 端点 | 说明 |
| ---- | ------------------------------ | ------------------ |
| GET | `/api/files/new-session` | 创建新会话（返回 16 位 hex session_id） |
| POST | `/api/files/upload` | 上传文件（流式写入磁盘，后台线程解析） |
| GET | `/api/files/parse-status/{session_id}/{file_id}` | 轮询文件解析进度 |
| POST | `/api/files/remove` | 移除已上传文件 |
| POST | `/api/files/alias` | 设置文件显示别名 |
| GET | `/api/files/session/{session_id}` | 获取完整会话状态 |

### 比对

| 方法 | 端点 | 说明 |
| ---- | ------------------------------ | ------------------ |
| POST | `/api/compare/mapping` | 设置各文件的主键列映射 |
| POST | `/api/compare/execute` | 同步执行比对（适用于小文件） |
| POST | `/api/compare/execute-async` | **推荐：** 异步执行比对（立即返回 task_id） |
| GET | `/api/compare/status/{task_id}` | 轮询异步比对进度与结果 |
| POST | `/api/compare/detail` | 分页查询明细（支持匹配/差异过滤器） |

### 导出

| 方法 | 端点 | 说明 |
| ---- | ------------------------------ | ------------------ |
| POST | `/api/export/excel` | 导出比对结果为 Excel（支持宽表/长表/原始数据） |

---

## 测试

所有测试集中在 `backend/test_comprehensive.py`，从 `backend/` 目录运行：

```bash
# 单元测试（比对引擎核心逻辑，无需启动服务）
python test_comprehensive.py --unit

# 集成测试（需要运行后端服务）
python test_comprehensive.py --integration

# 快速冒烟测试
python test_comprehensive.py --quick

# 并发测试（5 客户端 × 500 行）
python test_comprehensive.py --concurrency 5

# 跨 Worker 测试（模拟多进程竞态条件）
python test_comprehensive.py --cross-worker

# 引擎压力测试（无需 API，纯数据量压测）
python test_comprehensive.py --stress

# API 负载测试（30 并发）
python test_comprehensive.py --load 30

# 运行全部测试
python test_comprehensive.py
```

---

## 性能基准

使用 `benchmark.py` 进行性能评测：

```bash
# 引擎性能测试（5 万行 × 30 列）
python benchmark.py --engine-only --rows 50000 --cols 30

# 完整 API 测试
python benchmark.py --rows 10000 --cols 20

# 并发压测
python benchmark.py --concurrent 10 --rows 5000
```

核心优化措施：
- **全向量化计算** — 基于 pandas/numpy，避免逐行遍历（性能提升 50-100×）
- **python-calamine** — Rust 实现的 Excel 读取引擎，比 openpyxl 快 5-10×
- **xlsxwriter** — C 扩展的 Excel 写入引擎，大文件导出更快
- **异步比对** — 后台线程执行，不阻塞 HTTP 请求，支持进度轮询
- **AG Grid 虚拟滚动** — 前端万级行数流畅渲染

---

## 架构说明

### 多 Worker 进程隔离

后端使用 Gunicorn + 多 Uvicorn Worker 运行。这是最重要的架构约束：

- **进程间不共享内存** — `threading.Lock` 仅在同一进程内有效
- **会话持久化** — 所有会话状态通过 pickle 写入磁盘 `uploads/sessions/`，`get_session()` 同时检查文件 `mtime` 和文件大小来检测其他 Worker 的更新
- **异步任务进度** — 比对任务状态通过 JSON 文件存储在 `uploads/compare_tasks/`，任意 Worker 均可轮询
- **会话自动清理** — 后台定时器（30 分钟 TTL）同时检查内存时间戳和磁盘文件 mtime，避免误删

### 数据流

```
浏览器                    Nginx                    FastAPI Worker
  │                         │                           │
  ├─ 上传文件 ──────────►  ├─ /api/files/upload ────►  ├─ 流式写盘
  │                         │                           ├─ 后台线程解析
  │  ◄── 轮询解析进度 ────  ◄── /parse-status ────────  ◄── 返回状态
  │                         │                           │
  ├─ 设置主键 ──────────►  ├─ /api/compare/mapping ──►  ├─ 持久化会话
  │                         │                           │
  ├─ 提交比对 ──────────►  ├─ /execute-async ────────►  ├─ 线程池执行
  │  ◄── 轮询进度 ────────  ◄── /status/{task_id} ────  ◄── JSON 文件
  │                         │                           │
  ├─ 查询明细 ──────────►  ├─ /api/compare/detail ───►  ├─ DataFrame 切片
  │                         │                           │
  ├─ 导出结果 ◄──────────  ◄── /api/export/excel ─────  ◄── 流式生成
```

---

## 配置与环境变量

| 变量 | 说明 | 默认值 |
| ------ | ------------------------------ | ------ |
| `WORKERS` | Gunicorn worker 进程数 | `2` |
| `PYTHONUNBUFFERED` | Python 输出不缓冲（Docker 中建议设为 `1`） | 空 |

CORS 允许的来源在 `backend/app/main.py` 中配置，默认允许 `localhost:5173`、`localhost:3000`、`127.0.0.1:5173`。

Nginx 上传限制在 `frontend/nginx.conf` 中配置为 `client_max_body_size 100m`。

---

## 注意事项

1. **无数据库** — 所有状态存储在文件系统上。高并发多租户场景建议引入 Redis 作为会话存储。
2. **可选原生依赖** — `python-calamine` 和 `xlsxwriter` 不在 `requirements.txt` 中，建议手动安装以获得最佳性能。
3. **上传卷** — Docker 使用命名卷 `uploads` 存储上传文件，备份和清理时需特别处理。
4. **会话过期** — 会话在 30 分钟无活动后自动清理，比对任务文件在 1 小时后清理。
5. **并发限制** — 后端线程池最多同时执行 2 个比对任务，避免 CPU 资源争抢。
6. **CSV 检测** — 自动尝试多种编码（utf-8、gbk、gb2312、gb18030、latin-1）和分隔符（逗号、制表符、分号、竖线），通过评分算法选择最佳组合。
7. **npm 镜像** — 前端 `.npmrc` 默认指向 npmmirror.com，海外部署请移除或修改。

---

## License

MIT

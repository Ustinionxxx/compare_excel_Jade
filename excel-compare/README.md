# 智能多表比对分析工具

零代码、可视化的多 Excel 表比对与差异分析平台。上传 2~3 个 Excel 文件，灵活选择联合主键进行记录匹配，逐列对比数据是否一致，并支持差异高亮与数据导出。

## 功能特性

- **📁 文件管理** — 上传 `.xlsx` / `.xls`，自动识别表头，最多支持 3 个表
- **🔑 字段映射** — 为每个表独立选择主键列（支持多列联合主键），自动推荐相同列名
- **🔗 全外连接匹配** — 基于主键匹配记录，计算匹配状态（匹配成功 / 仅某表存在）
- **📊 统计看板** — 总唯一主键数、匹配数、仅各表存在数，逐列差异统计
- **🔍 明细钻取** — 点击统计卡片过滤明细，差异单元格红色高亮
- **📥 数据导出** — 导出筛选结果（Excel）或原始单表数据

## 快速启动

### 方式一：本地开发

**后端：**

```bash
cd backend
pip install -r requirements.txt
python -m uvicorn app.main:app --reload --port 8000
```

**前端：**

```bash
cd frontend
npm install
npm run dev
```

访问 http://localhost:5173

### 方式二：Docker

```bash
docker compose up -d
```

访问 http://localhost:5173

## 技术栈

| 层级   | 技术                           |
| ------ | ------------------------------ |
| 前端   | React 18 + TypeScript + Ant Design 5 |
| 后端   | Python 3.10+ + FastAPI + pandas + openpyxl |
| 存储   | 临时文件 `./uploads/`，结果内存缓存 |
| 容器化 | Docker + Nginx                  |

## API 概览

| 方法 | 端点                    | 说明               |
| ---- | ----------------------- | ------------------ |
| GET  | `/api/files/new-session` | 创建新会话         |
| POST | `/api/files/upload`      | 上传 Excel 文件    |
| POST | `/api/files/remove`      | 移除已上传文件     |
| GET  | `/api/files/session/{id}`| 获取会话状态       |
| POST | `/api/compare/mapping`   | 设置主键映射       |
| POST | `/api/compare/execute`   | 执行比对           |
| POST | `/api/compare/detail`    | 查询明细数据       |
| POST | `/api/export/excel`      | 导出 Excel         |

## 项目结构

```
excel-compare/
├── backend/
│   ├── app/
│   │   ├── main.py              # FastAPI 入口
│   │   ├── models.py            # Pydantic 模型
│   │   ├── routers/
│   │   │   ├── files.py         # 文件上传与管理
│   │   │   ├── compare.py       # 比对 API
│   │   │   └── export.py        # 导出 API
│   │   └── services/
│   │       ├── parser.py        # Excel 解析
│   │       ├── matcher.py       # 比对引擎
│   │       └── exporter.py      # 导出逻辑
│   ├── requirements.txt
│   └── Dockerfile
├── frontend/
│   ├── src/
│   │   ├── App.tsx              # 主应用
│   │   ├── api/index.ts         # API 客户端
│   │   ├── types/index.ts       # TypeScript 类型
│   │   └── components/
│   │       ├── FileUpload/      # 文件上传组件
│   │       ├── KeyMapping/      # 主键映射组件
│   │       ├── ColumnSelect/    # 对比列选择组件
│   │       ├── StatsBoard/      # 统计看板组件
│   │       ├── DiffTable/       # 差异明细表格
│   │       └── ExportBar/       # 导出按钮
│   ├── package.json
│   └── Dockerfile
├── docker-compose.yml
└── README.md
```

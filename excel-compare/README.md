# 智能多表比对分析工具

> 零代码、可视化的多 Excel/CSV 表比对与差异分析平台。

**🌐 在线体验：[http://1.14.195.251:5173](http://1.14.195.251:5173)**

---

## 这是什么？

在日常工作中，我们经常需要对比两个或三个 Excel 表的差异——比如：

- **采购单** vs **供应商交期表** vs **库存表**——哪些订单价格变了？哪些物料交期推迟了？三方的数量各是多少？
- **上月工资表** vs **本月工资表**——谁新增了？谁调薪了？
- **系统导出的库存表** vs **财务盘点的库存表**——哪些 SKU 数量对不上？

手动 VLOOKUP 费时费力，还容易出错。这个工具帮你**上传 → 选主键 → 一键比对 → 导出结果**，全程零代码。

**核心能力：** 基于联合主键做全外连接匹配，五类差异逐列对比（三表一致 / A 独异 / B 独异 / C 独异 / 三方互异），统计看板 + 明细钻取 + Excel 导出，一条龙完成。

---

## 目录

- [操作流程](#操作流程)
- [功能详解](#功能详解)
  - [Step 1：上传文件](#step-1上传文件)
  - [Step 2：设置主键](#step-2设置主键)
  - [Step 3：选择对比列](#step-3选择对比列)
  - [Step 4：查看结果](#step-4查看结果)
- [技术栈](#技术栈)
- [快速启动](#快速启动)
- [项目结构](#项目结构)
- [API 参考](#api-参考)
- [测试](#测试)
- [性能基准](#性能基准)
- [架构说明](#架构说明)
- [配置](#配置)
- [注意事项](#注意事项)

---

## 操作流程

```
  Step 1          Step 2          Step 3          Step 4
┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐
│  上传文件  │ → │  设置主键  │ → │ 选择对比列 │ → │  查看结果  │
│ 2~3 个表  │   │ 联合主键   │   │ 比对模式   │   │ 统计+明细  │
└──────────┘   └──────────┘   └──────────┘   └──────────┘
```

页面顶部有步骤条指示当前位置，随时可点击 **"重新开始"** 重置所有状态，开始新的比对。

---

## 功能详解

### Step 1：上传文件

**界面：** Ant Design 拖拽上传区域，提示 *"上传 Excel / CSV 文件（支持 .xlsx / .xls / .csv，最多 3 个文件）"*。

| 特性 | 说明 |
|---|---|
| 支持格式 | `.xlsx` / `.xls` / `.csv` |
| 文件数量 | 2~3 个（达到上限后提示先移除已有文件） |
| 大小限制 | 单个文件最大 100 MB |
| 上传方式 | 拖拽或点击选择，64KB 分块流式传输 |
| 解析方式 | 上传后后台线程自动解析，前端轮询显示进度 |
| 进度提示 | *"文件已保存，开始解析..."* → *"正在读取文件..."* → *"正在解析数据行..."* → *"校验列名..."* → *"解析完成"* |
| 别名设置 | 可为每个文件设置中文别名（如 "采购单"、"交期表"），方便后续结果显示 |
| 文件管理 | 支持随时删除已上传文件，重新上传 |

**CSV 智能检测：** 自动尝试多种编码（utf-8、gbk、gb2312、gb18030、latin-1）和分隔符（逗号、制表符、分号、竖线），通过评分算法选择最佳组合。

点击 **"下一步设置主键"** 进入 Step 2。

---

### Step 2：设置主键

**界面：** 每个文件一张卡片，显示 *"主键列（选择联合主键）"*，下拉多选列名。提示 *"为每个表选择用于记录匹配的主键列。可选择多列组成联合主键，各表主键列数量应一致。"*

| 特性 | 说明 |
|---|---|
| 主键选择 | 每个文件独立选择，支持下拉多选 |
| 联合主键 | 支持多列组合（如 "订单号 + 行号"），各表数量应一致 |
| 自动推荐 | 自动选中所有文件共有的第一个列名 |
| 类型处理 | 主键值统一转为字符串比较，自动处理数字/文本类型差异 |
| 智能提示 | 建议选择具有唯一标识意义的列（如 ID、编号等） |
| 校验 | 主键列数不一致时阻止进入下一步 |

点击 **"下一步：选择对比列"** 进入 Step 3。

---

### Step 3：选择对比列

**界面：** 列出所有文件共有的列名（点击 Tag 勾选/取消），底部显示比对模式选项（3 文件时）。

| 特性 | 说明 |
|---|---|
| 共有列识别 | 自动找出所有文件共有的列名，排除已选为主键的列 |
| 全选/清空 | 一键选中全部或清空 |
| 已选计数 | 实时显示 *"已选择 X / Y 列"* |
| **比对模式**（3 表专属） | 4 种模式可选：|
| | • **全量比对（三表两两比对）** — 同时比 A-B、A-C、B-C |
| | • **仅比 A vs B（忽略 C）** — 只看前两个表 |
| | • **仅比 A vs C（忽略 B）** — 跳过一个表 |
| | • **仅比 B vs C（忽略 A）** — 另一种两两组合 |

点击 **"开始比对"** 触发后台异步比对，显示细粒度进度：*"准备比对..."* → *"正在规范化数据..."* → *"正在执行表外连接..."* → *"正在计算匹配类型..."* → *"正在计算列差异（五类分类）..."* → *"比对完成"*。

---

### Step 4：查看结果

结果页分两部分：**统计看板** + **明细表格**。

#### 📊 统计看板

顶部一排统计卡片：

| 卡片 | 内容 |
|---|---|
| 总唯一主键数 | 所有文件去重后的总键数 |
| 匹配成功 | 所有表都匹配上的记录数（点击钻取） |
| 仅 A 存在 | 仅在第一个文件中存在的记录数（点击钻取） |
| 仅 B 存在 | 仅在第二个文件中存在的记录数（点击钻取） |
| 仅 C 存在 | 仅在第三个文件中存在的记录数（3 表时显示，点击钻取） |
| A+B（缺 C） | 仅在前两个文件中存在的记录数（3 表时显示） |
| A+C（缺 B） | 仅在第一、三个文件中存在的记录数（3 表时显示） |
| B+C（缺 A） | 仅在第二、三个文件中存在的记录数（3 表时显示） |

卡片下方是 **逐列差异统计表**，采用五类差异分类：

| 列 | 说明 |
|---|---|
| 对比列 | 列名 |
| 三表一致 | 该列在所有表中值相同的记录数（绿色标签，点击钻取） |
| A 表独异 | T1≠T2=T3，仅 A 表不同的记录数（橙色，点击钻取） |
| B 表独异 | T2≠T1=T3，仅 B 表不同的记录数（蓝色，3 表时显示） |
| C 表独异 | T3≠T1=T2，仅 C 表不同的记录数（青色，3 表时显示） |
| 三方互异 | 三个表的值各不相同（红色，3 表时显示） |
| 差异率 | 四类差异总数 / 全部匹配行 × 100% |
| 操作 | **"一键剔除"** — 从明细中隐藏该列；**"恢复"** — 重新显示 |

开关 **"仅显示有差异的列"** 可折叠所有差异数为 0 的列。

**钻取交互：** 点击统计卡片或差异标签自动跳转明细并设置对应过滤器。

#### 🔍 明细表格

基于 **AG Grid** 的虚拟滚动表格，流畅处理万级行数。

**匹配类型过滤器：**

| 场景 | 选项 |
|---|---|
| 基础 | 全部 / 匹配成功 / 仅 A 存在 / 仅 B 存在 |
| 3 表扩展 | 仅 C 存在 / A+B（缺 C）/ A+C（缺 B）/ B+C（缺 A） |

**差异过滤器（五类分类）：**

| 选项 | 含义 |
|---|---|
| 全部 | 不筛选 |
| 三表一致 | 该行所有对比列值完全相同 |
| 值不同（任意） | 该行至少有一列存在任意差异 |
| A 表独异 | A≠B=C 的列差异 |
| B 表独异 | B≠A=C 的列差异（3 表时显示） |
| C 表独异 | C≠A=B 的列差异（3 表时显示） |
| 三方互异 | A≠B≠C 的列差异（3 表时显示） |

**表格特性：**
- 匹配成功 + 差异过滤时切换到**聚焦模式**，仅展示匹配类型、主键和对比列的 A/B/C 表值
- 差异单元格红色高亮，一致单元格绿色高亮
- 分页显示，支持 10/20/50/100 条/页
- 支持按对比列进一步筛选
- 服务端排序与分页

#### 📥 导出

顶部 **导出按钮**，包含多个选项：

| 导出格式 | 说明 |
|---|---|
| **宽表格式** | 当前筛选结果，各表字段并列展示（适合直接查看） |
| **差异明细格式** | 每行一条差异记录，长表结构。两表时含 A/B 值，**三表时含 A/B/C 三表值**（适合进一步分析） |
| **导出原始数据** | 逐个导出各文件的原始数据 |

导出开关 **"包含差异列"**：在宽表导出中追加差异分类列（三表一致 / A 独异 / B 独异 / C 独异 / 三方互异）。

大数据集预估 >10MB 时自动切换流式导出，避免内存溢出。

---

## 技术栈

| 层级 | 技术 | 备注 |
|---|---|---|
| 前端框架 | React 18 + TypeScript + Vite 5 | SPA 单页应用 |
| UI 组件库 | Ant Design 5 + @ant-design/icons | 步骤条、卡片、表格、上传等 |
| 数据表格 | 原生 HTML Table（聚焦模式）/ AG Grid | 差异高亮、虚拟滚动 |
| HTTP | Axios | 上传进度、轮询、10 分钟超时 |
| 后端框架 | Python 3.10+ / FastAPI | 异步路由 |
| 服务器 | Gunicorn + Uvicorn Workers | 默认 2 进程，可扩展，10 分钟超时 |
| 数据处理 | pandas + numpy | 全向量化操作 |
| Excel 读取 | python-calamine → openpyxl → xlrd | 自动降级 |
| Excel 写入 | xlsxwriter → openpyxl | 自动降级 |
| CSV 解析 | 多编码 + 多分隔符自动检测 | 6 种编码 × 4 种分隔符 |
| 存储 | 文件系统 | pickle 会话 + JSON 任务状态 |
| 容器化 | Docker + Docker Compose + Nginx | 前端 Nginx 反向代理，10 分钟超时 |

---

## 快速启动

### 本地开发

**后端（终端 1）：**

```bash
cd backend
pip install -r requirements.txt

# 推荐：安装性能加速依赖
pip install python-calamine xlsxwriter

python -m uvicorn app.main:app --reload --port 8000
```

**前端（终端 2）：**

```bash
cd frontend
npm install
npm run dev
```

访问 [http://localhost:5173](http://localhost:5173)

### Docker 部署

```bash
# 默认 2 个 worker
docker compose up -d

# 自定义 worker 数量
WORKERS=4 docker compose up -d
```

访问 [http://localhost:5173](http://localhost:5173)

> 前端开发服务器自动代理 `/api` 到后端 `127.0.0.1:8000`；Docker 中由 Nginx 反向代理。

---

## 项目结构

```
excel-compare/
├── docker-compose.yml
├── benchmark.py                       # 性能压测工具
├── backend/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── test_comprehensive.py          # 综合测试套件（8 种模式）
│   └── app/
│       ├── main.py                    # FastAPI 入口，CORS，路由注册
│       ├── models.py                  # Pydantic 请求/响应模型
│       ├── routers/
│       │   ├── files.py               # 上传、解析轮询、会话管理、别名
│       │   ├── compare.py             # 主键映射、同步/异步比对、明细查询
│       │   └── export.py              # Excel 导出（内存/流式自动切换）
│       └── services/
│           ├── parser.py              # Excel/CSV 解析服务
│           ├── matcher.py             # 核心比对引擎（DiffEngine）
│           └── exporter.py            # Excel 生成服务
├── frontend/
│   ├── Dockerfile                     # 多阶段：Node 构建 → Nginx
│   ├── nginx.conf                     # 反向代理 + 100MB 上传限制
│   ├── vite.config.ts                 # 开发代理配置
│   └── src/
│       ├── App.tsx                    # 步骤向导状态机
│       ├── api/index.ts              # Axios 客户端
│       ├── types/index.ts            # TypeScript 类型定义
│       └── components/
│           ├── FileUpload/            # 拖拽上传 + 解析轮询 + 别名
│           ├── KeyMapping/            # 联合主键选择
│           ├── ColumnSelect/          # 对比列选择 + 比对模式
│           ├── StatsBoard/            # 统计看板 + 五类差异 + 列剔除
│           ├── DiffTable/             # 明细表格 + 五类过滤器
│           └── ExportBar/             # 导出下拉菜单（宽表/差异明细/原始）
└── README.md
```

---

## API 参考

### 文件与会话

| 方法 | 端点 | 说明 |
|---|---|---|
| `GET` | `/api/files/new-session` | 创建会话，返回 16 位 hex `session_id` |
| `POST` | `/api/files/upload` | 上传文件（流式写盘，后台线程解析） |
| `GET` | `/api/files/parse-status/{session_id}/{file_id}` | 轮询解析进度 |
| `POST` | `/api/files/remove` | 移除已上传文件 |
| `POST` | `/api/files/alias` | 设置文件别名 |
| `GET` | `/api/files/session/{session_id}` | 获取完整会话状态 |

### 比对

| 方法 | 端点 | 说明 |
|---|---|---|
| `POST` | `/api/compare/mapping` | 设置各文件主键列映射 |
| `POST` | `/api/compare/execute` | 同步比对（小文件 OK，大会阻塞） |
| `POST` | `/api/compare/execute-async` | **推荐：** 异步比对，返回 `task_id` 后立即返回 |
| `GET` | `/api/compare/status/{task_id}` | 轮询异步比对进度（progress 0-100） |
| `POST` | `/api/compare/detail` | 分页查询明细（支持 match/diff 过滤器） |

**比对模式（`mode` 参数）：** `all`（全量） / `ab` / `ac` / `bc`

**匹配筛选（`match_filter`）：** `all` / `matched` / `only_a` / `only_b` / `only_c` / `only_a_b` / `only_a_c` / `only_b_c`

**差异筛选（`diff_filter`）：** `all` / `same` / `different` / `value_match`（三表一致）/ `t1_diff`（A 独异）/ `t2_diff`（B 独异）/ `t3_diff`（C 独异）/ `all_diff`（三方互异）

**CompareResponse 结构：**
```json
{
  "session_id": "...",
  "total_keys": 100,
  "matched": 80,
  "only_a": 10,
  "only_b": 7,
  "only_c": 3,
  "column_stats": [
    {
      "column": "价格",
      "value_match": 70,
      "t1_diff": 5,
      "t2_diff": 3,
      "t3_diff": 2,
      "all_diff": 0
    }
  ],
  "match_type_labels": {
    "matched": "✓ 匹配",
    "only_a": "仅 采购单 存在",
    "only_a_b": "采购单+销售单（缺库存单）"
  }
}
```

### 导出

| 方法 | 端点 | 说明 |
|---|---|---|
| `POST` | `/api/export/excel` | 导出比对结果，支持 3 种格式 |

**导出格式（`export_type`）：** `filtered`（宽表）/ `diff_detail`（差异明细，三表时含 C 表值）/ `raw_*`（原始数据）

---

## 测试

所有测试在 `backend/test_comprehensive.py`，从 `backend/` 目录运行：

```bash
# 快速冒烟（30 秒）
python test_comprehensive.py --quick

# 引擎单元测试（无需启动服务）
python test_comprehensive.py --unit

# 集成测试（需运行后端）
python test_comprehensive.py --integration

# 并发测试（5 客户端 × 500 行）
python test_comprehensive.py --concurrency 5

# 跨 Worker 竞态测试
python test_comprehensive.py --cross-worker

# 引擎压力测试（无需 API）
python test_comprehensive.py --stress

# API 负载测试（30 并发）
python test_comprehensive.py --load 30

# 全部测试
python test_comprehensive.py
```

---

## 性能基准

```bash
# 引擎测试：5 万行 × 30 列
python benchmark.py --engine-only --rows 50000 --cols 30

# 完整 API 测试
python benchmark.py --rows 10000 --cols 20

# 并发测试
python benchmark.py --concurrent 10 --rows 5000
```

**性能关键点：**

| 优化项 | 效果 |
|---|---|
| pandas 全向量化计算 | 比逐行遍历快 50-100×，五类差异一次向量化赋值完成 |
| 布尔 Presence 标记 | 3 表匹配类型检测避免遍历所有数据列 |
| python-calamine（Rust） | Excel 读取比 openpyxl 快 5-10× |
| xlsxwriter（C 扩展） | Excel 写入更快 |
| 异步比对 + on_progress 回调 | 不阻塞 HTTP，细粒度进度轮询 |
| AG Grid 虚拟滚动 | 万级行数前端流畅 |

---

## 架构说明

### 多 Worker 进程隔离（重要）

后端通过 Gunicorn + 多 Uvicorn Worker 运行，**进程间不共享内存**：

- `threading.Lock` 仅同一进程内有效
- 所有会话状态通过 pickle 持久化到 `uploads/sessions/*.pickle`
- `get_session()` 同时检查文件 `mtime` 和文件大小，检测其他 Worker 的更新
- 异步比对任务状态通过 JSON 文件存到 `uploads/compare_tasks/`，任意 Worker 可轮询
- 会话 30 分钟无活动自动清理，比对任务文件 1 小时后清理
- **关键原则：修改会话状态后必须调用 `persist_session()`**，否则其他 Worker 看到过期数据

### 请求流程

```
浏览器 ──→ Nginx (:80) ──→ FastAPI (:8000)
  │                            │
  ├─ POST /api/files/upload    ├─ 流式写盘 → 后台线程解析
  ├─ GET  /parse-status        ├─ 读内存 + 磁盘状态
  ├─ POST /api/compare/mapping ├─ 持久化 pickle（含陈旧检查）
  ├─ POST /execute-async       ├─ 线程池执行比对 → JSON 进度
  ├─ GET  /status/{task_id}    ├─ 读 JSON 文件（跨 Worker 可见）
  ├─ POST /api/compare/detail  ├─ result_df 切片分页
  └─ POST /api/export/excel    └─ 流式生成 xlsx
```

### 错误诊断

比对过程中的错误消息区分步骤来源：
- **"设置主键映射失败"** — 发生在 `/api/compare/mapping` 调用阶段
- **"提交比对任务失败"** — 发生在 `/api/compare/execute-async` 调用阶段
- **"比对执行失败"** — 发生在后台比对或轮询阶段

前端会在浏览器控制台（F12 → Console）输出 `[compare]` 前缀的完整错误对象，便于排查。

---

## 配置

| 环境变量 | 说明 | 默认值 |
|---|---|---|
| `WORKERS` | Gunicorn worker 进程数 | `2` |
| `PYTHONUNBUFFERED` | Python 输出不缓冲 | `1`（Docker） |

CORS 允许的来源在 `backend/app/main.py` 中配置。Nginx 上传限制：`client_max_body_size 100m`。

超时配置（均为 10 分钟）：
- **Nginx** `proxy_read_timeout` / `proxy_send_timeout` = `600s`
- **Gunicorn** `--timeout 600`
- **Axios** `timeout: 600_000` ms

---

## 注意事项

1. **无数据库** — 所有状态存储在文件系统。高并发多租户场景建议引入 Redis。
2. **可选依赖** — `python-calamine` 和 `xlsxwriter` 不在 `requirements.txt` 中，手动安装可大幅提升性能。
3. **Docker 上传卷** — 使用命名卷 `uploads`，备份清理需特别处理。
4. **会话过期** — 30 分钟无活动自动清理；比对任务文件 1 小时后清理。
5. **并发限制** — 线程池最多同时执行 2 个比对任务。
6. **CSV 检测** — 自动尝试 6 种编码 × 4 种分隔符的组合。
7. **npm 镜像** — 前端 `.npmrc` 默认指向 npmmirror.com（中国镜像），海外部署请移除。
8. **Docker 镜像加速** — `docker-compose.yml` 注释了国内镜像加速配置。

---

## License

MIT

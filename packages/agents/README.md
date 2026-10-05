# Market Agents

A 股多智能体市场行为推断与状态择时框架 — 从多源行情/资金流数据出发，经 NMF 行为分解、HMM 市场状态识别与仓位优化，完成批量回测与组合分析。

## 功能概览

- **数据管线**：日线、分钟代理、资金流、北向/融资融券等多源拉取与特征工程
- **行为推断**：NMF 分解隐式交易主体活跃度，HMM/规则市场状态分类
- **回测优化**：Walk-forward 仓位映射优化，超额收益与 Sharpe 评估
- **全市场批处理**：4992 只 A 股增量下载与分析，断点续跑
- **PostgreSQL 存储**：行情、特征、分析结果、组合统一入库
- **Web 面板**：前后端分离 — FastAPI REST + 静态前端（ECharts）

## 项目结构

```
market-agents/
├── 后端/                   # 后端 = pip 包 market_platform.agents
│   ├── __init__.py
│   ├── cli.py
│   ├── settings.py
│   ├── 接口/               # REST API
│   ├── 核心/               # 路径、工具、通知、大模型、任务
│   ├── 采集/               # 数据拉取
│   ├── 分析/               # 特征、推断、回测、批处理、仿真
│   ├── 存储/               # PostgreSQL
│   ├── 配置/               # YAML 配置
│   ├── 数据/               # 运行时 CSV 元数据
│   └── 脚本/               # 运维脚本
├── 前端/                   # Vue 3 + Vite SPA
└── 文档/
```

详细规范见 [文档/结构说明.md](文档/结构说明.md)。

## 快速开始

### 环境

项目目录里**不必放 `.venv`**。推荐把 Python 包装在固定位置，项目里只做可编辑安装（`pip install -e`）。

**方式 A：共享虚拟环境（推荐）**

环境放在 `~/.venvs/`，不占项目空间，也不污染系统 Python：

```bash
python3 -m venv ~/.venvs/market-agents
source ~/.venvs/market-agents/bin/activate
cd /path/to/market-agents
pip install -U pip
pip install -e ".[dev,web,postgres]"   # 按需删减可选依赖
```

以后每次开发先 `source ~/.venvs/market-agents/bin/activate`，再进项目目录即可。

**方式 B：用户级安装（无 venv）**

包装进 `~/.local/`，同样不占项目目录：

```bash
pip install --user -U pip
pip install --user -e ".[web,postgres]"
export PATH="$HOME/.local/bin:$PATH"   # 确保能找到 market-agents 命令
```

> Ubuntu 24+ 若提示 *externally-managed-environment*，优先用方式 A；或 `pip install --user` / `pipx`。

**方式 C：项目内 venv（默认文档写法，可选）**

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,web,postgres]"
```

已有项目内 `.venv` 时，确认新环境可用后可删除以释放约 800MB：

```bash
rm -rf .venv market_platform.agents.egg-info .pytest_cache
```

| 可选依赖组 | 用途 |
|------------|------|
| `web` | FastAPI / `market-agents web` |
| `postgres` | PostgreSQL 存储 |
| `network` | `curl_cffi` 全市场拉取 |
| `dev` | pytest、ruff |

### 数据库

```bash
createdb 金融市场数据
market-agents pg init
```

### 单只股票完整管线

```bash
market-agents run --config 后端/配置/配置档/默认.yaml --backtest
```

### 全市场数据下载

```bash
python 后端/脚本/拉取全市场.py --prepare-list
python 后端/脚本/拉取全市场.py --limit 20
```

### 全市场增量分析

```bash
python 后端/脚本/分析全市场.py --status
python 后端/脚本/分析全市场.py --batch-size 20
```

### Web（前后端分离）

```bash
# 终端 1：API
market-agents web

# 终端 2：前端（pnpm，依赖在 ~/.local/share/pnpm/store）
cd 前端 && pnpm install && pnpm run dev
```

详见 [前端/说明.md](前端/说明.md)。

## CLI 常用命令

| 命令 | 说明 |
|------|------|
| `market-agents fetch` | 拉取单票数据 |
| `market-agents run --backtest` | 完整管线 + 回测 |
| `market-agents batch` | 批量分析 |
| `market-agents web` | 启动 API 服务 |
| `market-agents pg init` | 初始化数据库 |

## 许可证

MIT

# Market Predictor

沪深300 **多资产组合系统**：LLM 日频研判 + 量价 Transformer；组合侧为 **统一夏普六袖**（进攻 / 防守 / 现金流），只读 Web 展示回测与模拟盘。

> **免责声明**：本项目仅供研究与学习，历史回测不代表未来收益，不构成任何投资建议。

## 策略怎么理解

**主模式**（`portfolio.allocation_mode: unified_sharpe`）：

> 六袖统一账本：滚动夏普定组权，组内互补轮动；LLM 做 tilt / 敢砍敢加，不是单独「猜涨跌下单」。

| 层 | 做什么 |
|----|--------|
| 进攻 | 沪深300 + 纳指 ETF |
| 防守 | 黄金 + 国债 ETF |
| 现金流 | 货币 ETF + 逆回购 |
| 信号 · LLM | 七维估值/资金、外盘、新闻、状态 → 日频涨/跌/平 |
| 信号 · Transformer | 近 20 日量价序列 → 涨/跌/平概率（量价**不进** LLM Prompt） |
| 执行 | 周频调仓；降换手（EMA / 死区 / 渐进）；全袖口计手续费 |

配置真相源：`后端/配置/配置档/默认.yaml`。Web 策略条与 `策略摘要` API 与此对齐。

兼容旧路径：`allocation_mode: legacy` 时仍为 `core_overlay` 权益仓 + 三层闲置。

## 特性

- **统一夏普六袖** — 组间夏普锚定、组内互补、risk_on / risk_off
- **双通道信号** — LLM 基本面环境；Transformer 量价形态
- **费用与换手** — 可交易袖口按 \|Δw\| 计佣金+滑点；死区/渐进降换手
- **API 缓存** — DeepSeek 响应入库复用
- **只读 Web** — 净值、配置、归因、模拟盘（前端不触发交易）
- **定时更新** — systemd：收盘后增量回测 + 模拟盘

## 环境要求

- Python **3.11+**
- DeepSeek API Key（[platform.deepseek.com](https://platform.deepseek.com)）
- 网络可访问 akshare 数据源
- （可选）CUDA：加速 Transformer 训练/推理

## 快速开始

```bash
git clone https://github.com/zyz-951213/market-predictor.git
cd market-predictor

python -m venv .venv
source .venv/bin/activate

pip install -e .
# 或: pip install -r requirements.txt && pip install -e .

cp .env.example .env   # 填入 DEEPSEEK_API_KEY

market-predictor pipeline -- --step data
market-predictor pipeline -- --step train --model transformer   # 量价通道
market-predictor pipeline -- --step backtest --model llm

market-predictor web
cd 前端 && npm install && npm run dev
```

## 常用命令

| 命令 | 说明 |
|------|------|
| `market-predictor web` | 启动 FastAPI（只读 API，默认 `18765`） |
| `market-predictor pipeline -- --step data` | 拉数 / 特征（含 Transformer 衍生列） |
| `market-predictor pipeline -- --step train --model transformer` | 训练量价 Transformer |
| `market-predictor pipeline -- --step backtest --model llm` | LLM + 六袖主路径回测 |
| `python -m market_platform.predictor.脚本.今日研判` | 最新一期研判 |
| `market-predictor paper --once` | 模拟盘单次 |
| `market-predictor update --once` | 自动更新单次（**仅增量 LLM**，禁止定时全量） |
| `pytest -q` | 跑单测 |

## 配置块

| 块 | 作用 |
|----|------|
| `portfolio.unified_sharpe` | 六袖锚定、组内互补、降换手、risk_on/off |
| `backtest` | 佣金/滑点；legacy 仓位规则仍可读 |
| `model.llm` | DeepSeek、Prompt（无量价）、校准 |
| `model.transformer` | 量价序列模型（`enabled` + checkpoint） |
| `paper_trading` / `auto_update` | 模拟盘与周五/工作日收盘流水线 |
| `evaluation` | 主 KPI 样本外起点 |

## 项目结构

```
market-predictor/
├── 后端/                 # pip 包 market_platform.predictor
│   ├── 命令行.py
│   ├── 接口/ · 采集/ · 分析/ · 配置/ · 脚本/ · 存储/
│   ├── 模型/检查点/ · 数据/ · 日志/
├── 前端/源码/页面/       # 概览 · 回测 · 模拟盘 · 归因
├── deploy/systemd/       # API / 前端 / 更新 / 模拟盘
├── tests/                # 单测
└── pyproject.toml
```

## 前后端与部署

| 模式 | 说明 |
|------|------|
| 开发 | 后端 `18765` + Vite（见 `.env` / `FRONTEND_PORT`），代理 `/api` |
| 生产 | Nginx 托管 `前端/dist/`，反代 `/api`；或 `SERVE_FRONTEND=1` |
| systemd | `sudo ./deploy/install-systemd.sh` 或用户级单元（见 `deploy/`） |

详见 [前端/说明.md](前端/说明.md)。

## License

[MIT](LICENSE) — 发布前请阅读 [SECURITY.md](SECURITY.md)。

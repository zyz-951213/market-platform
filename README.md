# market-platform

统一 A 股量化平台:**一个仓库、一个数据库、一个站点、一个 CLI**。

| 层 | 内容 | 位置 |
|----|------|------|
| 数据底座 | 采集/增量入库/水位/DDL,只读查询台 | `src/market_platform/data/` |
| 行为分析 | NMF 行为分解 → 市场状态 → 仓位/回测/模拟盘 | `src/market_platform/agents/` |
| 指数预测 | Transformer 概率 + LLM 研判 → 六袖统一夏普 | `src/market_platform/predictor/` |
| 共享内核 | 路径/配置/存储/日志/静态托管 | `src/market_platform/core/` |
| 统一门户 | 一个服务托管站点 + 三域 API | `src/market_platform/门户/` |
| 统一站点 | 站点壳 + 三域子应用(单次构建) | `web/` |

## 快速开始

```bash
pip install -e ".[dev,postgres]"     # 安装(editable;src 布局)
cd web && node 构建.mjs              # 构建统一站点 → web/dist/

market-platform 健康                  # 三域配置与库连通性
market-platform web                  # 启动统一门户 :18800
```

站点入口 `http://127.0.0.1:18800/`,三域分别在 `/数据底座/`、`/行为分析/`、`/指数预测/`,
接口在 `/<域>/api/*`。

## 统一 CLI

```bash
market-platform web                     # 统一站点 + 三域 API(门户)
market-platform data  刷新全库水位        # 数据底座
market-platform agents run --backtest    # 行为分析
market-platform predictor web            # 指数预测
market-platform 健康                      # 配置与连通性一览
```

兼容旧入口:`market-agents`、`market-predictor`、`bin/market`。

## 目录约定

```
config/         唯一配置根:数据库.yaml(唯一 DSN 真相)+ <域>/配置档/ + .env
schema/         DDL、迁移/、命名说明.md(库命名/口径唯一权威)
src/market_platform/   代码(包目录只放代码)
web/            统一站点(模块/<域>/ 为三个子应用)
deploy/         systemd 单元 + 云端推送脚本 + 安装服务.sh
docs/           架构地图、逐域文档、任务卡、评审报告
tests/          统一测试根(平台契约 + 各域)
数据/ 输出/ 模型/ 日志/   运行态(不入库,按域分子目录)
packages/       迁移兼容层(可删,见 packages/README.md)
```

## 数据库

单库「金融市场数据」,三层 schema:

- `公共`(原始物理表 + 跨项目只读视图)、`行情增量`(水位/缺口/更新控制台)、
  项目层(`行为分析`、`指数预测`)
- search_path:行为分析 `行为分析, 公共, 行情增量`;指数预测 `指数预测, 公共, 行情增量`;
  数据底座直写 `公共.*`
- 口径/命名以 `schema/命名说明.md` 为准;在线 Web **只读**,写入只走 CLI 或显式开启写 API

## 文档

| 文档 | 内容 |
|------|------|
| `AGENTS.md` | 工程宪法(不变量,动手前必读) |
| `docs/ARCHITECTURE.md` | 架构地图与指针 |
| `docs/分析报告.md` | 全仓评审结论与整改清单 |
| `docs/tasks/TASK-*.md` | 任务卡 |
| `docs/<域>/` | 各域 README / 局部契约 / 系统说明 |
| `deploy/README.md` | 部署地图 |
| `web/README.md` | 统一站点构建与开发 |

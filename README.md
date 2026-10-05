# market-platform

统一 A 股量化平台：数据底座 + 行为分析 + 指数预测，三个业务模块共用一个
PostgreSQL 数据库「金融市场数据」。

## 目录

```
packages/
  data/       数据底座（原 database）：公共/行情增量查看、更新、DDL、水位
  agents/     行为分析（原 market-agents）：NMF/HMM 状态择时
  predictor/  指数预测（原 market-predictor）：LLM + Transformer 六袖组合
web/          统一前端（待合并）
schema/       数据库 DDL（待从 data 迁入）
deploy/       systemd / 部署（待统一）
config/       统一配置（待合并）
docs/         文档
```

## 统一入口

```bash
market agents <命令>         # 委托 market-agents
market predictor <命令>      # 委托 market-predictor
market data <脚本> [参数]    # 运行数据底座脚本
```

## 数据库

单库「金融市场数据」，schema 收敛为：`公共` + `行情增量` + 项目层
（`行为分析` / `指数预测`）。

- `market-agents` search_path：`行为分析, 公共, 行情增量`
- `market-predictor` search_path：`指数预测, 公共, 行情增量`

# ARCHITECTURE.md — market-platform 架构地图

> 顶层索引,定位用。**只读你需要的条目,不要读全库。** 细节在各自文档,这里只给指针。
> 不变量在根 `AGENTS.md`;命名/口径以 `packages/data/schema/命名说明.md` 为准(唯一权威)。

## 包
| 包 | 职责 | search_path | 契约/权威文档 |
|----|------|-------------|----------------|
| `data` | 数据底座:采集/更新/DDL/水位,在线只读查看 | 直接写 `公共.*` | `packages/data/schema/命名说明.md`(权威) |
| `agents` | 行为分析:NMF→HMM 状态择时 + 回测 | `行为分析, 公共, 行情增量` | `packages/agents/文档/架构说明.md` |
| `predictor` | 指数预测:LLM + Transformer 六袖组合 | `指数预测, 公共, 行情增量` | `packages/predictor/文档/系统说明.md` |

## 服务与端口
| 服务 | 端口 | 包 |
|------|------|----|
| `database-api` | 18766 | data |
| `database-frontend` | 19081 | data |
| `market-predictor-api` | 18765 | predictor |

统一前端 `web/` 待合并;`deploy/` `config/` `schema/` 待统一(见根 README)。

## 数据库
单库「金融市场数据」,三层 schema 见根 AGENTS.md §1。日行情切分、水位、DDL 顺序见 `schema/命名说明.md`。

## 数据流
- **agents**:多源行情/资金流 → 特征 → NMF 行为分解 → 状态特征 → HMM 市场状态 → 仓位优化 → 回测 → 残差/LLM 解释
- **predictor**:采集/特征 →(TF)`tf_proba_*` → LLM 研判(量价不进 Prompt)→ 六袖 `unified_sharpe` → 费用 → 只读 Web

## 重构中(待完成)
- `web/` 统一前端;`schema/` 从 data 迁入;`deploy/` systemd 统一;`config/` 合并。
- 每个包补一份 `packages/*/AGENTS.md` 写局部契约。

## 维护
- 本文件随架构变;**不变量**在根 AGENTS.md,不在此处。
- 命名/口径裁决 = `packages/data/schema/命名说明.md`。

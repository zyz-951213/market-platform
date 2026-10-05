# ARCHITECTURE.md — market-platform 架构地图

> 顶层索引,定位用。**只读你需要的条目,不要读全库。** 细节在各自文档,这里只给指针。
> 不变量在根 `AGENTS.md`;命名/口径以 `schema/命名说明.md` 为准(唯一权威)。

## 一、代码布局(唯一结构)

| 路径 | 职责 | 契约/权威文档 |
|------|------|----------------|
| `src/market_platform/core/` | 共享内核:路径/配置/存储/静态托管 | 根 `AGENTS.md` §2 |
| `src/market_platform/data/` | 数据底座:采集/更新/水位/DDL,只读查询台 | `docs/数据底座/AGENTS.md`、`schema/命名说明.md` |
| `src/market_platform/agents/` | 行为分析:NMF→状态→仓位/回测/模拟盘 | `docs/行为分析/AGENTS.md`、`docs/行为分析/架构说明.md` |
| `src/market_platform/predictor/` | 指数预测:TF 概率 + LLM → 六袖统一夏普 | `docs/指数预测/AGENTS.md`、`docs/指数预测/系统说明.md` |
| `src/market_platform/门户/` | 统一门户:站点 + 三域 API 单端口 | `deploy/README.md` |
| `src/market_platform/cli.py` | 统一 CLI:`market-platform {web,data,agents,predictor,健康}` | 本文件 §四 |

配置唯一根 `config/`;DDL 与迁移在 `schema/`;站点在 `web/`;部署在 `deploy/`;测试在 `tests/`。

## 二、服务与端口

| 服务 | 端口 | 入口 | 说明 |
|------|------|------|------|
| **统一门户** | **18800** | `market-platform web` | `/` 站点壳 + `/<域>/` 页面 + `/<域>/api/*` 三域 API |
| 数据底座 API | 18766 | `market_platform.data.接口.应用:app` | 调试用独立端口 |
| 行为分析 API | 8766 | `market-agents web` | 调试用独立端口 |
| 指数预测 API | 18765 | `market-predictor web` | 调试用独立端口 |

已退役:三套 Vite preview 前端服务(:19081 / :8080 / :19080)与其 systemd 单元。

## 三、数据流

- **数据底座**:数据源(baostock/akshare)→ `数据更新器_*` → `公共.*`(幂等 upsert)
  → `行情增量.同步水位` / `数据集登记`;Web 只读查询(连接级 `default_transaction_read_only`)。
- **行为分析**:采集 → `分析/特征` → `分析/推断`(NMF → 状态特征 → GMM 状态)→
  `分析/回测`(仓位网格/风控)→ `分析/模拟盘` → `接口` + `核心/通知`。
- **指数预测**:采集/特征 → `tf_proba_*` → LLM 研判(量价不进 Prompt)→ 六袖 `unified_sharpe`
  → 费用与换手 → 只读 Web。

## 四、统一 CLI 与契约

```bash
market-platform web | data <命令> | agents <命令> | predictor <命令> | 健康
```

公共契约(重构后必须保持):

- 可导入模块路径:`market_platform.{cli,门户.应用,data.接口.应用,agents.接口.应用,predictor.接口.应用}`
- 三个应用工厂签名:`create_app(*, serve_frontend=None, static_dir=None)`(行为分析另有
  `enable_scheduler`:门户内传 False,避免重复调度)
- 配置:`config/数据库.yaml`(连接/search_path 唯一真相)、`config/<域>/配置档/*.yaml`、`config/.env`
- 路径:`market_platform.core.路径` 是唯一根解析入口
- 站点路由:`/`、`/数据底座/`、`/行为分析/`、`/指数预测/`、`/<域>/api/*`、`/健康`

## 五、数据库

单库「金融市场数据」,三层 schema(公共 / 行情增量 / 项目层)。
日行情切分、水位、DDL 顺序见 `schema/命名说明.md`;迁移走
`schema/迁移/NNN_*.sql` + `python3 schema/应用迁移.py`(记录表 `行情增量.schema迁移`)。

## 六、评审与整改

- `docs/分析报告.md`:全仓评审结论(已修 / 遗留),遗留项按 TASK-003 处理。
- 重构任务卡:`docs/tasks/TASK-001.md`(内嵌仓折叠,已完成)、`TASK-002.md`(单库归一,已完成)。

## 七、维护

- 架构变则更新本文件;**不变量**在根 `AGENTS.md`,不在此处。
- 命名/口径裁决 = `schema/命名说明.md`。

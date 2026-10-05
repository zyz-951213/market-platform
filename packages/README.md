# packages/ — 迁移兼容层(可删)

本目录**不再包含任何源码**。三个域的原独立项目已于本次重构全部并入仓库根的统一结构:

| 原位置 | 现位置 |
|--------|--------|
| `packages/data/schema/` | `schema/`(DDL、`迁移/`、`命名说明.md`) |
| `packages/*/deploy/` | `deploy/{数据底座,指数预测,行为分析}/` |
| `packages/*/前端/` | `web/模块/{数据底座,行为分析,指数预测}/` |
| `packages/agents/scripts/` | `src/market_platform/agents/脚本/运维/` |
| `packages/predictor/tests/` | `tests/指数预测/` |
| `packages/*/{README,AGENTS,文档}/` | `docs/{数据底座,行为分析,指数预测}/` |
| 三域的 Python 代码 | `src/market_platform/{data,agents,predictor}/`(本次之前已迁入) |

## 为什么还留着目录

机器上**已安装的 systemd 用户单元**仍指向旧路径(通过 `systemctl --user cat` 可查),
在重装单元之前,必须让这些路径继续存在:

| 兼容项 | 为什么 |
|--------|--------|
| `packages/data/前端 → ../../web/模块/数据底座` | 旧 `database-frontend.service` 的 `WorkingDirectory` |
| `packages/data/.env`、`packages/predictor/.env` → `config/.env` | 旧单元的 `EnvironmentFile` |
| `packages/data/logs/` | 旧 `database-api/frontend.service` 的日志落盘目录(服务仍在运行) |
| `packages/{agents,predictor}/` 空目录 | 旧单元的 `WorkingDirectory` |

## 什么时候可以删

```bash
./deploy/安装服务.sh --all                                  # 安装新单元(路径指向新结构)
systemctl --user disable --now database-frontend.service \
  market-agents-frontend.service market-predictor-frontend.service
rm -rf packages/                                            # 全部兼容项不再需要
```

删除后若发现单元报错,执行 `systemctl --user cat <单元>` 核对是否仍引用 `packages/`。

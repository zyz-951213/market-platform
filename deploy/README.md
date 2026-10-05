# deploy — 部署地图

> 服务拓扑、单元文件与安装方式。**架构不变量见根 `AGENTS.md`,细节见 `docs/ARCHITECTURE.md`。**

## 一、统一门户(推荐,默认)

**一个服务、一个端口**同时托管统一站点与三域 API:

| 路径 | 内容 |
|------|------|
| `/` | 统一站点壳(`web/dist/index.html`) |
| `/数据底座/` `/数据底座/api/*` | 数据底座页面与 API |
| `/行为分析/` `/行为分析/api/*` | 行为分析页面与 API |
| `/指数预测/` `/指数预测/api/*` | 指数预测页面与 API |
| `/健康` | 三域装配状态 |

- 单元:`deploy/systemd/市场平台门户.service`(默认 `:18800`,可用 `门户_PORT` 覆盖)
- 目录约定:运行态数据/输出/模型/日志在仓库根对应目录,包目录只放代码，配置唯一在 `config/`。

## 二、三域独立端口(保留,便于单独调试)

| 服务 | 端口 | 单元 |
|------|------|------|
| 数据底座 API | 18766 | `数据底座/systemd/database-api.service` |
| 行为分析 API | 8766 | `行为分析/systemd/market-agents-api.service` |
| 指数预测 API | 18765 | `指数预测/systemd/market-predictor-api.service` |

三者与门户共用同一套代码与配置,可同时运行;门户内不启动行为分析的内置调度线程,
避免同一任务被多进程重复触发。

## 三、定时任务

| 单元 | 计划 | 说明 |
|------|------|------|
| `数据底座/systemd/数据更新.timer` | 周一~五 07:00 | 数据底座增量更新(幂等 upsert),**默认不启用** |
| `指数预测/systemd/market-predictor-update.timer` | 周五 16:45 | 指数预测增量更新(默认禁全量) |
| `指数预测/systemd/market-predictor-paper.timer` | 周五 16:30 | 指数预测模拟盘决策 |

## 四、安装

```bash
./deploy/安装服务.sh            # 装统一门户
./deploy/安装服务.sh --all      # 另装三域独立端口服务与定时器
./deploy/安装服务.sh --dry-run  # 只打印动作
```

单元模板里的 `@仓库根@` / `@PYTHON@` 由脚本替换为真实路径,换机器无需手改。

## 五、已退役

| 旧单元 | 去向 |
|--------|------|
| `database-frontend.service`(Vite preview :19081) | 由统一门户取代 |
| `market-predictor-frontend.service`(Vite preview :19080) | 由统一门户取代 |
| `market-agents-frontend.service`(Vite preview :8080) | 由统一门户取代 |
| 单包 `install-systemd.sh` / `install-user-systemd.sh` | 由 `安装服务.sh` 取代 |

## 六、云端推送(数据底座)

`数据底座/推送增量.sh`、`验证一致性.sh`、`迁入研究库.sh`、`扫描行情缺口.sh` 保留原用途。

- 凭据:**一律通过环境变量 `SSHPASS` 传入**,不要写进单元文件或提交进库。
- `推送增量.sh` 会对远端镜像库做窗口 `DELETE`+`COPY`/`TRUNCATE`;本地生产库**禁止**此类操作
  (父表必须 upsert,见根 `AGENTS.md` §3)。
- 推送后跑 `验证一致性.sh`。

## 七、已知约束

- `core.路径` 以仓库根为基准:非 editable 安装(wheel)需要设置 `MARKET_PLATFORM_ROOT`
  指向含 `config/` 与 `web/dist/` 的检出目录。
- 统一站点需先构建:`cd web && node 构建.mjs`(产物 `web/dist/`,不入库)。

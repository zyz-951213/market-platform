# AGENTS.md — market-platform 工程宪法

> 本文件是仓库的**不变量**。dsh / Codex 每轮自动加载,必须遵守。
> 动手前先读:本文件 + 目标域的 `docs/<域>/AGENTS.md` + `docs/ARCHITECTURE.md` 对应条目。
> 只改任务卡范围内的文件;要越界先停下问。

## 1. 分层与依赖(单向,不可逆)

- DB 三层:`公共`(原始物理表) → `行情增量`(水位/缺口) → 项目层
  (`行为分析` / `指数预测` / `隐因子` / `微盘股研究` / `特别处理研究`)。
- 依赖单向:项目层可读公共;**公共绝不可依赖任何项目层**。
- 代码按域隔离:`src/market_platform/{data,agents,predictor}`,靠 schema(search_path)与 API 隔离;
  跨域共享能力**只放** `src/market_platform/core/`,禁止域间直接 import。
  (已知违例:预测域分析层 import 接口层,见 `docs/分析报告.md` 遗留清单。)
- 前端只依赖后端 API,不 import 后端实现;`core/` 不依赖任何渲染库。

## 2. 单一事实来源(冲突裁决)

- 库命名/口径/DDL 顺序唯一权威 = `schema/命名说明.md`;与之冲突的注释/README/记忆一律以它为准。
- 配置唯一根 = `config/`:`config/数据库.yaml` 是连接与 search_path 的**唯一真相**;
  各域配置档在 `config/<域>/配置档/`;环境变量在 `config/.env`。
- 路径唯一入口 = `market_platform.core.路径`(仓库根/配置/站点/运行态)。
  任何模块**不得**再用 `Path(__file__).parents[n]` 自行推导仓库结构。
- 改配置后**必须重启服务**(存在进程内缓存)。
- 日行情两套口径**不可混**:`公共.日行情`(研究视图) ≠ `行为分析.日行情`(应用瘦表)。

## 3. 读写纪律

- 线上 Web **只读**;写入只走 CLI(`market-platform data 更新金融市场数据`);
  写 API 由 `UPDATE_API_ENABLED` 控制,**默认关**。
- 更新**幂等 + 增量**;父表(复合主键)用 upsert,**禁止 DELETE+COPY**(会级联删子表);默认禁止全量。
- 派生结果必须可从源重建;脚本重跑结果不变。
- schema 变更走 `schema/迁移/NNN_*.sql` + `python3 schema/应用迁移.py`;`schema/archive/` **勿对生产执行**。
- **脚本必须带 `if __name__ == "__main__"` 守卫**:历史上 `data/脚本/更新金融市场数据.py`
  在模块顶层直接跑 22 项生产更新,任何 `import`/pytest 收集都会写库。

## 4. 命名与数据源

- 中文命名为主(schema/表/字段/目录);代码文件沿用中文名 + 英文别名 shim
  (如 `接口/__init__.py` 里 `from . import 持仓 as holdings`),对外接口用英文别名视图
  (`daily_bars`/`mc_daily`/`st_daily`)。
- **路由路径参数名必须是 ASCII**:Starlette 不识别 `{中文:path}`(会退化为字面量路由)。
- 股票代码统一 6 位;行业权威 = 中信证券行业分类 2.0 修订版,回退链:
  东方财富 → baostock 证监会 → 申万一级。
- **数值列单位与缺失是硬约束**(2026-10-06 两类真实事故的根因):
  涨跌幅/收益率一律**小数**(`-0.0288` = -2.88%),源接口给百分数时必须用
  `数据更新器._pct_to_ratio()` 换算(baostock `pctChg` 就是百分数,曾让 47 个交易日
  放大 100 倍);**禁止 NaN/±Inf 入库** —— PostgreSQL `numeric` 能存 `'NaN'`,而
  `avg/sum` 会把它沿整段传播(`IS NULL` 查不出来,必须 `col = 'NaN'::numeric`)。
  写入侧统一走 `_to_n()` / `_insert()`;改完跑 `market-platform data 数据体检`。

## 5. 运行态与配置边界

- 包目录(`src/market_platform/**`)**只放代码**;数据/输出/模型/日志在仓库根
  `数据/ 输出/ 模型/ 日志/`,按域分子目录,不入库。
- 站点产物 `web/dist/` 不入库,由 `cd web && node 构建.mjs` 生成。
- 部署拓扑:统一门户(单端口 `:18800`)托管站点与三域 API;三域独立端口服务保留用于调试。
  门户内**不启动**行为分析内置调度线程,定时任务只由 systemd timer / CLI 单点触发。

## 6. 环境坑

- dsh 沙箱 `/tmp` 每次调用重建:跨调用复用的中间产物写**仓库内相对路径**,别用 /tmp。
- npm 缓存位于工作区外、沙箱内无法联网安装前端依赖:构建统一站点请复用
  `web/模块/*/node_modules`(`web/构建.mjs` 已直接调用其中的 vite)。
- 复合命令会被拒;大命令拆小步逐条执行。

## 7. 每个任务固定流程

1. 领任务卡 `docs/tasks/TASK-xxx.md`:范围 / 契约 / 验收 / 禁区。
2. **先复述任务 + 列出打算读的文件,等确认再改代码**(控制上下文,别全库乱翻)。
3. 契约(接口/表结构/search_path/路径)变了 → 回写本文件或 `docs/ARCHITECTURE.md`。
4. 验收必须可量化;跑不过不许声称完成。
5. 每阶段一个 commit,便于 `git reset --hard` 回滚。

## 8. 质量门(合入前必跑)

```bash
ruff check src/market_platform tests     # 必须零告警
pytest tests --no-header                 # 必须全绿
node web/构建.mjs                        # 站点可构建(改动前端/门户时)
market-platform 健康                      # 三域 search_path 与连通性
market-platform data 数据体检             # 涨跌幅量纲 / NaN 存量 / 市场收益计算段(改动数据层时)
```

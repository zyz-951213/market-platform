# TASK-002 — market-platform 单库归一重构(三站合一 + 去冗余 + 中文规范化)

状态: 进行中(自主执行,授权来自用户在会话中的直接指令)
授权原话:「把三个网站最好归并成一个项目,代码冗余和合成,规范化项目尽量中文目录命名,以一个二十年软件架构师去好好重构项目,自己执行就行直到完成完美交付」

## 目标(可验收的终局形态)

```
market-platform/
├─ AGENTS.md            宪法(回写:目录/路径/契约)
├─ README.md            一页看懂:一个仓库、一个库、一个前端、一个 CLI
├─ pyproject.toml       单包 + pytest/ruff 配置 + 完整 package-data
├─ config/             ★唯一配置根:数据库.yaml(唯一 DSN 真相)+ 配置档/*.yaml + .env(.example)
├─ schema/             ★DDL 与迁移(自 packages/data 迁入):迁移/NNN_*.sql + 应用迁移.py
├─ deploy/             ★systemd/ cpolar/ 运维脚本(自三包合并)
├─ docs/               ARCHITECTURE.md / 分析报告.md / tasks/
├─ src/market_platform/
│  ├─ core/            ★共享内核:路径.py 配置.py 存储.py 日志.py(唯一实现)
│  ├─ data/  agents/  predictor/   三域(中文目录保留)
│  ├─ web/             ★统一站点后端:网关,把三域 API 挂到一个端口
│  └─ cli.py           统一 CLI:market {data|agents|predictor|web}
├─ web/                ★统一前端(一个站点,三域路由,单代理)
├─ tests/              ★统一测试根
└─ packages/           退役(内容归并完毕后删除)
```

## 不变量(重构期间禁止破坏)

1. **部署契约**:`src/` 必须在 `sys.path`(editable `.pth`);入口点 `market_platform.cli:main`、
   `market_platform.agents.cli:main`、`market_platform.predictor.命令行:main`;
   systemd 现役 `market_platform.data.接口.应用:app`、`market_platform.predictor.cli`、`market-agents web` 必须仍可 import。
2. **线上服务**:`database-api:18766`、`database-frontend:19081`、`market-platform-portal:8000` 不中断;
   重构期间不重启、不停止现役服务(改动在下次重启生效)。
3. **生产数据库**:默认**只读**。禁止对 `公共.*` 写入(唯一例外:经用户同意后重扫水位);
   禁止执行 `schema/archive/`。
4. **可回滚**:每个阶段以独立 commit 落库;阶段验收不过则 `git reset --hard <锚点>`。

## 阶段与验收(每阶段必须给出可执行证据)

| 阶段 | 内容 | 验收证据 |
|------|------|----------|
| P1 | 折叠 `packages/{agents,predictor}` 内嵌 git 仓 | `git ls-files packages/agents packages/predictor` 有输出;`git status` 无 embedded repo 警告 |
| P1.5 | 消除"导入即执行"隐患 + 修 F821 必崩脚本 | `python -c "import market_platform.data.脚本.更新金融市场数据"` 不触发任何更新 |
| P2 | 目录归并:`schema/ deploy/ docs/ web/ tests/` 就位,`packages/` 退役 | `packages/` 不存在;`git ls-files` 三域文档/前端/schema 均在新位置 |
| P3 | 唯一配置根 + 路径内核 | 全仓无 `后端/` 路径引用;`core/路径.py` 为唯一根解析;`.env` 只解析仓库根 |
| P4 | 三站合一前端 | `web/` 单站点可构建;三域路由(数据底座/行为分析/指数预测)+ 单代理配置 |
| P5 | 去重与中文化 | 跨域重复文件清单为 0(共享部分在 `core/`);统一 CLI 四子命令可用 |
| P6 | 质量门 | `pytest` 全绿;`ruff check` 通过;CI 可跑;wheel 含配置/资源 |
| P7 | 交付验证 | 三域应用 import 通过 + 前端构建产物存在 + 服务冒烟 + 文档回写 + 验收报告 |

## 非目标(本轮不做)

- 不改业务算法(前视/口径问题只记录在 `docs/分析报告.md`,另开 TASK-003 处理)。
- 不重写前端页面业务逻辑(只做站点归并、路由、代理)。
- 不动生产库数据(除水位重扫需用户确认)。

## 风险与对策

| 风险 | 对策 |
|------|------|
| 目录大搬家导致 import 断裂 | 搬移后立刻跑「三域应用 import + 现有测试」,失败即回滚 |
| systemd 入口路径变化 | 入口模块路径视为公共契约,不改名;确需改则同步更新已安装 unit |
| 前端三套技术栈(Vue3×2 + 原生 JS×1) | 统一站点用 Vue3 + Vite;原生 JS 页面作为静态子目录挂载,先合并没后重写 |
| 误触发数据更新 | 所有脚本先加 `if __name__ == "__main__"` 守卫,再做任何 import 扫描 |

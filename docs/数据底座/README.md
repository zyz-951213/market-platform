# 金融市场数据管理系统

在线**只读**查看 PostgreSQL「金融市场数据」库。

公网：https://your-tunnel-2.vip.cpolar.cn

## 库架构

统一库已收敛为三层：`公共` + `行情增量` + 五个项目 schema。命名、search_path、日行情切分与 DDL 顺序见：

- **[schema/命名说明.md](schema/命名说明.md)**（权威）
- 历史 DDL：`schema/archive/`（勿对生产再执行）
- 编号迁移：`python3 schema/应用迁移.py`

## 功能

- **概览**（含水位滞后摘要）/ **表结构** / **查询**（条件查询 + 只读 SQL）
- 增删改、数据更新已禁用（写入走 CLI：`后端/脚本/更新金融市场数据.py`）

## 后台服务（systemd --user）

```bash
# 安装并启动
./deploy/install-user-systemd.sh

# 常用命令
systemctl --user status database-api database-frontend
systemctl --user restart database-api database-frontend
journalctl --user -u database-api -f
```

| 服务 | 端口 |
|------|------|
| `database-api` | `18766` |
| `database-frontend`（Vite Preview，代理 `/api`） | `19081` |
| cpolar 隧道 `your-tunnel-2` | → `19081` |

## 本地开发

```bash
# API
python -m uvicorn 后端.接口.应用:app --host 127.0.0.1 --port 18766

# 前端
cd 前端 && npm run dev
```

环境变量见 `.env.example`。

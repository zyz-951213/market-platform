# 指数预测 局部契约

> 上级:根 `AGENTS.md`。细节:`docs/指数预测/{系统说明,安全说明}.md`。

- 代码位置:`src/market_platform/predictor/`(采集 / 分析 / 接口 / 存储 / 脚本 / 核心)。
- search_path:`指数预测, 公共, 行情增量`(来自 `config/数据库.yaml`)。
- 配置真相源:`config/指数预测/配置档/默认.yaml`;有内存缓存 `get_config()` →
  **改配置必重启**;`本地.yaml` 可作本地覆盖(不入库)。
- 运行态:数据 `数据/指数预测`,模型 `模型/指数预测/检查点`,日志 `日志/指数预测`。
- 切决策频率(周↔日)要同步改三处:`task_instruction` / `api_system` / `api_system_weekly`
  (内嵌"本周/随后5日/下一交易日"等词)。
- 服务:统一门户 `:18800`(推荐);独立端口 `:18765`(调试);
  `deploy/指数预测/systemd/market-predictor-update.timer` 周五 16:45 增量(默认禁全量)。
- 回测图 `stale=true` 会触发 `.stale-overlay` 遮罩;去掉 toggle 才显示有效缓存。
- 量价**不进** LLM Prompt(六袖组合里 LLM 只做组内 tilt + risk_on/off)。
- 已知口径问题(待 TASK-003):`tf_proba_*` 全样本内样本推理、cycle 切分泄漏、宏观发布滞后、
  unified 静默回退 legacy、unified 主路径风控失效。

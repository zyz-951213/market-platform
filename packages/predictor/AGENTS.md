# predictor 局部契约

> 上级:根 `AGENTS.md`。细节见 `文档/系统说明.md`、`文档/架构说明.md`。

- search_path:`指数预测, 公共, 行情增量`
- 配置真相源:`后端/配置/配置档/默认.yaml`;有内存缓存 `get_config()` → **改配置必重启**(先 `lsof -ti:18765 | xargs kill -9`)。
- 切决策频率(周↔日)要同步改三处:`task_instruction` / `api_system` / `api_system_weekly`(内嵌"本周/随后5日/下一交易日"等词)。
- 服务:api `:18765`;frontend;`update.timer` 周五 16:45 增量(默认禁全量)。
- 回测图 `stale=true` 会触发 `.stale-overlay` 遮罩;去掉 toggle 才显示有效缓存。
- 量价**不进** LLM Prompt(六袖组合里 LLM 只做组内 tilt + risk_on/off)。

# 数据底座 局部契约

> 上级:根 `AGENTS.md`。命名/口径权威:`schema/命名说明.md`。细节:`docs/数据底座/README.md`。

- 代码位置:`src/market_platform/data/`(接口 / 存储 / 脚本 / 核心)。
- 在线 Web **只读**(连接级 `default_transaction_read_only=on`);写入只走
  `market-platform data 更新金融市场数据`;写 API 由 `UPDATE_API_ENABLED` 控制,**默认关**。
- 更新**幂等 + 增量**;父表(复合主键)upsert,**禁止 DELETE+COPY**(会 CASCADE 删已推子表);
  大表 date 增量窗先删再 COPY(仅限远端镜像库);推送后跑 `deploy/数据底座/验证一致性.sh`。
- schema 变更走 `schema/迁移/NNN_*.sql` + `python3 schema/应用迁移.py`
  (记录表 `行情增量.schema迁移`);`schema/archive/` **勿对生产执行**。
- 服务:统一门户 `:18800`(推荐);独立端口 `:18766`(调试)。
  定时:`deploy/数据底座/systemd/数据更新.timer`(**默认未启用**,启用前确认上游数据源)。
- 水位:`行情增量.同步水位` / `数据集登记` / `水位总览`;重扫
  `market-platform data 刷新全库水位`。
- 脚本一律带 `if __name__ == "__main__"` 守卫(导入不得触发写库)。

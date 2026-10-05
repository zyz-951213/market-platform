# data 局部契约

> 上级:根 `AGENTS.md`。权威:`schema/命名说明.md`(库命名/口径/DDL 顺序/水位)。

- 在线 Web **只读**;写入只走 `后端/脚本/更新金融市场数据.py`;写 API 默认关。
- 更新**幂等 + 增量**;父表(复合主键)upsert,**禁止 DELETE+COPY**(会 CASCADE 删已推子表);大表 date 增量窗先删再 COPY;推送后跑 `验证一致性.sh`。
- schema 变更走 `schema/迁移/NNN_*.sql` + `python3 schema/应用迁移.py`;`schema/archive/` **勿对生产执行**。
- 服务:`database-api` `:18766`;`database-frontend` `:19081`。
- 水位:`行情增量.同步水位`/`数据集登记`;重扫 `后端/脚本/刷新全库水位.py`。

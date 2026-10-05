-- 轻量 schema 迁移记录（幂等）
CREATE TABLE IF NOT EXISTS 行情增量.schema迁移 (
  版本 text PRIMARY KEY,
  说明 text,
  应用时间 timestamp without time zone NOT NULL DEFAULT now()
);

COMMENT ON TABLE 行情增量.schema迁移 IS '已应用的 schema/迁移/*.sql 版本号';

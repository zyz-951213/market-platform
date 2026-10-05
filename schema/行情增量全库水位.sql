-- 行情增量：全库可衡量水位
-- 数据集登记 = 要追踪什么；同步水位 = 实测状态（最早/最晚/行数/体积）

CREATE SCHEMA IF NOT EXISTS 行情增量;

CREATE TABLE IF NOT EXISTS 行情增量.数据集登记 (
  数据源     text NOT NULL,          -- schema
  数据集     text NOT NULL,          -- table 或逻辑接口名
  对象类型   text NOT NULL DEFAULT '表',  -- 表|视图|逻辑接口
  水位列     text,                   -- 增量锚点列；空则只盯行数/体积
  责任方     text NOT NULL,          -- database|行为分析|隐因子|指数预测|微盘股研究|特别处理研究|系统
  更新策略   text NOT NULL,          -- 增量|全量|冻结|派生|人工|配置
  更新入口   text,                   -- 脚本/函数名，便于对照
  是否启用   boolean NOT NULL DEFAULT true,
  备注       text,
  登记时间   timestamp without time zone NOT NULL DEFAULT now(),
  PRIMARY KEY (数据源, 数据集)
);

COMMENT ON TABLE 行情增量.数据集登记 IS '全库需追踪的数据集目录；决定水位扫哪些对象';

-- 扩展同步水位为可衡量快照（兼容旧 PK）
ALTER TABLE 行情增量.同步水位 ADD COLUMN IF NOT EXISTS 最早日 date;
ALTER TABLE 行情增量.同步水位 ADD COLUMN IF NOT EXISTS 水位列 text;
ALTER TABLE 行情增量.同步水位 ADD COLUMN IF NOT EXISTS 责任方 text;
ALTER TABLE 行情增量.同步水位 ADD COLUMN IF NOT EXISTS 更新策略 text;
ALTER TABLE 行情增量.同步水位 ADD COLUMN IF NOT EXISTS 表体积字节 bigint;
ALTER TABLE 行情增量.同步水位 ADD COLUMN IF NOT EXISTS 状态 text;

COMMENT ON TABLE 行情增量.同步水位 IS '各数据集实测水位；由 刷新全库水位 扫描写入，更新脚本亦应回写';

CREATE OR REPLACE VIEW 行情增量.水位总览 AS
SELECT
  COALESCE(r.数据源, w.数据源) AS 数据源,
  COALESCE(r.数据集, w.数据集) AS 数据集,
  COALESCE(r.对象类型, '表') AS 对象类型,
  COALESCE(w.水位列, r.水位列) AS 水位列,
  w.最早日,
  w.最后交易日 AS 最后日,
  w.行数,
  pg_size_pretty(w.表体积字节) AS 体积,
  w.表体积字节,
  COALESCE(w.责任方, r.责任方) AS 责任方,
  COALESCE(w.更新策略, r.更新策略) AS 更新策略,
  r.更新入口,
  w.状态,
  w.备注,
  w.更新时间 AS 水位刷新时间,
  CASE
    WHEN r.数据源 IS NULL THEN '水位有、登记无'
    WHEN w.数据源 IS NULL THEN '登记有、水位无'
    ELSE '已对齐'
  END AS 对齐
FROM 行情增量.数据集登记 r
FULL OUTER JOIN 行情增量.同步水位 w
  ON w.数据源 = r.数据源 AND w.数据集 = r.数据集
ORDER BY 1, 2;

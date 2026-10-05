-- ============================================================
-- Schema 收敛：公共 + 行情增量 + 各项目
-- 目标只保留：
--   公共, 行情增量,
--   行为分析, 指数预测, 隐因子, 微盘股研究, 特别处理研究
-- ============================================================

BEGIN;

-- ---------- 1) 拆掉依赖旧 schema 名的公共/兼容视图 ----------
DROP VIEW IF EXISTS 公共.日行情 CASCADE;
DROP VIEW IF EXISTS 公共.日行情_档案 CASCADE;
DROP VIEW IF EXISTS 公共.日行情_增量 CASCADE;
DROP VIEW IF EXISTS 公共.日行情_应用 CASCADE;
DROP VIEW IF EXISTS 公共.日行情_应用投影 CASCADE;
DROP VIEW IF EXISTS 公共.交易日历 CASCADE;
DROP VIEW IF EXISTS 公共.公司档案 CASCADE;
DROP VIEW IF EXISTS 公共.上市状态 CASCADE;
DROP VIEW IF EXISTS 公共.指数日行情 CASCADE;
DROP VIEW IF EXISTS 公共.证券档案 CASCADE;
DROP VIEW IF EXISTS 公共.个股日行情_原始 CASCADE;
DROP VIEW IF EXISTS 公共.指数日行情_原始 CASCADE;
DROP VIEW IF EXISTS 公共.日历_原始 CASCADE;
DROP VIEW IF EXISTS 公共.公司档案_原始 CASCADE;
DROP VIEW IF EXISTS 公共.日收益率 CASCADE;
DROP VIEW IF EXISTS 公共.月收益率 CASCADE;
DROP VIEW IF EXISTS 公共.行业归属 CASCADE;
DROP VIEW IF EXISTS 公共.市场收益 CASCADE;
DROP VIEW IF EXISTS 公共.新股发行 CASCADE;
DROP VIEW IF EXISTS 公共.无涨跌停 CASCADE;

DROP SCHEMA IF EXISTS 兼容 CASCADE;
DROP SCHEMA IF EXISTS 预测兼容 CASCADE;
DROP SCHEMA IF EXISTS 隐因子兼容 CASCADE;
DROP SCHEMA IF EXISTS 微盘兼容 CASCADE;
DROP SCHEMA IF EXISTS 特处兼容 CASCADE;

-- ---------- 2) 原始仓物理表并入 公共（无改名冲突）----------
DO $$
DECLARE r record;
BEGIN
  FOR r IN
    SELECT n.nspname AS sch, c.relname AS tbl
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE c.relkind = 'r'
      AND n.nspname IN ('核心市场数据','资金流','宏观经济','财务基本面','跨资产市场','主数据')
  LOOP
    EXECUTE format('ALTER TABLE %I.%I SET SCHEMA 公共', r.sch, r.tbl);
  END LOOP;
END $$;

-- 序列一并搬迁
DO $$
DECLARE r record;
BEGIN
  FOR r IN
    SELECT n.nspname AS sch, c.relname AS seq
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE c.relkind = 'S'
      AND n.nspname IN ('核心市场数据','资金流','宏观经济','财务基本面','跨资产市场','主数据')
  LOOP
    EXECUTE format('ALTER SEQUENCE %I.%I SET SCHEMA 公共', r.sch, r.seq);
  END LOOP;
END $$;

DROP SCHEMA IF EXISTS 核心市场数据 CASCADE;
DROP SCHEMA IF EXISTS 资金流 CASCADE;
DROP SCHEMA IF EXISTS 宏观经济 CASCADE;
DROP SCHEMA IF EXISTS 财务基本面 CASCADE;
DROP SCHEMA IF EXISTS 跨资产市场 CASCADE;
DROP SCHEMA IF EXISTS 主数据 CASCADE;

-- ---------- 3) 公共接口：研究日行情 = 档案 ∪ 增量 ----------
CREATE VIEW 公共.日行情_档案 AS
SELECT
  trade_date, stock_code, stock_name, fill_flag,
  open, close, high, low, volume, amount,
  days_since_prev, trade_status, avg_price, pct_chg,
  total_shares, float_shares, turnover,
  mkt_cap, float_mkt_cap,
  float_a_shares, float_b_shares,
  float_a_mkt_cap, float_b_mkt_cap,
  last_share_chg_date
FROM 微盘股研究.日行情
WHERE trade_date <= DATE '2026-05-20';

CREATE VIEW 公共.日行情_增量 AS
SELECT
  "交易日期" AS trade_date,
  "证券代码"::varchar(6) AS stock_code,
  NULL::varchar(40) AS stock_name,
  NULL::smallint AS fill_flag,
  "开盘价"::numeric(16,6) AS open,
  "收盘价"::numeric(16,6) AS close,
  "最高价"::numeric(16,6) AS high,
  "最低价"::numeric(16,6) AS low,
  "成交量(股)"::numeric(20,0) AS volume,
  "成交金额(元)"::numeric(20,3) AS amount,
  NULL::smallint AS days_since_prev,
  NULL::smallint AS trade_status,
  CASE WHEN "成交量(股)" IS NOT NULL AND "成交量(股)" <> 0
       THEN ("成交金额(元)" / "成交量(股)")::numeric(16,6) ELSE NULL END AS avg_price,
  "涨跌幅"::numeric(12,6) AS pct_chg,
  "总股数"::numeric(20,0) AS total_shares,
  "A股流通股数"::numeric(20,0) AS float_shares,
  NULL::numeric(12,6) AS turnover,
  "总市值(元)"::numeric(20,2) AS mkt_cap,
  "流通市值(元)"::numeric(20,2) AS float_mkt_cap,
  "A股流通股数"::numeric(20,2) AS float_a_shares,
  "B股流通股数"::numeric(20,2) AS float_b_shares,
  NULL::numeric(20,2) AS float_a_mkt_cap,
  NULL::numeric(20,2) AS float_b_mkt_cap,
  "最新股本变动日期" AS last_share_chg_date
FROM 公共.个股日行情文件
WHERE "交易日期" > DATE '2026-05-20';

CREATE VIEW 公共.日行情 AS
SELECT * FROM 公共.日行情_档案
UNION ALL
SELECT * FROM 公共.日行情_增量;

CREATE VIEW 公共.日行情_应用 AS SELECT * FROM 行为分析.日行情;
CREATE VIEW 公共.日行情_应用投影 AS
SELECT
  stock_code::character(6) AS 证券代码,
  trade_date AS 交易日期,
  open::double precision AS 开盘价,
  high::double precision AS 最高价,
  low::double precision AS 最低价,
  close::double precision AS 收盘价,
  volume::double precision AS 成交量,
  amount::double precision AS 成交额
FROM 公共.日行情;

-- 研究共享（仍以微盘物理表为档案真源的那些）
CREATE VIEW 公共.交易日历 AS SELECT * FROM 微盘股研究.交易日历;
CREATE VIEW 公共.公司档案 AS SELECT * FROM 微盘股研究.公司档案;
CREATE VIEW 公共.上市状态 AS SELECT * FROM 微盘股研究.上市状态;
CREATE VIEW 公共.指数日行情 AS SELECT * FROM 微盘股研究.指数日行情;
CREATE VIEW 公共.日收益率 AS SELECT * FROM 微盘股研究.日收益率;
CREATE VIEW 公共.月收益率 AS SELECT * FROM 微盘股研究.月收益率;
CREATE VIEW 公共.行业归属 AS SELECT * FROM 微盘股研究.行业归属;
CREATE VIEW 公共.市场收益 AS SELECT * FROM 微盘股研究.市场收益;
CREATE VIEW 公共.新股发行 AS SELECT * FROM 微盘股研究.新股发行;
CREATE VIEW 公共.无涨跌停 AS SELECT * FROM 微盘股研究.无涨跌停;

COMMENT ON SCHEMA 公共 IS '公共数据：原始仓物理表 + 跨项目稳定接口视图';
COMMENT ON SCHEMA 行情增量 IS '行情增量管道：水位与缺口';
COMMENT ON VIEW 公共.日行情 IS '研究日行情接口：档案∪核心增量';

COMMIT;

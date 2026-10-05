-- 隐因子档案/日历去物理拷贝：改为指向研究真源的视图
-- 因子取值 / 隐空间坐标 上的 FK 需先拆除（视图不能作 FK 目标）

BEGIN;

ALTER TABLE IF EXISTS 隐因子.因子取值 DROP CONSTRAINT IF EXISTS factor_values_stock_code_fkey;
ALTER TABLE IF EXISTS 隐因子.因子取值 DROP CONSTRAINT IF EXISTS factor_values_trade_date_fkey;
ALTER TABLE IF EXISTS 隐因子.隐空间坐标 DROP CONSTRAINT IF EXISTS latent_coords_stock_code_fkey;
ALTER TABLE IF EXISTS 隐因子.隐空间坐标 DROP CONSTRAINT IF EXISTS latent_coords_trade_date_fkey;

-- 兼容视图依赖物理表名，先去掉再重建
DROP VIEW IF EXISTS 隐因子兼容.stocks CASCADE;
DROP VIEW IF EXISTS 隐因子兼容.trading_dates CASCADE;

DROP TABLE IF EXISTS 隐因子.股票信息 CASCADE;
DROP TABLE IF EXISTS 隐因子.交易日历 CASCADE;

CREATE VIEW 隐因子.股票信息 AS
SELECT
  stock_code AS 证券代码,
  stock_name AS 证券简称,
  industry_name_a AS 行业甲,
  industry_name_b AS 行业乙,
  list_date AS 上市日期,
  COALESCE(NULLIF(activity_status, ''), 'active') AS 状态,
  NULL::timestamp without time zone AS 更新时间
FROM 微盘股研究.公司档案;

CREATE VIEW 隐因子.交易日历 AS
SELECT
  trade_date AS 交易日期,
  TRUE AS is_trade_day,
  EXTRACT(YEAR FROM trade_date)::smallint AS year,
  EXTRACT(MONTH FROM trade_date)::smallint AS month,
  EXTRACT(QUARTER FROM trade_date)::smallint AS quarter
FROM (
  SELECT DISTINCT trade_date FROM 微盘股研究.日行情
  UNION
  SELECT DISTINCT "交易日期" AS trade_date FROM 行为分析.日行情
) d;

CREATE OR REPLACE VIEW 隐因子兼容.stocks AS
SELECT
  证券代码 AS stock_code,
  证券简称 AS stock_name,
  行业甲 AS industry_a,
  行业乙 AS industry_b,
  上市日期 AS list_date,
  状态 AS status,
  更新时间 AS updated_at
FROM 隐因子.股票信息;

CREATE OR REPLACE VIEW 隐因子兼容.trading_dates AS
SELECT
  交易日期 AS trade_date,
  is_trade_day,
  year,
  month,
  quarter
FROM 隐因子.交易日历;

COMMIT;

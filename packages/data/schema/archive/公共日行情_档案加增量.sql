-- 公共.日行情：CSMAR 档案（微盘，冻结）∪ 核心 akshare/baostock 增量
-- 列顺序与 微盘股研究.日行情 完全一致；边界 2026-05-20 收盘价对齐

CREATE OR REPLACE VIEW 公共.日行情_档案 AS
SELECT
  trade_date, stock_code, stock_name, fill_flag,
  open, close, high, low,
  volume, amount,
  days_since_prev, trade_status, avg_price, pct_chg,
  total_shares, float_shares, turnover,
  mkt_cap, float_mkt_cap,
  float_a_shares, float_b_shares,
  float_a_mkt_cap, float_b_mkt_cap,
  last_share_chg_date
FROM 微盘股研究.日行情
WHERE trade_date <= DATE '2026-05-20';

CREATE OR REPLACE VIEW 公共.日行情_增量 AS
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
       THEN ("成交金额(元)" / "成交量(股)")::numeric(16,6)
       ELSE NULL END AS avg_price,
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
FROM 核心市场数据.个股日行情文件
WHERE "交易日期" > DATE '2026-05-20';

DROP TRIGGER IF EXISTS 拒绝写入 ON 公共.日行情;
DROP VIEW IF EXISTS 公共.日行情 CASCADE;

CREATE VIEW 公共.日行情 AS
SELECT * FROM 公共.日行情_档案
UNION ALL
SELECT * FROM 公共.日行情_增量;

COMMENT ON VIEW 公共.日行情 IS
  '研究日行情：≤2026-05-20 微盘CSMAR档案；之后为核心 akshare/baostock 增量';

-- 读口切到公共（断档后可见增量）
CREATE OR REPLACE VIEW 微盘兼容.mc_daily AS SELECT * FROM 公共.日行情;

DROP TRIGGER IF EXISTS 拒绝写入 ON 特别处理研究.日行情;
DROP VIEW IF EXISTS 特处兼容.st_daily CASCADE;
DROP VIEW IF EXISTS 特别处理研究.日行情 CASCADE;
CREATE VIEW 特别处理研究.日行情 AS SELECT * FROM 公共.日行情;
CREATE VIEW 特处兼容.st_daily AS SELECT * FROM 特别处理研究.日行情;

DROP TRIGGER IF EXISTS 拒绝写入 ON 隐因子.交易日历;
DROP VIEW IF EXISTS 隐因子兼容.trading_dates CASCADE;
DROP VIEW IF EXISTS 隐因子.交易日历 CASCADE;
CREATE VIEW 隐因子.交易日历 AS
SELECT
  trade_date AS 交易日期,
  TRUE AS is_trade_day,
  EXTRACT(YEAR FROM trade_date)::smallint AS year,
  EXTRACT(MONTH FROM trade_date)::smallint AS month,
  EXTRACT(QUARTER FROM trade_date)::smallint AS quarter
FROM (SELECT DISTINCT trade_date FROM 公共.日行情) d;
CREATE VIEW 隐因子兼容.trading_dates AS
SELECT 交易日期 AS trade_date, is_trade_day, year, month, quarter
FROM 隐因子.交易日历;

INSERT INTO 行情增量.同步水位 ("数据源", "数据集", "最后交易日", "行数", "备注", "更新时间")
SELECT '公共', '日行情', max(trade_date), count(*), '档案∪核心增量', now()
FROM 公共.日行情
ON CONFLICT ("数据源", "数据集") DO UPDATE SET
  "最后交易日"=EXCLUDED."最后交易日",
  "行数"=EXCLUDED."行数",
  "备注"=EXCLUDED."备注",
  "更新时间"=now();

INSERT INTO 行情增量.同步水位 ("数据源", "数据集", "最后交易日", "行数", "备注", "更新时间")
SELECT '微盘股研究', '日行情', max(trade_date), count(*), 'CSMAR档案(已冻结)', now()
FROM 微盘股研究.日行情
ON CONFLICT ("数据源", "数据集") DO UPDATE SET
  "最后交易日"=EXCLUDED."最后交易日",
  "行数"=EXCLUDED."行数",
  "备注"=EXCLUDED."备注",
  "更新时间"=now();

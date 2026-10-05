-- 微盘日收益率：去掉与日行情重复的 OHLCV/市值，只保留回报独有列
-- 原表 ~4.3GB → 窄表预计 ~1.5–2GB；对外仍用 日收益率 / mc_daily_returns 视图

CREATE TABLE IF NOT EXISTS 微盘股研究.日回报 (
  stock_code            character varying(6)  NOT NULL,
  trade_date            date                  NOT NULL,
  ret_div_reinvest      numeric(16, 8),
  ret_no_div            numeric(16, 8),
  comparable_close_div  numeric(16, 6),
  comparable_close      numeric(16, 6),
  market_type           smallint,
  last_share_chg_date   date,
  trade_status          character varying(10),
  after_hours_vol       numeric(20, 0),
  after_hours_amt       numeric(20, 3),
  prev_close_exch       numeric(16, 6),
  pct_chg               numeric(12, 6),
  limit_down            numeric(16, 6),
  limit_up              numeric(16, 6),
  limit_status          character varying(20),
  PRIMARY KEY (stock_code, trade_date)
);

CREATE INDEX IF NOT EXISTS 日回报_日期_索引 ON 微盘股研究.日回报 (trade_date);

INSERT INTO 微盘股研究.日回报 (
  stock_code, trade_date,
  ret_div_reinvest, ret_no_div,
  comparable_close_div, comparable_close,
  market_type, last_share_chg_date, trade_status,
  after_hours_vol, after_hours_amt, prev_close_exch,
  pct_chg, limit_down, limit_up, limit_status
)
SELECT
  stock_code, trade_date,
  ret_div_reinvest, ret_no_div,
  comparable_close_div, comparable_close,
  market_type, last_share_chg_date, trade_status,
  after_hours_vol, after_hours_amt, prev_close_exch,
  pct_chg, limit_down, limit_up, limit_status
FROM 微盘股研究.日收益率
ON CONFLICT (stock_code, trade_date) DO NOTHING;

-- 拆掉依赖视图后换主对象
DROP VIEW IF EXISTS 特别处理研究.st_daily_returns CASCADE;
DROP VIEW IF EXISTS 特别处理研究.日收益率 CASCADE;
DROP VIEW IF EXISTS 公共.日收益率 CASCADE;
DROP VIEW IF EXISTS 微盘股研究.mc_daily_returns CASCADE;

ALTER TABLE 微盘股研究.日收益率 RENAME TO 日收益率_旧json宽表;
-- 上面名仅过渡；立刻建视图并 DROP 旧表

CREATE OR REPLACE VIEW 微盘股研究.日收益率 AS
SELECT
  r.stock_code,
  r.trade_date,
  d.open,
  d.high,
  d.low,
  d.close,
  d.volume,
  d.amount,
  d.float_mkt_cap,
  d.mkt_cap,
  r.ret_div_reinvest,
  r.ret_no_div,
  r.comparable_close_div,
  r.comparable_close,
  r.market_type,
  COALESCE(r.last_share_chg_date, d.last_share_chg_date) AS last_share_chg_date,
  COALESCE(r.trade_status, d.trade_status::text) AS trade_status,
  r.after_hours_vol,
  r.after_hours_amt,
  r.prev_close_exch,
  COALESCE(r.pct_chg, d.pct_chg) AS pct_chg,
  r.limit_down,
  r.limit_up,
  r.limit_status
FROM 微盘股研究.日回报 r
LEFT JOIN 微盘股研究.日行情 d
  ON d.stock_code = r.stock_code AND d.trade_date = r.trade_date;

CREATE OR REPLACE VIEW 微盘股研究.mc_daily_returns AS
SELECT * FROM 微盘股研究.日收益率;

CREATE OR REPLACE VIEW 特别处理研究.日收益率 AS
SELECT * FROM 微盘股研究.日收益率;

CREATE OR REPLACE VIEW 特别处理研究.st_daily_returns AS
SELECT * FROM 特别处理研究.日收益率;

CREATE OR REPLACE VIEW 公共.日收益率 AS
SELECT * FROM 微盘股研究.日收益率;

-- 写回：落到窄表（兼容旧 import TRUNCATE+INSERT 需改脚本；视图 INSERT 走触发器）
CREATE OR REPLACE FUNCTION 微盘股研究.写入日收益率()
RETURNS TRIGGER AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    DELETE FROM 微盘股研究.日回报
    WHERE stock_code = OLD.stock_code AND trade_date = OLD.trade_date;
    RETURN OLD;
  END IF;
  INSERT INTO 微盘股研究.日回报 (
    stock_code, trade_date,
    ret_div_reinvest, ret_no_div,
    comparable_close_div, comparable_close,
    market_type, last_share_chg_date, trade_status,
    after_hours_vol, after_hours_amt, prev_close_exch,
    pct_chg, limit_down, limit_up, limit_status
  ) VALUES (
    NEW.stock_code, NEW.trade_date,
    NEW.ret_div_reinvest, NEW.ret_no_div,
    NEW.comparable_close_div, NEW.comparable_close,
    NEW.market_type, NEW.last_share_chg_date, NEW.trade_status,
    NEW.after_hours_vol, NEW.after_hours_amt, NEW.prev_close_exch,
    NEW.pct_chg, NEW.limit_down, NEW.limit_up, NEW.limit_status
  )
  ON CONFLICT (stock_code, trade_date) DO UPDATE SET
    ret_div_reinvest = EXCLUDED.ret_div_reinvest,
    ret_no_div = EXCLUDED.ret_no_div,
    comparable_close_div = EXCLUDED.comparable_close_div,
    comparable_close = EXCLUDED.comparable_close,
    market_type = EXCLUDED.market_type,
    last_share_chg_date = EXCLUDED.last_share_chg_date,
    trade_status = EXCLUDED.trade_status,
    after_hours_vol = EXCLUDED.after_hours_vol,
    after_hours_amt = EXCLUDED.after_hours_amt,
    prev_close_exch = EXCLUDED.prev_close_exch,
    pct_chg = EXCLUDED.pct_chg,
    limit_down = EXCLUDED.limit_down,
    limit_up = EXCLUDED.limit_up,
    limit_status = EXCLUDED.limit_status;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS 日收益率_写触发 ON 微盘股研究.日收益率;
CREATE TRIGGER 日收益率_写触发
INSTEAD OF INSERT OR UPDATE OR DELETE ON 微盘股研究.日收益率
FOR EACH ROW EXECUTE FUNCTION 微盘股研究.写入日收益率();

-- 行数校验通过后丢弃宽表（已执行）：
-- DROP TABLE 微盘股研究.日收益率_旧宽表;

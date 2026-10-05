-- 隐因子量价去重：CSMAR 段读 公共.日行情；断档后落 行情补丁
-- 因子取值 去掉物理量价列，由兼容视图 COALESCE 拼出

BEGIN;

CREATE TABLE IF NOT EXISTS 隐因子.行情补丁 (
  证券代码   varchar(10) NOT NULL,
  交易日期   date NOT NULL,
  开盘价     numeric(12,4),
  最高价     numeric(12,4),
  最低价     numeric(12,4),
  收盘价     numeric(12,4),
  volume     bigint,
  amount     numeric(16,2),
  turnover   numeric(8,4),
  mkt_cap    numeric(16,2),
  float_mkt_cap numeric(16,2),
  创建时间   timestamp without time zone DEFAULT now(),
  PRIMARY KEY (证券代码, 交易日期)
);

COMMENT ON TABLE 隐因子.行情补丁 IS 'CSMAR/微盘日行情断档后的量价补丁；主段请读 公共.日行情';

-- 迁入断档后量价（仅一次；已存在则跳过冲突）
INSERT INTO 隐因子.行情补丁 (
  证券代码, 交易日期, 开盘价, 最高价, 最低价, 收盘价,
  volume, amount, turnover, mkt_cap, float_mkt_cap
)
SELECT
  f."证券代码", f."交易日期", f."开盘价", f."最高价", f."最低价", f."收盘价",
  f.volume, f.amount, f.turnover, f.mkt_cap, f.float_mkt_cap
FROM 隐因子.因子取值 f
WHERE f."交易日期" > DATE '2026-05-20'
  AND f."收盘价" IS NOT NULL
ON CONFLICT DO NOTHING;

-- 去掉物理量价列（视图重建前先 drop 依赖兼容视图）
DROP VIEW IF EXISTS 隐因子兼容.factor_values CASCADE;

ALTER TABLE 隐因子.因子取值
  DROP COLUMN IF EXISTS "开盘价",
  DROP COLUMN IF EXISTS "最高价",
  DROP COLUMN IF EXISTS "最低价",
  DROP COLUMN IF EXISTS "收盘价",
  DROP COLUMN IF EXISTS volume,
  DROP COLUMN IF EXISTS amount,
  DROP COLUMN IF EXISTS turnover,
  DROP COLUMN IF EXISTS mkt_cap,
  DROP COLUMN IF EXISTS float_mkt_cap;

COMMIT;

-- 公共.个股日行情文件：只保留边界日之后的活水增量
-- 档案段由 微盘股研究.日行情（≤2026-05-20）承担；公共.日行情 = 档案 ∪ 增量

-- 应用投影改为走统一接口（勿再扫全量个股日行情文件）
DROP VIEW IF EXISTS 公共.日行情_应用投影 CASCADE;
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

-- 已执行：DELETE FROM 公共.个股日行情文件 WHERE "交易日期" <= DATE '2026-05-20';
-- VACUUM FULL ANALYZE 公共.个股日行情文件;

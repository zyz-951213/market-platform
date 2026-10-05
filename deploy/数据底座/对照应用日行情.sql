-- 对照：行为分析瘦表 vs 原始宽表投影（同日抽样）
-- 用法: psql -d 金融市场数据 -f deploy/对照应用日行情.sql

\set sample_date '2024-06-03'

WITH joined AS (
  SELECT
    a."证券代码",
    a."收盘价" AS 应用收盘,
    p."收盘价" AS 投影收盘,
    abs(a."收盘价" - p."收盘价") AS 绝对差
  FROM 行为分析.日行情 a
  JOIN 公共.日行情_应用投影 p
    ON a."证券代码" = p."证券代码" AND a."交易日期" = p."交易日期"
  WHERE a."交易日期" = :'sample_date'
)
SELECT
  count(*) AS 配对行数,
  count(*) FILTER (WHERE 绝对差 < 0.015) AS 近似一致,
  count(*) FILTER (WHERE 绝对差 >= 0.015) AS 明显不一致,
  round((avg(绝对差 / NULLIF(投影收盘, 0)) * 100)::numeric, 3) AS 平均相对误差pct
FROM joined;

SELECT
  (SELECT max("交易日期") FROM 行为分析.日行情) AS 应用最新日,
  (SELECT max("交易日期") FROM 公共.日行情_应用投影) AS 投影最新日,
  (SELECT max(trade_date) FROM 公共.日行情) AS 研究最新日;

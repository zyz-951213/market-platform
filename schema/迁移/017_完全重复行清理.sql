-- 017:清理"完全重复行"(无业务键唯一索引的表重跑时会把整行再插一遍)
--
-- 背景(2026-10-06 审计):`_insert` 对没有业务键唯一索引的表只能 `ON CONFLICT DO NOTHING`,
-- 而 DO NOTHING 没有唯一约束可冲突 —— 等于"重复插入不报错";重跑或同批重复取数即整行再写一遍。
-- 实测"内容完全相同"(除 序号/updated_at 外所有列相同)的行:分配文件 414、龙虎榜 224、
-- 龙虎榜营业部 1810、指数基本信息文件 8。
--
-- 只删**内容完全相同**的行(保留 序号 最小者);同业务键但内容不同的行一律保留 ——
-- 那属于"多来源/多批次版本"的语义问题,需业务确认后单独处理。
--
-- 实现要点:内容指纹必须**先物化**再关联(`f AS MATERIALIZED`)。若把 md5(...) 直接写在
-- JOIN 条件里,规划器无法用哈希连接,退化成嵌套循环 —— 542k 行的表会跑到 20 分钟以上
-- (2026-10-06 实测),物化后秒级完成。
--
-- 幂等:重复执行第二次已无重复行,影响 0 行。

-- ── 公共.分配文件(内容列 18 个)────────────────────────────────
CREATE TABLE IF NOT EXISTS 公共."分配文件_去重备份_20261006" (LIKE 公共."分配文件");

WITH f AS MATERIALIZED (
  SELECT "序号", md5(concat_ws('|', CASE WHEN a."证券代码" IS NULL THEN '\x00' ELSE a."证券代码"::text END, CASE WHEN a."除权(息)日期" IS NULL THEN '\x00' ELSE a."除权(息)日期"::text END, CASE WHEN a."公告日期" IS NULL THEN '\x00' ELSE a."公告日期"::text END, CASE WHEN a."支付日期" IS NULL THEN '\x00' ELSE a."支付日期"::text END, CASE WHEN a."分配类型" IS NULL THEN '\x00' ELSE a."分配类型"::text END, CASE WHEN a."分配比率" IS NULL THEN '\x00' ELSE a."分配比率"::text END, CASE WHEN a."配股价格" IS NULL THEN '\x00' ELSE a."配股价格"::text END, CASE WHEN a."财政年度" IS NULL THEN '\x00' ELSE a."财政年度"::text END, CASE WHEN a."预案公告日期" IS NULL THEN '\x00' ELSE a."预案公告日期"::text END, CASE WHEN a."预案内容" IS NULL THEN '\x00' ELSE a."预案内容"::text END, CASE WHEN a."实施方案内容" IS NULL THEN '\x00' ELSE a."实施方案内容"::text END, CASE WHEN a."送股比(每10股)" IS NULL THEN '\x00' ELSE a."送股比(每10股)"::text END, CASE WHEN a."转增比(每10股)" IS NULL THEN '\x00' ELSE a."转增比(每10股)"::text END, CASE WHEN a."派息比(每10股税前)" IS NULL THEN '\x00' ELSE a."派息比(每10股税前)"::text END, CASE WHEN a."股权登记日" IS NULL THEN '\x00' ELSE a."股权登记日"::text END, CASE WHEN a."分配对象" IS NULL THEN '\x00' ELSE a."分配对象"::text END, CASE WHEN a."股权登记日收盘价" IS NULL THEN '\x00' ELSE a."股权登记日收盘价"::text END, CASE WHEN a."source" IS NULL THEN '\x00' ELSE a."source"::text END)) AS 指纹 FROM 公共."分配文件" a
),
重复 AS (
  SELECT 指纹, min("序号") AS 保留 FROM f GROUP BY 1 HAVING count(*) > 1
)
INSERT INTO 公共."分配文件_去重备份_20261006"
SELECT a.* FROM 公共."分配文件" a
  JOIN f ON f."序号" = a."序号"
  JOIN 重复 r ON r.指纹 = f.指纹 AND a."序号" <> r.保留
 WHERE NOT EXISTS (SELECT 1 FROM 公共."分配文件_去重备份_20261006" d WHERE d."序号" = a."序号");

WITH f AS MATERIALIZED (
  SELECT "序号", md5(concat_ws('|', CASE WHEN a."证券代码" IS NULL THEN '\x00' ELSE a."证券代码"::text END, CASE WHEN a."除权(息)日期" IS NULL THEN '\x00' ELSE a."除权(息)日期"::text END, CASE WHEN a."公告日期" IS NULL THEN '\x00' ELSE a."公告日期"::text END, CASE WHEN a."支付日期" IS NULL THEN '\x00' ELSE a."支付日期"::text END, CASE WHEN a."分配类型" IS NULL THEN '\x00' ELSE a."分配类型"::text END, CASE WHEN a."分配比率" IS NULL THEN '\x00' ELSE a."分配比率"::text END, CASE WHEN a."配股价格" IS NULL THEN '\x00' ELSE a."配股价格"::text END, CASE WHEN a."财政年度" IS NULL THEN '\x00' ELSE a."财政年度"::text END, CASE WHEN a."预案公告日期" IS NULL THEN '\x00' ELSE a."预案公告日期"::text END, CASE WHEN a."预案内容" IS NULL THEN '\x00' ELSE a."预案内容"::text END, CASE WHEN a."实施方案内容" IS NULL THEN '\x00' ELSE a."实施方案内容"::text END, CASE WHEN a."送股比(每10股)" IS NULL THEN '\x00' ELSE a."送股比(每10股)"::text END, CASE WHEN a."转增比(每10股)" IS NULL THEN '\x00' ELSE a."转增比(每10股)"::text END, CASE WHEN a."派息比(每10股税前)" IS NULL THEN '\x00' ELSE a."派息比(每10股税前)"::text END, CASE WHEN a."股权登记日" IS NULL THEN '\x00' ELSE a."股权登记日"::text END, CASE WHEN a."分配对象" IS NULL THEN '\x00' ELSE a."分配对象"::text END, CASE WHEN a."股权登记日收盘价" IS NULL THEN '\x00' ELSE a."股权登记日收盘价"::text END, CASE WHEN a."source" IS NULL THEN '\x00' ELSE a."source"::text END)) AS 指纹 FROM 公共."分配文件" a
),
重复 AS (
  SELECT 指纹, min("序号") AS 保留 FROM f GROUP BY 1 HAVING count(*) > 1
)
DELETE FROM 公共."分配文件" a
 USING f, 重复 r
 WHERE f."序号" = a."序号" AND r.指纹 = f.指纹 AND a."序号" <> r.保留;

-- ── 公共.龙虎榜(内容列 26 个)────────────────────────────────
CREATE TABLE IF NOT EXISTS 公共."龙虎榜_去重备份_20261006" (LIKE 公共."龙虎榜");

WITH f AS MATERIALIZED (
  SELECT "序号", md5(concat_ws('|', CASE WHEN a."交易日期" IS NULL THEN '\x00' ELSE a."交易日期"::text END, CASE WHEN a."股票代码" IS NULL THEN '\x00' ELSE a."股票代码"::text END, CASE WHEN a."交易所" IS NULL THEN '\x00' ELSE a."交易所"::text END, CASE WHEN a."上榜原因" IS NULL THEN '\x00' ELSE a."上榜原因"::text END, CASE WHEN a."收盘价" IS NULL THEN '\x00' ELSE a."收盘价"::text END, CASE WHEN a."涨跌幅" IS NULL THEN '\x00' ELSE a."涨跌幅"::text END, CASE WHEN a."龙虎榜买入额" IS NULL THEN '\x00' ELSE a."龙虎榜买入额"::text END, CASE WHEN a."龙虎榜卖出额" IS NULL THEN '\x00' ELSE a."龙虎榜卖出额"::text END, CASE WHEN a."龙虎榜净买额" IS NULL THEN '\x00' ELSE a."龙虎榜净买额"::text END, CASE WHEN a."买入营业部" IS NULL THEN '\x00' ELSE a."买入营业部"::text END, CASE WHEN a."卖出营业部" IS NULL THEN '\x00' ELSE a."卖出营业部"::text END, CASE WHEN a."营业部买卖方向" IS NULL THEN '\x00' ELSE a."营业部买卖方向"::text END, CASE WHEN a."营业部金额" IS NULL THEN '\x00' ELSE a."营业部金额"::text END, CASE WHEN a."是否机构席位" IS NULL THEN '\x00' ELSE a."是否机构席位"::text END, CASE WHEN a."source" IS NULL THEN '\x00' ELSE a."source"::text END, CASE WHEN a."解读" IS NULL THEN '\x00' ELSE a."解读"::text END, CASE WHEN a."龙虎榜成交额" IS NULL THEN '\x00' ELSE a."龙虎榜成交额"::text END, CASE WHEN a."市场总成交额" IS NULL THEN '\x00' ELSE a."市场总成交额"::text END, CASE WHEN a."净买额占总成交比" IS NULL THEN '\x00' ELSE a."净买额占总成交比"::text END, CASE WHEN a."成交额占总成交比" IS NULL THEN '\x00' ELSE a."成交额占总成交比"::text END, CASE WHEN a."换手率" IS NULL THEN '\x00' ELSE a."换手率"::text END, CASE WHEN a."流通市值" IS NULL THEN '\x00' ELSE a."流通市值"::text END, CASE WHEN a."上榜后1日" IS NULL THEN '\x00' ELSE a."上榜后1日"::text END, CASE WHEN a."上榜后2日" IS NULL THEN '\x00' ELSE a."上榜后2日"::text END, CASE WHEN a."上榜后5日" IS NULL THEN '\x00' ELSE a."上榜后5日"::text END, CASE WHEN a."上榜后10日" IS NULL THEN '\x00' ELSE a."上榜后10日"::text END)) AS 指纹 FROM 公共."龙虎榜" a
),
重复 AS (
  SELECT 指纹, min("序号") AS 保留 FROM f GROUP BY 1 HAVING count(*) > 1
)
INSERT INTO 公共."龙虎榜_去重备份_20261006"
SELECT a.* FROM 公共."龙虎榜" a
  JOIN f ON f."序号" = a."序号"
  JOIN 重复 r ON r.指纹 = f.指纹 AND a."序号" <> r.保留
 WHERE NOT EXISTS (SELECT 1 FROM 公共."龙虎榜_去重备份_20261006" d WHERE d."序号" = a."序号");

WITH f AS MATERIALIZED (
  SELECT "序号", md5(concat_ws('|', CASE WHEN a."交易日期" IS NULL THEN '\x00' ELSE a."交易日期"::text END, CASE WHEN a."股票代码" IS NULL THEN '\x00' ELSE a."股票代码"::text END, CASE WHEN a."交易所" IS NULL THEN '\x00' ELSE a."交易所"::text END, CASE WHEN a."上榜原因" IS NULL THEN '\x00' ELSE a."上榜原因"::text END, CASE WHEN a."收盘价" IS NULL THEN '\x00' ELSE a."收盘价"::text END, CASE WHEN a."涨跌幅" IS NULL THEN '\x00' ELSE a."涨跌幅"::text END, CASE WHEN a."龙虎榜买入额" IS NULL THEN '\x00' ELSE a."龙虎榜买入额"::text END, CASE WHEN a."龙虎榜卖出额" IS NULL THEN '\x00' ELSE a."龙虎榜卖出额"::text END, CASE WHEN a."龙虎榜净买额" IS NULL THEN '\x00' ELSE a."龙虎榜净买额"::text END, CASE WHEN a."买入营业部" IS NULL THEN '\x00' ELSE a."买入营业部"::text END, CASE WHEN a."卖出营业部" IS NULL THEN '\x00' ELSE a."卖出营业部"::text END, CASE WHEN a."营业部买卖方向" IS NULL THEN '\x00' ELSE a."营业部买卖方向"::text END, CASE WHEN a."营业部金额" IS NULL THEN '\x00' ELSE a."营业部金额"::text END, CASE WHEN a."是否机构席位" IS NULL THEN '\x00' ELSE a."是否机构席位"::text END, CASE WHEN a."source" IS NULL THEN '\x00' ELSE a."source"::text END, CASE WHEN a."解读" IS NULL THEN '\x00' ELSE a."解读"::text END, CASE WHEN a."龙虎榜成交额" IS NULL THEN '\x00' ELSE a."龙虎榜成交额"::text END, CASE WHEN a."市场总成交额" IS NULL THEN '\x00' ELSE a."市场总成交额"::text END, CASE WHEN a."净买额占总成交比" IS NULL THEN '\x00' ELSE a."净买额占总成交比"::text END, CASE WHEN a."成交额占总成交比" IS NULL THEN '\x00' ELSE a."成交额占总成交比"::text END, CASE WHEN a."换手率" IS NULL THEN '\x00' ELSE a."换手率"::text END, CASE WHEN a."流通市值" IS NULL THEN '\x00' ELSE a."流通市值"::text END, CASE WHEN a."上榜后1日" IS NULL THEN '\x00' ELSE a."上榜后1日"::text END, CASE WHEN a."上榜后2日" IS NULL THEN '\x00' ELSE a."上榜后2日"::text END, CASE WHEN a."上榜后5日" IS NULL THEN '\x00' ELSE a."上榜后5日"::text END, CASE WHEN a."上榜后10日" IS NULL THEN '\x00' ELSE a."上榜后10日"::text END)) AS 指纹 FROM 公共."龙虎榜" a
),
重复 AS (
  SELECT 指纹, min("序号") AS 保留 FROM f GROUP BY 1 HAVING count(*) > 1
)
DELETE FROM 公共."龙虎榜" a
 USING f, 重复 r
 WHERE f."序号" = a."序号" AND r.指纹 = f.指纹 AND a."序号" <> r.保留;

-- ── 公共.龙虎榜营业部(内容列 17 个)────────────────────────────────
CREATE TABLE IF NOT EXISTS 公共."龙虎榜营业部_去重备份_20261006" (LIKE 公共."龙虎榜营业部");

WITH f AS MATERIALIZED (
  SELECT "序号", md5(concat_ws('|', CASE WHEN a."交易日期" IS NULL THEN '\x00' ELSE a."交易日期"::text END, CASE WHEN a."股票代码" IS NULL THEN '\x00' ELSE a."股票代码"::text END, CASE WHEN a."交易所" IS NULL THEN '\x00' ELSE a."交易所"::text END, CASE WHEN a."营业部代码" IS NULL THEN '\x00' ELSE a."营业部代码"::text END, CASE WHEN a."营业部名称" IS NULL THEN '\x00' ELSE a."营业部名称"::text END, CASE WHEN a."上榜原因" IS NULL THEN '\x00' ELSE a."上榜原因"::text END, CASE WHEN a."买卖方向" IS NULL THEN '\x00' ELSE a."买卖方向"::text END, CASE WHEN a."买入金额" IS NULL THEN '\x00' ELSE a."买入金额"::text END, CASE WHEN a."买入占比" IS NULL THEN '\x00' ELSE a."买入占比"::text END, CASE WHEN a."卖出金额" IS NULL THEN '\x00' ELSE a."卖出金额"::text END, CASE WHEN a."卖出占比" IS NULL THEN '\x00' ELSE a."卖出占比"::text END, CASE WHEN a."净额" IS NULL THEN '\x00' ELSE a."净额"::text END, CASE WHEN a."收盘价" IS NULL THEN '\x00' ELSE a."收盘价"::text END, CASE WHEN a."涨跌幅" IS NULL THEN '\x00' ELSE a."涨跌幅"::text END, CASE WHEN a."是否机构席位" IS NULL THEN '\x00' ELSE a."是否机构席位"::text END, CASE WHEN a."是否沪深股通" IS NULL THEN '\x00' ELSE a."是否沪深股通"::text END, CASE WHEN a."source" IS NULL THEN '\x00' ELSE a."source"::text END)) AS 指纹 FROM 公共."龙虎榜营业部" a
),
重复 AS (
  SELECT 指纹, min("序号") AS 保留 FROM f GROUP BY 1 HAVING count(*) > 1
)
INSERT INTO 公共."龙虎榜营业部_去重备份_20261006"
SELECT a.* FROM 公共."龙虎榜营业部" a
  JOIN f ON f."序号" = a."序号"
  JOIN 重复 r ON r.指纹 = f.指纹 AND a."序号" <> r.保留
 WHERE NOT EXISTS (SELECT 1 FROM 公共."龙虎榜营业部_去重备份_20261006" d WHERE d."序号" = a."序号");

WITH f AS MATERIALIZED (
  SELECT "序号", md5(concat_ws('|', CASE WHEN a."交易日期" IS NULL THEN '\x00' ELSE a."交易日期"::text END, CASE WHEN a."股票代码" IS NULL THEN '\x00' ELSE a."股票代码"::text END, CASE WHEN a."交易所" IS NULL THEN '\x00' ELSE a."交易所"::text END, CASE WHEN a."营业部代码" IS NULL THEN '\x00' ELSE a."营业部代码"::text END, CASE WHEN a."营业部名称" IS NULL THEN '\x00' ELSE a."营业部名称"::text END, CASE WHEN a."上榜原因" IS NULL THEN '\x00' ELSE a."上榜原因"::text END, CASE WHEN a."买卖方向" IS NULL THEN '\x00' ELSE a."买卖方向"::text END, CASE WHEN a."买入金额" IS NULL THEN '\x00' ELSE a."买入金额"::text END, CASE WHEN a."买入占比" IS NULL THEN '\x00' ELSE a."买入占比"::text END, CASE WHEN a."卖出金额" IS NULL THEN '\x00' ELSE a."卖出金额"::text END, CASE WHEN a."卖出占比" IS NULL THEN '\x00' ELSE a."卖出占比"::text END, CASE WHEN a."净额" IS NULL THEN '\x00' ELSE a."净额"::text END, CASE WHEN a."收盘价" IS NULL THEN '\x00' ELSE a."收盘价"::text END, CASE WHEN a."涨跌幅" IS NULL THEN '\x00' ELSE a."涨跌幅"::text END, CASE WHEN a."是否机构席位" IS NULL THEN '\x00' ELSE a."是否机构席位"::text END, CASE WHEN a."是否沪深股通" IS NULL THEN '\x00' ELSE a."是否沪深股通"::text END, CASE WHEN a."source" IS NULL THEN '\x00' ELSE a."source"::text END)) AS 指纹 FROM 公共."龙虎榜营业部" a
),
重复 AS (
  SELECT 指纹, min("序号") AS 保留 FROM f GROUP BY 1 HAVING count(*) > 1
)
DELETE FROM 公共."龙虎榜营业部" a
 USING f, 重复 r
 WHERE f."序号" = a."序号" AND r.指纹 = f.指纹 AND a."序号" <> r.保留;

-- ── 公共.指数基本信息文件(内容列 8 个)────────────────────────────────
CREATE TABLE IF NOT EXISTS 公共."指数基本信息文件_去重备份_20261006" (LIKE 公共."指数基本信息文件");

WITH f AS MATERIALIZED (
  SELECT "序号", md5(concat_ws('|', CASE WHEN a."交易所指数代码" IS NULL THEN '\x00' ELSE a."交易所指数代码"::text END, CASE WHEN a."指数名称" IS NULL THEN '\x00' ELSE a."指数名称"::text END, CASE WHEN a."开始日期" IS NULL THEN '\x00' ELSE a."开始日期"::text END, CASE WHEN a."基准日期" IS NULL THEN '\x00' ELSE a."基准日期"::text END, CASE WHEN a."基准点数" IS NULL THEN '\x00' ELSE a."基准点数"::text END, CASE WHEN a."样本范围" IS NULL THEN '\x00' ELSE a."样本范围"::text END, CASE WHEN a."计算公式" IS NULL THEN '\x00' ELSE a."计算公式"::text END, CASE WHEN a."source" IS NULL THEN '\x00' ELSE a."source"::text END)) AS 指纹 FROM 公共."指数基本信息文件" a
),
重复 AS (
  SELECT 指纹, min("序号") AS 保留 FROM f GROUP BY 1 HAVING count(*) > 1
)
INSERT INTO 公共."指数基本信息文件_去重备份_20261006"
SELECT a.* FROM 公共."指数基本信息文件" a
  JOIN f ON f."序号" = a."序号"
  JOIN 重复 r ON r.指纹 = f.指纹 AND a."序号" <> r.保留
 WHERE NOT EXISTS (SELECT 1 FROM 公共."指数基本信息文件_去重备份_20261006" d WHERE d."序号" = a."序号");

WITH f AS MATERIALIZED (
  SELECT "序号", md5(concat_ws('|', CASE WHEN a."交易所指数代码" IS NULL THEN '\x00' ELSE a."交易所指数代码"::text END, CASE WHEN a."指数名称" IS NULL THEN '\x00' ELSE a."指数名称"::text END, CASE WHEN a."开始日期" IS NULL THEN '\x00' ELSE a."开始日期"::text END, CASE WHEN a."基准日期" IS NULL THEN '\x00' ELSE a."基准日期"::text END, CASE WHEN a."基准点数" IS NULL THEN '\x00' ELSE a."基准点数"::text END, CASE WHEN a."样本范围" IS NULL THEN '\x00' ELSE a."样本范围"::text END, CASE WHEN a."计算公式" IS NULL THEN '\x00' ELSE a."计算公式"::text END, CASE WHEN a."source" IS NULL THEN '\x00' ELSE a."source"::text END)) AS 指纹 FROM 公共."指数基本信息文件" a
),
重复 AS (
  SELECT 指纹, min("序号") AS 保留 FROM f GROUP BY 1 HAVING count(*) > 1
)
DELETE FROM 公共."指数基本信息文件" a
 USING f, 重复 r
 WHERE f."序号" = a."序号" AND r.指纹 = f.指纹 AND a."序号" <> r.保留;

-- 未做(有意):不给这些表建业务键唯一索引 —— 候选键上都存在"同键不同内容"的行
-- (龙虎榜营业部 23025 组、分配文件 9286 组、龙虎榜 12 组、指数基本信息文件 2 组),
-- 真实业务键需业务确认;`market-platform data 数据体检` 会持续报告
-- "可调度表缺业务键唯一索引",确认后按表补迁移。

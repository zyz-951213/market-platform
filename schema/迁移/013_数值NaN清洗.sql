-- 013: numeric 'NaN' 清洗记录(幂等,可重复执行)
--
-- 背景:源数据的 'NaN' 字符串经 float() 变成 float('nan'),psycopg 会把它写成
-- PostgreSQL numeric 的**特殊值 NaN**。NaN 不是 NULL:
--   · `IS NULL` 查不出来,`count(col)` 照样计数;
--   · `avg`/`sum` 等聚合会**传播**它 —— 一个股票的异常派息(分配文件 派息比=NaN)
--     会把整个市场分段一整段区间的分位数变成 NaN(2026-05 深A/创业板/科创板实际发生)。
--
-- 本迁移只做"NaN → NULL"这一件事(语义上 NaN 在本库一律表示"缺失"),
-- 不影响任何真实数值。执行前请先跑一次只读体检:
--   market-platform data 清洗NaN
-- 需要留快照时用带备份的 CLI(会在 <表名>_NaN清洗备份_<日期> 留整行快照):
--   market-platform data 清洗NaN --apply
--
-- 2026-10-06 实测清理量:8 张表 / 23 列 / 7252 行(其中 分配文件 1396 行、
-- 无风险利率文件 6112 行、ETF基金申购赎回清单 1593 行、三大报表 3677 行)。


-- 分配文件:派息/送转比在源数据里存在 'NaN'(未分红 vs 缺失),按缺失处理
UPDATE "公共"."分配文件" SET
  "派息比(每10股税前)" = NULL WHERE "派息比(每10股税前)" = 'NaN'::numeric;
UPDATE "公共"."分配文件" SET
  "送股比(每10股)" = NULL WHERE "送股比(每10股)" = 'NaN'::numeric;
UPDATE "公共"."分配文件" SET
  "转增比(每10股)" = NULL WHERE "转增比(每10股)" = 'NaN'::numeric;
UPDATE "公共"."分配文件" SET
  "分配比率" = NULL WHERE "分配比率" = 'NaN'::numeric;

-- 无风险利率文件:1991–2019 年 LPR 尚未发布,源数据给的是 'NaN'
UPDATE "公共"."无风险利率文件" SET
  "无风险利率(%)" = NULL WHERE "无风险利率(%)" = 'NaN'::numeric;
UPDATE "公共"."无风险利率文件" SET
  "日度化无风险利率(%)" = NULL WHERE "日度化无风险利率(%)" = 'NaN'::numeric;
UPDATE "公共"."无风险利率文件" SET
  "周度化无风险利率(%)" = NULL WHERE "周度化无风险利率(%)" = 'NaN'::numeric;
UPDATE "公共"."无风险利率文件" SET
  "月度化无风险利率(%)" = NULL WHERE "月度化无风险利率(%)" = 'NaN'::numeric;

-- ETF 申购赎回清单:净值缺失
UPDATE "公共"."ETF基金申购赎回清单" SET
  "基金份额净值" = NULL WHERE "基金份额净值" = 'NaN'::numeric;

-- 三大报表:新准则下不再列示的科目(如 预收款项)、以及未披露科目,源数据给 'NaN'
UPDATE "公共"."资产负债表" SET "预收款项" = NULL WHERE "预收款项" = 'NaN'::numeric;
UPDATE "公共"."资产负债表" SET "存货净额" = NULL WHERE "存货净额" = 'NaN'::numeric;
UPDATE "公共"."资产负债表" SET "应收账款净额" = NULL WHERE "应收账款净额" = 'NaN'::numeric;
UPDATE "公共"."资产负债表" SET "应付账款" = NULL WHERE "应付账款" = 'NaN'::numeric;
UPDATE "公共"."资产负债表" SET "货币资金" = NULL WHERE "货币资金" = 'NaN'::numeric;
UPDATE "公共"."资产负债表" SET "所有者权益合计" = NULL WHERE "所有者权益合计" = 'NaN'::numeric;
UPDATE "公共"."资产负债表" SET "负债合计" = NULL WHERE "负债合计" = 'NaN'::numeric;

UPDATE "公共"."利润表" SET "营业利润" = NULL WHERE "营业利润" = 'NaN'::numeric;
UPDATE "公共"."利润表" SET "利润总额" = NULL WHERE "利润总额" = 'NaN'::numeric;
UPDATE "公共"."利润表" SET "营业总收入" = NULL WHERE "营业总收入" = 'NaN'::numeric;

UPDATE "公共"."现金流量表(合并)" SET
  "筹资活动产生的现金流量净额" = NULL WHERE "筹资活动产生的现金流量净额" = 'NaN'::numeric;
UPDATE "公共"."现金流量表(合并)" SET
  "投资活动产生的现金流量净额" = NULL WHERE "投资活动产生的现金流量净额" = 'NaN'::numeric;

-- 汇率文件:人民币元对美元汇率个别日期缺失
UPDATE "公共"."汇率文件" SET
  "人民币元对美元汇率" = NULL WHERE "人民币元对美元汇率" = 'NaN'::numeric;

-- 龙虎榜:卖出额缺失
UPDATE "公共"."龙虎榜" SET
  "龙虎榜卖出额" = NULL WHERE "龙虎榜卖出额" = 'NaN'::numeric;

-- 写入侧护栏(防止再产生):见
--   src/market_platform/data/存储/数据更新器.py  _to_n() / _insert()
-- NaN 与 ±Inf 一律按缺失处理。


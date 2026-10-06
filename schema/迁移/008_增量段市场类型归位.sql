-- 008:日市场回报率文件的「增量段」市场类型归位
--
-- 背景:007 把档案段(≤2026-05-20)的 市场类型 归位为 市场收益文件.market_type 编码;
-- 分界日之后由**旧版写入器**留下的 93 行仍是错标(47 行 'A股' + 46 行空),
-- 因为旧写入器把三个指数的行一律写成 'A股'。
--
-- 本迁移只做可证明的部分:按成交量与 公共.国内指数日行情文件 唯一匹配的行,
-- 用**指数中文简称**作为市场类型(与修复后的写入器一致);匹配不到的 46 行保持原样并留痕。
-- 实测:47/47 行都能唯一匹配到指数,0 歧义。

DO $$
DECLARE
  归位数 integer;
BEGIN
  WITH 候选 AS (
    SELECT m."序号", min(i."指数中文简称") AS 名称
    FROM 公共.日市场回报率文件 m
    JOIN 公共.国内指数日行情文件 i
      ON i."交易日期" = m."交易日期"
     AND i."成份证券成交量" IS NOT NULL
     AND abs(i."成份证券成交量" - m."日市场交易总股数") < 0.5
    WHERE m."市场类型" IN ('A股', '')
    GROUP BY m."序号"
    HAVING count(DISTINCT i."指数中文简称") = 1
  )
  UPDATE 公共.日市场回报率文件 m
     SET "市场类型" = 候选.名称,
         updated_at = now()
    FROM 候选
   WHERE m."序号" = 候选."序号";
  GET DIAGNOSTICS 归位数 = ROW_COUNT;
  RAISE NOTICE '增量段按成交量唯一匹配归位 % 行(市场类型=指数简称)', 归位数;
END $$;

DO $$
DECLARE
  剩余 integer;
BEGIN
  SELECT count(*) INTO 剩余 FROM 公共.日市场回报率文件 WHERE "市场类型" = '' OR "市场类型" IS NULL;
  IF 剩余 > 0 THEN
    RAISE NOTICE '仍有 % 行市场类型为空(无法按成交量唯一匹配),保持原样并已在登记表备注', 剩余;
  END IF;
END $$;

UPDATE 行情增量.数据集登记
   SET 备注 = COALESCE(NULLIF(备注, ''), '') ||
              CASE WHEN COALESCE(备注, '') = '' THEN '' ELSE ' | ' END ||
              '档案段(≤2026-05-20)市场类型=市场收益文件编码;增量段(>2026-05-20)=指数简称;'
              '两段口径不同,如需统一需先确认 market_type 编码(当前无文档)'
 WHERE 数据源 = '公共' AND 数据集 = '日市场回报率文件';

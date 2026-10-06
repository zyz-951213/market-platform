-- 007:日市场回报率文件 —— 按成交量归位市场类型(修正 006 的误判)
--
-- 为什么要有 007(重要):
--   006 假设同一 (交易日期, 市场类型) 下的多行是"重复",按"非空最多→序号最小"只保留一行。
--   随后核对发现 **假设是错的**:例如 2026-05-08 的 7 行 'B股',其成交量分别精确等于
--   `公共.市场收益文件` 中 7 个 market_type 编码(2/8/32/64/16/1/4)的 volume ——
--   也就是说:这些行**本来是不同市场**,只是 `市场类型` 列被整批错标成了同一个值。
--   按 006 的规则去重会丢掉 6/7 的真实数据。
--
--   实测(备份表全量核对):41424 行中 40965 行能够**唯一**匹配到某个 market_type 编码,
--   命中多个编码的行 = 0 ⇒ 可以可靠归位,不是猜测。
--
-- 本迁移的动作:
--   1) 校验 006 的备份表存在(没有就报错终止,避免无源可依);
--   2) 删掉 006 建的唯一索引,把表恢复成备份内容(即撤销 006 的错误去重);
--   3) 按成交量唯一匹配,把 `市场类型` 归位为 `公共.市场收益文件.market_type` 的编码;
--   4) 对仍然重复的 (交易日期, 市场类型) 按"非空最多→序号最小"保留一行;
--   5) 重建唯一索引;并把口径说明写进 数据集登记.备注。
--
-- 幂等:第 2 步每次都以备份为准重建,因此可重复执行。

DO $$
BEGIN
  IF to_regclass('公共.日市场回报率文件_清洗备份_20261006') IS NULL THEN
    RAISE EXCEPTION '缺少 006 的备份表 公共.日市场回报率文件_清洗备份_20261006,无法安全重建;请先执行 006';
  END IF;
END $$;

-- 1) 撤销 006 的唯一索引并恢复原数据
DROP INDEX IF EXISTS 公共.日市场回报率文件_交易日期_市场类型_uidx;

DELETE FROM 公共.日市场回报率文件;
INSERT INTO 公共.日市场回报率文件 SELECT * FROM 公共.日市场回报率文件_清洗备份_20261006;

DO $$
DECLARE
  恢复数 integer;
BEGIN
  SELECT count(*) INTO 恢复数 FROM 公共.日市场回报率文件;
  RAISE NOTICE '已从备份恢复 % 行(撤销 006 的错误去重)', 恢复数;
END $$;

-- 2) 按成交量唯一匹配,把市场类型归位为 market_type 编码
DO $$
DECLARE
  归位数 integer;
BEGIN
  WITH 候选 AS (
    SELECT m."序号", min(sm.market_type)::text AS 编码
    FROM 公共.日市场回报率文件 m
    JOIN 公共.市场收益文件 sm
      ON sm.trade_date = m."交易日期"
     AND sm.volume = m."日市场交易总股数"
    GROUP BY m."序号"
    HAVING count(DISTINCT sm.market_type) = 1     -- 只接受唯一命中
  )
  UPDATE 公共.日市场回报率文件 m
     SET "市场类型" = 候选.编码,
         updated_at = now()
    FROM 候选
   WHERE m."序号" = 候选."序号";
  GET DIAGNOSTICS 归位数 = ROW_COUNT;
  RAISE NOTICE '按成交量唯一匹配归位 % 行(市场类型 = 市场收益文件.market_type 编码)', 归位数;
END $$;

-- 3) 剩余重复组:保留非空字段最多、序号最小的一行
DO $$
DECLARE
  删前 integer;
  删后 integer;
BEGIN
  SELECT count(*) INTO 删前 FROM 公共.日市场回报率文件;
  WITH 排序 AS (
    SELECT "序号",
           row_number() OVER (
             PARTITION BY "交易日期", "市场类型"
             ORDER BY (
               (CASE WHEN "日市场交易总股数" IS NULL THEN 0 ELSE 1 END) +
               (CASE WHEN "日市场交易总金额" IS NULL THEN 0 ELSE 1 END) +
               (CASE WHEN "考虑现金红利再投资的日市场回报率(等权平均" IS NULL THEN 0 ELSE 1 END) +
               (CASE WHEN "考虑现金红利再投资的日市场回报率(流通市值" IS NULL THEN 0 ELSE 1 END) +
               (CASE WHEN "考虑现金红利再投资的日市场回报率(总市值加" IS NULL THEN 0 ELSE 1 END)
             ) DESC,
             "序号" ASC
           ) AS rn
    FROM 公共.日市场回报率文件
  )
  DELETE FROM 公共.日市场回报率文件 t
   USING 排序 s
   WHERE t."序号" = s."序号" AND s.rn > 1;
  SELECT count(*) INTO 删后 FROM 公共.日市场回报率文件;
  RAISE NOTICE '归位后仍有重复的键: % → % 行(仅此部分按保留规则清理)', 删前, 删后;
END $$;

-- 4) 重建唯一索引
CREATE UNIQUE INDEX IF NOT EXISTS 日市场回报率文件_交易日期_市场类型_uidx
  ON 公共.日市场回报率文件 ("交易日期", "市场类型");

COMMENT ON INDEX 公共.日市场回报率文件_交易日期_市场类型_uidx IS
  '业务键:(交易日期, 市场类型)。市场类型 = 公共.市场收益文件.market_type 的编码;
   历史批次曾被整批错标(如 7 个不同市场的行都写成 ''B股''),2026-10-06 按成交量唯一匹配归位。';

-- 5) 口径留痕
UPDATE 行情增量.数据集登记
   SET 备注 = COALESCE(NULLIF(备注, ''), '') ||
              CASE WHEN COALESCE(备注, '') = '' THEN '' ELSE ' | ' END ||
              '市场类型已按 市场收益文件.market_type 编码归位(2026-10-06 迁移 007);'
              '此前曾整批错标文字市场名,历史按 006 的去重已撤销(备份可查)'
 WHERE 数据源 = '公共' AND 数据集 = '日市场回报率文件';

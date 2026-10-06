-- 016:重算 `个股日行情文件.涨跌幅`(baostock 行),彻底消除量纲混用
--
-- 为什么还要一次:迁移 015 用"|涨跌幅| > 0.15 则除以 100"修复存量行,但
--   ① 当时仍在运行的旧回补进程(启动于修复之前)继续写入百分数行;
--   ② 涨幅小于 15% 的百分数行(|v| ≤ 0.15)无法靠阈值识别(如 -3.4925 实为 -3.49%)。
-- 因此改为**确定性重算**:baostock 行的 `涨跌幅` 由同日 `收盘价/昨收盘 - 1` 得到
-- (与 baostock 自己的 pctChg 同源,adjustflag=3 不复权,四舍五入到 6 位)。
-- 该式与当前值无关 ⇒ 天然幂等,重复执行结果不变。
--
-- 判据(执行后应满足):按日 avg(abs(涨跌幅)) ∈ [0.005, 0.06];max(abs(涨跌幅)) ≤ 6.9
--   (北交所新股上市首日可涨 600%+)。

-- 备份受影响行(仅首次建立)
CREATE TABLE IF NOT EXISTS 公共."个股日行情文件_涨跌幅重算备份_20261006" AS
SELECT * FROM 公共."个股日行情文件" WHERE false;

INSERT INTO 公共."个股日行情文件_涨跌幅重算备份_20261006"
SELECT * FROM 公共."个股日行情文件" f
WHERE f.source = 'baostock'
  AND f."昨收盘" IS NOT NULL AND f."昨收盘" > 0 AND f."收盘价" IS NOT NULL
  AND NOT EXISTS (
    SELECT 1 FROM 公共."个股日行情文件_涨跌幅重算备份_20261006" b WHERE b."序号" = f."序号"
  );

-- 确定性重算
UPDATE 公共."个股日行情文件"
   SET "涨跌幅" = round(("收盘价" / "昨收盘" - 1)::numeric, 6)
 WHERE source = 'baostock'
   AND "昨收盘" IS NOT NULL AND "昨收盘" > 0
   AND "收盘价" IS NOT NULL;

-- 自证:执行后仍存在的"百分数量纲"行数(应为 0)
DO $$
DECLARE n bigint;
BEGIN
  SELECT count(*) INTO n FROM 公共."个股日行情文件"
   WHERE source = 'baostock' AND "涨跌幅" IS NOT NULL AND abs("涨跌幅") > 8;
  IF n > 0 THEN
    RAISE EXCEPTION '仍存在 %.0f 行疑似百分数量纲(|涨跌幅| > 8)', n;
  END IF;
END $$;

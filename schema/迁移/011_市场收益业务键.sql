-- 011:市场收益文件 补业务键(让增量构建器真正幂等)
--
-- 背景:`公共.市场收益文件` 只有代理主键,构建器写入用 `ON CONFLICT DO NOTHING` 时**没有冲突目标**
-- ⇒ 重复运行会不断插入重复行(实测当前 0 重复,因为只跑过一次)。
-- 本迁移:先清理潜在重复,再按 (trade_date, market_type, 数据段) 建唯一索引。
--
-- 幂等:可重复执行。

-- 清理完全重复的业务键(保留行数最少的一行:自算段可重建,档案段保留最早导入的那条)
DELETE FROM 公共.市场收益文件 t
 USING 公共.市场收益文件 d
 WHERE t.ctid <> d.ctid
   AND t.trade_date = d.trade_date
   AND t.market_type = d.market_type
   AND t.数据段 = d.数据段
   AND t.ctid > d.ctid;

CREATE UNIQUE INDEX IF NOT EXISTS 市场收益文件_日期_分段_数据段_uidx
  ON 公共.市场收益文件 (trade_date, market_type, 数据段);

COMMENT ON INDEX 公共.市场收益文件_日期_分段_数据段_uidx IS
  '业务键:(trade_date, market_type, 数据段)。档案段来自 CSMAR,计算段由 公共.日行情 聚合(可重算)。';

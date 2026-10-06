-- 009:缺口登记补业务键唯一约束(否则扫描会重复插入)
--
-- 背景(TASK-003 D4):`行情增量.缺口登记` 此前只有 id 主键,没有 (数据源, 数据集, 起始日, 结束日)
-- 的业务键;扫描器若用 ON CONFLICT DO NOTHING 将**无法去重**,重复扫描会不断堆行。
-- 本迁移:先清理完全重复的行(若有),再补业务键唯一索引。
--
-- 幂等:可重复执行。

-- 清理业务键上完全重复的行(保留 id 最小的一行)
DELETE FROM 行情增量.缺口登记 t
 USING 行情增量.缺口登记 d
 WHERE t.id > d.id
   AND t.数据源 = d.数据源
   AND t.数据集 = d.数据集
   AND t.起始日 IS NOT DISTINCT FROM d.起始日
   AND t.结束日 IS NOT DISTINCT FROM d.结束日;

CREATE UNIQUE INDEX IF NOT EXISTS 缺口登记_业务键_uidx
  ON 行情增量.缺口登记 (数据源, 数据集, 起始日, 结束日);

COMMENT ON INDEX 行情增量.缺口登记_业务键_uidx IS
  '业务键:(数据源, 数据集, 起始日, 结束日)。扫描器按此幂等登记;状态在缺口修复后置为已修复。';

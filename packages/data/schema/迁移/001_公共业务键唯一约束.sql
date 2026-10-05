-- 公共仓：对无重复的业务键加 UNIQUE，便于 ON CONFLICT 幂等写入
-- 跳过：国内指数日行情文件、日市场回报率文件（仍有业务键重复，需先清洗）

CREATE UNIQUE INDEX IF NOT EXISTS 个股日行情文件_证券代码_交易日期_uidx
  ON 公共.个股日行情文件 ("证券代码", "交易日期");

CREATE UNIQUE INDEX IF NOT EXISTS 日历文件_日历日期_市场类型_uidx
  ON 公共.日历文件 ("日历日期", "市场类型");

CREATE UNIQUE INDEX IF NOT EXISTS 无风险利率文件_统计日期_基准_uidx
  ON 公共.无风险利率文件 ("统计日期", "无风险利率基准");

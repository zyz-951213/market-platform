#!/usr/bin/env bash
set -euo pipefail
psql -d "金融市场数据" -v ON_ERROR_STOP=1 <<'SQL'
INSERT INTO 行情增量.同步水位 ("数据源", "数据集", "最后交易日", "行数", "备注", "更新时间")
SELECT '公共', '个股日行情文件', MAX("交易日期"), COUNT(*), '原始宽表(akshare/baostock)', now()
FROM 公共.个股日行情文件
ON CONFLICT ("数据源", "数据集") DO UPDATE SET
  "最后交易日"=EXCLUDED."最后交易日", "行数"=EXCLUDED."行数", "备注"=EXCLUDED."备注", "更新时间"=now();

INSERT INTO 行情增量.同步水位 ("数据源", "数据集", "最后交易日", "行数", "备注", "更新时间")
SELECT '公共', '日行情', MAX(trade_date), COUNT(*), '档案∪增量接口', now()
FROM 公共.日行情
ON CONFLICT ("数据源", "数据集") DO UPDATE SET
  "最后交易日"=EXCLUDED."最后交易日", "行数"=EXCLUDED."行数", "备注"=EXCLUDED."备注", "更新时间"=now();

INSERT INTO 行情增量.同步水位 ("数据源", "数据集", "最后交易日", "行数", "备注", "更新时间")
SELECT '行为分析', '日行情', MAX("交易日期"), COUNT(*), '应用瘦表', now()
FROM 行为分析.日行情
ON CONFLICT ("数据源", "数据集") DO UPDATE SET
  "最后交易日"=EXCLUDED."最后交易日", "行数"=EXCLUDED."行数", "更新时间"=now();

SELECT * FROM 行情增量.同步水位 ORDER BY 1,2;
SQL

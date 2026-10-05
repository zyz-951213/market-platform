-- 特处第二轮共享去重：与微盘完全一致的表 → 视图
-- 保留物理表：曾用名、特别处理（有少量行差）、以及 ST 专有表

BEGIN;

DROP VIEW IF EXISTS 特处兼容.st_ipo CASCADE;
DROP VIEW IF EXISTS 特处兼容.st_no_limit CASCADE;
DROP VIEW IF EXISTS 特处兼容.st_market_returns CASCADE;
DROP VIEW IF EXISTS 特处兼容.st_industry CASCADE;
DROP VIEW IF EXISTS 特处兼容.st_monthly_returns CASCADE;
DROP VIEW IF EXISTS 特处兼容.st_daily_returns CASCADE;

DROP TABLE IF EXISTS 特别处理研究.新股发行 CASCADE;
DROP TABLE IF EXISTS 特别处理研究.无涨跌停 CASCADE;
DROP TABLE IF EXISTS 特别处理研究.市场收益 CASCADE;
DROP TABLE IF EXISTS 特别处理研究.行业归属 CASCADE;
DROP TABLE IF EXISTS 特别处理研究.月收益率 CASCADE;
DROP TABLE IF EXISTS 特别处理研究.日收益率 CASCADE;

CREATE VIEW 特别处理研究.新股发行 AS SELECT * FROM 微盘股研究.新股发行;
CREATE VIEW 特别处理研究.无涨跌停 AS SELECT * FROM 微盘股研究.无涨跌停;
CREATE VIEW 特别处理研究.市场收益 AS SELECT * FROM 微盘股研究.市场收益;
CREATE VIEW 特别处理研究.行业归属 AS SELECT * FROM 微盘股研究.行业归属;
CREATE VIEW 特别处理研究.月收益率 AS SELECT * FROM 微盘股研究.月收益率;
CREATE VIEW 特别处理研究.日收益率 AS SELECT * FROM 微盘股研究.日收益率;

CREATE OR REPLACE VIEW 特处兼容.st_ipo AS SELECT * FROM 特别处理研究.新股发行;
CREATE OR REPLACE VIEW 特处兼容.st_no_limit AS SELECT * FROM 特别处理研究.无涨跌停;
CREATE OR REPLACE VIEW 特处兼容.st_market_returns AS SELECT * FROM 特别处理研究.市场收益;
CREATE OR REPLACE VIEW 特处兼容.st_industry AS SELECT * FROM 特别处理研究.行业归属;
CREATE OR REPLACE VIEW 特处兼容.st_monthly_returns AS SELECT * FROM 特别处理研究.月收益率;
CREATE OR REPLACE VIEW 特处兼容.st_daily_returns AS SELECT * FROM 特别处理研究.日收益率;

COMMIT;

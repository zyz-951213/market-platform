-- 特处与微盘完全重复的五类事实表 → 改为指向微盘真源的视图
-- 预计释放约 4.9GB；特处兼容.st_* 重建后仍可用原英文表名

BEGIN;

-- 先去掉依赖兼容视图
DROP VIEW IF EXISTS 特处兼容.st_daily CASCADE;
DROP VIEW IF EXISTS 特处兼容.st_calendar CASCADE;
DROP VIEW IF EXISTS 特处兼容.st_company CASCADE;
DROP VIEW IF EXISTS 特处兼容.st_listing_status CASCADE;
DROP VIEW IF EXISTS 特处兼容.st_index_daily CASCADE;

DROP TABLE IF EXISTS 特别处理研究.日行情 CASCADE;
DROP TABLE IF EXISTS 特别处理研究.交易日历 CASCADE;
DROP TABLE IF EXISTS 特别处理研究.公司档案 CASCADE;
DROP TABLE IF EXISTS 特别处理研究.上市状态 CASCADE;
DROP TABLE IF EXISTS 特别处理研究.指数日行情 CASCADE;

CREATE VIEW 特别处理研究.日行情 AS
SELECT * FROM 微盘股研究.日行情;

CREATE VIEW 特别处理研究.交易日历 AS
SELECT * FROM 微盘股研究.交易日历;

CREATE VIEW 特别处理研究.公司档案 AS
SELECT * FROM 微盘股研究.公司档案;

CREATE VIEW 特别处理研究.上市状态 AS
SELECT * FROM 微盘股研究.上市状态;

CREATE VIEW 特别处理研究.指数日行情 AS
SELECT * FROM 微盘股研究.指数日行情;

-- 兼容英文名（与研究库兼容视图.sql 一致）
CREATE SCHEMA IF NOT EXISTS 特处兼容;
CREATE OR REPLACE VIEW 特处兼容.st_daily AS SELECT * FROM 特别处理研究.日行情;
CREATE OR REPLACE VIEW 特处兼容.st_calendar AS SELECT * FROM 特别处理研究.交易日历;
CREATE OR REPLACE VIEW 特处兼容.st_company AS SELECT * FROM 特别处理研究.公司档案;
CREATE OR REPLACE VIEW 特处兼容.st_listing_status AS SELECT * FROM 特别处理研究.上市状态;
CREATE OR REPLACE VIEW 特处兼容.st_index_daily AS SELECT * FROM 特别处理研究.指数日行情;

COMMIT;

-- 三层架构：公共接口 + 行情增量
-- 真源仍落在既有物理表；本 schema 只提供稳定只读接口（及增量水位表）

CREATE SCHEMA IF NOT EXISTS 公共;
CREATE SCHEMA IF NOT EXISTS 行情增量;

COMMENT ON SCHEMA 公共 IS '跨项目稳定接口：主数据与行情真源视图，项目 schema 应读此层而非互相拷贝';
COMMENT ON SCHEMA 行情增量 IS '行情增量管道元数据：水位、缺口；不含业务研究表';

-- ========== 研究口径日行情（由 公共日行情_档案加增量.sql 定义）==========
-- 公共.日行情 = 微盘档案(≤2026-05-20) ∪ 核心增量(akshare/baostock)
-- 此处保留占位说明；部署请执行 schema/公共日行情_档案加增量.sql

CREATE OR REPLACE VIEW 公共.交易日历 AS
SELECT * FROM 微盘股研究.交易日历;

CREATE OR REPLACE VIEW 公共.公司档案 AS
SELECT * FROM 微盘股研究.公司档案;

CREATE OR REPLACE VIEW 公共.上市状态 AS
SELECT * FROM 微盘股研究.上市状态;

CREATE OR REPLACE VIEW 公共.指数日行情 AS
SELECT * FROM 微盘股研究.指数日行情;

-- ========== 应用口径（行为分析瘦表 + 主数据）==========
CREATE OR REPLACE VIEW 公共.日行情_应用 AS
SELECT * FROM 行为分析.日行情;

CREATE OR REPLACE VIEW 公共.证券档案 AS
SELECT * FROM 主数据.证券档案;

-- ========== 原始仓（database 项目写入的宽表）==========
CREATE OR REPLACE VIEW 公共.个股日行情_原始 AS
SELECT * FROM 核心市场数据.个股日行情文件;

CREATE OR REPLACE VIEW 公共.指数日行情_原始 AS
SELECT * FROM 核心市场数据.国内指数日行情文件;

CREATE OR REPLACE VIEW 公共.日历_原始 AS
SELECT * FROM 核心市场数据.日历文件;

CREATE OR REPLACE VIEW 公共.公司档案_原始 AS
SELECT * FROM 核心市场数据.公司文件;

-- 研究扩展共享（真源仍在微盘）
CREATE OR REPLACE VIEW 公共.日收益率 AS
SELECT * FROM 微盘股研究.日收益率;

CREATE OR REPLACE VIEW 公共.月收益率 AS
SELECT * FROM 微盘股研究.月收益率;

CREATE OR REPLACE VIEW 公共.行业归属 AS
SELECT * FROM 微盘股研究.行业归属;

CREATE OR REPLACE VIEW 公共.市场收益 AS
SELECT * FROM 微盘股研究.市场收益;

CREATE OR REPLACE VIEW 公共.新股发行 AS
SELECT * FROM 微盘股研究.新股发行;

CREATE OR REPLACE VIEW 公共.无涨跌停 AS
SELECT * FROM 微盘股研究.无涨跌停;

-- 原始宽表 → 应用瘦表列形投影（复权/口径与行为分析.日行情未必一致，供对照与未来对齐）
CREATE OR REPLACE VIEW 公共.日行情_应用投影 AS
SELECT
  "证券代码" AS 证券代码,
  "交易日期" AS 交易日期,
  "开盘价"::double precision AS 开盘价,
  "最高价"::double precision AS 最高价,
  "最低价"::double precision AS 最低价,
  "收盘价"::double precision AS 收盘价,
  "成交量(股)"::double precision AS 成交量,
  "成交金额(元)"::double precision AS 成交额
FROM 核心市场数据.个股日行情文件;


-- ========== 行情增量：同步水位 ==========
CREATE TABLE IF NOT EXISTS 行情增量.同步水位 (
  数据源       text NOT NULL,
  数据集       text NOT NULL,
  最后交易日   date,
  行数         bigint,
  校验和       text,
  备注         text,
  更新时间     timestamp without time zone NOT NULL DEFAULT now(),
  PRIMARY KEY (数据源, 数据集)
);

COMMENT ON TABLE 行情增量.同步水位 IS '各行情数据集增量水位；由 database / 导入脚本维护';

CREATE TABLE IF NOT EXISTS 行情增量.缺口登记 (
  id           bigserial PRIMARY KEY,
  数据源       text NOT NULL,
  数据集       text NOT NULL,
  证券代码     text,
  起始日       date NOT NULL,
  结束日       date NOT NULL,
  状态         text NOT NULL DEFAULT '待补',
  备注         text,
  创建时间     timestamp without time zone NOT NULL DEFAULT now(),
  更新时间     timestamp without time zone NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS 缺口登记_状态_idx ON 行情增量.缺口登记 (状态);

-- 初始化水位（只插不覆盖，便于首次）
INSERT INTO 行情增量.同步水位 (数据源, 数据集, 最后交易日, 行数, 备注)
SELECT '微盘股研究', '日行情', max(trade_date), count(*), '研究真源 CSMAR'
FROM 微盘股研究.日行情
ON CONFLICT DO NOTHING;

INSERT INTO 行情增量.同步水位 (数据源, 数据集, 最后交易日, 行数, 备注)
SELECT '行为分析', '日行情', max("交易日期"), count(*), '应用瘦表'
FROM 行为分析.日行情
ON CONFLICT DO NOTHING;

INSERT INTO 行情增量.同步水位 (数据源, 数据集, 最后交易日, 行数, 备注)
SELECT '核心市场数据', '个股日行情文件', max("交易日期"), count(*), '原始宽表'
FROM 核心市场数据.个股日行情文件
ON CONFLICT DO NOTHING;

-- 统一库：金融市场数据
-- 在已有原始五 schema 之上，新增主数据 / 行为分析 / 指数预测

CREATE SCHEMA IF NOT EXISTS 主数据;
CREATE SCHEMA IF NOT EXISTS 行为分析;
CREATE SCHEMA IF NOT EXISTS 指数预测;

-- ========== 主数据 ==========
CREATE TABLE IF NOT EXISTS 主数据.证券档案 (
    证券代码 CHAR(6) PRIMARY KEY,
    证券简称 TEXT NOT NULL DEFAULT '',
    交易市场 CHAR(2) NOT NULL DEFAULT 'sz',
    所属行业 TEXT NOT NULL DEFAULT '',
    一级行业 TEXT NOT NULL DEFAULT '',
    二级行业 TEXT NOT NULL DEFAULT '',
    三级行业 TEXT NOT NULL DEFAULT '',
    数据路径 TEXT NOT NULL DEFAULT '',
    输出路径 TEXT NOT NULL DEFAULT '',
    更新时间 TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ========== 行为分析（原 market_agents）==========
CREATE TABLE IF NOT EXISTS 行为分析.日行情 (
    证券代码 CHAR(6) NOT NULL REFERENCES 主数据.证券档案(证券代码) ON DELETE CASCADE,
    交易日期 DATE NOT NULL,
    开盘价 DOUBLE PRECISION,
    最高价 DOUBLE PRECISION,
    最低价 DOUBLE PRECISION,
    收盘价 DOUBLE PRECISION,
    成交量 DOUBLE PRECISION,
    成交额 DOUBLE PRECISION,
    PRIMARY KEY (证券代码, 交易日期)
);
CREATE INDEX IF NOT EXISTS 日行情_交易日期_索引 ON 行为分析.日行情(交易日期);

CREATE TABLE IF NOT EXISTS 行为分析.资金流向 (
    证券代码 CHAR(6) NOT NULL REFERENCES 主数据.证券档案(证券代码) ON DELETE CASCADE,
    交易日期 DATE NOT NULL,
    收盘价 DOUBLE PRECISION,
    涨跌幅 DOUBLE PRECISION,
    主力净流入 DOUBLE PRECISION,
    主力净占比 DOUBLE PRECISION,
    超大单净流入 DOUBLE PRECISION,
    超大单净占比 DOUBLE PRECISION,
    大单净流入 DOUBLE PRECISION,
    大单净占比 DOUBLE PRECISION,
    中单净流入 DOUBLE PRECISION,
    中单净占比 DOUBLE PRECISION,
    小单净流入 DOUBLE PRECISION,
    小单净占比 DOUBLE PRECISION,
    PRIMARY KEY (证券代码, 交易日期)
);

CREATE TABLE IF NOT EXISTS 行为分析.策略净值 (
    证券代码 CHAR(6) NOT NULL REFERENCES 主数据.证券档案(证券代码) ON DELETE CASCADE,
    交易日期 DATE NOT NULL,
    建议仓位 DOUBLE PRECISION,
    市场状态 TEXT,
    状态得分 DOUBLE PRECISION,
    市场收益率 DOUBLE PRECISION,
    仓位 DOUBLE PRECISION,
    仓位变动 DOUBLE PRECISION,
    策略收益率 DOUBLE PRECISION,
    基准收益率 DOUBLE PRECISION,
    策略净值 DOUBLE PRECISION,
    基准净值 DOUBLE PRECISION,
    PRIMARY KEY (证券代码, 交易日期)
);
CREATE INDEX IF NOT EXISTS 策略净值_交易日期_索引 ON 行为分析.策略净值(交易日期);

CREATE TABLE IF NOT EXISTS 行为分析.标的时序 (
    证券代码 CHAR(6) NOT NULL REFERENCES 主数据.证券档案(证券代码) ON DELETE CASCADE,
    序列类型 TEXT NOT NULL,
    交易日期 DATE NOT NULL,
    载荷 JSONB NOT NULL,
    PRIMARY KEY (证券代码, 序列类型, 交易日期)
);
CREATE INDEX IF NOT EXISTS 标的时序_序列类型_索引 ON 行为分析.标的时序(序列类型);

CREATE TABLE IF NOT EXISTS 行为分析.标的产物 (
    证券代码 CHAR(6) NOT NULL REFERENCES 主数据.证券档案(证券代码) ON DELETE CASCADE,
    产物类型 TEXT NOT NULL,
    载荷 JSONB NOT NULL,
    更新时间 TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (证券代码, 产物类型)
);

CREATE TABLE IF NOT EXISTS 行为分析.标的摘要 (
    证券代码 CHAR(6) PRIMARY KEY REFERENCES 主数据.证券档案(证券代码) ON DELETE CASCADE,
    所属行业 TEXT NOT NULL DEFAULT '',
    证券简称 TEXT NOT NULL DEFAULT '',
    最新状态 TEXT NOT NULL DEFAULT '',
    建议仓位 DOUBLE PRECISION,
    策略收益率 DOUBLE PRECISION,
    基准收益率 DOUBLE PRECISION,
    超额收益率 DOUBLE PRECISION,
    策略夏普 DOUBLE PRECISION,
    异常数 DOUBLE PRECISION,
    状态 TEXT NOT NULL DEFAULT 'ok',
    输出路径 TEXT NOT NULL DEFAULT '',
    分析时间 TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS 标的摘要_行业_索引 ON 行为分析.标的摘要(所属行业);
CREATE INDEX IF NOT EXISTS 标的摘要_状态_索引 ON 行为分析.标的摘要(状态);

CREATE TABLE IF NOT EXISTS 行为分析.行业摘要 (
    所属行业 TEXT PRIMARY KEY,
    标的数量 INTEGER NOT NULL DEFAULT 0,
    平均超额收益率 DOUBLE PRECISION,
    中位超额收益率 DOUBLE PRECISION,
    胜率 DOUBLE PRECISION,
    平均策略收益率 DOUBLE PRECISION,
    平均基准收益率 DOUBLE PRECISION,
    平均策略夏普 DOUBLE PRECISION,
    平均建议仓位 DOUBLE PRECISION,
    更新时间 TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS 行为分析.市场时序 (
    序列名称 TEXT NOT NULL,
    交易日期 DATE NOT NULL,
    载荷 JSONB NOT NULL,
    PRIMARY KEY (序列名称, 交易日期)
);

CREATE TABLE IF NOT EXISTS 行为分析.组合 (
    组合编号 TEXT PRIMARY KEY,
    组合名称 TEXT NOT NULL DEFAULT '',
    元数据 JSONB NOT NULL,
    创建时间 TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS 行为分析.组合净值 (
    组合编号 TEXT NOT NULL REFERENCES 行为分析.组合(组合编号) ON DELETE CASCADE,
    交易日期 DATE NOT NULL,
    组合净值 DOUBLE PRECISION,
    基准净值 DOUBLE PRECISION,
    组合收益率 DOUBLE PRECISION,
    基准收益率 DOUBLE PRECISION,
    组合仓位 DOUBLE PRECISION,
    PRIMARY KEY (组合编号, 交易日期)
);

CREATE TABLE IF NOT EXISTS 行为分析.用户 (
    编号 SERIAL PRIMARY KEY,
    用户名 VARCHAR(50) NOT NULL UNIQUE,
    邮箱 VARCHAR(100) NOT NULL UNIQUE,
    密码哈希 VARCHAR(255) NOT NULL,
    是否启用 BOOLEAN DEFAULT TRUE,
    创建时间 TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    更新时间 TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS 行为分析.应用状态 (
    键 TEXT PRIMARY KEY,
    载荷 JSONB NOT NULL,
    更新时间 TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ========== 指数预测（原 market_predictor）==========
CREATE TABLE IF NOT EXISTS 指数预测.指数特征 (
    指数代码 TEXT NOT NULL,
    交易日期 DATE NOT NULL,
    载荷 JSONB NOT NULL,
    PRIMARY KEY (指数代码, 交易日期)
);
CREATE INDEX IF NOT EXISTS 指数特征_交易日期_索引 ON 指数预测.指数特征(交易日期);

CREATE TABLE IF NOT EXISTS 指数预测.时序数据 (
    数据集键 TEXT NOT NULL,
    交易日期 DATE NOT NULL,
    载荷 JSONB NOT NULL,
    PRIMARY KEY (数据集键, 交易日期)
);
CREATE INDEX IF NOT EXISTS 时序数据_数据集键_索引 ON 指数预测.时序数据(数据集键);

CREATE TABLE IF NOT EXISTS 指数预测.数据集快照 (
    数据集键 TEXT PRIMARY KEY,
    记录 JSONB NOT NULL,
    更新时间 TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS 指数预测.大模型缓存 (
    模型 TEXT NOT NULL,
    提示词版本 TEXT NOT NULL,
    提示词摘要 TEXT NOT NULL,
    行情日期 DATE NOT NULL,
    响应 JSONB NOT NULL,
    创建时间 TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (模型, 提示词版本, 提示词摘要, 行情日期)
);
CREATE INDEX IF NOT EXISTS 大模型缓存_行情日期_索引 ON 指数预测.大模型缓存(行情日期);

CREATE TABLE IF NOT EXISTS 指数预测.回测记录 (
    运行编号 TEXT PRIMARY KEY,
    是否最新 BOOLEAN NOT NULL DEFAULT FALSE,
    模型名称 TEXT,
    行情频率 TEXT,
    策略指纹 TEXT,
    数据截至 DATE,
    保存时间 TIMESTAMPTZ,
    指标 JSONB NOT NULL DEFAULT '{}'::jsonb,
    分类 JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS 回测记录_是否最新_索引 ON 指数预测.回测记录(是否最新) WHERE 是否最新;

CREATE TABLE IF NOT EXISTS 指数预测.回测信号 (
    运行编号 TEXT NOT NULL REFERENCES 指数预测.回测记录(运行编号) ON DELETE CASCADE,
    交易日期 DATE NOT NULL,
    载荷 JSONB NOT NULL,
    PRIMARY KEY (运行编号, 交易日期)
);
CREATE INDEX IF NOT EXISTS 回测信号_运行编号_索引 ON 指数预测.回测信号(运行编号);

CREATE TABLE IF NOT EXISTS 指数预测.拉取元数据 (
    数据集键 TEXT PRIMARY KEY,
    载荷 JSONB NOT NULL,
    更新时间 TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS 指数预测.应用状态 (
    键 TEXT PRIMARY KEY,
    载荷 JSONB NOT NULL,
    更新时间 TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

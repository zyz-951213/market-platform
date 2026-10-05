-- market-agents PostgreSQL schema（中文基表 + 英文兼容视图）
-- search_path 建议: 行为分析, 公共, 行情增量

CREATE SCHEMA IF NOT EXISTS 公共;
CREATE SCHEMA IF NOT EXISTS 行为分析;

CREATE TABLE IF NOT EXISTS 公共.证券档案 (
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

CREATE TABLE IF NOT EXISTS 行为分析.日行情 (
    证券代码 CHAR(6) NOT NULL REFERENCES 公共.证券档案(证券代码) ON DELETE CASCADE,
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
    证券代码 CHAR(6) NOT NULL REFERENCES 公共.证券档案(证券代码) ON DELETE CASCADE,
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
    证券代码 CHAR(6) NOT NULL REFERENCES 公共.证券档案(证券代码) ON DELETE CASCADE,
    交易日期 DATE NOT NULL,
    建议仓位 DOUBLE PRECISION,
    市场状态 TEXT,
    状态得分 DOUBLE PRECISION,
    仓位 DOUBLE PRECISION,
    仓位变动 DOUBLE PRECISION,
    策略收益率 DOUBLE PRECISION,
    基准收益率 DOUBLE PRECISION,
    策略净值 DOUBLE PRECISION,
    基准净值 DOUBLE PRECISION,
    PRIMARY KEY (证券代码, 交易日期)
);
CREATE INDEX IF NOT EXISTS 策略净值_交易日期_索引 ON 行为分析.策略净值(交易日期);

CREATE TABLE IF NOT EXISTS 行为分析.标的摘要 (
    证券代码 CHAR(6) PRIMARY KEY REFERENCES 公共.证券档案(证券代码) ON DELETE CASCADE,
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

CREATE TABLE IF NOT EXISTS 行为分析.市场时序 (
    序列名称 TEXT NOT NULL,
    交易日期 DATE NOT NULL,
    载荷 JSONB NOT NULL,
    PRIMARY KEY (序列名称, 交易日期)
);

CREATE TABLE IF NOT EXISTS 行为分析.分钟日特征 (
    证券代码 CHAR(6) NOT NULL REFERENCES 公共.证券档案(证券代码) ON DELETE CASCADE,
    交易日期 DATE NOT NULL,
    return_mean DOUBLE PRECISION,
    return_std DOUBLE PRECISION,
    volume_total DOUBLE PRECISION,
    amount_total DOUBLE PRECISION,
    volume_spike_mean DOUBLE PRECISION,
    volume_spike_max DOUBLE PRECISION,
    realized_vol_mean DOUBLE PRECISION,
    realized_vol_max DOUBLE PRECISION,
    vwap_dev_mean DOUBLE PRECISION,
    vwap_dev_std DOUBLE PRECISION,
    price_impact_mean DOUBLE PRECISION,
    price_impact_max DOUBLE PRECISION,
    high_low_range_mean DOUBLE PRECISION,
    open_volume_ratio DOUBLE PRECISION,
    tail_volume_ratio DOUBLE PRECISION,
    minute_bars DOUBLE PRECISION,
    PRIMARY KEY (证券代码, 交易日期)
);
CREATE INDEX IF NOT EXISTS 分钟日特征_日期_索引 ON 行为分析.分钟日特征(交易日期);

CREATE TABLE IF NOT EXISTS 行为分析.参与者活跃度 (
    证券代码 CHAR(6) NOT NULL REFERENCES 公共.证券档案(证券代码) ON DELETE CASCADE,
    交易日期 DATE NOT NULL,
    主力资金 DOUBLE PRECISION,
    北向资金 DOUBLE PRECISION,
    尾盘量能 DOUBLE PRECISION,
    融资融券 DOUBLE PRECISION,
    分级资金流 DOUBLE PRECISION,
    波动冲击 DOUBLE PRECISION,
    成交规模 DOUBLE PRECISION,
    PRIMARY KEY (证券代码, 交易日期)
);
CREATE INDEX IF NOT EXISTS 参与者活跃度_日期_索引 ON 行为分析.参与者活跃度(交易日期);

CREATE TABLE IF NOT EXISTS 行为分析.标的产物 (
    证券代码 CHAR(6) NOT NULL REFERENCES 公共.证券档案(证券代码) ON DELETE CASCADE,
    产物类型 TEXT NOT NULL,
    载荷 JSONB NOT NULL,
    更新时间 TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (证券代码, 产物类型)
);

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

CREATE TABLE IF NOT EXISTS 行为分析.应用状态 (
    键 TEXT PRIMARY KEY,
    载荷 JSONB NOT NULL,
    更新时间 TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 英文兼容视图（只读/可写简单映射，供旧 SQL 与外部工具使用）
CREATE OR REPLACE VIEW 行为分析.symbols AS
SELECT 证券代码 AS symbol, 证券简称 AS name, 交易市场 AS market,
       所属行业 AS sector, 一级行业 AS sector_l1, 二级行业 AS sector_l2, 三级行业 AS sector_l3,
       数据路径 AS data_path, 输出路径 AS output_path, 更新时间 AS updated_at
FROM 公共.证券档案;

CREATE OR REPLACE VIEW 行为分析.daily_bars AS
SELECT 证券代码 AS symbol, 交易日期 AS date,
       开盘价 AS open, 最高价 AS high, 最低价 AS low, 收盘价 AS close,
       成交量 AS volume, 成交额 AS amount
FROM 行为分析.日行情;

CREATE OR REPLACE VIEW 行为分析.fund_flow AS
SELECT 证券代码 AS symbol, 交易日期 AS date, 收盘价 AS close, 涨跌幅 AS pct_change,
       主力净流入 AS main_net_inflow, 主力净占比 AS main_net_ratio,
       超大单净流入 AS super_large_net_inflow, 超大单净占比 AS super_large_net_ratio,
       大单净流入 AS large_net_inflow, 大单净占比 AS large_net_ratio,
       中单净流入 AS medium_net_inflow, 中单净占比 AS medium_net_ratio,
       小单净流入 AS small_net_inflow, 小单净占比 AS small_net_ratio
FROM 行为分析.资金流向;

CREATE OR REPLACE VIEW 行为分析.equity_curves AS
SELECT 证券代码 AS symbol, 交易日期 AS date,
       建议仓位 AS suggested_position, 市场状态 AS market_state, 状态得分 AS state_score,
       基准收益率 AS market_return, 仓位 AS position, 仓位变动 AS position_change,
       策略收益率 AS strategy_return, 基准收益率 AS benchmark_return,
       策略净值 AS strategy_equity, 基准净值 AS benchmark_equity
FROM 行为分析.策略净值;

CREATE OR REPLACE VIEW 行为分析.symbol_summary AS
SELECT 证券代码 AS symbol, 所属行业 AS sector, 证券简称 AS name, 最新状态 AS latest_state,
       建议仓位 AS suggested_position, 策略收益率 AS strategy_return, 基准收益率 AS benchmark_return,
       超额收益率 AS excess_return, 策略夏普 AS strategy_sharpe, 异常数 AS anomalies,
       状态 AS status, 输出路径 AS output_path, 分析时间 AS analyzed_at
FROM 行为分析.标的摘要;

CREATE OR REPLACE VIEW 行为分析.market_series AS
SELECT 序列名称 AS series_name, 交易日期 AS date, 载荷 AS payload
FROM 行为分析.市场时序;

CREATE OR REPLACE VIEW 行为分析.symbol_artifacts AS
SELECT 证券代码 AS symbol,
       CASE 产物类型
           WHEN '参与者模式' THEN 'agent_patterns'
           WHEN '参与者标签' THEN 'agent_pattern_labels'
           WHEN '参与者名称' THEN 'agent_names'
           WHEN '优化仓位' THEN 'optimized_positions'
           WHEN '转移矩阵' THEN 'transition_matrix'
           WHEN '下一状态概率' THEN 'next_state_probs'
           ELSE 产物类型
       END AS artifact_type,
       载荷 AS payload,
       更新时间 AS updated_at
FROM 行为分析.标的产物;

CREATE OR REPLACE VIEW 行为分析.industry_summary AS
SELECT 所属行业 AS sector, 标的数量 AS n_symbols,
       平均超额收益率 AS mean_excess_return, 中位超额收益率 AS median_excess_return,
       胜率 AS win_rate, 平均策略收益率 AS mean_strategy_return,
       平均基准收益率 AS mean_benchmark_return, 平均策略夏普 AS mean_strategy_sharpe,
       平均建议仓位 AS mean_suggested_position, 更新时间 AS updated_at
FROM 行为分析.行业摘要;

CREATE OR REPLACE VIEW 行为分析.portfolios AS
SELECT 组合编号 AS portfolio_id, 组合名称 AS name, 元数据 AS metadata, 创建时间 AS created_at
FROM 行为分析.组合;

CREATE OR REPLACE VIEW 行为分析.portfolio_equity AS
SELECT 组合编号 AS portfolio_id, 交易日期 AS date,
       组合净值 AS portfolio_equity, 基准净值 AS benchmark_equity,
       组合收益率 AS portfolio_return, 基准收益率 AS benchmark_return,
       组合仓位 AS portfolio_position
FROM 行为分析.组合净值;

CREATE OR REPLACE VIEW 行为分析.app_state AS
SELECT 键 AS key, 载荷 AS payload, 更新时间 AS updated_at
FROM 行为分析.应用状态;

CREATE OR REPLACE VIEW 行为分析.symbol_series AS
SELECT 证券代码 AS symbol, 'agent_activity'::text AS series_type, 交易日期 AS date,
       jsonb_strip_nulls(jsonb_build_object(
           '主力资金', 主力资金, '北向资金', 北向资金, '尾盘量能', 尾盘量能,
           '融资融券', 融资融券, '分级资金流', 分级资金流, '波动冲击', 波动冲击, '成交规模', 成交规模
       )) AS payload
FROM 行为分析.参与者活跃度
UNION ALL
SELECT 证券代码 AS symbol, 'minute_daily_features'::text AS series_type, 交易日期 AS date,
       jsonb_strip_nulls(jsonb_build_object(
           'return_mean', return_mean, 'return_std', return_std,
           'volume_total', volume_total, 'amount_total', amount_total,
           'volume_spike_mean', volume_spike_mean, 'volume_spike_max', volume_spike_max,
           'realized_vol_mean', realized_vol_mean, 'realized_vol_max', realized_vol_max,
           'vwap_dev_mean', vwap_dev_mean, 'vwap_dev_std', vwap_dev_std,
           'price_impact_mean', price_impact_mean, 'price_impact_max', price_impact_max,
           'high_low_range_mean', high_low_range_mean,
           'open_volume_ratio', open_volume_ratio, 'tail_volume_ratio', tail_volume_ratio,
           'minute_bars', minute_bars
       )) AS payload
FROM 行为分析.分钟日特征;

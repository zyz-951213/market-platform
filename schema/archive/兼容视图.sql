-- 应用过渡期：英文视图名 + 英文字段，映射到中文物理表
-- search_path 优先 兼容，则旧 SQL 可暂不改表名

CREATE SCHEMA IF NOT EXISTS 兼容;

CREATE OR REPLACE VIEW 兼容.symbols AS
SELECT
    证券代码 AS symbol,
    证券简称 AS name,
    交易市场 AS market,
    所属行业 AS sector,
    一级行业 AS sector_l1,
    二级行业 AS sector_l2,
    三级行业 AS sector_l3,
    数据路径 AS data_path,
    输出路径 AS output_path,
    更新时间 AS updated_at
FROM 主数据.证券档案;

CREATE OR REPLACE VIEW 兼容.daily_bars AS
SELECT
    证券代码 AS symbol,
    交易日期 AS date,
    开盘价 AS open,
    最高价 AS high,
    最低价 AS low,
    收盘价 AS close,
    成交量 AS volume,
    成交额 AS amount
FROM 行为分析.日行情;

CREATE OR REPLACE VIEW 兼容.fund_flow AS
SELECT
    证券代码 AS symbol,
    交易日期 AS date,
    收盘价 AS close,
    涨跌幅 AS pct_change,
    主力净流入 AS main_net_inflow,
    主力净占比 AS main_net_ratio,
    超大单净流入 AS super_large_net_inflow,
    超大单净占比 AS super_large_net_ratio,
    大单净流入 AS large_net_inflow,
    大单净占比 AS large_net_ratio,
    中单净流入 AS medium_net_inflow,
    中单净占比 AS medium_net_ratio,
    小单净流入 AS small_net_inflow,
    小单净占比 AS small_net_ratio
FROM 行为分析.资金流向;

CREATE OR REPLACE VIEW 兼容.equity_curves AS
SELECT
    证券代码 AS symbol,
    交易日期 AS date,
    建议仓位 AS suggested_position,
    市场状态 AS market_state,
    状态得分 AS state_score,
    市场收益率 AS market_return,
    仓位 AS position,
    仓位变动 AS position_change,
    策略收益率 AS strategy_return,
    基准收益率 AS benchmark_return,
    策略净值 AS strategy_equity,
    基准净值 AS benchmark_equity
FROM 行为分析.策略净值;

CREATE OR REPLACE VIEW 兼容.symbol_summary AS
SELECT
    证券代码 AS symbol,
    所属行业 AS sector,
    证券简称 AS name,
    最新状态 AS latest_state,
    建议仓位 AS suggested_position,
    策略收益率 AS strategy_return,
    基准收益率 AS benchmark_return,
    超额收益率 AS excess_return,
    策略夏普 AS strategy_sharpe,
    异常数 AS anomalies,
    状态 AS status,
    输出路径 AS output_path,
    分析时间 AS analyzed_at
FROM 行为分析.标的摘要;

CREATE OR REPLACE VIEW 兼容.industry_summary AS
SELECT
    所属行业 AS sector,
    标的数量 AS n_symbols,
    平均超额收益率 AS mean_excess_return,
    中位超额收益率 AS median_excess_return,
    胜率 AS win_rate,
    平均策略收益率 AS mean_strategy_return,
    平均基准收益率 AS mean_benchmark_return,
    平均策略夏普 AS mean_strategy_sharpe,
    平均建议仓位 AS mean_suggested_position,
    更新时间 AS updated_at
FROM 行为分析.行业摘要;

CREATE OR REPLACE VIEW 兼容.market_series AS
SELECT
    序列名称 AS series_name,
    交易日期 AS date,
    载荷 AS payload
FROM 行为分析.市场时序;

CREATE OR REPLACE VIEW 兼容.portfolios AS
SELECT
    组合编号 AS portfolio_id,
    组合名称 AS name,
    元数据 AS metadata,
    创建时间 AS created_at
FROM 行为分析.组合;

CREATE OR REPLACE VIEW 兼容.portfolio_equity AS
SELECT
    组合编号 AS portfolio_id,
    交易日期 AS date,
    组合净值 AS portfolio_equity,
    基准净值 AS benchmark_equity,
    组合收益率 AS portfolio_return,
    基准收益率 AS benchmark_return,
    组合仓位 AS portfolio_position
FROM 行为分析.组合净值;

CREATE OR REPLACE VIEW 兼容.app_state AS
SELECT
    键 AS key,
    载荷 AS payload,
    更新时间 AS updated_at
FROM 行为分析.应用状态;

-- 时序/产物：库内已是中文类型值；视图把类型译回英文供旧代码
CREATE OR REPLACE VIEW 兼容.symbol_series AS
SELECT
    证券代码 AS symbol,
    CASE 序列类型
        WHEN '分钟日特征' THEN 'minute_daily_features'
        WHEN '参与者活跃度' THEN 'agent_activity'
        WHEN '日特征矩阵' THEN 'daily_feature_matrix'
        WHEN '状态特征' THEN 'state_features'
        WHEN '分钟行情' THEN 'minute_bars'
        ELSE 序列类型
    END AS series_type,
    交易日期 AS date,
    载荷 AS payload
FROM 行为分析.标的时序;

CREATE OR REPLACE VIEW 兼容.symbol_artifacts AS
SELECT
    证券代码 AS symbol,
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

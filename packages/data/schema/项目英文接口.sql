-- 英文接口收入各项目 schema（不再单独建兼容 schema）

-- ========== 行为分析 ==========
CREATE OR REPLACE VIEW 行为分析.symbols AS
SELECT
  证券代码 AS symbol, 证券简称 AS name, 交易市场 AS market,
  所属行业 AS sector, 一级行业 AS sector_l1, 二级行业 AS sector_l2,
  三级行业 AS sector_l3, 数据路径 AS data_path, 输出路径 AS output_path,
  更新时间 AS updated_at
FROM 公共.证券档案;

CREATE OR REPLACE VIEW 行为分析.daily_bars AS
SELECT 证券代码 AS symbol, 交易日期 AS date,
       开盘价 AS open, 最高价 AS high, 最低价 AS low, 收盘价 AS close,
       成交量 AS volume, 成交额 AS amount
FROM 行为分析.日行情;

CREATE OR REPLACE VIEW 行为分析.fund_flow AS
SELECT
  证券代码 AS symbol, 交易日期 AS date, 收盘价 AS close, 涨跌幅 AS pct_change,
  主力净流入 AS main_net_inflow, 主力净占比 AS main_net_ratio,
  超大单净流入 AS super_large_net_inflow, 超大单净占比 AS super_large_net_ratio,
  大单净流入 AS large_net_inflow, 大单净占比 AS large_net_ratio,
  中单净流入 AS medium_net_inflow, 中单净占比 AS medium_net_ratio,
  小单净流入 AS small_net_inflow, 小单净占比 AS small_net_ratio
FROM 行为分析.资金流向;

CREATE OR REPLACE VIEW 行为分析.equity_curves AS
SELECT
  证券代码 AS symbol, 交易日期 AS date,
  建议仓位 AS suggested_position, 市场状态 AS market_state, 状态得分 AS state_score,
  基准收益率 AS market_return, 仓位 AS position, 仓位变动 AS position_change,
  策略收益率 AS strategy_return, 基准收益率 AS benchmark_return,
  策略净值 AS strategy_equity, 基准净值 AS benchmark_equity
FROM 行为分析.策略净值;

CREATE OR REPLACE VIEW 行为分析.symbol_summary AS
SELECT
  证券代码 AS symbol, 所属行业 AS sector, 证券简称 AS name,
  最新状态 AS latest_state, 建议仓位 AS suggested_position,
  策略收益率 AS strategy_return, 基准收益率 AS benchmark_return,
  超额收益率 AS excess_return, 策略夏普 AS strategy_sharpe,
  异常数 AS anomalies, 状态 AS status, 输出路径 AS output_path,
  分析时间 AS analyzed_at
FROM 行为分析.标的摘要;

CREATE OR REPLACE VIEW 行为分析.industry_summary AS
SELECT
  所属行业 AS sector, 标的数量 AS n_symbols,
  平均超额收益率 AS mean_excess_return, 中位超额收益率 AS median_excess_return,
  胜率 AS win_rate, 平均策略收益率 AS mean_strategy_return,
  平均基准收益率 AS mean_benchmark_return, 平均策略夏普 AS mean_strategy_sharpe,
  平均建议仓位 AS mean_suggested_position, 更新时间 AS updated_at
FROM 行为分析.行业摘要;

CREATE OR REPLACE VIEW 行为分析.market_series AS
SELECT 序列名称 AS series_name, 交易日期 AS date, 载荷 AS payload
FROM 行为分析.市场时序;

CREATE OR REPLACE VIEW 行为分析.portfolios AS
SELECT 组合编号 AS portfolio_id, 组合名称 AS name, 元数据 AS metadata, 创建时间 AS created_at
FROM 行为分析.组合;

CREATE OR REPLACE VIEW 行为分析.portfolio_equity AS
SELECT
  组合编号 AS portfolio_id, 交易日期 AS date,
  组合净值 AS portfolio_equity, 基准净值 AS benchmark_equity,
  组合收益率 AS portfolio_return, 基准收益率 AS benchmark_return,
  组合仓位 AS portfolio_position
FROM 行为分析.组合净值;

CREATE OR REPLACE VIEW 行为分析.app_state AS
SELECT 键 AS key, 载荷 AS payload, 更新时间 AS updated_at
FROM 行为分析.应用状态;

-- 由窄表拼回 payload（原 JSON 宽表 标的时序 已删除）
CREATE OR REPLACE VIEW 行为分析.symbol_series AS
SELECT
  证券代码 AS symbol,
  'agent_activity'::text AS series_type,
  交易日期 AS date,
  jsonb_strip_nulls(jsonb_build_object(
    '主力资金', 主力资金,
    '北向资金', 北向资金,
    '尾盘量能', 尾盘量能,
    '融资融券', 融资融券,
    '分级资金流', 分级资金流,
    '波动冲击', 波动冲击,
    '成交规模', 成交规模
  )) AS payload
FROM 行为分析.参与者活跃度
UNION ALL
SELECT
  证券代码,
  'minute_daily_features',
  交易日期,
  jsonb_strip_nulls(jsonb_build_object(
    'return_mean', return_mean,
    'return_std', return_std,
    'volume_total', volume_total,
    'amount_total', amount_total,
    'volume_spike_mean', volume_spike_mean,
    'volume_spike_max', volume_spike_max,
    'realized_vol_mean', realized_vol_mean,
    'realized_vol_max', realized_vol_max,
    'vwap_dev_mean', vwap_dev_mean,
    'vwap_dev_std', vwap_dev_std,
    'price_impact_mean', price_impact_mean,
    'price_impact_max', price_impact_max,
    'high_low_range_mean', high_low_range_mean,
    'open_volume_ratio', open_volume_ratio,
    'tail_volume_ratio', tail_volume_ratio,
    'minute_bars', minute_bars
  ))
FROM 行为分析.分钟日特征;

CREATE OR REPLACE VIEW 行为分析.symbol_artifacts AS
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
  载荷 AS payload, 更新时间 AS updated_at
FROM 行为分析.标的产物;

CREATE OR REPLACE VIEW 行为分析.users AS
SELECT 编号 AS id, 用户名 AS username, 邮箱 AS email, 密码哈希 AS password_hash,
       是否启用 AS is_active, 创建时间 AS created_at, 更新时间 AS updated_at
FROM 行为分析.用户;

-- ========== 指数预测 ==========
CREATE OR REPLACE VIEW 指数预测.index_features AS
SELECT 指数代码 AS index_code, 交易日期 AS date, 载荷 AS payload FROM 指数预测.指数特征;
CREATE OR REPLACE VIEW 指数预测.time_series AS
SELECT 数据集键 AS dataset_key, 交易日期 AS date, 载荷 AS payload FROM 指数预测.时序数据;
CREATE OR REPLACE VIEW 指数预测.dataset_snapshots AS
SELECT 数据集键 AS dataset_key, 记录 AS records, 更新时间 AS updated_at FROM 指数预测.数据集快照;
CREATE OR REPLACE VIEW 指数预测.llm_cache AS
SELECT 模型 AS model, 提示词版本 AS prompt_version, 提示词摘要 AS prompt_digest,
       行情日期 AS bar_date, 响应 AS response, 创建时间 AS created_at
FROM 指数预测.大模型缓存;
CREATE OR REPLACE VIEW 指数预测.backtest_runs AS
SELECT 运行编号 AS run_id, 是否最新 AS is_latest, 模型名称 AS model_name,
       行情频率 AS bar_freq, 策略指纹 AS strategy_fingerprint, 数据截至 AS data_as_of,
       保存时间 AS saved_at, 指标 AS metrics, 分类 AS classification
FROM 指数预测.回测记录;
CREATE OR REPLACE VIEW 指数预测.backtest_signals AS
SELECT 运行编号 AS run_id, 交易日期 AS date, 载荷 AS payload FROM 指数预测.回测信号;
CREATE OR REPLACE VIEW 指数预测.fetch_meta AS
SELECT 数据集键 AS dataset_key, 载荷 AS payload, 更新时间 AS updated_at FROM 指数预测.拉取元数据;
CREATE OR REPLACE VIEW 指数预测.app_state AS
SELECT 键 AS key, 载荷 AS payload, 更新时间 AS updated_at FROM 指数预测.应用状态;

-- ========== 微盘股研究 ==========
CREATE OR REPLACE VIEW 微盘股研究.mc_daily AS SELECT * FROM 公共.日行情;
CREATE OR REPLACE VIEW 微盘股研究.mc_calendar AS SELECT * FROM 微盘股研究.交易日历;
CREATE OR REPLACE VIEW 微盘股研究.mc_company AS SELECT * FROM 微盘股研究.公司档案;
CREATE OR REPLACE VIEW 微盘股研究.mc_daily_returns AS SELECT * FROM 微盘股研究.日收益率;
CREATE OR REPLACE VIEW 微盘股研究.mc_definitions AS SELECT * FROM 微盘股研究.口径定义;
CREATE OR REPLACE VIEW 微盘股研究.mc_index_daily AS SELECT * FROM 微盘股研究.指数日行情;
CREATE OR REPLACE VIEW 微盘股研究.mc_industry AS SELECT * FROM 微盘股研究.行业归属;
CREATE OR REPLACE VIEW 微盘股研究.mc_ipo AS SELECT * FROM 微盘股研究.新股发行;
CREATE OR REPLACE VIEW 微盘股研究.mc_lifecycle AS SELECT * FROM 微盘股研究.生命周期;
CREATE OR REPLACE VIEW 微盘股研究.mc_listing_status AS SELECT * FROM 微盘股研究.上市状态;
CREATE OR REPLACE VIEW 微盘股研究.mc_market_returns AS SELECT * FROM 微盘股研究.市场收益;
CREATE OR REPLACE VIEW 微盘股研究.mc_monthly_portfolio AS SELECT * FROM 微盘股研究.月度组合;
CREATE OR REPLACE VIEW 微盘股研究.mc_monthly_ranking AS SELECT * FROM 微盘股研究.月度排名;
CREATE OR REPLACE VIEW 微盘股研究.mc_monthly_returns AS SELECT * FROM 微盘股研究.月收益率;
CREATE OR REPLACE VIEW 微盘股研究.mc_name_changes AS SELECT * FROM 微盘股研究.曾用名;
CREATE OR REPLACE VIEW 微盘股研究.mc_no_limit AS SELECT * FROM 微盘股研究.无涨跌停;
CREATE OR REPLACE VIEW 微盘股研究.mc_policy_events AS SELECT * FROM 微盘股研究.政策事件;
CREATE OR REPLACE VIEW 微盘股研究.mc_regime_monthly AS SELECT * FROM 微盘股研究.月度体制;
CREATE OR REPLACE VIEW 微盘股研究.mc_share_capital AS SELECT * FROM 微盘股研究.股本变动;
CREATE OR REPLACE VIEW 微盘股研究.mc_special_treatment AS SELECT * FROM 微盘股研究.特别处理;
CREATE OR REPLACE VIEW 微盘股研究.mc_st_overlap AS SELECT * FROM 微盘股研究.与ST重叠;

-- ========== 特别处理研究：共享改指公共/微盘，英文 st_* ==========
DROP VIEW IF EXISTS 特别处理研究.st_daily CASCADE;
DROP VIEW IF EXISTS 特别处理研究.st_calendar CASCADE;
DROP VIEW IF EXISTS 特别处理研究.st_company CASCADE;
DROP VIEW IF EXISTS 特别处理研究.st_listing_status CASCADE;
DROP VIEW IF EXISTS 特别处理研究.st_index_daily CASCADE;
DROP VIEW IF EXISTS 特别处理研究.st_ipo CASCADE;
DROP VIEW IF EXISTS 特别处理研究.st_no_limit CASCADE;
DROP VIEW IF EXISTS 特别处理研究.st_market_returns CASCADE;
DROP VIEW IF EXISTS 特别处理研究.st_industry CASCADE;
DROP VIEW IF EXISTS 特别处理研究.st_monthly_returns CASCADE;
DROP VIEW IF EXISTS 特别处理研究.st_daily_returns CASCADE;
DROP VIEW IF EXISTS 特别处理研究.日行情 CASCADE;
DROP VIEW IF EXISTS 特别处理研究.交易日历 CASCADE;
DROP VIEW IF EXISTS 特别处理研究.公司档案 CASCADE;
DROP VIEW IF EXISTS 特别处理研究.上市状态 CASCADE;
DROP VIEW IF EXISTS 特别处理研究.指数日行情 CASCADE;
DROP VIEW IF EXISTS 特别处理研究.新股发行 CASCADE;
DROP VIEW IF EXISTS 特别处理研究.无涨跌停 CASCADE;
DROP VIEW IF EXISTS 特别处理研究.市场收益 CASCADE;
DROP VIEW IF EXISTS 特别处理研究.行业归属 CASCADE;
DROP VIEW IF EXISTS 特别处理研究.月收益率 CASCADE;
DROP VIEW IF EXISTS 特别处理研究.日收益率 CASCADE;

CREATE VIEW 特别处理研究.日行情 AS SELECT * FROM 公共.日行情;
CREATE VIEW 特别处理研究.交易日历 AS SELECT * FROM 微盘股研究.交易日历;
CREATE VIEW 特别处理研究.公司档案 AS SELECT * FROM 微盘股研究.公司档案;
CREATE VIEW 特别处理研究.上市状态 AS SELECT * FROM 微盘股研究.上市状态;
CREATE VIEW 特别处理研究.指数日行情 AS SELECT * FROM 微盘股研究.指数日行情;
CREATE VIEW 特别处理研究.新股发行 AS SELECT * FROM 微盘股研究.新股发行;
CREATE VIEW 特别处理研究.无涨跌停 AS SELECT * FROM 微盘股研究.无涨跌停;
CREATE VIEW 特别处理研究.市场收益 AS SELECT * FROM 微盘股研究.市场收益;
CREATE VIEW 特别处理研究.行业归属 AS SELECT * FROM 微盘股研究.行业归属;
CREATE VIEW 特别处理研究.月收益率 AS SELECT * FROM 微盘股研究.月收益率;
CREATE VIEW 特别处理研究.日收益率 AS SELECT * FROM 微盘股研究.日收益率;

CREATE VIEW 特别处理研究.st_daily AS SELECT * FROM 特别处理研究.日行情;
CREATE VIEW 特别处理研究.st_calendar AS SELECT * FROM 特别处理研究.交易日历;
CREATE VIEW 特别处理研究.st_company AS SELECT * FROM 特别处理研究.公司档案;
CREATE VIEW 特别处理研究.st_listing_status AS SELECT * FROM 特别处理研究.上市状态;
CREATE VIEW 特别处理研究.st_index_daily AS SELECT * FROM 特别处理研究.指数日行情;
CREATE VIEW 特别处理研究.st_ipo AS SELECT * FROM 特别处理研究.新股发行;
CREATE VIEW 特别处理研究.st_no_limit AS SELECT * FROM 特别处理研究.无涨跌停;
CREATE VIEW 特别处理研究.st_market_returns AS SELECT * FROM 特别处理研究.市场收益;
CREATE VIEW 特别处理研究.st_industry AS SELECT * FROM 特别处理研究.行业归属;
CREATE VIEW 特别处理研究.st_monthly_returns AS SELECT * FROM 特别处理研究.月收益率;
CREATE VIEW 特别处理研究.st_daily_returns AS SELECT * FROM 特别处理研究.日收益率;
CREATE OR REPLACE VIEW 特别处理研究.st_annual AS SELECT * FROM 特别处理研究.年报摘要;
CREATE OR REPLACE VIEW 特别处理研究.st_fin_disclose AS SELECT * FROM 特别处理研究.财务披露;
CREATE OR REPLACE VIEW 特别处理研究.st_fin_solvency AS SELECT * FROM 特别处理研究.偿债能力;
CREATE OR REPLACE VIEW 特别处理研究.st_name_changes AS SELECT * FROM 特别处理研究.曾用名;
CREATE OR REPLACE VIEW 特别处理研究.st_special_treatment AS SELECT * FROM 特别处理研究.特别处理;
CREATE OR REPLACE VIEW 特别处理研究.st_st_rules AS SELECT * FROM 特别处理研究.ST规则;

-- ========== 隐因子：英文名 ==========
DROP VIEW IF EXISTS 隐因子.stocks CASCADE;
DROP VIEW IF EXISTS 隐因子.trading_dates CASCADE;
DROP VIEW IF EXISTS 隐因子.factor_values CASCADE;
DROP VIEW IF EXISTS 隐因子.factor_registry CASCADE;
DROP VIEW IF EXISTS 隐因子.factor_loadings CASCADE;
DROP VIEW IF EXISTS 隐因子.model_configs CASCADE;
DROP VIEW IF EXISTS 隐因子.latent_coords CASCADE;
DROP VIEW IF EXISTS 隐因子.交易日历 CASCADE;

CREATE VIEW 隐因子.stocks AS
SELECT
  证券代码 AS stock_code, 证券简称 AS stock_name,
  行业甲 AS industry_a, 行业乙 AS industry_b,
  上市日期 AS list_date, 状态 AS status, 更新时间 AS updated_at
FROM 隐因子.股票信息;

CREATE VIEW 隐因子.trading_dates AS
SELECT
  trade_date,
  TRUE AS is_trade_day,
  EXTRACT(YEAR FROM trade_date)::smallint AS year,
  EXTRACT(MONTH FROM trade_date)::smallint AS month,
  EXTRACT(QUARTER FROM trade_date)::smallint AS quarter
FROM (SELECT DISTINCT trade_date FROM 公共.日行情) d;

CREATE VIEW 隐因子.交易日历 AS
SELECT trade_date AS 交易日期, is_trade_day, year, month, quarter
FROM 隐因子.trading_dates;

CREATE VIEW 隐因子.factor_registry AS SELECT * FROM 隐因子.因子注册;
CREATE VIEW 隐因子.factor_loadings AS SELECT * FROM 隐因子.因子载荷;
CREATE VIEW 隐因子.model_configs AS SELECT * FROM 隐因子.模型配置;

CREATE VIEW 隐因子.latent_coords AS
SELECT
  NULL::bigint AS id,
  c."证券代码" AS stock_code,
  c."交易日期" AS trade_date,
  c."方法" AS method,
  c."成分数" AS n_components,
  c.d1, c.d2, c.d3, c.d4, c.d5, c.d6, c.d7, c.d8, c.d9,
  l.d1_label, l.d2_label, l.d3_label, l.d4_label, l.d5_label,
  l.d6_label, l.d7_label, l.d8_label, l.d9_label,
  c.explained_var_total,
  NULL::timestamp AS created_at
FROM 隐因子.隐空间坐标 c
LEFT JOIN 隐因子.隐维度标签 l
  ON l."方法" = c."方法" AND l."成分数" = c."成分数";

-- 研究项目英文兼容视图（表已中文重命名后）
CREATE SCHEMA IF NOT EXISTS 隐因子兼容;
CREATE SCHEMA IF NOT EXISTS 微盘兼容;
CREATE SCHEMA IF NOT EXISTS 特处兼容;

-- 隐因子：视图恢复英文表名；已改中文的常用列做别名
CREATE OR REPLACE VIEW 隐因子兼容.stocks AS
SELECT
  证券代码 AS stock_code,
  证券简称 AS stock_name,
  行业甲 AS industry_a,
  行业乙 AS industry_b,
  上市日期 AS list_date,
  状态 AS status,
  更新时间 AS updated_at
FROM 隐因子.股票信息;

CREATE OR REPLACE VIEW 隐因子兼容.trading_dates AS
SELECT
  交易日期 AS trade_date,
  is_trade_day,
  year,
  month,
  quarter
FROM 隐因子.交易日历;

CREATE OR REPLACE VIEW 隐因子兼容.factor_registry AS
SELECT * FROM 隐因子.因子注册;

CREATE OR REPLACE VIEW 隐因子兼容.factor_loadings AS
SELECT * FROM 隐因子.因子载荷;

CREATE OR REPLACE VIEW 隐因子兼容.model_configs AS
SELECT * FROM 隐因子.模型配置;

CREATE OR REPLACE VIEW 隐因子兼容.factor_values AS
SELECT
  id,
  证券代码 AS stock_code,
  交易日期 AS trade_date,
  开盘价 AS open,
  最高价 AS high,
  最低价 AS low,
  收盘价 AS close,
  volume, amount, turnover, mkt_cap, float_mkt_cap, amplitude,
  ret_1d, ret_5d, ret_10d, ret_20d, ret_60d,
  vol_5d, vol_10d, vol_20d, vol_60d,
  turnover_5d_avg, turnover_20d_avg, volume_ratio,
  high_minus_low_20d, close_position,
  rsi_14, macd, macd_signal, macd_hist,
  sma_5, sma_10, sma_20, sma_60, boll_upper, boll_lower, atr_14,
  kdj_k, kdj_d, kdj_j, wr_14, cci_20, bias_6, bias_12, bias_24, psy,
  roe, roa, gross_margin, net_margin, op_margin, eps, bps,
  rev_growth_yoy, profit_growth_yoy, asset_growth_yoy,
  debt_ratio, current_ratio, quick_ratio, cash_ratio,
  ocf_ps, ocf_to_profit, asset_turnover, receivable_turnover, inventory_turnover,
  pe_ttm, pe_ttm_percentile, pb, pb_percentile, ps_ttm, pcf_ttm, dividend_yield, peg,
  fund_main_net, fund_large_net, fund_medium_net, fund_small_net, fund_main_ratio,
  industry_ret_1d, industry_excess_1d, ind_ret_5d, ind_ret_20d, stock_rank_ind_20d,
  mom_1m, mom_3m, mom_6m, mom_12m, max_ret_60d, min_ret_60d, cum_ret_yy,
  ln_cap, ln_float_cap, is_sz, is_sh, is_bj, is_hs300, is_zz500, is_zz1000,
  ma5_pct, ma10_pct, ma20_pct, ma60_pct, vol_ma5_ratio,
  rsi_overbought, rsi_oversold, macd_cross, kdj_cross,
  three_black_crows, three_white_soldiers, created_at
FROM 隐因子.因子取值;

CREATE OR REPLACE VIEW 隐因子兼容.latent_coords AS
SELECT
  id,
  证券代码 AS stock_code,
  交易日期 AS trade_date,
  方法 AS method,
  成分数 AS n_components,
  d1,d2,d3,d4,d5,d6,d7,d8,d9,
  d1_label,d2_label,d3_label,d4_label,d5_label,d6_label,d7_label,d8_label,d9_label,
  explained_var_total,
  创建时间 AS created_at
FROM 隐因子.隐空间坐标;

-- 微盘 / 特处：表结构字段仍多为英文，仅表名中文化 → 视图恢复 mc_/st_ 表名
CREATE OR REPLACE VIEW 微盘兼容.mc_calendar AS SELECT * FROM 微盘股研究.交易日历;
CREATE OR REPLACE VIEW 微盘兼容.mc_company AS SELECT * FROM 微盘股研究.公司档案;
CREATE OR REPLACE VIEW 微盘兼容.mc_daily AS SELECT * FROM 微盘股研究.日行情;
CREATE OR REPLACE VIEW 微盘兼容.mc_daily_returns AS SELECT * FROM 微盘股研究.日收益率;
CREATE OR REPLACE VIEW 微盘兼容.mc_definitions AS SELECT * FROM 微盘股研究.口径定义;
CREATE OR REPLACE VIEW 微盘兼容.mc_index_daily AS SELECT * FROM 微盘股研究.指数日行情;
CREATE OR REPLACE VIEW 微盘兼容.mc_industry AS SELECT * FROM 微盘股研究.行业归属;
CREATE OR REPLACE VIEW 微盘兼容.mc_ipo AS SELECT * FROM 微盘股研究.新股发行;
CREATE OR REPLACE VIEW 微盘兼容.mc_lifecycle AS SELECT * FROM 微盘股研究.生命周期;
CREATE OR REPLACE VIEW 微盘兼容.mc_listing_status AS SELECT * FROM 微盘股研究.上市状态;
CREATE OR REPLACE VIEW 微盘兼容.mc_market_returns AS SELECT * FROM 微盘股研究.市场收益;
CREATE OR REPLACE VIEW 微盘兼容.mc_monthly_portfolio AS SELECT * FROM 微盘股研究.月度组合;
CREATE OR REPLACE VIEW 微盘兼容.mc_monthly_ranking AS SELECT * FROM 微盘股研究.月度排名;
CREATE OR REPLACE VIEW 微盘兼容.mc_monthly_returns AS SELECT * FROM 微盘股研究.月收益率;
CREATE OR REPLACE VIEW 微盘兼容.mc_name_changes AS SELECT * FROM 微盘股研究.曾用名;
CREATE OR REPLACE VIEW 微盘兼容.mc_no_limit AS SELECT * FROM 微盘股研究.无涨跌停;
CREATE OR REPLACE VIEW 微盘兼容.mc_policy_events AS SELECT * FROM 微盘股研究.政策事件;
CREATE OR REPLACE VIEW 微盘兼容.mc_regime_monthly AS SELECT * FROM 微盘股研究.月度体制;
CREATE OR REPLACE VIEW 微盘兼容.mc_share_capital AS SELECT * FROM 微盘股研究.股本变动;
CREATE OR REPLACE VIEW 微盘兼容.mc_special_treatment AS SELECT * FROM 微盘股研究.特别处理;
CREATE OR REPLACE VIEW 微盘兼容.mc_st_overlap AS SELECT * FROM 微盘股研究.与ST重叠;

CREATE OR REPLACE VIEW 特处兼容.st_annual AS SELECT * FROM 特别处理研究.年报摘要;
CREATE OR REPLACE VIEW 特处兼容.st_calendar AS SELECT * FROM 特别处理研究.交易日历;
CREATE OR REPLACE VIEW 特处兼容.st_company AS SELECT * FROM 特别处理研究.公司档案;
CREATE OR REPLACE VIEW 特处兼容.st_daily AS SELECT * FROM 特别处理研究.日行情;
CREATE OR REPLACE VIEW 特处兼容.st_daily_returns AS SELECT * FROM 特别处理研究.日收益率;
CREATE OR REPLACE VIEW 特处兼容.st_fin_disclose AS SELECT * FROM 特别处理研究.财务披露;
CREATE OR REPLACE VIEW 特处兼容.st_fin_solvency AS SELECT * FROM 特别处理研究.偿债能力;
CREATE OR REPLACE VIEW 特处兼容.st_index_daily AS SELECT * FROM 特别处理研究.指数日行情;
CREATE OR REPLACE VIEW 特处兼容.st_industry AS SELECT * FROM 特别处理研究.行业归属;
CREATE OR REPLACE VIEW 特处兼容.st_ipo AS SELECT * FROM 特别处理研究.新股发行;
CREATE OR REPLACE VIEW 特处兼容.st_listing_status AS SELECT * FROM 特别处理研究.上市状态;
CREATE OR REPLACE VIEW 特处兼容.st_market_returns AS SELECT * FROM 特别处理研究.市场收益;
CREATE OR REPLACE VIEW 特处兼容.st_monthly_returns AS SELECT * FROM 特别处理研究.月收益率;
CREATE OR REPLACE VIEW 特处兼容.st_name_changes AS SELECT * FROM 特别处理研究.曾用名;
CREATE OR REPLACE VIEW 特处兼容.st_no_limit AS SELECT * FROM 特别处理研究.无涨跌停;
CREATE OR REPLACE VIEW 特处兼容.st_special_treatment AS SELECT * FROM 特别处理研究.特别处理;
CREATE OR REPLACE VIEW 特处兼容.st_st_rules AS SELECT * FROM 特别处理研究.ST规则;

-- 注：特别处理研究.日行情/交易日历/公司档案/上市状态/指数日行情
-- 已是指向 微盘股研究 的视图（见 特处共享去重.sql）；上表兼容视图再包一层即可。


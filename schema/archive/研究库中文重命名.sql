-- 研究库表名中文化（执行前表已在对应中文 schema，英文表名）
-- 以及常用字段中文化；宽表因子列保留英文名

BEGIN;

-- ========== 隐因子 ==========
ALTER TABLE IF EXISTS 隐因子.stocks RENAME TO 股票信息;
ALTER TABLE IF EXISTS 隐因子.trading_dates RENAME TO 交易日历;
ALTER TABLE IF EXISTS 隐因子.factor_registry RENAME TO 因子注册;
ALTER TABLE IF EXISTS 隐因子.factor_values RENAME TO 因子取值;
ALTER TABLE IF EXISTS 隐因子.latent_coords RENAME TO 隐空间坐标;
ALTER TABLE IF EXISTS 隐因子.factor_loadings RENAME TO 因子载荷;
ALTER TABLE IF EXISTS 隐因子.model_configs RENAME TO 模型配置;

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='隐因子' AND table_name='股票信息' AND column_name='stock_code') THEN
    ALTER TABLE 隐因子.股票信息 RENAME COLUMN stock_code TO 证券代码;
    ALTER TABLE 隐因子.股票信息 RENAME COLUMN stock_name TO 证券简称;
    ALTER TABLE 隐因子.股票信息 RENAME COLUMN industry_a TO 行业甲;
    ALTER TABLE 隐因子.股票信息 RENAME COLUMN industry_b TO 行业乙;
    ALTER TABLE 隐因子.股票信息 RENAME COLUMN list_date TO 上市日期;
    ALTER TABLE 隐因子.股票信息 RENAME COLUMN status TO 状态;
    ALTER TABLE 隐因子.股票信息 RENAME COLUMN updated_at TO 更新时间;
  END IF;
  IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='隐因子' AND table_name='交易日历' AND column_name='trade_date') THEN
    ALTER TABLE 隐因子.交易日历 RENAME COLUMN trade_date TO 交易日期;
  END IF;
  IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='隐因子' AND table_name='因子取值' AND column_name='stock_code') THEN
    ALTER TABLE 隐因子.因子取值 RENAME COLUMN stock_code TO 证券代码;
    ALTER TABLE 隐因子.因子取值 RENAME COLUMN trade_date TO 交易日期;
    ALTER TABLE 隐因子.因子取值 RENAME COLUMN open TO 开盘价;
    ALTER TABLE 隐因子.因子取值 RENAME COLUMN high TO 最高价;
    ALTER TABLE 隐因子.因子取值 RENAME COLUMN low TO 最低价;
    ALTER TABLE 隐因子.因子取值 RENAME COLUMN close TO 收盘价;
  END IF;
  IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='隐因子' AND table_name='隐空间坐标' AND column_name='stock_code') THEN
    ALTER TABLE 隐因子.隐空间坐标 RENAME COLUMN stock_code TO 证券代码;
    ALTER TABLE 隐因子.隐空间坐标 RENAME COLUMN trade_date TO 交易日期;
    ALTER TABLE 隐因子.隐空间坐标 RENAME COLUMN method TO 方法;
    ALTER TABLE 隐因子.隐空间坐标 RENAME COLUMN n_components TO 成分数;
    ALTER TABLE 隐因子.隐空间坐标 RENAME COLUMN created_at TO 创建时间;
  END IF;
END $$;

-- ========== 微盘股研究（去 mc_ 前缀并中文）==========
ALTER TABLE IF EXISTS 微盘股研究.mc_calendar RENAME TO 交易日历;
ALTER TABLE IF EXISTS 微盘股研究.mc_company RENAME TO 公司档案;
ALTER TABLE IF EXISTS 微盘股研究.mc_daily RENAME TO 日行情;
ALTER TABLE IF EXISTS 微盘股研究.mc_daily_returns RENAME TO 日收益率;
ALTER TABLE IF EXISTS 微盘股研究.mc_definitions RENAME TO 口径定义;
ALTER TABLE IF EXISTS 微盘股研究.mc_index_daily RENAME TO 指数日行情;
ALTER TABLE IF EXISTS 微盘股研究.mc_industry RENAME TO 行业归属;
ALTER TABLE IF EXISTS 微盘股研究.mc_ipo RENAME TO 新股发行;
ALTER TABLE IF EXISTS 微盘股研究.mc_lifecycle RENAME TO 生命周期;
ALTER TABLE IF EXISTS 微盘股研究.mc_listing_status RENAME TO 上市状态;
ALTER TABLE IF EXISTS 微盘股研究.mc_market_returns RENAME TO 市场收益;
ALTER TABLE IF EXISTS 微盘股研究.mc_monthly_portfolio RENAME TO 月度组合;
ALTER TABLE IF EXISTS 微盘股研究.mc_monthly_ranking RENAME TO 月度排名;
ALTER TABLE IF EXISTS 微盘股研究.mc_monthly_returns RENAME TO 月收益率;
ALTER TABLE IF EXISTS 微盘股研究.mc_name_changes RENAME TO 曾用名;
ALTER TABLE IF EXISTS 微盘股研究.mc_no_limit RENAME TO 无涨跌停;
ALTER TABLE IF EXISTS 微盘股研究.mc_policy_events RENAME TO 政策事件;
ALTER TABLE IF EXISTS 微盘股研究.mc_regime_monthly RENAME TO 月度体制;
ALTER TABLE IF EXISTS 微盘股研究.mc_share_capital RENAME TO 股本变动;
ALTER TABLE IF EXISTS 微盘股研究.mc_special_treatment RENAME TO 特别处理;
ALTER TABLE IF EXISTS 微盘股研究.mc_st_overlap RENAME TO 与ST重叠;

-- ========== 特别处理研究 ==========
ALTER TABLE IF EXISTS 特别处理研究.st_annual RENAME TO 年报摘要;
ALTER TABLE IF EXISTS 特别处理研究.st_calendar RENAME TO 交易日历;
ALTER TABLE IF EXISTS 特别处理研究.st_company RENAME TO 公司档案;
ALTER TABLE IF EXISTS 特别处理研究.st_daily RENAME TO 日行情;
ALTER TABLE IF EXISTS 特别处理研究.st_daily_returns RENAME TO 日收益率;
ALTER TABLE IF EXISTS 特别处理研究.st_fin_disclose RENAME TO 财务披露;
ALTER TABLE IF EXISTS 特别处理研究.st_fin_solvency RENAME TO 偿债能力;
ALTER TABLE IF EXISTS 特别处理研究.st_index_daily RENAME TO 指数日行情;
ALTER TABLE IF EXISTS 特别处理研究.st_industry RENAME TO 行业归属;
ALTER TABLE IF EXISTS 特别处理研究.st_ipo RENAME TO 新股发行;
ALTER TABLE IF EXISTS 特别处理研究.st_listing_status RENAME TO 上市状态;
ALTER TABLE IF EXISTS 特别处理研究.st_market_returns RENAME TO 市场收益;
ALTER TABLE IF EXISTS 特别处理研究.st_monthly_returns RENAME TO 月收益率;
ALTER TABLE IF EXISTS 特别处理研究.st_name_changes RENAME TO 曾用名;
ALTER TABLE IF EXISTS 特别处理研究.st_no_limit RENAME TO 无涨跌停;
ALTER TABLE IF EXISTS 特别处理研究.st_special_treatment RENAME TO 特别处理;
ALTER TABLE IF EXISTS 特别处理研究.st_st_rules RENAME TO ST规则;

COMMIT;

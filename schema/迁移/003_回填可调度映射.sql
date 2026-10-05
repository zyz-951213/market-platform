-- 回填 database 责任方可调度表的 拉取源/更新函数/期望锚点/批量标签
-- 字段映射：源侧异构字段在各更新函数内归一到公共表列；此处只登记调度元数据

UPDATE 行情增量.数据集登记 AS r SET
  拉取源 = v.拉取源,
  更新函数 = v.更新函数,
  可调度 = v.可调度,
  批量标签 = v.批量标签,
  发布滞后天数 = v.滞后天数,
  期望锚点 = v.期望锚点,
  超时秒 = v.超时秒,
  并发组 = v.并发组,
  更新入口 = COALESCE(NULLIF(r.更新入口, ''), v.更新函数)
FROM (VALUES
  -- 日频
  ('公共', '个股日行情文件', 'baostock', 'baostock.update_daily_bars', true, '日频', 0, '交易日', 7200, 'baostock'),
  ('公共', '国内指数日行情文件', 'akshare', 'akshare.update_index_daily', true, '日频', 0, '交易日', 1800, 'akshare'),
  ('公共', '日市场回报率文件', 'akshare', '补充.update_market_daily', true, '日频', 0, '交易日', 1800, '补充'),
  ('公共', '汇率文件', 'akshare', 'akshare.update_exchange_rate', true, '日频', 0, '自然日', 600, 'akshare'),
  ('公共', '国内期货日交易数据', 'akshare', 'akshare.update_futures_daily', true, '日频', 0, '交易日', 1800, 'akshare'),
  ('公共', '无风险利率文件', 'akshare', 'akshare.update_risk_free', true, '日频', 0, '自然日', 600, 'akshare'),
  ('公共', '个股资金流向', 'akshare', '补充.update_money_flow', true, '资金流', 0, '交易日', 7200, '补充'),
  ('公共', '融资融券', 'akshare', '补充.update_margin', true, '资金流', 1, '交易日', 1800, '补充'),
  ('公共', '龙虎榜', 'akshare', '补充.update_dragon_tiger', true, '资金流', 1, '交易日', 1800, '补充'),
  ('公共', '龙虎榜营业部', 'akshare', '补充.update_dragon_tiger_dept', true, '资金流', 1, '交易日', 1800, '补充'),
  ('公共', 'ETF基金申购赎回清单', 'akshare', '补充.update_etf_list', true, '日频', 1, '交易日', 1800, '补充'),
  ('公共', 'ETF融资融券交易明细表', 'akshare', '补充.update_etf_margin', true, '日频', 1, '交易日', 1800, '补充'),
  -- 主数据 / 事件
  ('公共', '公司文件', 'baostock', '补充.update_company_info', true, '主数据', 0, '自然日', 1800, '补充'),
  ('公共', '上市状态变更表', 'baostock', '补充.update_list_status', true, '主数据', 0, '自然日', 1800, '补充'),
  ('公共', '分配文件', 'baostock', '补充.update_dividend', true, '主数据', 0, '自然日', 1800, '补充'),
  ('公共', '指数基本信息文件', 'akshare', '补充.update_index_info', true, '主数据', 0, '自然日', 600, '补充'),
  -- 宏观（发布约滞后 30–45 天）
  ('公共', '各层次货币供应量月度文件', 'akshare', 'akshare.update_money_supply', true, '宏观', 40, '月初', 600, 'akshare'),
  ('公共', '居民消费价格涨幅表(月)', 'akshare', 'akshare.update_cpi', true, '宏观', 40, '月初', 600, 'akshare'),
  ('公共', '采购经理人指数表', 'akshare', 'akshare.update_pmi', true, '宏观', 10, '月初', 600, 'akshare'),
  ('公共', '国内生产总值表(季)', 'akshare', 'akshare.update_gdp', true, '宏观', 45, '季末', 600, 'akshare'),
  ('公共', '固定资产投资表(月)', 'akshare', 'akshare.update_fixed_invest', true, '宏观', 40, '月初', 600, 'akshare'),
  ('公共', '工业生产者出厂价格涨幅表(月)', 'akshare', 'akshare.update_ppi', true, '宏观', 40, '月初', 600, 'akshare'),
  ('公共', '人民币存款准备金率表', 'akshare', 'akshare.update_reserve_rate', true, '宏观', 0, '自然日', 600, 'akshare'),
  ('公共', '社会消费品零售总额表(月)', 'akshare', 'akshare.update_retail_sales', true, '宏观', 40, '月初', 600, 'akshare'),
  ('公共', '规模以上工业增加值增速表(月)', 'akshare', 'akshare.update_industrial_output', true, '宏观', 40, '月初', 600, 'akshare'),
  ('公共', '央行公开市场业务', 'akshare', 'akshare.update_open_market', true, '宏观', 1, '自然日', 600, 'akshare'),
  -- 财报
  ('公共', '利润表', 'baostock', 'baostock.update_income', true, '财报', 30, '季末', 7200, 'baostock'),
  ('公共', '资产负债表', 'baostock', 'baostock.update_balance', true, '财报', 30, '季末', 7200, 'baostock'),
  ('公共', '现金流量表(合并)', 'baostock', '补充.update_cashflow', true, '财报', 30, '季末', 7200, '补充'),
  ('公共', '预披露日期表', 'akshare', 'akshare.update_report_dates', true, '财报', 0, '自然日', 1800, 'akshare')
) AS v(数据源, 数据集, 拉取源, 更新函数, 可调度, 批量标签, 滞后天数, 期望锚点, 超时秒, 并发组)
WHERE r.数据源 = v.数据源 AND r.数据集 = v.数据集;

-- 明确不可调度
UPDATE 行情增量.数据集登记 SET
  可调度 = false,
  拉取源 = COALESCE(拉取源, CASE 更新策略
    WHEN '派生' THEN '派生'
    WHEN '冻结' THEN '无'
    WHEN '人工' THEN '人工'
    ELSE 拉取源 END),
  期望锚点 = CASE WHEN 期望锚点 = '无' AND 更新策略 = '冻结' THEN '无' ELSE 期望锚点 END
WHERE 责任方 = 'database'
  AND 数据集 IN ('日行情', '日历文件', '证券档案', '上市公司基本信息年度表', '国内商品期货基本信息表', '年、中、季报基本情况文件');

UPDATE 行情增量.数据集登记 SET 可调度 = false
WHERE 责任方 <> 'database' OR 更新策略 = '冻结';

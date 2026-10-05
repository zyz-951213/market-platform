-- 行为分析.标的时序 → 窄表（去掉 JSONB 膨胀）
-- 仅保留两类分析主输入：参与者活跃度、分钟日特征

CREATE TABLE IF NOT EXISTS 行为分析.参与者活跃度 (
  证券代码 character(6) NOT NULL REFERENCES 公共.证券档案(证券代码) ON DELETE CASCADE,
  交易日期 date NOT NULL,
  主力资金 double precision,
  北向资金 double precision,
  尾盘量能 double precision,
  融资融券 double precision,
  分级资金流 double precision,
  波动冲击 double precision,
  成交规模 double precision,
  PRIMARY KEY (证券代码, 交易日期)
);

CREATE TABLE IF NOT EXISTS 行为分析.分钟日特征 (
  证券代码 character(6) NOT NULL REFERENCES 公共.证券档案(证券代码) ON DELETE CASCADE,
  交易日期 date NOT NULL,
  return_mean double precision,
  return_std double precision,
  volume_total double precision,
  amount_total double precision,
  volume_spike_mean double precision,
  volume_spike_max double precision,
  realized_vol_mean double precision,
  realized_vol_max double precision,
  vwap_dev_mean double precision,
  vwap_dev_std double precision,
  price_impact_mean double precision,
  price_impact_max double precision,
  high_low_range_mean double precision,
  open_volume_ratio double precision,
  tail_volume_ratio double precision,
  minute_bars double precision,
  PRIMARY KEY (证券代码, 交易日期)
);

CREATE INDEX IF NOT EXISTS 参与者活跃度_日期_索引 ON 行为分析.参与者活跃度 (交易日期);
CREATE INDEX IF NOT EXISTS 分钟日特征_日期_索引 ON 行为分析.分钟日特征 (交易日期);

-- 从 JSONB 标的时序灌入（幂等：ON CONFLICT DO NOTHING，可重复跑）
INSERT INTO 行为分析.参与者活跃度 (
  证券代码, 交易日期,
  主力资金, 北向资金, 尾盘量能, 融资融券, 分级资金流, 波动冲击, 成交规模
)
SELECT
  证券代码, 交易日期,
  NULLIF(载荷->>'主力资金', '')::double precision,
  NULLIF(载荷->>'北向资金', '')::double precision,
  NULLIF(载荷->>'尾盘量能', '')::double precision,
  NULLIF(载荷->>'融资融券', '')::double precision,
  NULLIF(载荷->>'分级资金流', '')::double precision,
  NULLIF(载荷->>'波动冲击', '')::double precision,
  NULLIF(载荷->>'成交规模', '')::double precision
FROM 行为分析.标的时序
WHERE 序列类型 = '参与者活跃度'
ON CONFLICT (证券代码, 交易日期) DO NOTHING;

INSERT INTO 行为分析.分钟日特征 (
  证券代码, 交易日期,
  return_mean, return_std, volume_total, amount_total,
  volume_spike_mean, volume_spike_max,
  realized_vol_mean, realized_vol_max,
  vwap_dev_mean, vwap_dev_std,
  price_impact_mean, price_impact_max,
  high_low_range_mean, open_volume_ratio, tail_volume_ratio, minute_bars
)
SELECT
  证券代码, 交易日期,
  NULLIF(载荷->>'return_mean', '')::double precision,
  NULLIF(载荷->>'return_std', '')::double precision,
  NULLIF(载荷->>'volume_total', '')::double precision,
  NULLIF(载荷->>'amount_total', '')::double precision,
  NULLIF(载荷->>'volume_spike_mean', '')::double precision,
  NULLIF(载荷->>'volume_spike_max', '')::double precision,
  NULLIF(载荷->>'realized_vol_mean', '')::double precision,
  NULLIF(载荷->>'realized_vol_max', '')::double precision,
  NULLIF(载荷->>'vwap_dev_mean', '')::double precision,
  NULLIF(载荷->>'vwap_dev_std', '')::double precision,
  NULLIF(载荷->>'price_impact_mean', '')::double precision,
  NULLIF(载荷->>'price_impact_max', '')::double precision,
  NULLIF(载荷->>'high_low_range_mean', '')::double precision,
  NULLIF(载荷->>'open_volume_ratio', '')::double precision,
  NULLIF(载荷->>'tail_volume_ratio', '')::double precision,
  NULLIF(载荷->>'minute_bars', '')::double precision
FROM 行为分析.标的时序
WHERE 序列类型 = '分钟日特征'
ON CONFLICT (证券代码, 交易日期) DO NOTHING;

-- 英文兼容视图：由窄表拼回 payload（读路径兼容）
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

-- 写触发器：落到窄表
CREATE OR REPLACE FUNCTION 行为分析.写入标的时序()
RETURNS TRIGGER AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF OLD.series_type = 'agent_activity' THEN
            DELETE FROM 行为分析.参与者活跃度
            WHERE 证券代码 = OLD.symbol AND 交易日期 = OLD.date;
        ELSIF OLD.series_type = 'minute_daily_features' THEN
            DELETE FROM 行为分析.分钟日特征
            WHERE 证券代码 = OLD.symbol AND 交易日期 = OLD.date;
        END IF;
        RETURN OLD;
    END IF;

    IF NEW.series_type = 'agent_activity' THEN
        INSERT INTO 行为分析.参与者活跃度 (
          证券代码, 交易日期,
          主力资金, 北向资金, 尾盘量能, 融资融券, 分级资金流, 波动冲击, 成交规模
        ) VALUES (
          NEW.symbol, NEW.date,
          NULLIF(NEW.payload->>'主力资金', '')::double precision,
          NULLIF(NEW.payload->>'北向资金', '')::double precision,
          NULLIF(NEW.payload->>'尾盘量能', '')::double precision,
          NULLIF(NEW.payload->>'融资融券', '')::double precision,
          NULLIF(NEW.payload->>'分级资金流', '')::double precision,
          NULLIF(NEW.payload->>'波动冲击', '')::double precision,
          NULLIF(NEW.payload->>'成交规模', '')::double precision
        )
        ON CONFLICT (证券代码, 交易日期) DO UPDATE SET
          主力资金 = EXCLUDED.主力资金,
          北向资金 = EXCLUDED.北向资金,
          尾盘量能 = EXCLUDED.尾盘量能,
          融资融券 = EXCLUDED.融资融券,
          分级资金流 = EXCLUDED.分级资金流,
          波动冲击 = EXCLUDED.波动冲击,
          成交规模 = EXCLUDED.成交规模;
    ELSIF NEW.series_type = 'minute_daily_features' THEN
        INSERT INTO 行为分析.分钟日特征 (
          证券代码, 交易日期,
          return_mean, return_std, volume_total, amount_total,
          volume_spike_mean, volume_spike_max,
          realized_vol_mean, realized_vol_max,
          vwap_dev_mean, vwap_dev_std,
          price_impact_mean, price_impact_max,
          high_low_range_mean, open_volume_ratio, tail_volume_ratio, minute_bars
        ) VALUES (
          NEW.symbol, NEW.date,
          NULLIF(NEW.payload->>'return_mean', '')::double precision,
          NULLIF(NEW.payload->>'return_std', '')::double precision,
          NULLIF(NEW.payload->>'volume_total', '')::double precision,
          NULLIF(NEW.payload->>'amount_total', '')::double precision,
          NULLIF(NEW.payload->>'volume_spike_mean', '')::double precision,
          NULLIF(NEW.payload->>'volume_spike_max', '')::double precision,
          NULLIF(NEW.payload->>'realized_vol_mean', '')::double precision,
          NULLIF(NEW.payload->>'realized_vol_max', '')::double precision,
          NULLIF(NEW.payload->>'vwap_dev_mean', '')::double precision,
          NULLIF(NEW.payload->>'vwap_dev_std', '')::double precision,
          NULLIF(NEW.payload->>'price_impact_mean', '')::double precision,
          NULLIF(NEW.payload->>'price_impact_max', '')::double precision,
          NULLIF(NEW.payload->>'high_low_range_mean', '')::double precision,
          NULLIF(NEW.payload->>'open_volume_ratio', '')::double precision,
          NULLIF(NEW.payload->>'tail_volume_ratio', '')::double precision,
          NULLIF(NEW.payload->>'minute_bars', '')::double precision
        )
        ON CONFLICT (证券代码, 交易日期) DO UPDATE SET
          return_mean = EXCLUDED.return_mean,
          return_std = EXCLUDED.return_std,
          volume_total = EXCLUDED.volume_total,
          amount_total = EXCLUDED.amount_total,
          volume_spike_mean = EXCLUDED.volume_spike_mean,
          volume_spike_max = EXCLUDED.volume_spike_max,
          realized_vol_mean = EXCLUDED.realized_vol_mean,
          realized_vol_max = EXCLUDED.realized_vol_max,
          vwap_dev_mean = EXCLUDED.vwap_dev_mean,
          vwap_dev_std = EXCLUDED.vwap_dev_std,
          price_impact_mean = EXCLUDED.price_impact_mean,
          price_impact_max = EXCLUDED.price_impact_max,
          high_low_range_mean = EXCLUDED.high_low_range_mean,
          open_volume_ratio = EXCLUDED.open_volume_ratio,
          tail_volume_ratio = EXCLUDED.tail_volume_ratio,
          minute_bars = EXCLUDED.minute_bars;
    ELSE
        RAISE EXCEPTION '行为分析.symbol_series 已不再接受 ephemeral 序列类型: %', NEW.series_type;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS 标的时序_写触发 ON 行为分析.symbol_series;
CREATE TRIGGER 标的时序_写触发
INSTEAD OF INSERT OR UPDATE OR DELETE ON 行为分析.symbol_series
FOR EACH ROW EXECUTE FUNCTION 行为分析.写入标的时序();

-- 行数校验通过后丢弃 JSON 宽表（已执行）：
-- DROP TABLE 行为分析.标的时序;

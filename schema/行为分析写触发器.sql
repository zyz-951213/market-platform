-- 行为分析英文视图写回触发器（落到窄表；原 JSON 标的时序已删除）

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

CREATE OR REPLACE FUNCTION 行为分析.写入标的产物()
RETURNS TRIGGER AS $$
DECLARE
    v_type TEXT;
BEGIN
    IF TG_OP = 'DELETE' THEN
        v_type := CASE OLD.artifact_type
            WHEN 'agent_patterns' THEN '参与者模式'
            WHEN 'agent_pattern_labels' THEN '参与者标签'
            WHEN 'agent_names' THEN '参与者名称'
            WHEN 'optimized_positions' THEN '优化仓位'
            WHEN 'transition_matrix' THEN '转移矩阵'
            WHEN 'next_state_probs' THEN '下一状态概率'
            ELSE OLD.artifact_type
        END;
        DELETE FROM 行为分析.标的产物
        WHERE 证券代码 = OLD.symbol AND 产物类型 = v_type;
        RETURN OLD;
    END IF;

    v_type := CASE NEW.artifact_type
        WHEN 'agent_patterns' THEN '参与者模式'
        WHEN 'agent_pattern_labels' THEN '参与者标签'
        WHEN 'agent_names' THEN '参与者名称'
        WHEN 'optimized_positions' THEN '优化仓位'
        WHEN 'transition_matrix' THEN '转移矩阵'
        WHEN 'next_state_probs' THEN '下一状态概率'
        ELSE NEW.artifact_type
    END;

    INSERT INTO 行为分析.标的产物(证券代码, 产物类型, 载荷, 更新时间)
    VALUES (NEW.symbol, v_type, NEW.payload, COALESCE(NEW.updated_at, NOW()))
    ON CONFLICT (证券代码, 产物类型) DO UPDATE
    SET 载荷 = EXCLUDED.载荷, 更新时间 = EXCLUDED.更新时间;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS 标的产物_写触发 ON 行为分析.symbol_artifacts;
CREATE TRIGGER 标的产物_写触发
INSTEAD OF INSERT OR UPDATE OR DELETE ON 行为分析.symbol_artifacts
FOR EACH ROW EXECUTE FUNCTION 行为分析.写入标的产物();

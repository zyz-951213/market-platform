-- 带类型翻译的视图不可自动更新，用触发器写回中文物理表

CREATE OR REPLACE FUNCTION 兼容.写入标的时序()
RETURNS TRIGGER AS $$
DECLARE
    v_type TEXT;
BEGIN
    IF TG_OP = 'DELETE' THEN
        v_type := CASE OLD.series_type
            WHEN 'minute_daily_features' THEN '分钟日特征'
            WHEN 'agent_activity' THEN '参与者活跃度'
            WHEN 'daily_feature_matrix' THEN '日特征矩阵'
            WHEN 'state_features' THEN '状态特征'
            WHEN 'minute_bars' THEN '分钟行情'
            ELSE OLD.series_type
        END;
        DELETE FROM 行为分析.标的时序
        WHERE 证券代码 = OLD.symbol AND 序列类型 = v_type AND 交易日期 = OLD.date;
        RETURN OLD;
    END IF;

    v_type := CASE NEW.series_type
        WHEN 'minute_daily_features' THEN '分钟日特征'
        WHEN 'agent_activity' THEN '参与者活跃度'
        WHEN 'daily_feature_matrix' THEN '日特征矩阵'
        WHEN 'state_features' THEN '状态特征'
        WHEN 'minute_bars' THEN '分钟行情'
        ELSE NEW.series_type
    END;

    IF TG_OP = 'UPDATE' THEN
        UPDATE 行为分析.标的时序
        SET 载荷 = NEW.payload
        WHERE 证券代码 = NEW.symbol AND 序列类型 = v_type AND 交易日期 = NEW.date;
        IF NOT FOUND THEN
            INSERT INTO 行为分析.标的时序(证券代码, 序列类型, 交易日期, 载荷)
            VALUES (NEW.symbol, v_type, NEW.date, NEW.payload);
        END IF;
        RETURN NEW;
    END IF;

    INSERT INTO 行为分析.标的时序(证券代码, 序列类型, 交易日期, 载荷)
    VALUES (NEW.symbol, v_type, NEW.date, NEW.payload)
    ON CONFLICT (证券代码, 序列类型, 交易日期) DO UPDATE SET 载荷 = EXCLUDED.载荷;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS 标的时序_写触发 ON 兼容.symbol_series;
CREATE TRIGGER 标的时序_写触发
INSTEAD OF INSERT OR UPDATE OR DELETE ON 兼容.symbol_series
FOR EACH ROW EXECUTE FUNCTION 兼容.写入标的时序();

CREATE OR REPLACE FUNCTION 兼容.写入标的产物()
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

DROP TRIGGER IF EXISTS 标的产物_写触发 ON 兼容.symbol_artifacts;
CREATE TRIGGER 标的产物_写触发
INSTEAD OF INSERT OR UPDATE OR DELETE ON 兼容.symbol_artifacts
FOR EACH ROW EXECUTE FUNCTION 兼容.写入标的产物();

-- 行为分析.equity_curves：市场收益率 ≡ 基准收益率，物理只留一列

CREATE OR REPLACE FUNCTION 行为分析.equity_curves_写回() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  v_bench double precision;
BEGIN
  IF TG_OP = 'DELETE' THEN
    DELETE FROM 行为分析.策略净值
    WHERE 证券代码 = OLD.symbol AND 交易日期 = OLD.date;
    RETURN OLD;
  END IF;

  v_bench := COALESCE(NEW.benchmark_return, NEW.market_return);

  INSERT INTO 行为分析.策略净值 (
    证券代码, 交易日期, 建议仓位, 市场状态, 状态得分,
    仓位, 仓位变动, 策略收益率, 基准收益率, 策略净值, 基准净值
  ) VALUES (
    NEW.symbol, NEW.date, NEW.suggested_position, NEW.market_state, NEW.state_score,
    NEW.position, NEW.position_change, NEW.strategy_return, v_bench,
    NEW.strategy_equity, NEW.benchmark_equity
  )
  ON CONFLICT (证券代码, 交易日期) DO UPDATE SET
    建议仓位 = COALESCE(EXCLUDED.建议仓位, 行为分析.策略净值.建议仓位),
    市场状态 = COALESCE(EXCLUDED.市场状态, 行为分析.策略净值.市场状态),
    状态得分 = COALESCE(EXCLUDED.状态得分, 行为分析.策略净值.状态得分),
    仓位 = COALESCE(EXCLUDED.仓位, 行为分析.策略净值.仓位),
    仓位变动 = COALESCE(EXCLUDED.仓位变动, 行为分析.策略净值.仓位变动),
    策略收益率 = COALESCE(EXCLUDED.策略收益率, 行为分析.策略净值.策略收益率),
    基准收益率 = COALESCE(EXCLUDED.基准收益率, 行为分析.策略净值.基准收益率),
    策略净值 = COALESCE(EXCLUDED.策略净值, 行为分析.策略净值.策略净值),
    基准净值 = COALESCE(EXCLUDED.基准净值, 行为分析.策略净值.基准净值);
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS equity_curves_写回 ON 行为分析.equity_curves;
CREATE TRIGGER equity_curves_写回
  INSTEAD OF INSERT OR UPDATE OR DELETE ON 行为分析.equity_curves
  FOR EACH ROW EXECUTE FUNCTION 行为分析.equity_curves_写回();

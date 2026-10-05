-- 隐因子.factor_values 写回：因子列→因子取值；量价→行情补丁

CREATE OR REPLACE FUNCTION 隐因子.factor_values_写回() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    DELETE FROM 隐因子.行情补丁
     WHERE "证券代码" = OLD.stock_code AND "交易日期" = OLD.trade_date;
    DELETE FROM 隐因子.因子取值
     WHERE "证券代码" = OLD.stock_code AND "交易日期" = OLD.trade_date;
    RETURN OLD;
  END IF;

  INSERT INTO 隐因子.因子取值 ("证券代码", "交易日期")
  VALUES (NEW.stock_code, NEW.trade_date)
  ON CONFLICT ("证券代码", "交易日期") DO NOTHING;

  -- 仅当提供了收盘价等量价时写入补丁（CSMAR 主段可不写）
  IF NEW.close IS NOT NULL OR NEW.open IS NOT NULL THEN
    INSERT INTO 隐因子.行情补丁 (
      "证券代码", "交易日期", "开盘价", "最高价", "最低价", "收盘价",
      volume, amount, turnover, mkt_cap, float_mkt_cap
    ) VALUES (
      NEW.stock_code, NEW.trade_date,
      NEW.open, NEW.high, NEW.low, NEW.close,
      NEW.volume, NEW.amount, NEW.turnover, NEW.mkt_cap, NEW.float_mkt_cap
    )
    ON CONFLICT ("证券代码", "交易日期") DO UPDATE SET
      "开盘价" = COALESCE(EXCLUDED."开盘价", 隐因子.行情补丁."开盘价"),
      "最高价" = COALESCE(EXCLUDED."最高价", 隐因子.行情补丁."最高价"),
      "最低价" = COALESCE(EXCLUDED."最低价", 隐因子.行情补丁."最低价"),
      "收盘价" = COALESCE(EXCLUDED."收盘价", 隐因子.行情补丁."收盘价"),
      volume = COALESCE(EXCLUDED.volume, 隐因子.行情补丁.volume),
      amount = COALESCE(EXCLUDED.amount, 隐因子.行情补丁.amount),
      turnover = COALESCE(EXCLUDED.turnover, 隐因子.行情补丁.turnover),
      mkt_cap = COALESCE(EXCLUDED.mkt_cap, 隐因子.行情补丁.mkt_cap),
      float_mkt_cap = COALESCE(EXCLUDED.float_mkt_cap, 隐因子.行情补丁.float_mkt_cap);
  END IF;

  -- 同步少量常写因子列（增量脚本会写 ret_1d）
  IF NEW.ret_1d IS NOT NULL THEN
    UPDATE 隐因子.因子取值
       SET ret_1d = NEW.ret_1d
     WHERE "证券代码" = NEW.stock_code AND "交易日期" = NEW.trade_date;
  END IF;

  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS factor_values_写回 ON 隐因子.factor_values;
CREATE TRIGGER factor_values_写回
  INSTEAD OF INSERT OR UPDATE OR DELETE ON 隐因子.factor_values
  FOR EACH ROW EXECUTE FUNCTION 隐因子.factor_values_写回();

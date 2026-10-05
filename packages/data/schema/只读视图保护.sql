-- 公共/共享视图只读保护（禁止穿透写入真源）

CREATE OR REPLACE FUNCTION 公共.拒绝写入() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION '只读视图，禁止写入: %.%', TG_TABLE_SCHEMA, TG_TABLE_NAME;
END;
$$;

DO $$
DECLARE
  r record;
BEGIN
  FOR r IN
    SELECT * FROM (VALUES
      ('隐因子', '股票信息'),
      ('隐因子', '交易日历'),
      ('特别处理研究', '日行情'),
      ('特别处理研究', '交易日历'),
      ('特别处理研究', '公司档案'),
      ('特别处理研究', '上市状态'),
      ('特别处理研究', '指数日行情'),
      ('特别处理研究', '新股发行'),
      ('特别处理研究', '无涨跌停'),
      ('特别处理研究', '市场收益'),
      ('特别处理研究', '行业归属'),
      ('特别处理研究', '月收益率'),
      ('特别处理研究', '日收益率'),
      ('公共', '日行情'),
      ('公共', '交易日历'),
      ('公共', '公司档案'),
      ('公共', '上市状态'),
      ('公共', '指数日行情'),
      ('公共', '日收益率'),
      ('公共', '月收益率'),
      ('公共', '行业归属'),
      ('公共', '市场收益'),
      ('公共', '新股发行'),
      ('公共', '无涨跌停'),
      ('公共', '日行情_应用'),
      ('公共', '日行情_应用投影'),
      ('公共', '证券档案'),
      ('公共', '个股日行情_原始'),
      ('公共', '指数日行情_原始'),
      ('公共', '日历_原始'),
      ('公共', '公司档案_原始')
    ) AS t(schema_name, view_name)
  LOOP
    EXECUTE format(
      'DROP TRIGGER IF EXISTS 拒绝写入 ON %I.%I',
      r.schema_name, r.view_name
    );
    EXECUTE format(
      'CREATE TRIGGER 拒绝写入
         INSTEAD OF INSERT OR UPDATE OR DELETE ON %I.%I
         FOR EACH ROW EXECUTE FUNCTION 公共.拒绝写入()',
      r.schema_name, r.view_name
    );
  END LOOP;
END $$;

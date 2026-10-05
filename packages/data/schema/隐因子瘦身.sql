-- 隐因子瘦身：隐空间坐标去掉重复标签列；因子取值去掉冗余主键/索引

-- 1) 标签收拢到一张小表（全库仅 1 套标签）
CREATE TABLE IF NOT EXISTS 隐因子.隐维度标签 (
  方法       character varying(20)  NOT NULL,
  成分数     smallint               NOT NULL,
  d1_label   character varying(50),
  d2_label   character varying(50),
  d3_label   character varying(50),
  d4_label   character varying(50),
  d5_label   character varying(50),
  d6_label   character varying(50),
  d7_label   character varying(50),
  d8_label   character varying(50),
  d9_label   character varying(50),
  PRIMARY KEY (方法, 成分数)
);

INSERT INTO 隐因子.隐维度标签 (
  方法, 成分数,
  d1_label, d2_label, d3_label, d4_label, d5_label,
  d6_label, d7_label, d8_label, d9_label
)
SELECT DISTINCT ON (方法, 成分数)
  方法, 成分数,
  d1_label, d2_label, d3_label, d4_label, d5_label,
  d6_label, d7_label, d8_label, d9_label
FROM 隐因子.隐空间坐标
WHERE d1_label IS NOT NULL
ORDER BY 方法, 成分数
ON CONFLICT (方法, 成分数) DO UPDATE SET
  d1_label = EXCLUDED.d1_label,
  d2_label = EXCLUDED.d2_label,
  d3_label = EXCLUDED.d3_label,
  d4_label = EXCLUDED.d4_label,
  d5_label = EXCLUDED.d5_label,
  d6_label = EXCLUDED.d6_label,
  d7_label = EXCLUDED.d7_label,
  d8_label = EXCLUDED.d8_label,
  d9_label = EXCLUDED.d9_label;

DROP VIEW IF EXISTS 隐因子.latent_coords CASCADE;

ALTER TABLE 隐因子.隐空间坐标
  DROP COLUMN IF EXISTS d1_label,
  DROP COLUMN IF EXISTS d2_label,
  DROP COLUMN IF EXISTS d3_label,
  DROP COLUMN IF EXISTS d4_label,
  DROP COLUMN IF EXISTS d5_label,
  DROP COLUMN IF EXISTS d6_label,
  DROP COLUMN IF EXISTS d7_label,
  DROP COLUMN IF EXISTS d8_label,
  DROP COLUMN IF EXISTS d9_label,
  DROP COLUMN IF EXISTS 创建时间;

ALTER TABLE 隐因子.隐空间坐标 DROP CONSTRAINT IF EXISTS latent_coords_pkey;
ALTER TABLE 隐因子.隐空间坐标 DROP COLUMN IF EXISTS id;
DROP INDEX IF EXISTS 隐因子.idx_lc_stock_date;
DROP INDEX IF EXISTS 隐因子.idx_lc_method;
ALTER TABLE 隐因子.隐空间坐标
  DROP CONSTRAINT IF EXISTS latent_coords_stock_code_trade_date_method_n_components_key;
ALTER TABLE 隐因子.隐空间坐标
  ADD PRIMARY KEY (证券代码, 交易日期, 方法, 成分数);

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

CREATE OR REPLACE FUNCTION 隐因子.latent_coords_写回() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    DELETE FROM 隐因子.隐空间坐标
    WHERE "证券代码" = OLD.stock_code
      AND "交易日期" = OLD.trade_date
      AND "方法" = OLD.method
      AND "成分数" = OLD.n_components;
    RETURN OLD;
  END IF;

  INSERT INTO 隐因子.隐空间坐标 (
    "证券代码", "交易日期", "方法", "成分数",
    d1, d2, d3, d4, d5, d6, d7, d8, d9, explained_var_total
  ) VALUES (
    NEW.stock_code, NEW.trade_date, COALESCE(NEW.method, 'pca'), COALESCE(NEW.n_components, 9),
    NEW.d1, NEW.d2, NEW.d3, NEW.d4, NEW.d5, NEW.d6, NEW.d7, NEW.d8, NEW.d9,
    NEW.explained_var_total
  )
  ON CONFLICT ("证券代码", "交易日期", "方法", "成分数") DO UPDATE SET
    d1 = EXCLUDED.d1, d2 = EXCLUDED.d2, d3 = EXCLUDED.d3,
    d4 = EXCLUDED.d4, d5 = EXCLUDED.d5, d6 = EXCLUDED.d6,
    d7 = EXCLUDED.d7, d8 = EXCLUDED.d8, d9 = EXCLUDED.d9,
    explained_var_total = EXCLUDED.explained_var_total;

  IF NEW.d1_label IS NOT NULL THEN
    INSERT INTO 隐因子.隐维度标签 (
      "方法", "成分数",
      d1_label, d2_label, d3_label, d4_label, d5_label,
      d6_label, d7_label, d8_label, d9_label
    ) VALUES (
      COALESCE(NEW.method, 'pca'), COALESCE(NEW.n_components, 9),
      NEW.d1_label, NEW.d2_label, NEW.d3_label, NEW.d4_label, NEW.d5_label,
      NEW.d6_label, NEW.d7_label, NEW.d8_label, NEW.d9_label
    )
    ON CONFLICT ("方法", "成分数") DO UPDATE SET
      d1_label = COALESCE(EXCLUDED.d1_label, 隐因子.隐维度标签.d1_label),
      d2_label = COALESCE(EXCLUDED.d2_label, 隐因子.隐维度标签.d2_label),
      d3_label = COALESCE(EXCLUDED.d3_label, 隐因子.隐维度标签.d3_label),
      d4_label = COALESCE(EXCLUDED.d4_label, 隐因子.隐维度标签.d4_label),
      d5_label = COALESCE(EXCLUDED.d5_label, 隐因子.隐维度标签.d5_label),
      d6_label = COALESCE(EXCLUDED.d6_label, 隐因子.隐维度标签.d6_label),
      d7_label = COALESCE(EXCLUDED.d7_label, 隐因子.隐维度标签.d7_label),
      d8_label = COALESCE(EXCLUDED.d8_label, 隐因子.隐维度标签.d8_label),
      d9_label = COALESCE(EXCLUDED.d9_label, 隐因子.隐维度标签.d9_label);
  END IF;
  RETURN NEW;
END;
$$;

CREATE TRIGGER latent_coords_写回
  INSTEAD OF INSERT OR UPDATE OR DELETE ON 隐因子.latent_coords
  FOR EACH ROW EXECUTE FUNCTION 隐因子.latent_coords_写回();

-- 2) 因子取值：去掉 surrogate id + 重复索引 + created_at
DROP VIEW IF EXISTS 隐因子.factor_values CASCADE;

ALTER TABLE 隐因子.因子取值 DROP CONSTRAINT IF EXISTS factor_values_pkey;
DROP INDEX IF EXISTS 隐因子.idx_fv_stock_date;
ALTER TABLE 隐因子.因子取值 DROP CONSTRAINT IF EXISTS factor_values_stock_code_trade_date_key;
ALTER TABLE 隐因子.因子取值 DROP COLUMN IF EXISTS id;
ALTER TABLE 隐因子.因子取值 DROP COLUMN IF EXISTS created_at;
ALTER TABLE 隐因子.因子取值 ADD PRIMARY KEY ("证券代码", "交易日期");

-- factor_values 视图由 隐因子因子取值视图.sql 重建（含量价 COALESCE）

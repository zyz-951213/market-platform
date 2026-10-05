-- 更新控制台：登记扩展 + 运行轨迹 + 期望水位/新鲜度（方案 B）
-- 运行编号由应用层生成 UUID，不依赖 pgcrypto

-- ========== 数据集登记扩展 ==========
ALTER TABLE 行情增量.数据集登记 ADD COLUMN IF NOT EXISTS 拉取源 text;
ALTER TABLE 行情增量.数据集登记 ADD COLUMN IF NOT EXISTS 更新函数 text;
ALTER TABLE 行情增量.数据集登记 ADD COLUMN IF NOT EXISTS 可调度 boolean NOT NULL DEFAULT false;
ALTER TABLE 行情增量.数据集登记 ADD COLUMN IF NOT EXISTS 批量标签 text;
ALTER TABLE 行情增量.数据集登记 ADD COLUMN IF NOT EXISTS 发布滞后天数 integer NOT NULL DEFAULT 0;
ALTER TABLE 行情增量.数据集登记 ADD COLUMN IF NOT EXISTS 期望锚点 text NOT NULL DEFAULT '无';
ALTER TABLE 行情增量.数据集登记 ADD COLUMN IF NOT EXISTS 超时秒 integer NOT NULL DEFAULT 3600;
ALTER TABLE 行情增量.数据集登记 ADD COLUMN IF NOT EXISTS 并发组 text;

COMMENT ON COLUMN 行情增量.数据集登记.拉取源 IS 'akshare|baostock|人工|派生|无';
COMMENT ON COLUMN 行情增量.数据集登记.更新函数 IS '稳定 key，对应后端函数注册表';
COMMENT ON COLUMN 行情增量.数据集登记.可调度 IS '前端/作业可否触发；冻结与无函数为 false';
COMMENT ON COLUMN 行情增量.数据集登记.期望锚点 IS '交易日|自然日|月初|季末|无';
COMMENT ON COLUMN 行情增量.数据集登记.发布滞后天数 IS '期望日 = 锚点日 - 滞后天数';

ALTER TABLE 行情增量.同步水位 ADD COLUMN IF NOT EXISTS 最近成功时间 timestamp without time zone;
ALTER TABLE 行情增量.同步水位 ADD COLUMN IF NOT EXISTS 最近运行编号 uuid;

CREATE TABLE IF NOT EXISTS 行情增量.更新运行 (
  运行编号   uuid PRIMARY KEY,
  触发方式   text NOT NULL,
  范围说明   text,
  状态       text NOT NULL DEFAULT '排队',
  开始时间   timestamp without time zone NOT NULL DEFAULT now(),
  结束时间   timestamp without time zone,
  成功数     integer NOT NULL DEFAULT 0,
  失败数     integer NOT NULL DEFAULT 0,
  跳过数     integer NOT NULL DEFAULT 0,
  摘要       jsonb,
  创建人     text
);

CREATE TABLE IF NOT EXISTS 行情增量.更新运行明细 (
  运行编号     uuid NOT NULL REFERENCES 行情增量.更新运行(运行编号) ON DELETE CASCADE,
  数据源       text NOT NULL,
  数据集       text NOT NULL,
  拉取源       text,
  更新函数     text,
  状态         text NOT NULL DEFAULT '排队',
  开始时间     timestamp without time zone,
  结束时间     timestamp without time zone,
  耗时秒       numeric,
  消息         text,
  错误         text,
  水位前最后日 date,
  水位后最后日 date,
  水位是否推进 boolean,
  PRIMARY KEY (运行编号, 数据源, 数据集)
);

CREATE INDEX IF NOT EXISTS 更新运行_开始时间_idx ON 行情增量.更新运行 (开始时间 DESC);
CREATE INDEX IF NOT EXISTS 更新运行明细_状态_idx ON 行情增量.更新运行明细 (状态);

CREATE OR REPLACE FUNCTION 行情增量.期望最后日(
  锚点 text,
  滞后天数 integer
) RETURNS date
LANGUAGE plpgsql
STABLE
AS $$
DECLARE
  d date;
BEGIN
  IF 锚点 IS NULL OR 锚点 = '无' THEN
    RETURN NULL;
  ELSIF 锚点 = '自然日' THEN
    RETURN CURRENT_DATE - COALESCE(滞后天数, 0);
  ELSIF 锚点 = '月初' THEN
    RETURN (date_trunc('month', CURRENT_DATE)::date - 1) - COALESCE(滞后天数, 0);
  ELSIF 锚点 = '季末' THEN
    RETURN (date_trunc('quarter', CURRENT_DATE)::date - 1) - COALESCE(滞后天数, 0);
  ELSIF 锚点 = '交易日' THEN
    SELECT MAX(c."日历日期")::date INTO d
    FROM 公共.日历文件 c
    WHERE c."日历日期" <= (CURRENT_DATE - COALESCE(滞后天数, 0))
      AND (
        c."开市情况"::text = '开市'
        OR c."开市情况"::text = '1'
      );
    IF d IS NULL THEN
      SELECT MAX(c."日历日期")::date INTO d
      FROM 公共.日历文件 c
      WHERE c."日历日期" <= (CURRENT_DATE - COALESCE(滞后天数, 0));
    END IF;
    RETURN d;
  ELSE
    RETURN NULL;
  END IF;
END;
$$;

CREATE OR REPLACE VIEW 行情增量.更新控制台 AS
SELECT
  r.数据源,
  r.数据集,
  r.对象类型,
  r.水位列,
  r.责任方,
  r.更新策略,
  r.拉取源,
  r.更新函数,
  r.可调度,
  r.批量标签,
  r.发布滞后天数,
  r.期望锚点,
  r.超时秒,
  r.并发组,
  r.是否启用,
  r.更新入口,
  w.最早日,
  w.最后交易日 AS 数据最后日,
  w.行数,
  w.表体积字节,
  pg_size_pretty(w.表体积字节) AS 体积,
  w.状态 AS 水位状态,
  w.最近成功时间,
  w.最近运行编号,
  w.更新时间 AS 水位刷新时间,
  行情增量.期望最后日(r.期望锚点, r.发布滞后天数) AS 期望最后日,
  CASE
    WHEN NOT COALESCE(r.是否启用, true) THEN '已停用'
    WHEN r.更新策略 = '冻结' THEN '冻结'
    WHEN NOT COALESCE(r.可调度, false) THEN '不可调度'
    WHEN 行情增量.期望最后日(r.期望锚点, r.发布滞后天数) IS NULL THEN '未知'
    WHEN w.最后交易日 IS NULL THEN '无数据'
    WHEN w.最后交易日 >= 行情增量.期望最后日(r.期望锚点, r.发布滞后天数) THEN '已对齐'
    ELSE '可更新'
  END AS 新鲜度,
  CASE
    WHEN w.最后交易日 IS NOT NULL
         AND 行情增量.期望最后日(r.期望锚点, r.发布滞后天数) IS NOT NULL
      THEN (行情增量.期望最后日(r.期望锚点, r.发布滞后天数) - w.最后交易日)
    ELSE NULL
  END AS 落后天数
FROM 行情增量.数据集登记 r
LEFT JOIN 行情增量.同步水位 w
  ON w.数据源 = r.数据源 AND w.数据集 = r.数据集;

COMMENT ON VIEW 行情增量.更新控制台 IS '方案B：库内水位 + 期望日 + 可调度；无源端探测';

"""市场收益构建 —— 用 `公共.日行情` 聚合出「市场收益文件」的增量段。

背景(TASK-004):
  `公共.市场收益` 视图读 `公共.市场收益文件`,但该表只有 CSMAR 档案段(≤ 档案分界日),
  分界日之后**没有任何写入者** ⇒ 视图在增量段为空。
  本模块按档案段的同一口径,从 `公共.日行情`(覆盖档案∪增量)聚合出每日每分段指标,
  写入 `公共.市场收益文件` 的**计算段**(列 `数据段`='计算')。

口径(与档案段对齐,已实测):
  · 分段编码见 `公共.市场类型字典`(1=沪A 2=沪B 4=深A 8=深B 16=创业板 32=科创板 64=北交所);
  · 只统计**正常交易**的股票:档案段 `trade_status = 0` 为正常、1 为停牌,
    增量段(抓取器写入)该列为 NULL,按正常交易处理;
  · volume/amount = 求和,n_firms = 家数,ret_ew = pct_chg 等权平均;
  · **已知近似**:档案段的 ret_ew 是"考虑现金红利再投资"口径,本构建器用 pct_chg 等权,
    实测差异 ≤5bp(2026-05-20 逐分段比对);
  · **流通/总市值加权**:用**前一交易日**市值作权重(实测复现度:7 个分段中 5 个 ≤2bp,
    科创板在该样例日差约 78bp,由中芯国际/华虹等大权重股驱动,原因未能完全反推,已在 --校验 中量化);
  · `*_div`(考虑现金红利再投资)列**不计算**,保持 NULL —— 本构建器不做分红调整。

用法:
    market-platform data 构建市场收益 [--since YYYY-MM-DD] [--until YYYY-MM-DD]
"""

from __future__ import annotations

from typing import Any

from market_platform.core.配置 import 数据库DSN, 日行情档案分界日

# 分段归类 SQL:与 公共.市场类型字典.证券代码前缀 对应(前缀命中顺序即此顺序)
# 用 left() 而非 LIKE 'xx%':psycopg 会把 % 当占位符,LIKE 需要转义且易错
分段表达式 = """
    CASE
      WHEN left(stock_code, 2) = '60'  THEN 1
      WHEN left(stock_code, 3) = '900' THEN 2
      WHEN left(stock_code, 2) = '00'  THEN 4
      WHEN left(stock_code, 3) = '200' THEN 8
      WHEN left(stock_code, 2) = '30'  THEN 16
      WHEN left(stock_code, 2) = '68'  THEN 32
      WHEN left(stock_code, 1) IN ('8', '4') OR left(stock_code, 3) = '920' THEN 64
    END
"""


def 构建市场收益(
    *,
    since: str | None = None,
    until: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """把 [since, until] 的分段指标写入 市场收益文件(数据段='计算')。

    默认区间:分区日 + 1 天 → 日行情最新日。
    幂等:按 (trade_date, market_type, 数据段='计算') upsert。
    """
    import psycopg

    分界日 = since or 日行情档案分界日()
    统计: dict[str, Any] = {"起": None, "止": None, "写入行": 0, "跳过": 0}

    with psycopg.connect(数据库DSN(), autocommit=False) as conn, conn.cursor() as cur:
        cur.execute('SELECT max(trade_date) FROM "公共"."日行情"')
        最新 = cur.fetchone()[0]
        止 = until or (str(最新) if 最新 else None)
        if not 止:
            统计["跳过"] = 1
            return 统计

        cur.execute(
            f"""
            WITH 今 AS (
              SELECT trade_date, stock_code, pct_chg, volume, amount, {分段表达式} AS market_type
              FROM "公共"."日行情"
              WHERE trade_date > %s::date AND trade_date <= %s::date
                -- 档案段 trade_status=0 表示正常交易、1 表示停牌;
                -- 增量段(抓取器写入)该列为 NULL ⇒ 视为正常交易
                AND (trade_status = 0 OR trade_status IS NULL)
            ),
            昨 AS (   -- 前一交易日市值:作为加权权重(避免同日权重的前视争议)
              SELECT trade_date, stock_code, float_mkt_cap, mkt_cap,
                     lead(trade_date) OVER (PARTITION BY stock_code ORDER BY trade_date) AS 次日
              FROM "公共"."日行情"
              WHERE trade_date <= %s::date AND trade_date > (%s::date - INTERVAL '30 days')
            )
            SELECT j.trade_date, j.market_type,
                   count(*) AS n_firms,
                   sum(j.volume) AS volume,
                   sum(j.amount) AS amount,
                   avg(j.pct_chg) AS ret_ew,
                   sum(j.pct_chg * y.float_mkt_cap) / NULLIF(sum(y.float_mkt_cap), 0) AS ret_float_vw,
                   sum(j.pct_chg * y.mkt_cap)      / NULLIF(sum(y.mkt_cap), 0)      AS ret_total_vw
            FROM 今 j
            LEFT JOIN 昨 y ON y.stock_code = j.stock_code AND y.次日 = j.trade_date
            WHERE j.market_type IS NOT NULL
            GROUP BY 1, 2
            ORDER BY j.trade_date, j.market_type
            """,
            (分界日, 止, 止, 分界日),
        )
        行列表 = cur.fetchall()
        统计["起"] = str(分界日)
        统计["止"] = str(止)

        if dry_run:
            统计["将写入行"] = len(行列表)
            conn.rollback()
            return 统计

        for 日期, 编码, 家数, 成交量, 成交额, 等权, 流通加权, 总市值加权 in 行列表:
            cur.execute(
                """
                INSERT INTO "公共"."市场收益文件"
                  (trade_date, market_type, volume, amount, ret_ew, ret_float_vw,
                   ret_total_vw, n_firms, 数据段)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, '计算')
                ON CONFLICT (trade_date, market_type, 数据段) DO UPDATE
                  SET volume = EXCLUDED.volume,
                      amount = EXCLUDED.amount,
                      ret_ew = EXCLUDED.ret_ew,
                      ret_float_vw = EXCLUDED.ret_float_vw,
                      ret_total_vw = EXCLUDED.ret_total_vw,
                      n_firms = EXCLUDED.n_firms
                RETURNING (xmax = 0) AS 新增
                """,
                (日期, 编码, 成交量, 成交额, 等权, 流通加权, 总市值加权, 家数),
            )
            行结果 = cur.fetchone()
            if 行结果 and 行结果[0]:
                统计["写入行"] += 1
            else:
                统计["重算行"] = 统计.get("重算行", 0) + 1
        conn.commit()
    return 统计


def 一致性校验(*, 样例日: str = "2026-05-20") -> dict[str, Any]:
    """把"计算段口径"与"档案段"在同一交易日上对比,证明口径一致。

    返回每个分段的 (档案家数, 计算家数, 成交量是否一致, 等权收益差 bp)。
    """
    import psycopg

    出: dict[str, Any] = {"样例日": 样例日, "分段": []}
    with psycopg.connect(数据库DSN(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            WITH 今 AS (
              SELECT trade_date, stock_code, pct_chg, volume, {分段表达式} AS market_type
              FROM "公共"."日行情"
              WHERE trade_date = %s::date AND (trade_status = 0 OR trade_status IS NULL)
            ),
            昨 AS (
              SELECT stock_code, float_mkt_cap, mkt_cap,
                     lead(trade_date) OVER (PARTITION BY stock_code ORDER BY trade_date) AS 次日
              FROM "公共"."日行情"
              WHERE trade_date <= %s::date AND trade_date > (%s::date - INTERVAL '30 days')
            ),
            b AS (
              SELECT j.market_type, count(*) AS 家数, sum(j.volume) AS 成交量, avg(j.pct_chg) AS 等权,
                     sum(j.pct_chg * y.float_mkt_cap) / NULLIF(sum(y.float_mkt_cap), 0) AS 流通加权,
                     sum(j.pct_chg * y.mkt_cap) / NULLIF(sum(y.mkt_cap), 0) AS 总市值加权
              FROM 今 j LEFT JOIN 昨 y ON y.stock_code = j.stock_code AND y.次日 = j.trade_date
              WHERE j.market_type IS NOT NULL
              GROUP BY 1
            )
            SELECT m.market_type, m.n_firms, b.家数, (m.volume = b.成交量),
                   m.ret_ew, b.等权, m.ret_float_vw, b.流通加权, m.ret_total_vw, b.总市值加权
            FROM "公共"."市场收益文件" m
            LEFT JOIN b ON b.market_type = m.market_type
            WHERE m.trade_date = %s::date AND m.数据段 = '档案'
            ORDER BY m.market_type
            """,
            (样例日, 样例日, 样例日, 样例日),
        )

        def _差bp(档案值, 计算值):
            if 档案值 is None or 计算值 is None:
                return None
            return round((float(计算值) - float(档案值)) * 10000, 2)

        for (编码, 档案家数, 计算家数, 成交量一致, 档案等权, 计算等权,
             档案流通, 计算流通, 档案总市值, 计算总市值) in cur.fetchall():
            出["分段"].append(
                {
                    "编码": 编码,
                    "档案家数": 档案家数,
                    "计算家数": 计算家数,
                    "成交量一致": bool(成交量一致),
                    "等权差bp": _差bp(档案等权, 计算等权),
                    "流通加权差bp": _差bp(档案流通, 计算流通),
                    "总市值加权差bp": _差bp(档案总市值, 计算总市值),
                }
            )
    return 出


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="构建市场收益增量段(由 公共.日行情 聚合)")
    parser.add_argument("--since", default=None, help=f"起始日(默认档案分界日 {日行情档案分界日()})")
    parser.add_argument("--until", default=None, help="结束日(默认 公共.日行情 最新日)")
    parser.add_argument("--dry-run", action="store_true", help="只统计将写入多少行")
    parser.add_argument("--校验", action="store_true", help="与档案段做同口径一致性校验")
    args = parser.parse_args(argv)

    if args.校验:
        结果 = 一致性校验()
        print(f"一致性校验(样例日 {结果['样例日']}):")
        for 段 in 结果["分段"]:
            print(
                f"  编码 {段['编码']:>2}: 家数 {段['档案家数']:>5}/{段['计算家数']:>5} "
                f"成交量一致={段['成交量一致']} "
                f"等权差={段['等权差bp']}bp 流通加权差={段['流通加权差bp']}bp "
                f"总市值差={段['总市值加权差bp']}bp"
            )
        return 0

    统计 = 构建市场收益(since=args.since, until=args.until, dry_run=args.dry_run)
    if args.dry_run:
        print(f"[dry-run] 区间 {统计['起']} → {统计['止']},将写入 {统计.get('将写入行', 0)} 行")
    else:
        print(f"区间 {统计['起']} → {统计['止']}:新增 {统计['写入行']} 行,"
              f"重算 {统计.get('重算行', 0)} 行")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

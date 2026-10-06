"""市场收益构建 —— 用 `公共.日行情` 聚合出「市场收益文件」的增量段。

背景(TASK-004):
  `公共.市场收益` 视图读 `公共.市场收益文件`,但该表只有 CSMAR 档案段(≤ 档案分界日),
  分界日之后**没有任何写入者** ⇒ 视图在增量段为空。
  本模块按档案段的同一口径,从 `公共.日行情`(覆盖档案∪增量)聚合出每日每分段指标,
  写入 `公共.市场收益文件` 的**计算段**(列 `数据段`='计算')。

口径(与档案段对齐,已实测):
  · 分段编码见 `公共.市场类型字典`(1=沪A 2=沪B 4=深A 8=深B 16=创业板 32=科创板 64=北交所);
    深市 B 股含 `201xxx`(如 201872),只写 `200xxx` 会少数一只;
  · 只统计**正常交易**的股票:档案段 `trade_status = 0` 为正常、1 为停牌,
    增量段(抓取器写入)该列为 NULL,按正常交易处理;
  · volume/amount = 求和,n_firms = **去重**家数,ret_ew = pct_chg 等权平均;
  · **已知近似**:档案段的等权列是"考虑现金红利再投资"口径,本构建器 ret_ew 用 pct_chg
    等权,实测差异 ≤5bp(2026-05-20 逐分段比对);
  · **权重口径**(2026-10-06 逐分段实测对齐,见 `权重列()`):权重取**前一交易日**市值,
    但市值列本身按本市场口径换算 ——
      流通市值:沪/深 B 股用 `float_b_mkt_cap`;科创板用 `float_a_mkt_cap`;
                其余用 `float_mkt_cap`;
      总市值  :沪深 B 股用 `收盘价 × total_shares`;科创板用 A 股总市值
                (`float_a_mkt_cap + (mkt_cap - float_mkt_cap)`);其余用 `mkt_cap`。
    原因:`float_mkt_cap`/`mkt_cap` 对含 H 股的 A 股公司(中芯国际 688981:总股本 80.1 亿
    含 H,A 股流通仅 20.0 亿)与 B 股公司会把权重放大数倍 —— 曾使科创板流通市值加权
    偏差 78bp,改用 A 股口径后降到 0.11bp;B 股市场同理(沪B 总市值差 32.76bp → 3.05bp)。
  · `*_div`(考虑现金红利再投资的日收益率)由 `close + 每股税前现金红利` 相对前收计算,
    分红取 `公共.分配文件` 当日除权息记录的 `派息比(每10股税前)/10`(送转不影响总收益)。

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
      WHEN left(stock_code, 3) IN ('200', '201') THEN 8
      WHEN left(stock_code, 2) = '30'  THEN 16
      WHEN left(stock_code, 2) = '68'  THEN 32
      WHEN left(stock_code, 1) IN ('8', '4') OR left(stock_code, 3) = '920' THEN 64
    END
"""


# 市值权重口径 —— 逐分段实测对齐 CSMAR 档案段(明细见模块 docstring「权重口径」)。
# 抽成函数是为了让「构建」与「一致性校验」用同一份定义,避免两处各自漂移。
#   流通市值:沪/深 B 股用 B 股流通市值;科创板用 A 股流通市值(剔除 H 股);其余用 float_mkt_cap
#   总市值  :B 股用 收盘价×总股本;B 股以外含 H 股的公司用 A 股总市值;其余用 mkt_cap
def 权重列(别名: str) -> str:
    return f"""
      CASE WHEN {别名}.market_type IN (2, 8) THEN {别名}.float_b_mkt_cap
           WHEN {别名}.market_type = 32    THEN {别名}.float_a_mkt_cap
           ELSE {别名}.float_mkt_cap END AS 流通市值,
      CASE WHEN {别名}.market_type IN (2, 8) THEN {别名}.前收 * {别名}.total_shares
           WHEN {别名}.market_type = 32    THEN {别名}.float_a_mkt_cap + ({别名}.mkt_cap - {别名}.float_mkt_cap)
           ELSE {别名}.mkt_cap END AS 总市值
    """


# 权重回退:2026-07-27 起抓取器写入的增量行没有市值列,用"最近已知股本 × 当日收盘价"重建。
# 股本口径与 权重列() 一一对应(A 股剔除 H 股、B 股用 B 股股本),只是把"当日前收×股本"
# 换成"当日前收 × 结转股本";口径来源会写进 市场收益文件.权重口径 供消费方甄别。
结转权重列 = """
  CASE WHEN y.market_type IN (2, 8) THEN y.前收 * z.total_shares
       ELSE y.前收 * (COALESCE(z.float_a_shares, z.float_shares)
                      + (z.total_shares - COALESCE(z.float_shares, 0))) END AS 结转总市值,
  CASE WHEN y.market_type IN (2, 8) THEN y.前收 * COALESCE(z.float_b_shares, z.float_shares)
       WHEN y.market_type = 32    THEN y.前收 * z.float_a_shares
       ELSE y.前收 * COALESCE(z.float_a_shares, z.float_shares) END AS 结转流通市值
"""


# 覆盖阈值:当日总家数 / 参照家数 低于该值即视为"覆盖不足"(计算段结果不可与档案段直接比较)
覆盖阈值 = 0.90


def _参照总家数(conn, 分界日: str) -> int | None:
    """参照家数 = 档案段最后 20 个交易日的日均总家数(即"应有覆盖")。"""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT round(avg(n)) FROM (
              SELECT trade_date, sum(n_firms) AS n   -- 一个交易日 7 个分段,合计才是全市场家数
              FROM "公共"."市场收益文件"
              WHERE 数据段 = '档案' AND trade_date <= %s::date
              GROUP BY trade_date ORDER BY trade_date DESC LIMIT 20
            ) t
            """,
            (分界日,),
        )
        行 = cur.fetchone()
    return int(行[0]) if 行 and 行[0] else None


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
              SELECT trade_date, stock_code, pct_chg, close, volume, amount,
                     {分段表达式} AS market_type
              FROM "公共"."日行情"
              WHERE trade_date > %s::date AND trade_date <= %s::date
                -- 档案段 trade_status=0 表示正常交易、1 表示停牌;
                -- 增量段(抓取器写入)该列为 NULL ⇒ 视为正常交易
                AND (trade_status = 0 OR trade_status IS NULL)
            ),
            昨 AS (   -- 前一交易日收盘/市值,并按分段口径换算成权重(市值作权重,收盘算分红口径)
              -- 窗口必须覆盖**整个聚合区间**:以 分界日 为锚点向前取 30 天,
              -- 否则区间前半段取不到"前一日"行,权重会整段为 NULL(2026-10-06 曾误锚到止日)。
              SELECT trade_date, stock_code, close AS 前收,
                     {分段表达式} AS market_type,
                     float_mkt_cap, float_a_mkt_cap, float_b_mkt_cap, mkt_cap,
                     total_shares, float_shares, float_a_shares, float_b_shares,
                     lead(trade_date) OVER (PARTITION BY stock_code ORDER BY trade_date) AS 次日
              FROM "公共"."日行情"
              WHERE trade_date <= %s::date AND trade_date > (%s::date - INTERVAL '30 days')
            ),
            结转 AS (  -- 最近一次有股本记录的日期(市值列缺失时回退用)
              SELECT DISTINCT ON (stock_code)
                     stock_code, trade_date AS 股本日,
                     total_shares, float_shares, float_a_shares, float_b_shares
              FROM "公共"."日行情"
              WHERE trade_date <= %s::date
                -- 任一股本列有值即可作为结转来源(深B 的 float_shares 为空、只有 float_b_shares)
                AND COALESCE(total_shares, float_shares, float_a_shares, float_b_shares) IS NOT NULL
              ORDER BY stock_code, trade_date DESC
            ),
            权 AS (   -- 权重:优先当日市值列,缺失时回退"结转股本 × 当日收盘"
              SELECT y.stock_code, y.前收, y.次日, y.market_type AS 段,
                     COALESCE(w.流通市值, k.结转流通市值) AS 流通市值,
                     COALESCE(w.总市值, k.结转总市值)     AS 总市值,
                     -- 注意:关联不上(整行 NULL)也算"权重需结转",否则 FILTER 会漏计
                     COALESCE(w.流通市值 IS NULL, TRUE) AS 权重结转
              FROM 昨 y
              LEFT JOIN 结转 z ON z.stock_code = y.stock_code
              LEFT JOIN LATERAL (SELECT {权重列("y")}) w ON TRUE
              LEFT JOIN LATERAL (SELECT {结转权重列}) k ON TRUE
            ),
            红 AS (   -- 除权息当日每股税前现金红利(每10股派息 / 10)
              -- 必须按 (股票, 除权日) 聚合后再关联:同一票在区间内可有多条除权息
              -- 记录,若只按代码关联会让 今⋈红 行放大,虚增 n_firms/volume/amount;
              -- 派息比在源数据里存在 'NaN' 字面量(见 schema/迁移/013),按 0 处理。
              SELECT trim("证券代码") AS stock_code,
                     "除权(息)日期" AS 除权日,
                     sum(CASE WHEN "派息比(每10股税前)" = 'NaN'::numeric THEN 0
                              ELSE COALESCE("派息比(每10股税前)", 0) END) / 10.0 AS 每股红利
              FROM "公共"."分配文件"
              WHERE "除权(息)日期" BETWEEN %s::date AND %s::date
              GROUP BY 1, 2
            ),
            日日 AS (   -- 当日全市场家数(用于覆盖度;必须按日统计,不能整段去重)
              SELECT trade_date, count(DISTINCT stock_code) AS 当日总家数 FROM 今 GROUP BY 1
            )
            SELECT j.trade_date, j.market_type,
                   count(DISTINCT j.stock_code) AS n_firms,
                   sum(j.volume) AS volume,
                   sum(j.amount) AS amount,
                   avg(j.pct_chg) AS ret_ew,
                   sum(j.pct_chg * w.流通市值) / NULLIF(sum(w.流通市值), 0) AS ret_float_vw,
                   sum(j.pct_chg * w.总市值)   / NULLIF(sum(w.总市值), 0)   AS ret_total_vw,
                   d.当日总家数,
                   -- 分红再投资口径:仅调整现金红利(送转不影响总收益)
                   avg((j.close + COALESCE(红.每股红利, 0)) / NULLIF(w.前收, 0) - 1) AS ret_ew_div,
                   sum(((j.close + COALESCE(红.每股红利, 0)) / NULLIF(w.前收, 0) - 1) * w.流通市值)
                     / NULLIF(sum(w.流通市值), 0) AS ret_float_vw_div,
                   sum(((j.close + COALESCE(红.每股红利, 0)) / NULLIF(w.前收, 0) - 1) * w.总市值)
                     / NULLIF(sum(w.总市值), 0) AS ret_total_vw_div,
                   -- 权重口径披露:全段都用当日市值 = 当日市值;有回退 = 股本结转(见迁移 014)
                   CASE WHEN count(*) FILTER (WHERE w.权重结转) = 0
                        THEN '当日市值' ELSE '股本结转' END AS 权重口径
            FROM 今 j
            LEFT JOIN 权 w ON w.stock_code = j.stock_code AND w.次日 = j.trade_date
            LEFT JOIN 红 ON 红.stock_code = j.stock_code AND 红.除权日 = j.trade_date
            JOIN 日日 d ON d.trade_date = j.trade_date
            WHERE j.market_type IS NOT NULL
            GROUP BY 1, 2, d.当日总家数
            ORDER BY j.trade_date, j.market_type
            """,
            # 今(分界,止) 昨(<=止, 窗口锚=分界) 结转(<=止) 红(分界,止)
            (分界日, 止, 止, 分界日, 止, 分界日, 止),
        )
        行列表 = cur.fetchall()
        统计["起"] = str(分界日)
        统计["止"] = str(止)

        if dry_run:
            统计["将写入行"] = len(行列表)
            conn.rollback()
            return 统计

        参照家数 = _参照总家数(conn, 分界日)
        统计["参照日总家数"] = 参照家数
        覆盖不足日: list[str] = []

        for (日期, 编码, 家数, 成交量, 成交额, 等权, 流通加权, 总市值加权,
             当日总家数, 等权div, 流通div, 总市值div, 口径) in 行列表:
            覆盖度 = (float(当日总家数) / 参照家数) if 参照家数 else None
            覆盖完整 = bool(覆盖度 is None or 覆盖度 >= 覆盖阈值)
            if not 覆盖完整 and str(日期) not in 覆盖不足日:
                覆盖不足日.append(str(日期))
            cur.execute(
                """
                INSERT INTO "公共"."市场收益文件"
                  (trade_date, market_type, volume, amount, ret_ew, ret_float_vw,
                   ret_total_vw, ret_ew_div, ret_float_vw_div, ret_total_vw_div,
                   n_firms, 数据段, 覆盖度, 覆盖完整, 权重口径)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, '计算', %s, %s, %s)
                ON CONFLICT (trade_date, market_type, 数据段) DO UPDATE
                  SET volume = EXCLUDED.volume,
                      amount = EXCLUDED.amount,
                      ret_ew = EXCLUDED.ret_ew,
                      ret_float_vw = EXCLUDED.ret_float_vw,
                      ret_total_vw = EXCLUDED.ret_total_vw,
                      ret_ew_div = EXCLUDED.ret_ew_div,
                      ret_float_vw_div = EXCLUDED.ret_float_vw_div,
                      ret_total_vw_div = EXCLUDED.ret_total_vw_div,
                      n_firms = EXCLUDED.n_firms,
                      覆盖度 = EXCLUDED.覆盖度,
                      覆盖完整 = EXCLUDED.覆盖完整,
                      权重口径 = EXCLUDED.权重口径
                RETURNING (xmax = 0) AS 新增
                """,
                (日期, 编码, 成交量, 成交额, 等权, 流通加权, 总市值加权,
                 等权div, 流通div, 总市值div, 家数, 覆盖度, 覆盖完整, 口径),
            )
            行结果 = cur.fetchone()
            if 行结果 and 行结果[0]:
                统计["写入行"] += 1
            else:
                统计["重算行"] = 统计.get("重算行", 0) + 1
        conn.commit()
        统计["覆盖不足日"] = 覆盖不足日
        if 覆盖不足日:
            import logging

            logging.getLogger(__name__).warning(
                "覆盖不足的交易日 %d 个(当日股票数 < 参照家数 %.0f%%),这些日期的计算段不可与档案段直接比较: %s",
                len(覆盖不足日), 覆盖阈值 * 100, ", ".join(覆盖不足日[:8]),
            )
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
              SELECT stock_code, {分段表达式} AS market_type, close AS 前收,
                     total_shares, float_shares, float_a_shares, float_b_shares,
                     mkt_cap, float_mkt_cap, float_a_mkt_cap, float_b_mkt_cap,
                     lead(trade_date) OVER (PARTITION BY stock_code ORDER BY trade_date) AS 次日
              FROM "公共"."日行情"
              WHERE trade_date <= %s::date AND trade_date > (%s::date - INTERVAL '30 days')
            ),
            结转 AS (
              SELECT DISTINCT ON (stock_code) stock_code, total_shares, float_shares,
                     float_a_shares, float_b_shares
              FROM "公共"."日行情"
              WHERE trade_date <= %s::date
                AND COALESCE(total_shares, float_shares, float_a_shares, float_b_shares) IS NOT NULL
              ORDER BY stock_code, trade_date DESC
            ),
            权 AS (   -- 与构建器同一口径(含"股本结转"回退),保证校验比的是同一套权重
              SELECT y.stock_code, y.次日,
                     COALESCE(w.流通市值, k.结转流通市值) AS 流通市值,
                     COALESCE(w.总市值, k.结转总市值) AS 总市值
              FROM 昨 y
              LEFT JOIN LATERAL (SELECT {权重列("y")}) w ON TRUE
              LEFT JOIN 结转 z ON z.stock_code = y.stock_code
              LEFT JOIN LATERAL (SELECT {结转权重列}) k ON TRUE
            ),
            b AS (
              SELECT j.market_type, count(distinct j.stock_code) AS 家数, sum(j.volume) AS 成交量, avg(j.pct_chg) AS 等权,
                     sum(j.pct_chg * w.流通市值) / NULLIF(sum(w.流通市值), 0) AS 流通加权,
                     sum(j.pct_chg * w.总市值) / NULLIF(sum(w.总市值), 0) AS 总市值加权
              FROM 今 j LEFT JOIN 权 w ON w.stock_code = j.stock_code AND w.次日 = j.trade_date
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
            (样例日, 样例日, 样例日, 样例日, 样例日),
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

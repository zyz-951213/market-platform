"""修复 `行为分析.日行情` 的口径与缺口(以 `公共.日行情` 为源)。

背景(2026-10-06 审计):
  `行为分析.日行情` 13.6M 行里:
    · **1271 万行(93%)的 `成交额` 等于 `成交量`** —— 上游把 akshare 腾讯接口
      `stock_zh_a_hist_tx` 的 `amount` 列(该列实际装的是**成交量,单位手**)既当了
      `volume` 又当了 `amount`(`采集/数据源.py::_normalize_daily` 的腾讯分支);
    · 成交量单位混用:1199 万行是"手"(= 公共层股数 /100)、154 万行是"股"。
  后果:凡是用成交额的行为特征(资金流向代理、VWAP、活跃度、模拟盘成本)都失真;
  `数据源.py` 里 `vwap = amount / volume` 也只有在"股 + 元"下才成立。

本脚本以 `公共.日行情`(股/元,自洽:额/量≈价格)为源,做两件事:
  1. **统一口径**:把两侧都存在的 (证券代码, 交易日) 行的 `成交量/成交额` 重写为
     公共层的值(股/元);
  2. **补残缺日**:把"应用层已有该标的、但缺该交易日"的行从公共层补上
     (只补应用层已收录的标的与已存在的交易日,不改变应用层的标的范围)。

幂等:重跑时口径已一致的行的更新影响为 0;缺失日已存在则 `ON CONFLICT DO NOTHING`。

用法:
    market-platform agents 修复日行情 [--since YYYY-MM-DD] [--until YYYY-MM-DD] [--dry-run]
"""

from __future__ import annotations

from typing import Any

# 每天一次事务,避免 1300 万行单事务(锁与 WAL 都会失控)
批天数 = 30


def 修复应用日行情(
    *,
    since: str | None = None,
    until: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    import psycopg

    from market_platform.core.配置 import 数据库DSN

    统计: dict[str, Any] = {"口径修复行": 0, "补齐缺失行": 0, "无源换算行": 0,
                            "批次数": 0, "起": None, "止": None}

    with psycopg.connect(数据库DSN(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute('SELECT min("交易日期"), max("交易日期") FROM 行为分析.日行情')
        最早, 最新 = cur.fetchone()
        if 最早 is None:
            return 统计
        起 = max(str(since), str(最早)) if since else str(最早)
        止 = min(str(until), str(最新)) if until else str(最新)
        统计["起"], 统计["止"] = 起, 止

        # 逐批(按交易日区间切)处理
        cur.execute(
            """
            SELECT DISTINCT "交易日期" FROM 行为分析.日行情
             WHERE "交易日期" BETWEEN %s AND %s ORDER BY 1
            """,
            (起, 止),
        )
        交易日 = [r[0] for r in cur.fetchall()]

    if not 交易日:
        return 统计

    for i in range(0, len(交易日), 批天数):
        批次 = 交易日[i:i + 批天数]
        甲, 乙 = str(批次[0]), str(批次[-1])
        统计["批次数"] += 1
        with psycopg.connect(数据库DSN(), autocommit=False) as conn, conn.cursor() as cur:
            if dry_run:
                cur.execute(
                    """
                    SELECT count(*) FROM 行为分析.日行情 a
                      JOIN 公共.日行情 p
                        ON p.stock_code = a.证券代码 AND p.trade_date = a.交易日期
                     WHERE a."交易日期" BETWEEN %s AND %s
                       AND (a."成交量" IS DISTINCT FROM p.volume OR a."成交额" IS DISTINCT FROM p.amount)
                    """,
                    (甲, 乙),
                )
                待修 = cur.fetchone()[0]
                cur.execute(
                    """
                    SELECT count(*) FROM 公共.日行情 p
                     WHERE p.trade_date BETWEEN %s AND %s
                       AND p.stock_code IN (SELECT DISTINCT "证券代码" FROM 行为分析.日行情)
                       AND NOT EXISTS (
                         SELECT 1 FROM 行为分析.日行情 a
                          WHERE a."证券代码" = p.stock_code AND a."交易日期" = p.trade_date)
                       AND p.trade_date IN (SELECT DISTINCT "交易日期" FROM 行为分析.日行情)
                    """,
                    (甲, 乙),
                )
                待补 = cur.fetchone()[0]
                统计["口径修复行"] += 待修
                统计["补齐缺失行"] += 待补
                cur.execute(
                    """
                    SELECT count(*) FROM 行为分析.日行情 a
                     WHERE a."交易日期" BETWEEN %s AND %s
                       AND a."成交额" = a."成交量" AND a."成交量" > 0
                       AND a."收盘价" IS NOT NULL AND a."收盘价" <> 1
                       AND NOT EXISTS (SELECT 1 FROM 公共.日行情 p
                                        WHERE p.stock_code = a."证券代码" AND p.trade_date = a."交易日期")
                    """,
                    (甲, 乙),
                )
                统计["无源换算行"] += cur.fetchone()[0]
                continue

            # ① 统一口径(股/元)
            cur.execute(
                """
                UPDATE 行为分析.日行情 a
                   SET "成交量" = p.volume, "成交额" = p.amount,
                       "开盘价" = p.open, "最高价" = p.high, "最低价" = p.low, "收盘价" = p.close
                  FROM 公共.日行情 p
                 WHERE p.stock_code = a."证券代码" AND p.trade_date = a."交易日期"
                   AND a."交易日期" BETWEEN %s AND %s
                   AND (a."成交量" IS DISTINCT FROM p.volume
                        OR a."成交额" IS DISTINCT FROM p.amount
                        OR a."收盘价" IS DISTINCT FROM p.close)
                """,
                (甲, 乙),
            )
            统计["口径修复行"] += cur.rowcount or 0

            # ② 补残缺日(仅应用层已收录的标的 + 已存在的交易日,不改标的范围)
            cur.execute(
                """
                INSERT INTO 行为分析.日行情
                  ("证券代码", "交易日期", "开盘价", "最高价", "最低价", "收盘价", "成交量", "成交额")
                SELECT p.stock_code, p.trade_date, p.open, p.high, p.low, p.close, p.volume, p.amount
                  FROM 公共.日行情 p
                 WHERE p.trade_date BETWEEN %s AND %s
                   AND p.stock_code IN (SELECT DISTINCT "证券代码" FROM 行为分析.日行情)
                   AND p.trade_date IN (SELECT DISTINCT "交易日期" FROM 行为分析.日行情)
                ON CONFLICT ("证券代码", "交易日期") DO NOTHING
                """,
                (甲, 乙),
            )
            统计["补齐缺失行"] += cur.rowcount or 0

            # ③ 无源可比行:公共层缺该日,只能靠"腾讯签名"判定 ——
            #    `成交额 = 成交量`(且成交量 > 0)只可能来自"把成交量当成交额"的旧写入路径,
            #    此时成交量是"手"口径 ⇒ ×100 转股,成交额按 股 × 收盘 估算(VWAP≈收盘)。
            #    幂等:换算后 `成交额 = 成交量 × 100 × 收盘` 只有收盘价恰为 1 时才仍相等,
            #    故显式排除收盘价 = 1 的行(那类行数值上本就无法区分,留给体检报告)。
            cur.execute(
                """
                UPDATE 行为分析.日行情 a
                   SET "成交量" = a."成交量" * 100,
                       "成交额" = a."成交量" * 100 * a."收盘价"
                 WHERE a."交易日期" BETWEEN %s AND %s
                   AND a."成交额" = a."成交量" AND a."成交量" > 0
                   AND a."收盘价" IS NOT NULL AND a."收盘价" <> 1
                   AND NOT EXISTS (
                     SELECT 1 FROM 公共.日行情 p
                      WHERE p.stock_code = a."证券代码" AND p.trade_date = a."交易日期")
                """,
                (甲, 乙),
            )
            统计["无源换算行"] += cur.rowcount or 0
            conn.commit()

    return 统计


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="以 公共.日行情 为源修复 行为分析.日行情 口径与缺口")
    parser.add_argument("--since", default=None)
    parser.add_argument("--until", default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    统计 = 修复应用日行情(since=args.since, until=args.until, dry_run=args.dry_run)
    print(
        f"区间 {统计['起']} → {统计['止']}:批次数 {统计['批次数']},"
        f"{'将' if args.dry_run else '已'}修复口径 {统计['口径修复行']} 行,"
        f"{'将' if args.dry_run else '已'}补齐缺失 {统计['补齐缺失行']} 行,"
        f"{'将' if args.dry_run else '已'}无源换算 {统计['无源换算行']} 行"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

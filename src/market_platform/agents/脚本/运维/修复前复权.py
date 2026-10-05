#!/usr/bin/env python3
"""修复前复权脏数据：多轮清洗全市场 → 残留异常票 sina 优先重拉 → 重分析。"""

from __future__ import annotations
from market_platform.agents.脚本.运维.安全护栏 import 加入安全参数, 破坏性操作护栏

import argparse
import logging
import time
from dataclasses import replace
from pathlib import Path

import pandas as pd

from market_platform.agents.settings import AppConfig
from market_platform.agents.分析.批处理.增量 import run_incremental_batch
from market_platform.agents.核心.路径.项目 import profile_path, repo_root
from market_platform.agents.存储 import data_access as store
from market_platform.agents.存储.数据库 import PostgresStore, load_database_settings
from market_platform.agents.存储.行情清洗 import daily_extreme_stats, sanitize_daily_ohlcv
from market_platform.agents.采集.批量拉取 import fetch_symbol_lite
from market_platform.agents.采集.股票池 import infer_market

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("repair_qfq")


def list_extreme_symbols(st: PostgresStore) -> pd.DataFrame:
    return st._read_sql(
        """
        WITH r AS (
          SELECT 证券代码 AS symbol, 交易日期 AS date, 收盘价 AS close,
                 收盘价 / NULLIF(LAG(收盘价) OVER (PARTITION BY 证券代码 ORDER BY 交易日期), 0) - 1 AS ret
          FROM 行为分析.日行情
        )
        SELECT symbol,
               COUNT(*) FILTER (WHERE ABS(ret) > 0.5)::int AS n50,
               COUNT(*) FILTER (WHERE ABS(ret) > 0.3)::int AS n30,
               MAX(ABS(ret)) AS max_abs_ret
        FROM r
        GROUP BY symbol
        HAVING COUNT(*) FILTER (WHERE ABS(ret) > 0.5) > 0
            OR COUNT(*) FILTER (WHERE ABS(ret) > 0.3) >= 10
        ORDER BY n50 DESC, n30 DESC
        """
    )


def resanitize_all(
    st: PostgresStore,
    symbols: list[str] | None = None,
    *,
    护栏=None,
) -> tuple[int, list[str]]:
    """对日线再跑加强清洗并整表替换。返回 (改写只数, 仍异常代码)。"""
    if symbols is None:
        syms = st._read_sql(
            "SELECT 证券代码 AS symbol FROM 公共.证券档案 ORDER BY 证券代码"
        )["symbol"].astype(str).str.zfill(6).tolist()
    else:
        syms = [str(s).zfill(6) for s in symbols]
    changed = 0
    still_bad: list[str] = []
    for i, sym in enumerate(syms, 1):
        raw = st._read_sql(
            """
            SELECT 交易日期 AS date, 开盘价 AS open, 最高价 AS high, 最低价 AS low,
                   收盘价 AS close, 成交量 AS volume, 成交额 AS amount, 证券代码 AS symbol
            FROM 行为分析.日行情 WHERE 证券代码=%s ORDER BY 交易日期
            """,
            (sym,),
        )
        if raw.empty:
            continue
        before = len(raw)
        before_stats = daily_extreme_stats(raw)
        clean = sanitize_daily_ohlcv(raw)
        after_stats = daily_extreme_stats(clean)
        if clean.empty:
            logger.warning("[%d/%d] %s sanitize emptied, keep original", i, len(syms), sym)
            still_bad.append(sym)
            continue
        if len(clean) != before or before_stats["n50"] or before_stats["n30"]:
            # 评审 D9:整表写回(replace=True)不可逆,必须经过 dry-run/确认护栏
            if 护栏 is not None and 护栏.拦截(
                动作=f"整表写回 行为分析.日行情(replace=True): {sym} 原 {before} 行 → {len(clean)} 行",
                影响="该标的全部日线被替换,后续重分析基于新数据",
            ):
                changed += 1
                continue
            store.save_daily_bars(sym, clean, replace=True)
            if len(clean) != before or before_stats["n50"] > after_stats["n50"]:
                changed += 1
                if i <= 30 or i % 200 == 0 or after_stats["n50"] or after_stats["n30"] >= 5:
                    logger.info(
                        "[%d/%d] %s %d→%d n50 %s→%s n30 %s→%s",
                        i,
                        len(syms),
                        sym,
                        before,
                        len(clean),
                        before_stats["n50"],
                        after_stats["n50"],
                        before_stats["n30"],
                        after_stats["n30"],
                    )
        if after_stats["n50"] > 0 or after_stats["n30"] >= 5:
            still_bad.append(sym)
        elif i % 500 == 0:
            logger.info("[%d/%d] resanitize progress changed=%d still_bad=%d", i, len(syms), changed, len(still_bad))
    return changed, still_bad


def delete_orphans(st: PostgresStore) -> dict[str, int]:
    conn = st._conn()
    counts: dict[str, int] = {}
    stmts = {
        "策略净值": """
            DELETE FROM 行为分析.策略净值 e
            WHERE NOT EXISTS (
                SELECT 1 FROM 行为分析.日行情 d
                WHERE d.证券代码=e.证券代码 AND d.交易日期=e.交易日期
            )
        """,
        "参与者活跃度": """
            DELETE FROM 行为分析.参与者活跃度 a
            WHERE NOT EXISTS (
                SELECT 1 FROM 行为分析.日行情 d
                WHERE d.证券代码=a.证券代码 AND d.交易日期=a.交易日期
            )
        """,
        "分钟日特征": """
            DELETE FROM 行为分析.分钟日特征 f
            WHERE NOT EXISTS (
                SELECT 1 FROM 行为分析.日行情 d
                WHERE d.证券代码=f.证券代码 AND d.交易日期=f.交易日期
            )
        """,
    }
    with conn.cursor() as cur:
        for name, sql in stmts.items():
            cur.execute(sql)
            counts[name] = int(cur.rowcount or 0)
        conn.commit()
    return counts


def refetch_sina_first(config: AppConfig, symbols: list[str]) -> tuple[int, int]:
    ok = err = 0
    # sina 往往前复权更稳，优先试用
    cfg = replace(config, data=replace(config.data, daily_sources=["sina", "eastmoney", "tencent"]))
    for i, sym in enumerate(symbols, 1):
        market = infer_market(sym)
        logger.info("[%d/%d] sina-first refetch %s", i, len(symbols), sym)
        row = fetch_symbol_lite(cfg, sym, market, build_features=True, skip_news=True)
        if row.status == "ok":
            ok += 1
            # 验收
            bars = store.load_daily_bars(sym)
            stt = daily_extreme_stats(bars)
            if stt["n50"] > 0:
                logger.warning("  still n50=%s after refetch %s source=%s", stt["n50"], sym, row.daily_source)
        else:
            err += 1
            logger.warning("refetch failed %s: %s", sym, row.error)
        time.sleep(0.25)
    return ok, err


def reanalyze(config: AppConfig, symbols: list[str], batch_size: int = 20) -> tuple[int, int]:
    st = PostgresStore(load_database_settings())
    # 护栏在下方创建后传入 resanitize_all(见调用处)
    names = st._read_sql(
        """
        SELECT 证券代码 AS symbol, 证券简称 AS name, 所属行业 AS sector,
               一级行业 AS sector_l1, 交易市场 AS market
        FROM 公共.证券档案 WHERE 证券代码 = ANY(%s)
        """,
        (symbols,),
    )
    names["symbol"] = names["symbol"].astype(str).str.zfill(6)
    by = {r.symbol: r for r in names.itertuples(index=False)}
    pending = []
    for sym in symbols:
        r = by.get(sym)
        market = str(getattr(r, "market", "") or infer_market(sym)) if r else infer_market(sym)
        name = str(getattr(r, "name", "") or "") if r else ""
        sector = str(getattr(r, "sector", "") or getattr(r, "sector_l1", "") or "") if r else ""
        pending.append((sym, market, name, sector))
    total_ok = total_err = 0
    for i in range(0, len(pending), batch_size):
        batch = pending[i : i + batch_size]
        logger.info("Analyze batch %d-%d / %d", i + 1, i + len(batch), len(pending))
        df = run_incremental_batch(config, batch, optimize=True)
        total_ok += int((df["status"] == "ok").sum()) if not df.empty else 0
        total_err += int((df["status"] == "error").sum()) if not df.empty else 0
        time.sleep(0.3)
    return total_ok, total_err


def summarize(st: PostgresStore) -> None:
    agg = st._read_sql(
        """
        WITH r AS (
          SELECT 证券代码 AS symbol,
                 收盘价 / NULLIF(LAG(收盘价) OVER (PARTITION BY 证券代码 ORDER BY 交易日期), 0) - 1 AS ret
          FROM 行为分析.日行情
        ),
        per AS (
          SELECT symbol,
                 COUNT(*) FILTER (WHERE ABS(ret) > 0.5) AS n50,
                 COUNT(*) FILTER (WHERE ABS(ret) > 0.3) AS n30
          FROM r GROUP BY symbol
        )
        SELECT COUNT(*) n_symbols,
               COUNT(*) FILTER (WHERE n50 > 0) syms_gt50,
               COALESCE(SUM(n50),0) days_gt50,
               COUNT(*) FILTER (WHERE n30 >= 10) syms_many_gt30
        FROM per
        """
    )
    logger.info("Market daily check: %s", agg.to_dict(orient="records")[0])
    abs50 = st._read_sql(
        """
        SELECT COUNT(*) n FROM 行为分析.标的摘要
        WHERE 状态='ok' AND (ABS(策略收益率)>50 OR ABS(基准收益率)>50)
        """
    )
    logger.info("Summary |return|>50 count: %s", int(abs50.iloc[0]["n"]))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=profile_path("全市场分析.yaml"))
    parser.add_argument("--skip-resanitize", action="store_true")
    parser.add_argument("--skip-refetch", action="store_true")
    parser.add_argument("--skip-reanalyze", action="store_true")
    parser.add_argument("--only-extreme", action="store_true", default=True,
                        help="默认只处理仍有极端涨跌的标的（更快且合理）")
    parser.add_argument("--all-symbols", action="store_true", help="全市场日线再清洗")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--batch-size", type=int, default=20)
    加入安全参数(parser)   # 评审 D9:破坏性操作必须可 dry-run 且需显式确认
    args = parser.parse_args()

    护栏 = 破坏性操作护栏(脚本名="修复前复权", dry_run=args.dry_run, confirmed=args.yes)
    st = PostgresStore(load_database_settings())
    config = AppConfig.from_yaml(args.config)
    config.data.start_date = config.data.resolved_start_date()

    extreme = list_extreme_symbols(st)
    logger.info("Pre-check extreme symbols: %d", len(extreme))
    if args.limit:
        extreme = extreme.head(args.limit)

    touch_syms: set[str] = set(extreme["symbol"].astype(str).str.zfill(6))

    if not args.skip_resanitize:
        if args.all_symbols:
            changed, still = resanitize_all(st, None, 护栏=护栏)
            touch_syms |= set(still)
        else:
            changed, still = resanitize_all(st, sorted(touch_syms), 护栏=护栏)
        logger.info("Resanitize done: changed=%d still_bad=%d", changed, len(still))
        touch_syms |= set(still)
    else:
        still = sorted(touch_syms)

    # 重扫极端名单（清洗后）
    extreme2 = list_extreme_symbols(st)
    refetch_list = sorted(set(extreme2["symbol"].astype(str).str.zfill(6).tolist()) | set(still))
    if args.limit:
        refetch_list = refetch_list[: args.limit]
    logger.info("Need sina-first refetch: %d", len(refetch_list))

    if not args.skip_refetch and refetch_list:
        ok, err = refetch_sina_first(config, refetch_list)
        logger.info("Refetch done ok=%d err=%d", ok, err)
        touch_syms |= set(refetch_list)

    # 对齐孤儿
    counts = delete_orphans(st)
    logger.info("Orphan cleanup: %s", counts)

    # 仍异常或本次动过的都重分析
    extreme3 = list_extreme_symbols(st)
    analyze_list = sorted(touch_syms | set(extreme3["symbol"].astype(str).str.zfill(6)))
    if args.limit:
        analyze_list = analyze_list[: args.limit]
    logger.info("Reanalyze candidates: %d", len(analyze_list))

    if not args.skip_reanalyze and analyze_list:
        ok, err = reanalyze(config, analyze_list, batch_size=args.batch_size)
        logger.info("Reanalyze finished ok=%d err=%d", ok, err)
        counts = delete_orphans(st)
        logger.info("Post-analyze orphan cleanup: %s", counts)

    summarize(st)
    out = repo_root() / "logs" / "qfq_extreme_after.csv"
    extreme_after = list_extreme_symbols(st)
    names = st._read_sql(
        "SELECT 证券代码 AS symbol, 证券简称 AS name FROM 公共.证券档案"
    )
    names["symbol"] = names["symbol"].astype(str).str.zfill(6)
    extreme_after["symbol"] = extreme_after["symbol"].astype(str).str.zfill(6)
    extreme_after.merge(names, on="symbol", how="left").to_csv(out, index=False)
    logger.info("Wrote %s (%d rows)", out, len(extreme_after))


if __name__ == "__main__":
    main()

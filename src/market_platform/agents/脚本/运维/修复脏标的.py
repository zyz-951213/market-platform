#!/usr/bin/env python3
"""清理净值/状态/特征中无日线对齐的孤儿行，并对问题标的重拉日线 + 重分析。"""

from __future__ import annotations

import argparse
import logging
import time
from pathlib import Path

import pandas as pd

from market_platform.agents.settings import AppConfig
from market_platform.agents.分析.批处理.增量 import run_incremental_batch
from market_platform.agents.核心.路径.项目 import profile_path, repo_root
from market_platform.agents.存储.数据库 import PostgresStore, load_database_settings
from market_platform.agents.采集.股票池 import infer_market
from market_platform.agents.采集.批量拉取 import fetch_symbol_lite

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("repair_dirty_data")


def delete_orphans(st: PostgresStore) -> dict[str, int]:
    """删除策略净值 / 窄表时序中无对应日行情的行。"""
    conn = st._conn()
    counts: dict[str, int] = {}
    stmts = {
        "策略净值": """
            DELETE FROM 行为分析.策略净值 e
            WHERE NOT EXISTS (
                SELECT 1 FROM 行为分析.日行情 d
                WHERE d.证券代码 = e.证券代码 AND d.交易日期 = e.交易日期
            )
        """,
        "参与者活跃度": """
            DELETE FROM 行为分析.参与者活跃度 a
            WHERE NOT EXISTS (
                SELECT 1 FROM 行为分析.日行情 d
                WHERE d.证券代码 = a.证券代码 AND d.交易日期 = a.交易日期
            )
        """,
        "分钟日特征": """
            DELETE FROM 行为分析.分钟日特征 f
            WHERE NOT EXISTS (
                SELECT 1 FROM 行为分析.日行情 d
                WHERE d.证券代码 = f.证券代码 AND d.交易日期 = f.交易日期
            )
        """,
    }
    with conn.cursor() as cur:
        for name, sql in stmts.items():
            logger.info("Deleting orphans from %s ...", name)
            cur.execute(sql)
            counts[name] = int(cur.rowcount or 0)
            logger.info("  deleted %d rows from %s", counts[name], name)
        conn.commit()
    return counts


def load_problem_symbols(report: Path) -> pd.DataFrame:
    if report.exists():
        df = pd.read_csv(report)
        df["symbol"] = df["symbol"].astype(str).str.zfill(6)
        return df
    # fallback: scan DB
    st = PostgresStore(load_database_settings())
    return st._read_sql(
        """
        SELECT DISTINCT e.证券代码 AS symbol
        FROM 行为分析.策略净值 e
        LEFT JOIN 行为分析.日行情 d
          ON d.证券代码 = e.证券代码 AND d.交易日期 = e.交易日期
        WHERE d.证券代码 IS NULL
        """
    )


def resanitize_existing_daily(st: PostgresStore, symbols: list[str]) -> int:
    """对已有日线再跑一遍清洗（剔异常跳价），整表写回。"""
    from market_platform.agents.存储 import data_access as store

    fixed = 0
    for i, sym in enumerate(symbols, 1):
        # 直接读原始，避免 load 时已过滤导致无法删脏点
        raw = st.load_daily_bars(sym)
        if raw.empty:
            continue
        before = len(raw)
        store.save_daily_bars(sym, raw, replace=True)
        after = len(st.load_daily_bars(sym))
        if after != before:
            fixed += 1
            logger.info("[%d/%d] resanitize %s: %d → %d", i, len(symbols), sym, before, after)
        elif i % 100 == 0:
            logger.info("[%d/%d] resanitize progress", i, len(symbols))
    return fixed


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, default=repo_root() / "logs" / "bad_symbols_report.csv")
    parser.add_argument("--config", type=Path, default=profile_path("全市场分析.yaml"))
    parser.add_argument("--skip-delete", action="store_true")
    parser.add_argument("--skip-refetch", action="store_true")
    parser.add_argument("--skip-reanalyze", action="store_true")
    parser.add_argument("--refetch-extreme-only", action="store_true", default=True)
    parser.add_argument("--batch-size", type=int, default=20)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    st = PostgresStore(load_database_settings())
    if not args.skip_delete:
        counts = delete_orphans(st)
        logger.info("Orphan cleanup done: %s", counts)

    report = load_problem_symbols(args.report)
    if report.empty:
        logger.info("No problem symbols in report")
        return
    if args.limit:
        report = report.head(args.limit)

    symbols = sorted(report["symbol"].astype(str).str.zfill(6).unique().tolist())
    logger.info("Problem symbols: %d", len(symbols))

    # 先对问题标的日线再清洗一遍（去掉 |涨跌|>50%）
    resanitize_existing_daily(st, symbols)

    config = AppConfig.from_yaml(args.config)
    config.data.start_date = config.data.resolved_start_date()

    refetch_syms = symbols
    if "n50" in report.columns:
        extreme = report[report["n50"].fillna(0).astype(int) >= 5]["symbol"].astype(str).str.zfill(6)
        refetch_syms = sorted(extreme.unique().tolist()) if args.refetch_extreme_only else symbols
        # 始终包含 002010 等有孤儿+极端的
        must = report[
            (report.get("orphan_n", pd.Series(dtype=int)).fillna(0).astype(int) > 0)
            & (report.get("n50", pd.Series(dtype=int)).fillna(0).astype(int) >= 5)
        ]["symbol"].astype(str).str.zfill(6)
        refetch_syms = sorted(set(refetch_syms) | set(must.tolist()))

    if not args.skip_refetch and refetch_syms:
        logger.info("Refetching %d symbols with extreme daily moves...", len(refetch_syms))
        ok = err = 0
        for i, sym in enumerate(refetch_syms, 1):
            market = infer_market(sym)
            logger.info("[%d/%d] refetch %s", i, len(refetch_syms), sym)
            row = fetch_symbol_lite(config, sym, market, build_features=True, skip_news=True)
            if row.status == "ok":
                ok += 1
            else:
                err += 1
                logger.warning("refetch failed %s: %s", sym, row.error)
            time.sleep(0.3)
        logger.info("Refetch done: ok=%d err=%d", ok, err)

    if not args.skip_reanalyze:
        # 构建 pending 元组
        names = st._read_sql(
            """
            SELECT 证券代码 AS symbol, 证券简称 AS name, 所属行业 AS sector,
                   一级行业 AS sector_l1, 交易市场 AS market
            FROM 公共.证券档案 WHERE 证券代码 = ANY(%s)
            """,
            (symbols,),
        )
        names["symbol"] = names["symbol"].astype(str).str.zfill(6)
        by_sym = {r.symbol: r for r in names.itertuples(index=False)}
        pending: list[tuple[str, str, str, str]] = []
        for sym in symbols:
            r = by_sym.get(sym)
            market = str(getattr(r, "market", "") or infer_market(sym)) if r else infer_market(sym)
            name = str(getattr(r, "name", "") or "") if r else ""
            sector = str(getattr(r, "sector", "") or getattr(r, "sector_l1", "") or "") if r else ""
            pending.append((sym, market, name, sector))

        logger.info("Reanalyzing %d symbols...", len(pending))
        total_ok = total_err = 0
        for i in range(0, len(pending), args.batch_size):
            batch = pending[i : i + args.batch_size]
            logger.info("Analyze batch %d-%d / %d", i + 1, i + len(batch), len(pending))
            df = run_incremental_batch(config, batch, optimize=True)
            ok = int((df["status"] == "ok").sum()) if not df.empty else 0
            err = int((df["status"] == "error").sum()) if not df.empty else 0
            total_ok += ok
            total_err += err
            time.sleep(0.5)
        logger.info("Reanalyze finished: ok=%d error=%d", total_ok, total_err)

    # verify 002010
    orphan = st._read_sql(
        """
        SELECT COUNT(*) AS n FROM 行为分析.策略净值 e
        LEFT JOIN 行为分析.日行情 d
          ON d.证券代码=e.证券代码 AND d.交易日期=e.交易日期
        WHERE e.证券代码='002010' AND d.证券代码 IS NULL
        """
    )
    neg = st._read_sql(
        """
        SELECT COUNT(*) AS n FROM 行为分析.日行情
        WHERE 证券代码='002010' AND (开盘价 < 0 OR 收盘价 < 0)
        """
    )
    logger.info("002010 verify: equity orphans=%s neg_px=%s", int(orphan.iloc[0]["n"]), int(neg.iloc[0]["n"]))


if __name__ == "__main__":
    main()

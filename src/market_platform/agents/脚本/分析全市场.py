#!/usr/bin/env python3
"""全市场增量分析 — 对已下载完成的股票边下边分析。"""

from __future__ import annotations

import argparse
import logging
import time
from pathlib import Path

from market_platform.agents.settings import AppConfig
from market_platform.agents.分析.批处理.增量 import analyze_status, discover_pending, run_incremental_batch
from market_platform.agents.核心.工具.读写 import ensure_dir
from market_platform.agents.核心.路径.项目 import profile_path, project_root
from market_platform.agents.采集.行业.行业映射 import enrich_universe_with_sector, prepare_sector_map

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("analyze_universe")

DEFAULT_CONFIG = profile_path("全市场分析.yaml")


def _chunk(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def run_batches(
    config: AppConfig,
    pending: list[tuple[str, str, str, str]],
    *,
    batch_size: int,
    optimize: bool,
) -> tuple[int, int]:
    total_ok = 0
    total_err = 0
    batches = list(_chunk(pending, batch_size))
    for i, batch in enumerate(batches, start=1):
        logger.info("Batch %d/%d — analyzing %d symbols...", i, len(batches), len(batch))
        df = run_incremental_batch(config, batch, optimize=optimize)
        ok = int((df["status"] == "ok").sum()) if not df.empty else 0
        err = int((df["status"] == "error").sum()) if not df.empty else 0
        total_ok += ok
        total_err += err
        logger.info(
            "Batch %d done: ok=%d error=%d | cumulative ok=%d err=%d",
            i,
            ok,
            err,
            total_ok,
            total_err,
        )
        time.sleep(1)
    return total_ok, total_err


def main() -> None:
    parser = argparse.ArgumentParser(description="Incremental universe analysis from PostgreSQL cache")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--batch-size", type=int, default=20)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--interval", type=int, default=60)
    parser.add_argument("--no-optimize", action="store_true")
    parser.add_argument("--prepare-sectors", action="store_true")
    parser.add_argument(
        "--reanalyze",
        action="store_true",
        help="强制重跑已分析标的（应用新仓位/状态逻辑）",
    )
    args = parser.parse_args()

    ensure_dir(project_root() / "logs")
    config = AppConfig.from_yaml(args.config)
    config.data.start_date = config.data.resolved_start_date()

    if args.prepare_sectors:
        df = prepare_sector_map(config.data.root, config.output.root, refresh=True)
        enrich_universe_with_sector(config.data.root)
        print(f"[sectors] saved {len(df)} symbols -> {config.data.root / 'sector_map.csv'}")
        return

    if not (config.data.root / "sector_map.csv").exists():
        logger.info("Building sector map...")
        prepare_sector_map(config.data.root, config.output.root, refresh=False)
        enrich_universe_with_sector(config.data.root)

    if args.status:
        st = analyze_status(config.data.root, config.output.root)
        print(
            f"数据就绪: {st['data_ready']}/{st['universe_total']} | "
            f"已分析: {st['analyzed']} | 待分析: {st['pending']} | 失败: {st['analyze_errors']}"
        )
        return

    optimize = not args.no_optimize

    if args.reanalyze:
        pending = discover_pending(
            config.data.root,
            config.output.root,
            skip_analyzed=False,
            limit=args.limit,
        )
        logger.info("Reanalyze queue: %d symbols (optimize=%s)", len(pending), optimize)
        if not pending:
            logger.info("Nothing to reanalyze")
            return
        ok, err = run_batches(config, pending, batch_size=args.batch_size, optimize=optimize)
        logger.info("Reanalyze finished: ok=%d error=%d", ok, err)
        return

    processed = 0
    while True:
        batch_limit = args.batch_size
        if args.limit is not None:
            remaining = args.limit - processed
            if remaining <= 0:
                break
            batch_limit = min(batch_limit, remaining)

        pending = discover_pending(
            config.data.root,
            config.output.root,
            skip_analyzed=True,
            limit=batch_limit,
        )
        if not pending:
            if not args.loop:
                logger.info("No pending symbols to analyze")
                break
            st = analyze_status(config.data.root, config.output.root)
            logger.info(
                "Waiting %ds — data_ready=%d analyzed=%d pending=%d",
                args.interval,
                st["data_ready"],
                st["analyzed"],
                st["pending"],
            )
            time.sleep(args.interval)
            continue

        logger.info("Analyzing %d symbols...", len(pending))
        df = run_incremental_batch(config, pending, optimize=optimize)
        ok = int((df["status"] == "ok").sum()) if not df.empty else 0
        err = int((df["status"] == "error").sum()) if not df.empty else 0
        processed += len(pending)
        logger.info("Batch done: ok=%d error=%d processed=%d", ok, err, processed)
        if not args.loop:
            break
        time.sleep(2)


if __name__ == "__main__":
    main()

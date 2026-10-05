#!/usr/bin/env python3
"""全市场数据批量拉取。"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from market_platform.agents.settings import AppConfig
from market_platform.agents.核心.路径.项目 import profile_path
from market_platform.agents.采集.批量拉取 import prepare_universe, run_universe_fetch

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("fetch_universe")

DEFAULT_CONFIG = profile_path("全市场拉取.yaml")


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch market data for A-share universe")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--prepare-list", action="store_true")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--no-features", action="store_true")
    parser.add_argument("--include-st", action="store_true")
    parser.add_argument("--include-bj", action="store_true")
    parser.add_argument("--refresh-list", action="store_true")
    args = parser.parse_args()

    config = AppConfig.from_yaml(args.config)
    boards = ("sh", "sz", "bj") if args.include_bj else ("sh", "sz")

    universe = prepare_universe(
        config.data.root,
        refresh_list=args.refresh_list or args.prepare_list,
        exclude_st=not args.include_st,
        boards=boards,
    )
    logger.info("Universe size: %d symbols", len(universe))

    if args.prepare_list:
        print(f"[universe] saved {len(universe)} symbols -> {config.data.root / 'universe.csv'}")
        return

    if not args.all and args.limit is None:
        parser.error("Specify --all or --limit N")

    config.data.start_date = config.data.resolved_start_date()
    summary = run_universe_fetch(
        config,
        universe,
        build_features=not args.no_features,
        skip_existing=not args.refresh,
        limit=args.limit,
        offset=args.offset,
    )

    ok = int((summary["status"] == "ok").sum())
    skip = int((summary["status"] == "skipped").sum())
    err = int((summary["status"] == "error").sum())
    print(f"[fetch] done ok={ok} skipped={skip} error={err}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""后台拉取行业映射，东财不可用时自动回退 baostock。"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from market_platform.agents.settings import AppConfig
from market_platform.agents.核心.路径.项目 import profile_path
from market_platform.agents.采集.行业.行业映射 import build_sector_map, load_sector_map, save_sector_map

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("fetch_sector_map")

DEFAULT_CONFIG = profile_path("全市场拉取.yaml")


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch and save sector_map.csv")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--baostock", action="store_true")
    args = parser.parse_args()

    config = AppConfig.from_yaml(args.config)
    df = build_sector_map(config.data.root, config.output.root, prefer_baostock=args.baostock)
    save_sector_map(df, config.data.root)
    loaded = load_sector_map(config.data.root)
    uncategorized = (loaded["sector"] == "未分类").sum()
    logger.info("Sector map complete: %d symbols (%d 未分类)", len(loaded), uncategorized)
    print(f"[sector_map] saved {len(loaded)} symbols, 未分类={uncategorized}")


if __name__ == "__main__":
    main()

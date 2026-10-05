#!/usr/bin/env python3
"""从东财 F10 拉取中信一/二/三级行业，写入 sector_hierarchy.csv。"""

from __future__ import annotations

import argparse
import logging
from market_platform.agents.核心.路径.项目 import data_dir


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", type=Path, default=None)
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--workers", type=int, default=24)
    args = parser.parse_args()
    root = args.data_root or data_dir()
    df = build_citic_hierarchy_df(root, refresh=args.refresh, max_workers=args.workers)
    print(f"L1={df['sector_l1'].nunique()} L2={df['sector_l2'].nunique()} L3={df['sector_l3'].nunique()} symbols={len(df)}")


if __name__ == "__main__":
    main()

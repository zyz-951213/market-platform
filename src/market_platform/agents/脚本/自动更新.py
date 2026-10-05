#!/usr/bin/env python3
"""自动数据更新：检测过期行情 → 增量拉取 → 触发分析。"""

from __future__ import annotations

import argparse
import logging
import time
from pathlib import Path

from market_platform.agents.settings import AppConfig
from market_platform.agents.核心.任务.运行器 import run_auto_update, update_status
from market_platform.agents.核心.工具.读写 import ensure_dir
from market_platform.agents.核心.路径.项目 import profile_path, project_root

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("auto_update")

DEFAULT_CONFIG = profile_path("自动更新.yaml")


def _load_auto_section(path: Path) -> dict:
    import yaml

    if not path.exists():
        return {}
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    section = raw.get("auto_update", {})
    return section if isinstance(section, dict) else {}


def run_once(args: argparse.Namespace) -> int:
    config = AppConfig.from_yaml(args.config)
    auto = _load_auto_section(args.config)
    try:
        result = run_auto_update(
            config,
            fetch_limit=args.fetch_limit or int(auto.get("fetch_limit", 50)),
            analyze_limit=args.analyze_limit or int(auto.get("analyze_limit", 50)),
            refresh_universe_list=args.refresh_list or bool(auto.get("refresh_universe_list", False)),
            refresh_market_data=not args.no_market_refresh,
            build_features=not args.no_features,
            optimize=not args.no_optimize,
        )
    except RuntimeError as exc:
        logger.warning("%s", exc)
        return 2
    print(
        f"[auto-update] fetch {result.fetch_ok}/{result.fetch_attempted} | "
        f"analyze {result.analyze_ok}/{result.analyze_attempted} | "
        f"stale {result.stale_before} | {result.duration_seconds:.1f}s"
    )
    return 0 if result.fetch_error == 0 and result.analyze_error == 0 else 1


def main() -> None:
    parser = argparse.ArgumentParser(description="Automatic data fetch and incremental analysis")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--fetch-limit", type=int, default=None)
    parser.add_argument("--analyze-limit", type=int, default=None)
    parser.add_argument("--refresh-list", action="store_true")
    parser.add_argument("--no-market-refresh", action="store_true")
    parser.add_argument("--no-features", action="store_true")
    parser.add_argument("--no-optimize", action="store_true")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--interval", type=int, default=None)
    args = parser.parse_args()

    ensure_dir(project_root() / "logs")
    auto = _load_auto_section(args.config)

    if args.status:
        status = update_status(AppConfig.from_yaml(args.config), rescan=True)
        for key, value in status.items():
            print(f"{key}: {value}")
        return

    interval = args.interval or int(auto.get("loop_interval_seconds", 3600))
    if not args.loop:
        raise SystemExit(run_once(args))

    logger.info("Loop mode interval=%ds", interval)
    while True:
        code = run_once(args)
        if code == 2:
            logger.info("Skipped run because another update is active")
        time.sleep(interval)


if __name__ == "__main__":
    main()

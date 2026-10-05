"""Daily auto-update scheduler — data + incremental backtest + paper trading."""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime

try:
    from dotenv import load_dotenv
    from market_platform.predictor.核心.路径.项目 import repo_root

    load_dotenv(repo_root() / ".env")
except ImportError:
    pass

from loguru import logger

from market_platform.predictor.核心.工具 import setup_logging
from market_platform.predictor.分析.自动更新 import run_daily_update
from market_platform.predictor.配置 import get_config


def _is_trading_day() -> bool:
    return datetime.now().weekday() < 5


def run_once(*, force: bool = False) -> int:
    cfg = get_config().get("auto_update", {})
    if not cfg.get("enabled", True):
        logger.info("自动更新未启用，跳过")
        return 0
    if not force and not _is_trading_day():
        logger.info("非交易日，跳过")
        return 0
    result = run_daily_update(force=force)
    logger.info("自动更新结果: status={} backtest={}", result.get("status"), result.get("backtest"))
    return 0 if result.get("status") in {"ok", "skipped", "disabled"} else 1


def run_daemon() -> None:
    cfg = get_config().get("auto_update", {})
    schedule_time = str(cfg.get("schedule_time", "16:45"))
    hour, minute = (int(x) for x in schedule_time.split(":")[:2])
    logger.info("自动更新守护进程: 每日 {:02d}:{:02d} 执行", hour, minute)
    last_run_date = None
    while True:
        now = datetime.now()
        if (
            now.weekday() < 5
            and now.hour == hour
            and now.minute == minute
            and last_run_date != now.date()
        ):
            try:
                run_once()
            except Exception as exc:
                logger.exception("自动更新失败: {}", exc)
            last_run_date = now.date()
        time.sleep(30)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="收盘后自动更新（数据+增量回测+模拟实盘）")
    parser.add_argument("--once", action="store_true", help="立即执行")
    parser.add_argument("--force", action="store_true", help="忽略交易日检查")
    parser.add_argument("--daemon", action="store_true", help="内嵌定时守护")
    args = parser.parse_args(argv)
    setup_logging()
    if args.daemon:
        run_daemon()
    elif args.once:
        sys.exit(run_once(force=args.force))
    else:
        parser.print_help()
        sys.exit(2)


if __name__ == "__main__":
    main()

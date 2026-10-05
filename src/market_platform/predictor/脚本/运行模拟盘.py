"""模拟实盘定时任务 — 收盘后自动拉数、研判、调仓"""

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
from market_platform.predictor.分析.模拟盘.引擎 import PaperTradingEngine
from market_platform.predictor.配置 import get_config
from market_platform.predictor.接口.模拟盘 import run_paper_trading_cycle


def _is_trading_day() -> bool:
    """简单判断：周一到周五视为交易日（节假日需手动跳过）。"""
    return datetime.now().weekday() < 5


def run_once(*, force: bool = False) -> int:
    cfg = get_config().get("paper_trading", {})
    if not cfg.get("enabled", True):
        logger.info("模拟实盘未启用，跳过")
        return 0

    if not force and not _is_trading_day():
        logger.info("非交易日，跳过")
        return 0

    from market_platform.predictor.分析.推断.集合元模型 import list_member_providers

    providers = list_member_providers()
    exit_code = 0
    for i, prov in enumerate(providers):
        state = PaperTradingEngine(provider=prov).load_state()
        if not state.get("auto_trade_enabled", True) and not force:
            logger.info("[{}] 自动交易已关闭，跳过", prov)
            continue
        result = run_paper_trading_cycle(
            force=force,
            provider=prov,
            refresh_data=(i == 0),
        )
        logger.info(
            "[{}] 模拟实盘结果: {} {}",
            prov,
            result.get("status"),
            result.get("message", ""),
        )
        if result.get("status") not in {"ok", "skipped", "disabled"}:
            exit_code = 1
    return exit_code


def run_daemon() -> None:
    cfg = get_config().get("paper_trading", {})
    schedule_time = str(cfg.get("schedule_time", "16:30"))
    hour, minute = (int(x) for x in schedule_time.split(":")[:2])
    logger.info("模拟实盘守护进程启动，每日 {:02d}:{:02d} 执行", hour, minute)

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
                logger.exception("模拟实盘执行失败: {}", exc)
            last_run_date = now.date()
        time.sleep(30)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="模拟实盘调度")
    parser.add_argument("--once", action="store_true", help="立即执行一轮")
    parser.add_argument("--force", action="store_true", help="强制处理最新 K 线")
    parser.add_argument("--daemon", action="store_true", help="守护进程模式（内嵌定时）")
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

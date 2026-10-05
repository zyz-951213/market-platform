"""
工具函数模块 — 日志、可视化、辅助功能
"""

import sys

from loguru import logger

from market_platform.predictor.配置 import get_config
from market_platform.predictor.核心.路径.项目 import logs_dir


def setup_logging(level: str = "INFO"):
    """配置日志"""
    cfg = get_config().get("logging", {})
    level = cfg.get("level", level)
    log_root = logs_dir()
    log_root.mkdir(parents=True, exist_ok=True)

    logger.remove()
    logger.add(
        sys.stderr,
        format="<green>{time:HH:mm:ss}</green> | <level>{level:>7}</level> | <cyan>{message}</cyan>",
        level=level,
        colorize=True,
    )
    logger.add(
        str(log_root / "train_{time:YYYY-MM-DD}.log"),
        rotation="1 day",
        retention="30 days",
        level="DEBUG",
    )
    if cfg.get("structured", True):
        logger.add(
            str(log_root / "events_{time:YYYY-MM-DD}.jsonl"),
            rotation="1 day",
            retention="30 days",
            level="INFO",
            serialize=True,
            filter=lambda r: r["extra"].get("structured") is True,
        )

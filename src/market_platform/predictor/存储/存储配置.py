from __future__ import annotations

from pathlib import Path

from market_platform.core.数据库配置 import (
    DatabaseSettings,
    load_database_settings as _load,
)
from market_platform.predictor.核心.路径.项目 import database_config_path

__all__ = ["DatabaseSettings", "load_database_settings"]


def load_database_settings(path: Path | None = None) -> DatabaseSettings:
    return _load(
        path or database_config_path(),
        search_path_default="指数预测, 公共, 行情增量",
    )

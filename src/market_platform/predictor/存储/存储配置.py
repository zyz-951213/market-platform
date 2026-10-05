"""指数预测域数据库设置 —— 委托 core(唯一解析实现),只保留本域 search_path 默认值。"""

from __future__ import annotations

from pathlib import Path

from market_platform.core.配置 import 数据库设置
from market_platform.core.数据库配置 import DatabaseSettings

__all__ = ["DatabaseSettings", "load_database_settings"]

默认搜索路径 = "指数预测, 公共, 行情增量"


def load_database_settings(path: Path | None = None) -> DatabaseSettings:
    if path is not None:
        from market_platform.core.数据库配置 import load_database_settings as 旧加载

        return 旧加载(path, search_path_default=默认搜索路径)

    设置 = 数据库设置("指数预测")
    if not 设置.search_path:
        设置.search_path = 默认搜索路径
    return 设置

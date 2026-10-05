"""数据底座域路径解析 —— 委托 `market_platform.core.路径`(唯一根)。"""

from __future__ import annotations

from pathlib import Path

from market_platform.core.路径 import (
    仓库根,
    域_数据底座,
    域配置档,
    数据目录,
    数据库配置,
    schema根,
)


def backend_root() -> Path:
    """历史语义为包内根目录,已归一为仓库根。"""
    return 仓库根()


def repo_root() -> Path:
    return 仓库根()


def config_dir() -> Path:
    from market_platform.core.路径 import 配置根

    return 配置根()


def database_config_path() -> Path:
    return 数据库配置()


def profile_path(name: str) -> Path:
    return 域配置档(域_数据底座, name)


def data_dir() -> Path:
    return 数据目录(域_数据底座)


def schema_dir() -> Path:
    """DDL 与迁移目录(统一到仓库 schema/)。"""
    return schema根()

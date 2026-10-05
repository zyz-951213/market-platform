"""统一数据库配置：兼容 agents（dual_write/parquet）与 predictor（write_files/file）。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml


DEFAULT_DSN = "postgresql:///%E9%87%91%E8%9E%8D%E5%B8%82%E5%9C%BA%E6%95%B0%E6%8D%AE"
DEFAULT_SEARCH_PATH = "公共, 行情增量"


@dataclass
class DatabaseSettings:
    """兼容并集：同时保留 agents 与 predictor 两套字段。"""

    enabled: bool = False
    dsn: str = DEFAULT_DSN
    search_path: str = DEFAULT_SEARCH_PATH
    read_from_pg: bool = True
    batch_size: int = 2000
    connect_timeout: int = 10
    # agents 使用
    dual_write: bool = True
    write_parquet: bool = True
    parquet_fallback: bool = True
    # predictor 使用
    write_files: bool = False
    file_fallback: bool = False

    @classmethod
    def from_dict(
        cls,
        raw: dict,
        *,
        search_path_default: str = DEFAULT_SEARCH_PATH,
    ) -> "DatabaseSettings":
        write_files = raw.get("write_files", raw.get("write_parquet", False))
        file_fallback = raw.get("file_fallback", raw.get("parquet_fallback", False))
        dual_write = raw.get("dual_write", True)
        write_parquet = raw.get("write_parquet", raw.get("dual_write", True))
        parquet_fallback = raw.get("parquet_fallback", True)
        return cls(
            enabled=bool(raw.get("enabled", False)),
            dsn=str(raw.get("dsn", DEFAULT_DSN)),
            search_path=str(raw.get("search_path", search_path_default)),
            read_from_pg=bool(raw.get("read_from_pg", True)),
            batch_size=int(raw.get("batch_size", 2000)),
            connect_timeout=int(raw.get("connect_timeout", 10)),
            dual_write=bool(dual_write),
            write_parquet=bool(write_parquet),
            parquet_fallback=bool(parquet_fallback),
            write_files=bool(write_files),
            file_fallback=bool(file_fallback),
        )


def load_database_settings(
    config_path: Path,
    *,
    search_path_default: str = DEFAULT_SEARCH_PATH,
) -> DatabaseSettings:
    """读取 YAML 配置并应用 DATABASE_URL / DATABASE_ENABLED 环境变量覆盖。"""
    raw: dict = {}
    if config_path.exists():
        loaded = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        if isinstance(loaded, dict):
            raw = loaded

    settings = DatabaseSettings.from_dict(raw, search_path_default=search_path_default)

    env_url = os.environ.get("DATABASE_URL", "").strip()
    if env_url:
        settings.dsn = env_url

    env_enabled = os.environ.get("DATABASE_ENABLED", "").strip().lower()
    if env_enabled in {"1", "true", "yes", "on"}:
        settings.enabled = True

    return settings

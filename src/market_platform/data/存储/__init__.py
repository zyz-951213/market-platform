"""数据底座存储层入口 —— 库配置唯一来源 `config/数据库.yaml`。

历史版本读包内 `配置/数据库.yaml`(三份不一致副本之一),现已收敛;
`AppSettings` 保留为兼容门面,内部走 `core.配置`。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict

from market_platform.core.配置 import 数据库设置
from market_platform.core.路径 import 域_数据底座


@dataclass
class DatabaseInfo:
    dsn: str
    label: str
    description: str = ""


@dataclass
class AppSettings:
    databases: Dict[str, DatabaseInfo] = None  # type: ignore[assignment]

    @classmethod
    def load(cls) -> "AppSettings":
        设置 = 数据库设置(域_数据底座)
        dbs: Dict[str, DatabaseInfo] = {}
        if 设置.dsn:
            dbs["金融市场数据"] = DatabaseInfo(
                dsn=设置.dsn,
                label="金融市场数据",
                description="统一 A 股量化库(公共 / 行情增量 / 行为分析 / 指数预测)",
            )
        env_dsn = os.environ.get("DATABASE_URL", "").strip()
        if env_dsn:
            dbs["default"] = DatabaseInfo(dsn=env_dsn, label="环境变量数据库")
        return cls(databases=dbs)


settings = AppSettings.load()

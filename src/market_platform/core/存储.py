"""共享存储底座：PostgreSQL 连接基类 + 通用 helper。"""

from __future__ import annotations

import logging
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable

import pandas as pd


logger = logging.getLogger("market_platform.core.存储")


class PostgresConnection:
    """PostgreSQL 连接基类（懒加载 + search_path 设置）。"""

    default_search_path = "公共, 行情增量"
    package_hint = "market-platform[postgres]"
    schema_path: Path | None = None
    settings_loader = None

    def __init__(self, settings=None) -> None:
        self.settings = settings if settings is not None else self.settings_loader()
        self._conn = None

    @property
    def available(self) -> bool:
        return bool(self.settings.enabled and self.settings.dsn)

    def connect(self):
        if self._conn is not None and not self._conn.closed:
            return self._conn
        try:
            import psycopg
        except ImportError as exc:  # pragma: no cover - optional dep
            raise RuntimeError(f"请安装 postgres 依赖: pip install '{self.package_hint}'") from exc
        self._conn = psycopg.connect(
            self.settings.dsn,
            connect_timeout=self.settings.connect_timeout,
            autocommit=False,
        )
        path = getattr(self.settings, "search_path", "") or self.default_search_path
        with self._conn.cursor() as cur:
            parts = [p.strip() for p in path.split(",") if p.strip()]
            quoted = ", ".join('"' + p.replace('"', "") + '"' for p in parts)
            cur.execute(f"SET search_path TO {quoted}")
        self._conn.commit()
        return self._conn

    def close(self) -> None:
        if self._conn is not None and not self._conn.closed:
            self._conn.close()
        self._conn = None

    def ping(self) -> bool:
        if not self.available:
            return False
        try:
            with self.connect().cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("PostgreSQL ping failed: %s", exc)
            return False

    def init_schema(self) -> None:
        if self.schema_path is None:
            raise RuntimeError("未配置 schema_path")
        sql = self.schema_path.read_text(encoding="utf-8")
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql)
            conn.commit()


def chunked(rows: list, size: int) -> Iterable[list]:
    for i in range(0, len(rows), size):
        yield rows[i : i + size]


def to_date(value: Any) -> date | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, pd.Timestamp):
        return value.date()
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed.date()

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


class PostgresStoreBase:
    """域存储层共享基类 —— 连接/读取/形变/健康检查的唯一实现。

    背景(TASK-003 C2):agents 与 predictor 各有 `PostgresStore`,其中
    `__init__` / `enabled` / `_conn` / `init_schema` / `ping` **逐字相同**,
    `_read_sql` 与 `_payload_rows_to_df` 也几乎相同(仅日期类型处理不同)。
    这里收敛为一份,子类只保留域专属的读写方法。

    子类约定:
      · 在 `__init__` 里调用 `super().__init__(settings)`(或设置好 `self.settings`);
      · `schema_path` 若存在,`init_schema()` 会执行它建表。
    """

    settings_loader = None
    # 子类可指定自己的连接类(域内子类带默认 search_path / schema.sql),默认用 core 的
    连接类 = PostgresConnection

    def __init__(self, settings=None) -> None:
        self.settings = settings if settings is not None else self.settings_loader()
        self._pg = self.连接类(self.settings)

    @property
    def enabled(self) -> bool:
        return self._pg.available

    def _conn(self):
        return self._pg.connect()

    def init_schema(self) -> None:
        self._pg.init_schema()

    def ping(self) -> bool:
        return self._pg.ping()

    def close(self) -> None:
        self._pg.close()

    def _read_sql(self, sql: str, params: tuple | list | None = None) -> pd.DataFrame:
        """读取为 DataFrame;出错先回滚再抛(避免连接留在失败事务里)。"""
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params or ())
                cols = [desc[0] for desc in cur.description] if cur.description else []
                rows = cur.fetchall()
        except Exception:
            conn.rollback()
            raise
        if not rows:
            return pd.DataFrame(columns=cols)
        return pd.DataFrame(rows, columns=cols)

    _日期为日期对象: bool = False

    def _payload_rows_to_df(
        self, df: pd.DataFrame, *, 日期为日期对象: bool | None = None
    ) -> pd.DataFrame:
        """把 (date, payload jsonb) 窄表展开成宽表。

        `日期为日期对象=True` 时把 date 列转成 `date`(行为分析窄表口径);
        否则保留 datetime(指数预测口径)。
        """
        用日期对象 = self._日期为日期对象 if 日期为日期对象 is None else 日期为日期对象
        if df is None or df.empty:
            return pd.DataFrame()
        records: list[dict] = []
        for row in df.itertuples(index=False):
            payload = getattr(row, "payload", {}) or {}
            if isinstance(payload, str):
                import json

                payload = json.loads(payload)
            rec = {"date": getattr(row, "date")}
            if isinstance(payload, dict):
                rec.update(payload)
            records.append(rec)
        out = pd.DataFrame(records)
        if "date" in out.columns:
            out["date"] = pd.to_datetime(out["date"])
            if 用日期对象:
                out["date"] = out["date"].dt.date
        return out

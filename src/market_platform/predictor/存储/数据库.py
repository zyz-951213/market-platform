from __future__ import annotations

import json
import logging
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from market_platform.predictor.存储.存储配置 import DatabaseSettings, load_database_settings

from market_platform.core.存储 import PostgresConnection as _BaseConnection
logger = logging.getLogger(__name__)

_SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def _to_date(value: Any) -> date | None:
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


def _json_default(obj: Any) -> Any:
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    if hasattr(obj, "item"):
        try:
            return obj.item()
        except (TypeError, ValueError):
            pass
    return str(obj)


def _sanitize_record(rec: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in rec.items():
        if pd.isna(v):
            out[k] = None
        elif isinstance(v, (pd.Timestamp, datetime, date)):
            out[k] = pd.Timestamp(v).isoformat()
        elif isinstance(v, (int, float, str, bool)) or v is None:
            out[k] = v
        else:
            out[k] = str(v)
    return out


def _chunked(rows: list, size: int) -> Iterable[list]:
    for i in range(0, len(rows), size):
        yield rows[i : i + size]


class PostgresConnection(_BaseConnection):
    """懒加载 PostgreSQL 连接。"""

    default_search_path = "指数预测, 公共, 行情增量"
    package_hint = "market-predictor[postgres]"
    schema_path = _SCHEMA_PATH
    settings_loader = staticmethod(load_database_settings)

class PostgresStore:
    def __init__(self, settings: DatabaseSettings | None = None) -> None:
        self.settings = settings or load_database_settings()
        self._pg = PostgresConnection(self.settings)

    @property
    def enabled(self) -> bool:
        return self._pg.available

    def init_schema(self) -> None:
        self._pg.init_schema()

    def ping(self) -> bool:
        return self._pg.ping()

    def _conn(self):
        return self._pg.connect()

    def _read_sql(self, sql: str, params: tuple | list | None = None) -> pd.DataFrame:
        conn = self._conn()
        with conn.cursor() as cur:
            cur.execute(sql, params or ())
            cols = [desc[0] for desc in cur.description] if cur.description else []
            rows = cur.fetchall()
        if not rows:
            return pd.DataFrame(columns=cols)
        return pd.DataFrame(rows, columns=cols)

    def _executemany_tuple(self, sql: str, rows: list[tuple]) -> int:
        if not rows:
            return 0
        conn = self._conn()
        total = 0
        with conn.cursor() as cur:
            for chunk in _chunked(rows, self.settings.batch_size):
                cur.executemany(sql, chunk)
                total += len(chunk)
        conn.commit()
        return total

    def _payload_rows_to_df(self, df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            return pd.DataFrame()
        records: list[dict[str, Any]] = []
        for row in df.itertuples(index=False):
            payload = getattr(row, "payload", {}) or {}
            if isinstance(payload, str):
                payload = json.loads(payload)
            rec = {"date": getattr(row, "date")}
            if isinstance(payload, dict):
                rec.update(payload)
            records.append(rec)
        out = pd.DataFrame(records)
        if "date" in out.columns:
            out["date"] = pd.to_datetime(out["date"])
        return out

    def upsert_index_features(self, index_code: str, df: pd.DataFrame) -> int:
        if df.empty:
            return 0
        rows: list[tuple[str, date, str]] = []
        for rec in df.to_dict(orient="records"):
            dt = _to_date(rec.get("date"))
            if dt is None:
                continue
            payload = _sanitize_record({k: v for k, v in rec.items() if k != "date"})
            rows.append((index_code, dt, json.dumps(payload, ensure_ascii=False, default=_json_default)))
        sql = """
            INSERT INTO "指数特征" ("指数代码", "交易日期", "载荷")
            VALUES (%s, %s, %s::jsonb)
            ON CONFLICT ("指数代码", "交易日期") DO UPDATE SET "载荷" = EXCLUDED."载荷"
        """
        return self._executemany_tuple(sql, rows)

    def load_index_feature_overview(self, index_code: str) -> dict[str, Any] | None:
        """轻量概览：条数 / 起止日 / 最新一行（供 stale 检测），避免拉全表。"""
        sql_counts = """
            SELECT COUNT(*) AS total_bars,
                   MIN("交易日期") AS start_date,
                   MAX("交易日期") AS end_date
            FROM "指数特征"
            WHERE "指数代码" = %s
        """
        cdf = self._read_sql(sql_counts, (index_code,))
        if cdf.empty or int(cdf.iloc[0]["total_bars"] or 0) <= 0:
            return None
        total = int(cdf.iloc[0]["total_bars"])
        start_date = cdf.iloc[0]["start_date"]
        end_date = cdf.iloc[0]["end_date"]
        latest_df = self.load_index_features(index_code, limit=1)
        latest = latest_df.iloc[-1].to_dict() if not latest_df.empty else {}
        if "date" in latest:
            latest["date"] = str(latest["date"])[:10]
        labeled = 0
        try:
            sql_labeled = """
                SELECT COUNT(*) AS n FROM "指数特征"
                WHERE "指数代码" = %s
                  AND ("载荷" ? 'label_direction')
                  AND ("载荷"->>'label_direction') IS NOT NULL
                  AND ("载荷"->>'label_direction') NOT IN ('', 'NaN', 'null')
            """
            ldf = self._read_sql(sql_labeled, (index_code,))
            if not ldf.empty:
                labeled = int(ldf.iloc[0]["n"] or 0)
        except Exception:
            labeled = 0
        return {
            "total_bars": total,
            "labeled_bars": labeled,
            "start_date": str(start_date)[:10] if start_date is not None else None,
            "end_date": str(end_date)[:10] if end_date is not None else None,
            "latest": latest,
            "feature_columns": len(latest.keys()) if latest else 0,
        }

    def load_index_features(
        self,
        index_code: str,
        *,
        columns: list[str] | None = None,
        start: str | None = None,
        end: str | None = None,
        limit: int | None = None,
    ) -> pd.DataFrame:
        clauses = ['"指数代码" = %s']
        params: list[Any] = [index_code]
        if start:
            clauses.append('"交易日期" >= %s')
            params.append(_to_date(start) or start)
        if end:
            clauses.append('"交易日期" <= %s')
            params.append(_to_date(end) or end)
        where = " AND ".join(clauses)
        if limit is not None and int(limit) > 0:
            sql = f"""
                SELECT date, payload FROM (
                    SELECT "交易日期" AS date, "载荷" AS payload
                    FROM "指数特征"
                    WHERE {where}
                    ORDER BY "交易日期" DESC
                    LIMIT %s
                ) t
                ORDER BY date
            """
            params.append(int(limit))
        else:
            sql = f"""
                SELECT "交易日期" AS date, "载荷" AS payload
                FROM "指数特征"
                WHERE {where}
                ORDER BY "交易日期"
            """
        df = self._payload_rows_to_df(self._read_sql(sql, tuple(params)))
        if columns and not df.empty:
            keep = ["date", *[c for c in columns if c != "date" and c in df.columns]]
            # 载荷里可能缺列：补齐缺失列为 NaN
            for c in columns:
                if c != "date" and c not in df.columns:
                    df[c] = pd.NA
                    keep.append(c)
            df = df.loc[:, list(dict.fromkeys(keep))]
        return df

    def upsert_time_series(self, dataset_key: str, df: pd.DataFrame) -> int:
        if df.empty or "date" not in df.columns:
            return 0
        rows: list[tuple[str, date, str]] = []
        for rec in df.to_dict(orient="records"):
            dt = _to_date(rec.get("date"))
            if dt is None:
                continue
            payload = _sanitize_record({k: v for k, v in rec.items() if k != "date"})
            rows.append((dataset_key, dt, json.dumps(payload, ensure_ascii=False, default=_json_default)))
        sql = """
            INSERT INTO "时序数据" ("数据集键", "交易日期", "载荷")
            VALUES (%s, %s, %s::jsonb)
            ON CONFLICT ("数据集键", "交易日期") DO UPDATE SET "载荷" = EXCLUDED."载荷"
        """
        return self._executemany_tuple(sql, rows)

    def load_time_series(self, dataset_key: str) -> pd.DataFrame:
        sql = """
            SELECT "交易日期" AS date, "载荷" AS payload FROM "时序数据"
            WHERE "数据集键" = %s ORDER BY "交易日期"
        """
        return self._payload_rows_to_df(self._read_sql(sql, (dataset_key,)))

    def save_dataset_snapshot(self, dataset_key: str, df: pd.DataFrame) -> None:
        records = [_sanitize_record(r) for r in df.to_dict(orient="records")]
        sql = """
            INSERT INTO "数据集快照" ("数据集键", "记录", "更新时间")
            VALUES (%s, %s::jsonb, NOW())
            ON CONFLICT ("数据集键") DO UPDATE SET
                "记录" = EXCLUDED."记录",
                "更新时间" = NOW()
        """
        conn = self._conn()
        with conn.cursor() as cur:
            cur.execute(sql, (dataset_key, json.dumps(records, ensure_ascii=False, default=_json_default)))
        conn.commit()

    def load_dataset_snapshot(self, dataset_key: str) -> pd.DataFrame:
        sql = 'SELECT "记录" FROM "数据集快照" WHERE "数据集键" = %s'
        conn = self._conn()
        with conn.cursor() as cur:
            cur.execute(sql, (dataset_key,))
            row = cur.fetchone()
        if not row or not row[0]:
            return pd.DataFrame()
        records = row[0]
        if isinstance(records, str):
            records = json.loads(records)
        df = pd.DataFrame(records)
        if "date" in df.columns:
            df["date"] = pd.to_datetime(df["date"])
        return df

    def save_llm_cache(
        self,
        *,
        model: str,
        prompt_version: str,
        prompt_digest: str,
        bar_date: str,
        response: dict[str, Any],
    ) -> None:
        sql = """
            INSERT INTO "大模型缓存" ("模型", "提示词版本", "提示词摘要", "行情日期", "响应", "创建时间")
            VALUES (%s, %s, %s, %s, %s::jsonb, NOW())
            ON CONFLICT ("模型", "提示词版本", "提示词摘要", "行情日期")
            DO UPDATE SET "响应" = EXCLUDED."响应", "创建时间" = NOW()
        """
        conn = self._conn()
        with conn.cursor() as cur:
            cur.execute(
                sql,
                (
                    model,
                    prompt_version,
                    prompt_digest,
                    _to_date(bar_date),
                    json.dumps(response, ensure_ascii=False, default=_json_default),
                ),
            )
        conn.commit()

    def load_llm_cache(
        self,
        *,
        model: str,
        prompt_version: str,
        prompt_digest: str,
        bar_date: str,
    ) -> dict[str, Any] | None:
        sql = """
            SELECT "响应" FROM "大模型缓存"
            WHERE "模型" = %s AND "提示词版本" = %s AND "提示词摘要" = %s AND "行情日期" = %s
        """
        conn = self._conn()
        with conn.cursor() as cur:
            cur.execute(sql, (model, prompt_version, prompt_digest, _to_date(bar_date)))
            row = cur.fetchone()
        return row[0] if row else None

    def save_backtest_run(self, payload: dict[str, Any], *, is_latest: bool = False) -> None:
        body = dict(payload)
        run_id = body["run_id"]
        signals = body.pop("signals", []) or []
        classification = {
            k: body.pop(k)
            for k in list(body.keys())
            if k in {
                "directional_accuracy", "flat_recall", "high_conf_accuracy", "pred_flat_rate",
            }
        }
        metrics = {
            k: body.get(k)
            for k in (
                "run_id", "model_name", "bar_freq", "strategy_fingerprint", "data_as_of", "saved_at",
                "accuracy", "total_return", "annualized_return", "sharpe_ratio", "max_drawdown",
                "win_rate", "total_trades", "periods_in_market", "benchmark_total_return",
                "benchmark_max_drawdown", "excess_return", "benchmark_start_date", "benchmark_end_date",
                "avg_position", "cash_mgmt_enabled", "cash_return", "strategy_only_return",
                "llm_provider", "llm_model", "prompt_version", "llm_call_count", "weekly_decision",
                "week_accuracy", "day_accuracy",
            )
        }
        conn = self._conn()
        with conn.cursor() as cur:
            # 按模型名称清「最新」标记，避免多渠道互踩只剩一家 is_latest
            if is_latest and metrics.get("model_name"):
                cur.execute(
                    'UPDATE "回测记录" SET "是否最新" = FALSE '
                    'WHERE "是否最新" = TRUE AND "模型名称" = %s',
                    (metrics.get("model_name"),),
                )
            cur.execute(
                """
                INSERT INTO "回测记录" (
                    "运行编号", "是否最新", "模型名称", "行情频率", "策略指纹",
                    "数据截至", "保存时间", "指标", "分类"
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb)
                ON CONFLICT ("运行编号") DO UPDATE SET
                    "是否最新" = EXCLUDED."是否最新",
                    "模型名称" = EXCLUDED."模型名称",
                    "行情频率" = EXCLUDED."行情频率",
                    "策略指纹" = EXCLUDED."策略指纹",
                    "数据截至" = EXCLUDED."数据截至",
                    "保存时间" = EXCLUDED."保存时间",
                    "指标" = EXCLUDED."指标",
                    "分类" = EXCLUDED."分类"
                """,
                (
                    run_id,
                    is_latest,
                    metrics.get("model_name"),
                    metrics.get("bar_freq"),
                    metrics.get("strategy_fingerprint"),
                    _to_date(metrics.get("data_as_of")),
                    metrics.get("saved_at"),
                    json.dumps(metrics, ensure_ascii=False, default=_json_default),
                    json.dumps(classification, ensure_ascii=False, default=_json_default),
                ),
            )
            cur.execute('DELETE FROM "回测信号" WHERE "运行编号" = %s', (run_id,))
        conn.commit()

        rows: list[tuple[str, date, str]] = []
        for sig in signals:
            dt = _to_date(sig.get("date"))
            if dt is None:
                continue
            rows.append((run_id, dt, json.dumps(_sanitize_record(sig), ensure_ascii=False, default=_json_default)))
        if rows:
            sql = """
                INSERT INTO "回测信号" ("运行编号", "交易日期", "载荷")
                VALUES (%s, %s, %s::jsonb)
                ON CONFLICT ("运行编号", "交易日期") DO UPDATE SET "载荷" = EXCLUDED."载荷"
            """
            self._executemany_tuple(sql, rows)

    def _backtest_payload_from_row(self, metrics: dict, classification: dict, signals: list) -> dict[str, Any]:
        out = dict(metrics or {})
        out.update(classification or {})
        out["signals"] = signals
        return out

    def load_latest_backtest(self) -> dict[str, Any] | None:
        sql = """
            SELECT "运行编号" AS run_id, "指标" AS metrics, "分类" AS classification
            FROM "回测记录"
            WHERE "是否最新" = TRUE ORDER BY "保存时间" DESC NULLS LAST LIMIT 1
        """
        df = self._read_sql(sql)
        if df.empty:
            sql2 = """
                SELECT "运行编号" AS run_id, "指标" AS metrics, "分类" AS classification
                FROM "回测记录"
                ORDER BY "保存时间" DESC NULLS LAST LIMIT 1
            """
            df = self._read_sql(sql2)
        if df.empty:
            return None
        row = df.iloc[0]
        run_id = row["run_id"]
        metrics = row["metrics"] if isinstance(row["metrics"], dict) else json.loads(row["metrics"])
        classification = row["classification"] if isinstance(row["classification"], dict) else json.loads(row["classification"])
        signals = self.load_backtest_signals(run_id)
        return self._backtest_payload_from_row(metrics, classification, signals)

    def load_backtest_by_model_name(self, model_name: str) -> dict[str, Any] | None:
        sql = """
            SELECT "运行编号" AS run_id, "指标" AS metrics, "分类" AS classification
            FROM "回测记录"
            WHERE "模型名称" = %s
            ORDER BY "保存时间" DESC NULLS LAST LIMIT 1
        """
        df = self._read_sql(sql, (model_name,))
        if df.empty:
            return None
        row = df.iloc[0]
        run_id = row["run_id"]
        metrics = row["metrics"] if isinstance(row["metrics"], dict) else json.loads(row["metrics"])
        classification = row["classification"] if isinstance(row["classification"], dict) else json.loads(row["classification"])
        signals = self.load_backtest_signals(run_id)
        return self._backtest_payload_from_row(metrics, classification, signals)

    def load_latest_backtest_metrics(self) -> dict[str, Any] | None:
        """最新回测的 metrics/classification，不加载 signals（供 overview 轻量接口）。"""
        sql = """
            SELECT "运行编号" AS run_id, "是否最新" AS is_latest,
                   "指标" AS metrics, "分类" AS classification
            FROM "回测记录"
            WHERE "是否最新" = TRUE ORDER BY "保存时间" DESC NULLS LAST LIMIT 1
        """
        df = self._read_sql(sql)
        if df.empty:
            sql2 = """
                SELECT "运行编号" AS run_id, "是否最新" AS is_latest,
                       "指标" AS metrics, "分类" AS classification
                FROM "回测记录"
                ORDER BY "保存时间" DESC NULLS LAST LIMIT 1
            """
            df = self._read_sql(sql2)
        if df.empty:
            return None
        row = df.iloc[0]
        metrics = row["metrics"] if isinstance(row["metrics"], dict) else json.loads(row["metrics"])
        classification = (
            row["classification"]
            if isinstance(row["classification"], dict)
            else json.loads(row["classification"] or "{}")
        )
        out = dict(metrics or {})
        out.update(classification or {})
        out["run_id"] = row["run_id"]
        out["is_latest"] = bool(row["is_latest"])
        return out

    def load_backtest_metrics_by_model_name(self, model_name: str) -> dict[str, Any] | None:
        """只取 metrics / classification，不加载 signals（供渠道汇总表）。"""
        sql = """
            SELECT "运行编号" AS run_id, "是否最新" AS is_latest,
                   "指标" AS metrics, "分类" AS classification
            FROM "回测记录"
            WHERE "模型名称" = %s
            ORDER BY "保存时间" DESC NULLS LAST LIMIT 1
        """
        df = self._read_sql(sql, (model_name,))
        if df.empty:
            return None
        row = df.iloc[0]
        metrics = row["metrics"] if isinstance(row["metrics"], dict) else json.loads(row["metrics"])
        classification = row["classification"] if isinstance(row["classification"], dict) else json.loads(row["classification"])
        out = dict(metrics or {})
        out.update(classification or {})
        out["run_id"] = row["run_id"]
        out["is_latest"] = bool(row["is_latest"])
        return out

    def load_backtest_run(self, run_id: str) -> dict[str, Any] | None:
        sql = 'SELECT "指标" AS metrics, "分类" AS classification FROM "回测记录" WHERE "运行编号" = %s'
        df = self._read_sql(sql, (run_id,))
        if df.empty:
            return None
        row = df.iloc[0]
        metrics = row["metrics"] if isinstance(row["metrics"], dict) else json.loads(row["metrics"])
        classification = row["classification"] if isinstance(row["classification"], dict) else json.loads(row["classification"])
        signals = self.load_backtest_signals(run_id)
        return self._backtest_payload_from_row(metrics, classification, signals)

    def load_backtest_signals(
        self,
        run_id: str,
        *,
        columns: list[str] | None = None,
        start: str | None = None,
        end: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        clauses = ['"运行编号" = %s']
        params: list[Any] = [run_id]
        if start:
            clauses.append('"交易日期" >= %s')
            params.append(_to_date(start) or start)
        if end:
            clauses.append('"交易日期" <= %s')
            params.append(_to_date(end) or end)
        where = " AND ".join(clauses)
        # limit：取最近 N 条，仍按日期正序返回
        if limit is not None and int(limit) > 0:
            sql = f"""
                SELECT date, payload FROM (
                    SELECT "交易日期" AS date, "载荷" AS payload
                    FROM "回测信号"
                    WHERE {where}
                    ORDER BY "交易日期" DESC
                    LIMIT %s
                ) t
                ORDER BY date
            """
            params.append(int(limit))
        else:
            sql = f"""
                SELECT "交易日期" AS date, "载荷" AS payload
                FROM "回测信号"
                WHERE {where}
                ORDER BY "交易日期"
            """
        df = self._read_sql(sql, tuple(params))
        keep = {"date", *(columns or [])} if columns else None
        out: list[dict[str, Any]] = []
        for row in df.itertuples(index=False):
            payload = row.payload if isinstance(row.payload, dict) else json.loads(row.payload)
            if not isinstance(payload, dict):
                continue
            payload = dict(payload)
            payload.setdefault("date", str(row.date)[:10])
            if keep is not None:
                payload = {k: v for k, v in payload.items() if k in keep}
            out.append(payload)
        return out

    def list_backtest_runs(self, limit: int = 30) -> list[dict[str, Any]]:
        sql = """
            SELECT "指标" AS metrics FROM "回测记录"
            ORDER BY "保存时间" DESC NULLS LAST LIMIT %s
        """
        df = self._read_sql(sql, (limit,))
        runs = []
        for row in df.itertuples(index=False):
            metrics = row.metrics if isinstance(row.metrics, dict) else json.loads(row.metrics)
            runs.append(metrics)
        return runs

    def save_fetch_meta(self, dataset_key: str, payload: Any) -> None:
        sql = """
            INSERT INTO "拉取元数据" ("数据集键", "载荷", "更新时间")
            VALUES (%s, %s::jsonb, NOW())
            ON CONFLICT ("数据集键") DO UPDATE SET "载荷" = EXCLUDED."载荷", "更新时间" = NOW()
        """
        conn = self._conn()
        with conn.cursor() as cur:
            cur.execute(sql, (dataset_key, json.dumps(payload, ensure_ascii=False, default=_json_default)))
        conn.commit()

    def load_fetch_meta(self, dataset_key: str) -> Any | None:
        sql = 'SELECT "载荷" FROM "拉取元数据" WHERE "数据集键" = %s'
        conn = self._conn()
        with conn.cursor() as cur:
            cur.execute(sql, (dataset_key,))
            row = cur.fetchone()
        return row[0] if row else None

    def save_app_state(self, key: str, payload: dict[str, Any]) -> None:
        sql = """
            INSERT INTO "应用状态" ("键", "载荷", "更新时间")
            VALUES (%s, %s::jsonb, NOW())
            ON CONFLICT ("键") DO UPDATE SET "载荷" = EXCLUDED."载荷", "更新时间" = NOW()
        """
        conn = self._conn()
        with conn.cursor() as cur:
            cur.execute(sql, (key, json.dumps(payload, ensure_ascii=False, default=_json_default)))
        conn.commit()

    def load_app_state(self, key: str) -> dict[str, Any] | None:
        sql = 'SELECT "载荷" FROM "应用状态" WHERE "键" = %s'
        conn = self._conn()
        with conn.cursor() as cur:
            cur.execute(sql, (key,))
            row = cur.fetchone()
        if not row:
            return None
        payload = row[0]
        if isinstance(payload, dict):
            return payload
        return json.loads(payload)


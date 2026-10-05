from __future__ import annotations

import json
import logging
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable

import pandas as pd
from market_platform.agents.存储.存储配置 import DatabaseSettings, load_database_settings

from market_platform.core.存储 import PostgresConnection as _BaseConnection
logger = logging.getLogger(__name__)

_SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def _normalize_symbol(symbol: str) -> str:
    return str(symbol).strip().zfill(6)


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


def _num(value: Any) -> float | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if out != out or out in (float("inf"), float("-inf")):
        return None
    return out


def _json_safe_value(value: Any) -> Any:
    """JSON/JSONB 不能存 NaN/Infinity。"""
    if value is None:
        return None
    if isinstance(value, float):
        if pd.isna(value) or value in (float("inf"), float("-inf")):
            return None
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    item = getattr(value, "item", None)
    if callable(item):
        try:
            return _json_safe_value(item())
        except Exception:
            pass
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return value


def _json_safe_obj(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _json_safe_obj(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe_obj(v) for v in value]
    return _json_safe_value(value)


def _text(value: Any, default: str = "") -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return default
    return str(value)


def _optional_text(value: Any) -> str | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value)
    return text if text else None


class PostgresConnection(_BaseConnection):
    """懒加载 PostgreSQL 连接。"""

    default_search_path = "行为分析, 公共, 行情增量"
    package_hint = "market-agents[postgres]"
    schema_path = _SCHEMA_PATH
    settings_loader = staticmethod(load_database_settings)

def _chunked(rows: list[tuple], size: int) -> Iterable[list[tuple]]:
    for i in range(0, len(rows), size):
        yield rows[i : i + size]


_SERIES_TYPE_TO_CN = {
    "minute_daily_features": "分钟日特征",
    "agent_activity": "参与者活跃度",
    "daily_feature_matrix": "日特征矩阵",
    "state_features": "状态特征",
    "minute_bars": "分钟行情",
}
_AGENT_ACTIVITY_COLS = (
    "主力资金",
    "北向资金",
    "尾盘量能",
    "融资融券",
    "分级资金流",
    "波动冲击",
    "成交规模",
)
_MINUTE_FEATURE_COLS = (
    "return_mean",
    "return_std",
    "volume_total",
    "amount_total",
    "volume_spike_mean",
    "volume_spike_max",
    "realized_vol_mean",
    "realized_vol_max",
    "vwap_dev_mean",
    "vwap_dev_std",
    "price_impact_mean",
    "price_impact_max",
    "high_low_range_mean",
    "open_volume_ratio",
    "tail_volume_ratio",
    "minute_bars",
)
_ARTIFACT_TYPE_TO_CN = {
    "agent_patterns": "参与者模式",
    "agent_pattern_labels": "参与者标签",
    "agent_names": "参与者名称",
    "optimized_positions": "优化仓位",
    "transition_matrix": "转移矩阵",
    "next_state_probs": "下一状态概率",
}
_ARTIFACT_TYPE_FROM_CN = {v: k for k, v in _ARTIFACT_TYPE_TO_CN.items()}


def _series_type_cn(series_type: str) -> str:
    return _SERIES_TYPE_TO_CN.get(str(series_type), str(series_type))


def _artifact_type_cn(artifact_type: str) -> str:
    key = str(artifact_type)
    return _ARTIFACT_TYPE_TO_CN.get(key, key)


def _artifact_type_en(artifact_type: str) -> str:
    key = str(artifact_type)
    return _ARTIFACT_TYPE_FROM_CN.get(key, key)


class PostgresStore:
    def __init__(self, settings: DatabaseSettings | None = None) -> None:
        self.settings = settings or load_database_settings()
        self._pg = PostgresConnection(self.settings)

    @property
    def enabled(self) -> bool:
        return self._pg.available

    def init_schema(self) -> None:
        self._pg.init_schema()

    def migrate_equity_merge_states(self) -> dict[str, Any]:
        """兼容旧脚本：中文库已无独立 market_states / OHLCV 列，直接回报当前结构。"""
        conn = self._conn()
        stats: dict[str, Any] = {
            "had_market_states_table": False,
            "state_score_backfilled": 0,
            "state_only_rows_inserted": 0,
            "dropped_market_states": False,
            "schema": "行为分析.策略净值",
        }
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT column_name FROM information_schema.columns
                WHERE table_schema = '行为分析' AND table_name = '策略净值'
                ORDER BY ordinal_position
                """
            )
            stats["columns"] = [r[0] for r in cur.fetchall()]
            cur.execute(
                """
                SELECT EXISTS (
                    SELECT 1 FROM information_schema.tables
                    WHERE table_schema = '行为分析' AND table_name = 'market_states'
                      AND table_type = 'BASE TABLE'
                )
                """
            )
            stats["had_market_states_table"] = bool(cur.fetchone()[0])
        return stats

    def ping(self) -> bool:
        return self._pg.ping()

    def _conn(self):
        return self._pg.connect()

    def upsert_symbols(self, rows: list[dict[str, Any]]) -> int:
        if not rows:
            return 0
        sql = """
            INSERT INTO 公共.证券档案 (
                证券代码, 证券简称, 交易市场, 所属行业, 一级行业, 二级行业, 三级行业,
                数据路径, 输出路径, 更新时间
            ) VALUES (
                %(symbol)s, %(name)s, %(market)s, %(sector)s, %(sector_l1)s,
                %(sector_l2)s, %(sector_l3)s, %(data_path)s, %(output_path)s, NOW()
            )
            ON CONFLICT (证券代码) DO UPDATE SET
                证券简称 = COALESCE(NULLIF(EXCLUDED.证券简称, ''), 公共.证券档案.证券简称),
                交易市场 = EXCLUDED.交易市场,
                所属行业 = COALESCE(NULLIF(EXCLUDED.所属行业, ''), 公共.证券档案.所属行业),
                一级行业 = COALESCE(NULLIF(EXCLUDED.一级行业, ''), 公共.证券档案.一级行业),
                二级行业 = COALESCE(NULLIF(EXCLUDED.二级行业, ''), 公共.证券档案.二级行业),
                三级行业 = COALESCE(NULLIF(EXCLUDED.三级行业, ''), 公共.证券档案.三级行业),
                数据路径 = COALESCE(NULLIF(EXCLUDED.数据路径, ''), 公共.证券档案.数据路径),
                输出路径 = COALESCE(NULLIF(EXCLUDED.输出路径, ''), 公共.证券档案.输出路径),
                更新时间 = NOW()
        """
        payload = []
        for row in rows:
            sym = _normalize_symbol(row.get("symbol", ""))
            if not sym:
                continue
            payload.append(
                {
                    "symbol": sym,
                    "name": _text(row.get("name")),
                    "market": _text(row.get("market", "sz"), "sz")[:2],
                    "sector": _text(row.get("sector")),
                    "sector_l1": _text(row.get("sector_l1") or row.get("sector")),
                    "sector_l2": _text(row.get("sector_l2") or row.get("sector_l1") or row.get("sector")),
                    "sector_l3": _text(row.get("sector_l3") or row.get("sector_l2") or row.get("sector")),
                    "data_path": _text(row.get("data_path") or row.get("path")),
                    "output_path": _text(row.get("output_path")),
                }
            )
        return self._executemany(sql, payload)

    def ensure_symbol(self, symbol: str, *, name: str = "", market: str = "sz") -> None:
        sym = _normalize_symbol(symbol)
        self.upsert_symbols([{"symbol": sym, "name": name, "market": market}])

    def upsert_daily_bars(self, symbol: str, df: pd.DataFrame) -> int:
        rows = self._daily_bar_rows(symbol, df)
        if not rows:
            return 0
        self.ensure_symbol(symbol)
        sql = """
            INSERT INTO 行为分析.日行情 (
                证券代码, 交易日期, 开盘价, 最高价, 最低价, 收盘价, 成交量, 成交额
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (证券代码, 交易日期) DO UPDATE SET
                开盘价 = EXCLUDED.开盘价,
                最高价 = EXCLUDED.最高价,
                最低价 = EXCLUDED.最低价,
                收盘价 = EXCLUDED.收盘价,
                成交量 = EXCLUDED.成交量,
                成交额 = EXCLUDED.成交额
        """
        return self._executemany_tuple(sql, rows)

    def replace_daily_bars(self, symbol: str, df: pd.DataFrame) -> int:
        """整表替换某标的日线（先删后写），避免脏日残留。"""
        sym = _normalize_symbol(symbol)
        rows = self._daily_bar_rows(sym, df)
        self.ensure_symbol(sym)
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM 行为分析.日行情 WHERE 证券代码 = %s", (sym,))
                if rows:
                    cur.executemany(
                        """
                        INSERT INTO 行为分析.日行情 (
                            证券代码, 交易日期, 开盘价, 最高价, 最低价, 收盘价, 成交量, 成交额
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        rows,
                    )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        return len(rows)

    def upsert_fund_flow(self, symbol: str, df: pd.DataFrame) -> int:
        rows = self._fund_flow_rows(symbol, df)
        if not rows:
            return 0
        self.ensure_symbol(symbol)
        sql = """
            INSERT INTO 行为分析.资金流向 (
                证券代码, 交易日期, 收盘价, 涨跌幅, 主力净流入, 主力净占比,
                超大单净流入, 超大单净占比, 大单净流入, 大单净占比,
                中单净流入, 中单净占比, 小单净流入, 小单净占比
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            ON CONFLICT (证券代码, 交易日期) DO UPDATE SET
                收盘价 = EXCLUDED.收盘价,
                涨跌幅 = EXCLUDED.涨跌幅,
                主力净流入 = EXCLUDED.主力净流入,
                主力净占比 = EXCLUDED.主力净占比,
                超大单净流入 = EXCLUDED.超大单净流入,
                超大单净占比 = EXCLUDED.超大单净占比,
                大单净流入 = EXCLUDED.大单净流入,
                大单净占比 = EXCLUDED.大单净占比,
                中单净流入 = EXCLUDED.中单净流入,
                中单净占比 = EXCLUDED.中单净占比,
                小单净流入 = EXCLUDED.小单净流入,
                小单净占比 = EXCLUDED.小单净占比
        """
        return self._executemany_tuple(sql, rows)

    def upsert_equity_curve(self, symbol: str, df: pd.DataFrame) -> int:
        rows = self._equity_curve_rows(symbol, df)
        if not rows:
            return 0
        self.ensure_symbol(symbol)
        sql = """
            INSERT INTO 行为分析.策略净值 (
                证券代码, 交易日期, 建议仓位, 市场状态, 状态得分,
                仓位, 仓位变动, 策略收益率, 基准收益率, 策略净值, 基准净值
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            ON CONFLICT (证券代码, 交易日期) DO UPDATE SET
                建议仓位 = EXCLUDED.建议仓位,
                市场状态 = EXCLUDED.市场状态,
                状态得分 = COALESCE(EXCLUDED.状态得分, 行为分析.策略净值.状态得分),
                仓位 = EXCLUDED.仓位,
                仓位变动 = EXCLUDED.仓位变动,
                策略收益率 = EXCLUDED.策略收益率,
                基准收益率 = EXCLUDED.基准收益率,
                策略净值 = EXCLUDED.策略净值,
                基准净值 = EXCLUDED.基准净值
        """
        return self._executemany_tuple(sql, rows)

    def upsert_market_states(self, symbol: str, df: pd.DataFrame) -> int:
        """兼容旧调用：状态写入策略净值（不再使用独立 market_states 表）。"""
        rows = self._market_state_rows(symbol, df)
        if not rows:
            return 0
        self.ensure_symbol(symbol)
        sql = """
            INSERT INTO 行为分析.策略净值 (证券代码, 交易日期, 市场状态, 状态得分, 建议仓位)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (证券代码, 交易日期) DO UPDATE SET
                市场状态 = EXCLUDED.市场状态,
                状态得分 = COALESCE(EXCLUDED.状态得分, 行为分析.策略净值.状态得分),
                建议仓位 = EXCLUDED.建议仓位
        """
        return self._executemany_tuple(sql, rows)

    def upsert_symbol_summary(self, rows: list[dict[str, Any]]) -> int:
        if not rows:
            return 0
        symbols = [{"symbol": r.get("symbol"), "name": r.get("name", ""), "sector": r.get("sector", "")} for r in rows]
        self.upsert_symbols(symbols)
        sql = """
            INSERT INTO 行为分析.标的摘要 (
                证券代码, 所属行业, 证券简称, 最新状态, 建议仓位,
                策略收益率, 基准收益率, 超额收益率,
                策略夏普, 异常数, 状态, 输出路径, 分析时间
            ) VALUES (
                %(symbol)s, %(sector)s, %(name)s, %(latest_state)s, %(suggested_position)s,
                %(strategy_return)s, %(benchmark_return)s, %(excess_return)s,
                %(strategy_sharpe)s, %(anomalies)s, %(status)s, %(output_path)s, NOW()
            )
            ON CONFLICT (证券代码) DO UPDATE SET
                所属行业 = COALESCE(NULLIF(EXCLUDED.所属行业, ''), 行为分析.标的摘要.所属行业),
                证券简称 = COALESCE(NULLIF(EXCLUDED.证券简称, ''), 行为分析.标的摘要.证券简称),
                最新状态 = EXCLUDED.最新状态,
                建议仓位 = EXCLUDED.建议仓位,
                策略收益率 = EXCLUDED.策略收益率,
                基准收益率 = EXCLUDED.基准收益率,
                超额收益率 = EXCLUDED.超额收益率,
                策略夏普 = EXCLUDED.策略夏普,
                异常数 = EXCLUDED.异常数,
                状态 = EXCLUDED.状态,
                输出路径 = EXCLUDED.输出路径,
                分析时间 = NOW()
        """
        payload = []
        for row in rows:
            sym = _normalize_symbol(row.get("symbol", ""))
            if not sym:
                continue
            payload.append(
                {
                    "symbol": sym,
                    "sector": _text(row.get("sector")),
                    "name": _text(row.get("name")),
                    "latest_state": _text(row.get("latest_state")),
                    "suggested_position": _num(row.get("suggested_position")),
                    "strategy_return": _num(row.get("strategy_return")),
                    "benchmark_return": _num(row.get("benchmark_return")),
                    "excess_return": _num(row.get("excess_return")),
                    "strategy_sharpe": _num(row.get("strategy_sharpe")),
                    "anomalies": _num(row.get("anomalies")),
                    "status": _text(row.get("status", "ok"), "ok"),
                    "output_path": _text(row.get("path") or row.get("output_path")),
                }
            )
        return self._executemany(sql, payload)

    def upsert_market_series(self, series_name: str, df: pd.DataFrame) -> int:
        if df.empty or "date" not in df.columns:
            return 0
        rows: list[tuple[str, date, str]] = []
        for rec in df.to_dict(orient="records"):
            dt = _to_date(rec.get("date"))
            if dt is None:
                continue
            payload = {k: _json_safe_value(v) for k, v in rec.items() if k != "date"}
            rows.append(
                (
                    series_name,
                    dt,
                    json.dumps(payload, ensure_ascii=False, default=str, allow_nan=False),
                )
            )
        if not rows:
            return 0
        sql = """
            INSERT INTO 行为分析.市场时序 (序列名称, 交易日期, 载荷)
            VALUES (%s, %s, %s::jsonb)
            ON CONFLICT (序列名称, 交易日期) DO UPDATE SET 载荷 = EXCLUDED.载荷
        """
        return self._executemany_tuple(sql, rows)

    def load_daily_bars(self, symbol: str, *, limit: int | None = None) -> pd.DataFrame:
        sym = _normalize_symbol(symbol)
        if limit:
            sql = """
                SELECT 交易日期 AS date, 开盘价 AS open, 最高价 AS high, 最低价 AS low,
                       收盘价 AS close, 成交量 AS volume, 成交额 AS amount, 证券代码 AS symbol
                FROM 行为分析.日行情 WHERE 证券代码 = %s ORDER BY 交易日期 DESC LIMIT %s
            """
            df = self._read_sql(sql, (sym, int(limit)))
            if not df.empty:
                return df.sort_values("date")
            return df
        sql = """
            SELECT 交易日期 AS date, 开盘价 AS open, 最高价 AS high, 最低价 AS low,
                   收盘价 AS close, 成交量 AS volume, 成交额 AS amount, 证券代码 AS symbol
            FROM 行为分析.日行情 WHERE 证券代码 = %s ORDER BY 交易日期
        """
        return self._read_sql(sql, (sym,))

    def load_symbol_summary(self) -> pd.DataFrame:
        sql = """
            SELECT 证券代码 AS symbol, 所属行业 AS sector, 证券简称 AS name,
                   最新状态 AS latest_state, 建议仓位 AS suggested_position,
                   策略收益率 AS strategy_return, 基准收益率 AS benchmark_return,
                   超额收益率 AS excess_return, 策略夏普 AS strategy_sharpe,
                   异常数 AS anomalies, 状态 AS status, 输出路径 AS path
            FROM 行为分析.标的摘要
            ORDER BY 证券代码
        """
        return self._read_sql(sql)

    def load_kline_batch(self, symbols: list[str], *, limit: int | None = 90) -> dict[str, pd.DataFrame]:
        if not symbols:
            return {}
        syms = [_normalize_symbol(s) for s in symbols]
        placeholders = ",".join(["%s"] * len(syms))
        take_all = limit is None or int(limit) <= 0
        sql = f"""
            SELECT 证券代码 AS symbol, 交易日期 AS date,
                   开盘价 AS open, 最高价 AS high, 最低价 AS low, 收盘价 AS close
            FROM 行为分析.日行情
            WHERE 证券代码 IN ({placeholders})
            ORDER BY 证券代码, 交易日期
        """
        df = self._read_sql(sql, tuple(syms))
        if df.empty:
            return {}
        out: dict[str, pd.DataFrame] = {}
        for sym, grp in df.groupby("symbol"):
            g = grp.sort_values("date")
            if not take_all:
                g = g.tail(int(limit))
            out[str(sym)] = g
        return out

    def upsert_symbol_series(self, symbol: str, series_type: str, df: pd.DataFrame) -> int:
        sym = _normalize_symbol(symbol)
        if df.empty:
            return 0
        self.ensure_symbol(sym)
        cn = _series_type_cn(series_type)
        if cn == "参与者活跃度":
            return self._upsert_narrow_series(sym, "行为分析.参与者活跃度", _AGENT_ACTIVITY_COLS, df)
        if cn == "分钟日特征":
            return self._upsert_narrow_series(sym, "行为分析.分钟日特征", _MINUTE_FEATURE_COLS, df)
        raise ValueError(f"标的时序已改为窄表，不再写入序列类型: {series_type}")

    def load_symbol_series(self, symbol: str, series_type: str) -> pd.DataFrame:
        sym = _normalize_symbol(symbol)
        cn = _series_type_cn(series_type)
        if cn == "参与者活跃度":
            cols = ", ".join(f'"{c}"' for c in _AGENT_ACTIVITY_COLS)
            sql = f"""
                SELECT 交易日期 AS date, {cols}
                FROM 行为分析.参与者活跃度
                WHERE 证券代码 = %s ORDER BY 交易日期
            """
            out = self._read_sql(sql, (sym,))
        elif cn == "分钟日特征":
            cols = ", ".join(_MINUTE_FEATURE_COLS)
            sql = f"""
                SELECT 交易日期 AS date, {cols}
                FROM 行为分析.分钟日特征
                WHERE 证券代码 = %s ORDER BY 交易日期
            """
            out = self._read_sql(sql, (sym,))
        else:
            return pd.DataFrame()
        if out.empty:
            return out
        out["date"] = pd.to_datetime(out["date"]).dt.date
        return out

    def upsert_symbol_artifact(self, symbol: str, artifact_type: str, payload: Any) -> None:
        sym = _normalize_symbol(symbol)
        self.ensure_symbol(sym)
        sql = """
            INSERT INTO 行为分析.标的产物 (证券代码, 产物类型, 载荷, 更新时间)
            VALUES (%s, %s, %s::jsonb, NOW())
            ON CONFLICT (证券代码, 产物类型) DO UPDATE SET
                载荷 = EXCLUDED.载荷, 更新时间 = NOW()
        """
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    sql,
                    (
                        sym,
                        _artifact_type_cn(artifact_type),
                        json.dumps(
                            _json_safe_obj(payload),
                            ensure_ascii=False,
                            default=str,
                            allow_nan=False,
                        ),
                    ),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    def load_symbol_artifact(self, symbol: str, artifact_type: str) -> Any | None:
        sym = _normalize_symbol(symbol)
        sql = """
            SELECT 载荷 FROM 行为分析.标的产物
            WHERE 证券代码 = %s AND 产物类型 = %s
        """
        conn = self._conn()
        with conn.cursor() as cur:
            cur.execute(sql, (sym, _artifact_type_cn(artifact_type)))
            row = cur.fetchone()
        return row[0] if row else None

    def load_fund_flow(self, symbol: str) -> pd.DataFrame:
        sym = _normalize_symbol(symbol)
        sql = """
            SELECT 证券代码 AS symbol, 交易日期 AS date, 收盘价 AS close, 涨跌幅 AS pct_change,
                   主力净流入 AS main_net_inflow, 主力净占比 AS main_net_ratio,
                   超大单净流入 AS super_large_net_inflow, 超大单净占比 AS super_large_net_ratio,
                   大单净流入 AS large_net_inflow, 大单净占比 AS large_net_ratio,
                   中单净流入 AS medium_net_inflow, 中单净占比 AS medium_net_ratio,
                   小单净流入 AS small_net_inflow, 小单净占比 AS small_net_ratio
            FROM 行为分析.资金流向
            WHERE 证券代码 = %s ORDER BY 交易日期
        """
        return self._read_sql(sql, (sym,))

    def load_equity_curve(self, symbol: str) -> pd.DataFrame:
        sym = _normalize_symbol(symbol)
        sql = """
            SELECT 证券代码 AS symbol, 交易日期 AS date,
                   建议仓位 AS suggested_position, 市场状态 AS market_state,
                   状态得分 AS state_score, 基准收益率 AS market_return,
                   仓位 AS position, 仓位变动 AS position_change,
                   策略收益率 AS strategy_return, 基准收益率 AS benchmark_return,
                   策略净值 AS strategy_equity, 基准净值 AS benchmark_equity
            FROM 行为分析.策略净值
            WHERE 证券代码 = %s ORDER BY 交易日期
        """
        return self._read_sql(sql, (sym,))

    def list_equity_curve_years(self, *, min_symbols: int = 100) -> list[int]:
        """可用年份列表（由权益曲线日期跨度展开，避免全表 GROUP BY）。"""
        del min_symbols
        span = self._read_sql(
            "SELECT MIN(交易日期) AS d0, MAX(交易日期) AS d1 FROM 行为分析.策略净值"
        )
        if span.empty or span.iloc[0]["d0"] is None or span.iloc[0]["d1"] is None:
            return []
        y0 = int(pd.Timestamp(span.iloc[0]["d0"]).year)
        y1 = int(pd.Timestamp(span.iloc[0]["d1"]).year)
        if y1 < y0:
            return []
        return list(range(y1, y0 - 1, -1))

    def load_equity_returns_for_year(self, year: int) -> pd.DataFrame:
        """日历年内权益曲线首末净值推算策略/基准/超额收益。"""
        y = int(year)
        start = f"{y}-01-01"
        end = f"{y + 1}-01-01"
        sql = """
            WITH firsts AS (
                SELECT DISTINCT ON (证券代码)
                       证券代码 AS symbol, 策略净值 AS s0, 基准净值 AS b0, 交易日期 AS d0
                FROM 行为分析.策略净值
                WHERE 交易日期 >= %s::date AND 交易日期 < %s::date
                  AND 策略净值 IS NOT NULL AND 策略净值 > 0
                ORDER BY 证券代码, 交易日期 ASC
            ),
            lasts AS (
                SELECT DISTINCT ON (证券代码)
                       证券代码 AS symbol, 策略净值 AS s1, 基准净值 AS b1, 交易日期 AS d1
                FROM 行为分析.策略净值
                WHERE 交易日期 >= %s::date AND 交易日期 < %s::date
                  AND 策略净值 IS NOT NULL AND 策略净值 > 0
                ORDER BY 证券代码, 交易日期 DESC
            )
            SELECT f.symbol,
                   CASE WHEN f.s0 > 0 THEN l.s1 / f.s0 - 1.0 END AS strategy_return,
                   CASE WHEN f.b0 > 0 AND l.b1 IS NOT NULL THEN l.b1 / f.b0 - 1.0 END AS benchmark_return
            FROM firsts f
            JOIN lasts l USING (symbol)
            WHERE f.d0 < l.d1
        """
        df = self._read_sql(sql, (start, end, start, end))
        if df.empty:
            return pd.DataFrame(columns=["symbol", "strategy_return", "benchmark_return", "excess_return"])
        df["symbol"] = df["symbol"].astype(str).str.zfill(6)
        for col in ("strategy_return", "benchmark_return"):
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df["excess_return"] = df["strategy_return"] - df["benchmark_return"]
        return df

    def load_market_states(self, symbol: str) -> pd.DataFrame:
        sym = _normalize_symbol(symbol)
        sql = """
            SELECT 交易日期 AS date, 市场状态 AS market_state,
                   状态得分 AS state_score, 建议仓位 AS suggested_position
            FROM 行为分析.策略净值
            WHERE 证券代码 = %s
            ORDER BY 交易日期
        """
        return self._read_sql(sql, (sym,))

    def load_market_state_recent(
        self,
        *,
        tail: int = 2,
        symbols: list[str] | None = None,
        lookback_days: int = 14,
    ) -> pd.DataFrame:
        """每标的最近 tail 条状态（读自策略净值）。"""
        n = max(int(tail), 1)
        lookback = max(int(lookback_days), n + 5)
        syms: list[str] | None = None
        if symbols is not None:
            syms = [str(s).zfill(6) for s in symbols if str(s).strip()]
            if not syms:
                return pd.DataFrame(
                    columns=["symbol", "date", "market_state", "suggested_position"]
                )

        if n == 2:
            if syms is None:
                sql = """
                    WITH bounds AS (
                        SELECT MAX(交易日期) AS d1 FROM 行为分析.策略净值
                    ),
                    latest AS (
                        SELECT DISTINCT ON (m.证券代码)
                               m.证券代码 AS symbol, m.交易日期 AS date,
                               m.市场状态 AS market_state, m.建议仓位 AS suggested_position
                        FROM 行为分析.策略净值 m, bounds b
                        WHERE m.交易日期 >= b.d1 - %s
                          AND m.市场状态 IS NOT NULL
                        ORDER BY m.证券代码, m.交易日期 DESC
                    ),
                    prev AS (
                        SELECT DISTINCT ON (m.证券代码)
                               m.证券代码 AS symbol, m.交易日期 AS date,
                               m.市场状态 AS market_state, m.建议仓位 AS suggested_position
                        FROM 行为分析.策略净值 m
                        JOIN latest l ON l.symbol = m.证券代码
                        WHERE m.交易日期 < l.date AND m.交易日期 >= l.date - %s
                          AND m.市场状态 IS NOT NULL
                        ORDER BY m.证券代码, m.交易日期 DESC
                    )
                    SELECT symbol, date, market_state, suggested_position FROM latest
                    UNION ALL
                    SELECT symbol, date, market_state, suggested_position FROM prev
                    ORDER BY symbol, date
                """
                return self._read_sql(sql, (lookback, lookback))
            sql = """
                WITH latest AS (
                    SELECT DISTINCT ON (证券代码)
                           证券代码 AS symbol, 交易日期 AS date,
                           市场状态 AS market_state, 建议仓位 AS suggested_position
                    FROM 行为分析.策略净值
                    WHERE 证券代码 = ANY(%s) AND 市场状态 IS NOT NULL
                    ORDER BY 证券代码, 交易日期 DESC
                ),
                prev AS (
                    SELECT DISTINCT ON (m.证券代码)
                           m.证券代码 AS symbol, m.交易日期 AS date,
                           m.市场状态 AS market_state, m.建议仓位 AS suggested_position
                    FROM 行为分析.策略净值 m
                    JOIN latest l ON l.symbol = m.证券代码
                    WHERE m.交易日期 < l.date AND m.市场状态 IS NOT NULL
                    ORDER BY m.证券代码, m.交易日期 DESC
                )
                SELECT symbol, date, market_state, suggested_position FROM latest
                UNION ALL
                SELECT symbol, date, market_state, suggested_position FROM prev
                ORDER BY symbol, date
            """
            return self._read_sql(sql, (syms,))

        if syms is not None:
            sql = """
                WITH ranked AS (
                    SELECT 证券代码 AS symbol, 交易日期 AS date,
                           市场状态 AS market_state, 建议仓位 AS suggested_position,
                           ROW_NUMBER() OVER (PARTITION BY 证券代码 ORDER BY 交易日期 DESC) AS rn
                    FROM 行为分析.策略净值
                    WHERE 证券代码 = ANY(%s)
                      AND 市场状态 IS NOT NULL
                      AND 交易日期 >= (SELECT MAX(交易日期) - %s FROM 行为分析.策略净值)
                )
                SELECT symbol, date, market_state, suggested_position
                FROM ranked
                WHERE rn <= %s
                ORDER BY symbol, date
            """
            return self._read_sql(sql, (syms, lookback, n))
        sql = """
            WITH ranked AS (
                SELECT 证券代码 AS symbol, 交易日期 AS date,
                       市场状态 AS market_state, 建议仓位 AS suggested_position,
                       ROW_NUMBER() OVER (PARTITION BY 证券代码 ORDER BY 交易日期 DESC) AS rn
                FROM 行为分析.策略净值
                WHERE 市场状态 IS NOT NULL
                  AND 交易日期 >= (SELECT MAX(交易日期) - %s FROM 行为分析.策略净值)
            )
            SELECT symbol, date, market_state, suggested_position
            FROM ranked
            WHERE rn <= %s
            ORDER BY symbol, date
        """
        return self._read_sql(sql, (lookback, n))

    def count_data_ready(self, *, require_features: bool = True) -> int:
        """有日线（及可选特征）的标的数，单次 SQL，避免逐票探测。"""
        if require_features:
            sql = """
                SELECT COUNT(*) AS n
                FROM 公共.证券档案 s
                WHERE EXISTS (SELECT 1 FROM 行为分析.日行情 d WHERE d.证券代码 = s.证券代码)
                  AND EXISTS (SELECT 1 FROM 行为分析.分钟日特征 f WHERE f.证券代码 = s.证券代码)
            """
            df = self._read_sql(sql)
        else:
            sql = """
                SELECT COUNT(*) AS n
                FROM 公共.证券档案 s
                WHERE EXISTS (SELECT 1 FROM 行为分析.日行情 d WHERE d.证券代码 = s.证券代码)
            """
            df = self._read_sql(sql)
        if df.empty:
            return 0
        return int(df.iloc[0]["n"] or 0)

    def count_analyzed(self) -> int:
        sql = "SELECT COUNT(*) AS n FROM 行为分析.标的摘要 WHERE 状态 = 'ok'"
        df = self._read_sql(sql)
        if df.empty:
            return 0
        return int(df.iloc[0]["n"] or 0)

    def load_symbols_registry(self) -> pd.DataFrame:
        sql = """
            SELECT 证券代码 AS symbol, 证券简称 AS name, 所属行业 AS sector,
                   一级行业 AS sector_l1, 二级行业 AS sector_l2, 三级行业 AS sector_l3,
                   数据路径 AS path, 交易市场 AS market, 输出路径 AS output_path
            FROM 公共.证券档案
            ORDER BY 一级行业, 二级行业, 三级行业, 证券简称, 证券代码
        """
        df = self._read_sql(sql)
        if not df.empty:
            df["symbol"] = df["symbol"].astype(str).str.zfill(6)
        return df

    def max_daily_bar_date(self, symbol: str) -> date | None:
        sym = _normalize_symbol(symbol)
        sql = "SELECT MAX(交易日期) FROM 行为分析.日行情 WHERE 证券代码 = %s"
        conn = self._conn()
        with conn.cursor() as cur:
            cur.execute(sql, (sym,))
            row = cur.fetchone()
        if not row or row[0] is None:
            return None
        val = row[0]
        return val if isinstance(val, date) else _to_date(val)

    def has_symbol_series(self, symbol: str, series_type: str) -> bool:
        sym = _normalize_symbol(symbol)
        cn = _series_type_cn(series_type)
        if cn == "参与者活跃度":
            table = "行为分析.参与者活跃度"
        elif cn == "分钟日特征":
            table = "行为分析.分钟日特征"
        else:
            return False
        sql = f"SELECT 1 FROM {table} WHERE 证券代码 = %s LIMIT 1"
        conn = self._conn()
        with conn.cursor() as cur:
            cur.execute(sql, (sym,))
            return cur.fetchone() is not None

    def symbol_has_daily_bars(self, symbol: str) -> bool:
        sym = _normalize_symbol(symbol)
        sql = "SELECT 1 FROM 行为分析.日行情 WHERE 证券代码 = %s LIMIT 1"
        conn = self._conn()
        with conn.cursor() as cur:
            cur.execute(sql, (sym,))
            return cur.fetchone() is not None

    def symbol_is_analyzed(self, symbol: str) -> bool:
        sym = _normalize_symbol(symbol)
        sql = "SELECT 1 FROM 行为分析.标的摘要 WHERE 证券代码 = %s AND 状态 = 'ok' LIMIT 1"
        conn = self._conn()
        with conn.cursor() as cur:
            cur.execute(sql, (sym,))
            return cur.fetchone() is not None

    def upsert_industry_summary(self, rows: list[dict[str, Any]]) -> int:
        if not rows:
            return 0
        sql = """
            INSERT INTO 行为分析.行业摘要 (
                所属行业, 标的数量, 平均超额收益率, 中位超额收益率, 胜率,
                平均策略收益率, 平均基准收益率, 平均策略夏普,
                平均建议仓位, 更新时间
            ) VALUES (
                %(sector)s, %(n_symbols)s, %(mean_excess_return)s, %(median_excess_return)s,
                %(win_rate)s, %(mean_strategy_return)s, %(mean_benchmark_return)s,
                %(mean_strategy_sharpe)s, %(mean_suggested_position)s, NOW()
            )
            ON CONFLICT (所属行业) DO UPDATE SET
                标的数量 = EXCLUDED.标的数量,
                平均超额收益率 = EXCLUDED.平均超额收益率,
                中位超额收益率 = EXCLUDED.中位超额收益率,
                胜率 = EXCLUDED.胜率,
                平均策略收益率 = EXCLUDED.平均策略收益率,
                平均基准收益率 = EXCLUDED.平均基准收益率,
                平均策略夏普 = EXCLUDED.平均策略夏普,
                平均建议仓位 = EXCLUDED.平均建议仓位,
                更新时间 = NOW()
        """
        return self._executemany(sql, rows)

    def load_industry_summary(self) -> pd.DataFrame:
        sql = """
            SELECT 所属行业 AS sector, 标的数量 AS n_symbols,
                   平均超额收益率 AS mean_excess_return, 中位超额收益率 AS median_excess_return,
                   胜率 AS win_rate, 平均策略收益率 AS mean_strategy_return,
                   平均基准收益率 AS mean_benchmark_return, 平均策略夏普 AS mean_strategy_sharpe,
                   平均建议仓位 AS mean_suggested_position, 更新时间 AS updated_at
            FROM 行为分析.行业摘要
            ORDER BY 所属行业
        """
        return self._read_sql(sql)

    def load_oos_excess_map(self) -> dict[str, float]:
        """批量读取优化仓位中的样本外超额。"""
        sql = """
            SELECT 证券代码 AS symbol, 载荷->'oos_metrics'->>'excess_return' AS oos
            FROM 行为分析.标的产物
            WHERE 产物类型 = '优化仓位'
              AND 载荷 ? 'oos_metrics'
        """
        df = self._read_sql(sql)
        out: dict[str, float] = {}
        if df.empty:
            return out
        for row in df.itertuples(index=False):
            sym = _normalize_symbol(getattr(row, "symbol", ""))
            raw = getattr(row, "oos", None)
            if not sym or raw is None or str(raw).strip() == "":
                continue
            try:
                out[sym] = float(raw)
            except (TypeError, ValueError):
                continue
        return out

    def load_equity_date_spans(self) -> pd.DataFrame:
        """各标的回测净值曲线起止日期。"""
        sql = """
            SELECT 证券代码 AS symbol, MIN(交易日期) AS start_date, MAX(交易日期) AS end_date
            FROM 行为分析.策略净值
            GROUP BY 证券代码
        """
        df = self._read_sql(sql)
        if not df.empty:
            df["symbol"] = df["symbol"].astype(str).str.zfill(6)
        return df

    def save_portfolio(self, portfolio_id: str, name: str, metadata: dict, equity: pd.DataFrame) -> None:
        sql_meta = """
            INSERT INTO 行为分析.组合 (组合编号, 组合名称, 元数据, 创建时间)
            VALUES (%s, %s, %s::jsonb, NOW())
            ON CONFLICT (组合编号) DO UPDATE SET
                组合名称 = EXCLUDED.组合名称, 元数据 = EXCLUDED.元数据
        """
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    sql_meta,
                    (
                        portfolio_id,
                        name,
                        json.dumps(_json_safe_obj(metadata), ensure_ascii=False, default=str, allow_nan=False),
                    ),
                )
                cur.execute("DELETE FROM 行为分析.组合净值 WHERE 组合编号 = %s", (portfolio_id,))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        if equity.empty:
            return
        rows: list[tuple] = []
        for rec in equity.to_dict(orient="records"):
            dt = _to_date(rec.get("date"))
            if dt is None:
                continue
            rows.append(
                (
                    portfolio_id,
                    dt,
                    _num(rec.get("portfolio_equity")),
                    _num(rec.get("benchmark_equity")),
                    _num(rec.get("portfolio_return")),
                    _num(rec.get("benchmark_return")),
                    _num(rec.get("portfolio_position")),
                )
            )
        sql_eq = """
            INSERT INTO 行为分析.组合净值 (
                组合编号, 交易日期, 组合净值, 基准净值,
                组合收益率, 基准收益率, 组合仓位
            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (组合编号, 交易日期) DO UPDATE SET
                组合净值 = EXCLUDED.组合净值,
                基准净值 = EXCLUDED.基准净值,
                组合收益率 = EXCLUDED.组合收益率,
                基准收益率 = EXCLUDED.基准收益率,
                组合仓位 = EXCLUDED.组合仓位
        """
        self._executemany_tuple(sql_eq, rows)

    def list_portfolios(self) -> list[dict[str, Any]]:
        sql = """
            SELECT 组合编号 AS portfolio_id, 组合名称 AS name,
                   元数据 AS metadata, 创建时间 AS created_at
            FROM 行为分析.组合
            ORDER BY 创建时间 DESC
        """
        df = self._read_sql(sql)
        return df.to_dict(orient="records")

    def load_portfolio_equity(self, portfolio_id: str) -> pd.DataFrame:
        sql = """
            SELECT 交易日期 AS date, 组合净值 AS portfolio_equity, 基准净值 AS benchmark_equity,
                   组合收益率 AS portfolio_return, 基准收益率 AS benchmark_return,
                   组合仓位 AS portfolio_position
            FROM 行为分析.组合净值
            WHERE 组合编号 = %s ORDER BY 交易日期
        """
        return self._read_sql(sql, (portfolio_id,))

    def load_portfolio_metadata(self, portfolio_id: str) -> dict | None:
        sql = """
            SELECT 组合名称, 元数据, 创建时间
            FROM 行为分析.组合 WHERE 组合编号 = %s
        """
        conn = self._conn()
        with conn.cursor() as cur:
            cur.execute(sql, (portfolio_id,))
            row = cur.fetchone()
        if not row:
            return None
        return {"portfolio_id": portfolio_id, "name": row[0], "metadata": row[1], "created_at": row[2]}

    def save_app_state(self, key: str, payload: Any) -> None:
        sql = """
            INSERT INTO 行为分析.应用状态 (键, 载荷, 更新时间)
            VALUES (%s, %s::jsonb, NOW())
            ON CONFLICT (键) DO UPDATE SET 载荷 = EXCLUDED.载荷, 更新时间 = NOW()
        """
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    sql,
                    (key, json.dumps(_json_safe_obj(payload), ensure_ascii=False, default=str, allow_nan=False)),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    def load_app_state(self, key: str) -> Any | None:
        sql = "SELECT 载荷 FROM 行为分析.应用状态 WHERE 键 = %s"
        conn = self._conn()
        with conn.cursor() as cur:
            cur.execute(sql, (key,))
            row = cur.fetchone()
        return row[0] if row else None

    def load_market_series_df(self, series_name: str) -> pd.DataFrame:
        sql = """
            SELECT 交易日期 AS date, 载荷 AS payload
            FROM 行为分析.市场时序
            WHERE 序列名称 = %s ORDER BY 交易日期
        """
        return self._payload_rows_to_df(self._read_sql(sql, (series_name,)))

    def _read_sql(self, sql: str, params: tuple | list | None = None) -> pd.DataFrame:
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

    def _executemany(self, sql: str, rows: list[dict[str, Any]]) -> int:
        if not rows:
            return 0
        conn = self._conn()
        total = 0
        try:
            with conn.cursor() as cur:
                for chunk in _chunked(rows, self.settings.batch_size):
                    cur.executemany(sql, chunk)
                    total += len(chunk)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        return total

    def _executemany_tuple(self, sql: str, rows: list[tuple]) -> int:
        if not rows:
            return 0
        conn = self._conn()
        total = 0
        try:
            with conn.cursor() as cur:
                for chunk in _chunked(rows, self.settings.batch_size):
                    cur.executemany(sql, chunk)
                    total += len(chunk)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        return total

    def _daily_bar_rows(self, symbol: str, df: pd.DataFrame) -> list[tuple]:
        sym = _normalize_symbol(symbol)
        rows: list[tuple] = []
        for rec in df.to_dict(orient="records"):
            dt = _to_date(rec.get("date"))
            if dt is None:
                continue
            rows.append(
                (
                    sym,
                    dt,
                    _num(rec.get("open")),
                    _num(rec.get("high")),
                    _num(rec.get("low")),
                    _num(rec.get("close")),
                    _num(rec.get("volume")),
                    _num(rec.get("amount")),
                )
            )
        return rows

    def _fund_flow_rows(self, symbol: str, df: pd.DataFrame) -> list[tuple]:
        sym = _normalize_symbol(symbol)
        rows: list[tuple] = []
        for rec in df.to_dict(orient="records"):
            dt = _to_date(rec.get("date"))
            if dt is None:
                continue
            rows.append(
                (
                    sym,
                    dt,
                    _num(rec.get("close")),
                    _num(rec.get("pct_change")),
                    _num(rec.get("main_net_inflow")),
                    _num(rec.get("main_net_ratio")),
                    _num(rec.get("super_large_net_inflow")),
                    _num(rec.get("super_large_net_ratio")),
                    _num(rec.get("large_net_inflow")),
                    _num(rec.get("large_net_ratio")),
                    _num(rec.get("medium_net_inflow")),
                    _num(rec.get("medium_net_ratio")),
                    _num(rec.get("small_net_inflow")),
                    _num(rec.get("small_net_ratio")),
                )
            )
        return rows

    def _equity_curve_rows(self, symbol: str, df: pd.DataFrame) -> list[tuple]:
        sym = _normalize_symbol(symbol)
        rows: list[tuple] = []
        for rec in df.to_dict(orient="records"):
            dt = _to_date(rec.get("date"))
            if dt is None:
                continue
            bench = _num(rec.get("benchmark_return"))
            if bench is None:
                bench = _num(rec.get("market_return"))
            rows.append(
                (
                    sym,
                    dt,
                    _num(rec.get("suggested_position")),
                    _optional_text(rec.get("market_state")),
                    _num(rec.get("state_score")),
                    _num(rec.get("position")),
                    _num(rec.get("position_change")),
                    _num(rec.get("strategy_return")),
                    bench,
                    _num(rec.get("strategy_equity")),
                    _num(rec.get("benchmark_equity")),
                )
            )
        return rows

    def _market_state_rows(self, symbol: str, df: pd.DataFrame) -> list[tuple]:
        sym = _normalize_symbol(symbol)
        rows: list[tuple] = []
        for rec in df.to_dict(orient="records"):
            dt = _to_date(rec.get("date"))
            if dt is None:
                continue
            rows.append(
                (
                    sym,
                    dt,
                    _optional_text(rec.get("market_state")),
                    _num(rec.get("state_score")),
                    _num(rec.get("suggested_position")),
                )
            )
        return rows

    def _upsert_narrow_series(
        self, symbol: str, table: str, cols: tuple[str, ...], df: pd.DataFrame
    ) -> int:
        rows: list[tuple] = []
        for rec in df.to_dict(orient="records"):
            dt = _to_date(rec.get("date"))
            if dt is None:
                continue
            vals = tuple(_num(rec.get(c)) for c in cols)
            rows.append((symbol, dt, *vals))
        if not rows:
            return 0
        quoted_cols = [f'"{c}"' if not c.isascii() else c for c in cols]
        col_sql = ", ".join(("证券代码", "交易日期", *quoted_cols))
        placeholders = ", ".join(["%s"] * (2 + len(cols)))
        updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in quoted_cols)
        sql = f"""
            INSERT INTO {table} ({col_sql})
            VALUES ({placeholders})
            ON CONFLICT (证券代码, 交易日期) DO UPDATE SET {updates}
        """
        return self._executemany_tuple(sql, rows)

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
            out["date"] = pd.to_datetime(out["date"]).dt.date
        return out

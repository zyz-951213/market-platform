"""PostgreSQL 统一数据访问层。"""

from __future__ import annotations

import logging
from datetime import date
from functools import lru_cache
from typing import Any

import pandas as pd
from market_platform.agents.存储.存储配置 import DatabaseSettings, load_database_settings
from market_platform.agents.存储.数据库 import PostgresStore

logger = logging.getLogger(__name__)

SERIES_MINUTE_DAILY = "minute_daily_features"
SERIES_FEATURE_MATRIX = "daily_feature_matrix"  # 已停写：分析时内存重建
SERIES_AGENT_ACTIVITY = "agent_activity"
SERIES_STATE_FEATURES = "state_features"  # 已停写：分析时内存重建
SERIES_MINUTE_BARS = "minute_bars"  # 已停写：日线代理分钟，无增量信息

# 禁止再写入的衍生系列（历史行可由 scripts/purge_ephemeral_series.py 清理）
SERIES_EPHEMERAL: frozenset[str] = frozenset(
    {SERIES_FEATURE_MATRIX, SERIES_STATE_FEATURES, SERIES_MINUTE_BARS}
)

ARTIFACT_AGENT_PATTERNS = "agent_patterns"
ARTIFACT_AGENT_LABELS = "agent_pattern_labels"
ARTIFACT_AGENT_NAMES = "agent_names"
ARTIFACT_OPTIMIZED_POSITIONS = "optimized_positions"
ARTIFACT_TRANSITION_MATRIX = "transition_matrix"
ARTIFACT_NEXT_STATE_PROBS = "next_state_probs"


@lru_cache(maxsize=1)
def _settings() -> DatabaseSettings:
    return load_database_settings()


@lru_cache(maxsize=1)
def _store() -> PostgresStore:
    s = _settings()
    effective = DatabaseSettings(
        enabled=True,
        dsn=s.dsn,
        search_path=s.search_path,
        dual_write=s.dual_write,
        read_from_pg=s.read_from_pg,
        write_parquet=s.write_parquet,
        parquet_fallback=s.parquet_fallback,
        batch_size=s.batch_size,
        connect_timeout=s.connect_timeout,
    )
    return PostgresStore(effective)


def pg_enabled() -> bool:
    return _settings().enabled and bool(_settings().dsn)


def _use_pg_read() -> bool:
    return pg_enabled() and _settings().read_from_pg


def _use_pg_write() -> bool:
    return pg_enabled()


def save_daily_bars(symbol: str, df: pd.DataFrame, *, replace: bool = False) -> None:
    if not _use_pg_write() or df is None or df.empty:
        return
    from market_platform.agents.存储.行情清洗 import sanitize_daily_ohlcv

    cleaned = sanitize_daily_ohlcv(df)
    if cleaned.empty:
        logger.warning("Skip save_daily_bars %s: no valid OHLC rows after sanitize", symbol)
        return
    if replace:
        _store().replace_daily_bars(symbol, cleaned)
    else:
        _store().upsert_daily_bars(symbol, cleaned)


def load_daily_bars(symbol: str, *, limit: int | None = None) -> pd.DataFrame:
    if _use_pg_read():
        from market_platform.agents.存储.行情清洗 import sanitize_daily_ohlcv

        df = _store().load_daily_bars(symbol, limit=limit)
        if not df.empty:
            return sanitize_daily_ohlcv(df)
    return pd.DataFrame()


def save_fund_flow(symbol: str, df: pd.DataFrame) -> None:
    if _use_pg_write() and not df.empty:
        _store().upsert_fund_flow(symbol, df)


def load_fund_flow(symbol: str) -> pd.DataFrame:
    if _use_pg_read():
        df = _store().load_fund_flow(symbol)
        if not df.empty:
            return df
    return pd.DataFrame()


def save_series(symbol: str, series_type: str, df: pd.DataFrame) -> None:
    if series_type in SERIES_EPHEMERAL:
        logger.debug("Skip ephemeral series write %s/%s", symbol, series_type)
        return
    if _use_pg_write() and not df.empty:
        _store().upsert_symbol_series(symbol, series_type, df)


def load_series(symbol: str, series_type: str) -> pd.DataFrame:
    if _use_pg_read():
        df = _store().load_symbol_series(symbol, series_type)
        if not df.empty:
            return df
    return pd.DataFrame()


def save_artifact(symbol: str, artifact_type: str, payload: Any) -> None:
    if _use_pg_write():
        if isinstance(payload, pd.DataFrame):
            payload = payload.to_dict(orient="records")
        _store().upsert_symbol_artifact(symbol, artifact_type, payload)


def load_artifact(symbol: str, artifact_type: str) -> Any | None:
    if _use_pg_read():
        return _store().load_symbol_artifact(symbol, artifact_type)
    return None


def load_artifact_df(symbol: str, artifact_type: str) -> pd.DataFrame:
    payload = load_artifact(symbol, artifact_type)
    if payload is None:
        return pd.DataFrame()
    if isinstance(payload, list):
        return pd.DataFrame(payload)
    if isinstance(payload, dict):
        return pd.DataFrame([payload])
    return pd.DataFrame()


def save_market_states(symbol: str, df: pd.DataFrame) -> None:
    if _use_pg_write() and not df.empty:
        _store().upsert_market_states(symbol, df)


def load_market_states(symbol: str) -> pd.DataFrame:
    if _use_pg_read():
        df = _store().load_market_states(symbol)
        if not df.empty:
            return df
    return pd.DataFrame()


def load_market_state_recent(*, tail: int = 2, symbols: list[str] | None = None) -> pd.DataFrame:
    if _use_pg_read():
        return _store().load_market_state_recent(tail=tail, symbols=symbols)
    return pd.DataFrame()


def save_equity_curve(symbol: str, df: pd.DataFrame) -> None:
    if _use_pg_write() and not df.empty:
        _store().upsert_equity_curve(symbol, df)


def load_equity_curve(symbol: str) -> pd.DataFrame:
    if _use_pg_read():
        df = _store().load_equity_curve(symbol)
        if not df.empty:
            return df
    return pd.DataFrame()


def list_equity_curve_years(*, min_symbols: int = 100) -> list[int]:
    if _use_pg_read():
        return _store().list_equity_curve_years(min_symbols=min_symbols)
    return []


def load_equity_returns_for_year(year: int) -> pd.DataFrame:
    if _use_pg_read():
        return _store().load_equity_returns_for_year(int(year))
    return pd.DataFrame(columns=["symbol", "strategy_return", "benchmark_return", "excess_return"])


def save_symbol_summary_rows(rows: list[dict]) -> None:
    if _use_pg_write() and rows:
        _store().upsert_symbol_summary(rows)


def upsert_symbols_registry(rows: list[dict]) -> int:
    if _use_pg_write() and rows:
        return _store().upsert_symbols(rows)
    return 0


def load_symbol_summary() -> pd.DataFrame:
    if _use_pg_read():
        df = _store().load_symbol_summary()
        if not df.empty:
            df["symbol"] = df["symbol"].astype(str).str.zfill(6)
            return df
    return pd.DataFrame()


def load_symbols_registry() -> pd.DataFrame:
    if _use_pg_read():
        df = _store().load_symbols_registry()
        if not df.empty:
            return df
    return pd.DataFrame()


def load_hierarchy_df() -> pd.DataFrame:
    from market_platform.agents.核心.路径.层级 import normalize_sector_hierarchy_df

    df = load_symbols_registry()
    if df.empty:
        return pd.DataFrame()
    cols = ["symbol", "name", "sector", "sector_l1", "sector_l2", "sector_l3"]
    out = df[[c for c in cols if c in df.columns]].copy()
    out["symbol"] = out["symbol"].astype(str).str.zfill(6)
    return normalize_sector_hierarchy_df(out)


def save_nmf_outputs(
    symbol: str,
    activity: pd.DataFrame,
    patterns: pd.DataFrame,
    *,
    agent_names: dict[str, str] | None = None,
    labels: pd.DataFrame | None = None,
) -> None:
    save_series(symbol, SERIES_AGENT_ACTIVITY, activity)
    save_artifact(symbol, ARTIFACT_AGENT_PATTERNS, patterns)
    if agent_names:
        save_artifact(symbol, ARTIFACT_AGENT_NAMES, agent_names)
    if labels is not None and not labels.empty:
        save_artifact(symbol, ARTIFACT_AGENT_LABELS, labels)


def load_agent_activity(symbol: str) -> pd.DataFrame:
    return load_series(symbol, SERIES_AGENT_ACTIVITY)


def load_agent_patterns(symbol: str) -> pd.DataFrame:
    payload = load_artifact(symbol, ARTIFACT_AGENT_PATTERNS)
    if isinstance(payload, list):
        return pd.DataFrame(payload)
    return pd.DataFrame()


def load_agent_names(symbol: str) -> dict[str, str]:
    payload = load_artifact(symbol, ARTIFACT_AGENT_NAMES)
    return payload if isinstance(payload, dict) else {}


def load_agent_labels(symbol: str) -> pd.DataFrame:
    return load_artifact_df(symbol, ARTIFACT_AGENT_LABELS)


def save_optimized_positions(symbol: str, payload: dict) -> None:
    save_artifact(symbol, ARTIFACT_OPTIMIZED_POSITIONS, payload)


def load_optimized_positions(symbol: str) -> dict:
    payload = load_artifact(symbol, ARTIFACT_OPTIMIZED_POSITIONS)
    return payload if isinstance(payload, dict) else {}


def save_transition_matrix(symbol: str, df: pd.DataFrame) -> None:
    save_artifact(symbol, ARTIFACT_TRANSITION_MATRIX, df.to_dict())


def save_next_state_probs(symbol: str, df: pd.DataFrame) -> None:
    out = df.reset_index().rename(columns={"index": "state"})
    save_artifact(symbol, ARTIFACT_NEXT_STATE_PROBS, out.to_dict(orient="records"))


def load_next_state_probs(symbol: str) -> pd.DataFrame:
    df = load_artifact_df(symbol, ARTIFACT_NEXT_STATE_PROBS)
    if df.empty:
        return pd.DataFrame()
    if "state" in df.columns:
        return df.set_index("state")
    return df


def save_market_series(name: str, df: pd.DataFrame) -> None:
    if _use_pg_write() and not df.empty:
        _store().upsert_market_series(name, df)


def save_industry_summary(df: pd.DataFrame) -> None:
    if _use_pg_write() and not df.empty:
        _store().upsert_industry_summary(df.to_dict(orient="records"))


def load_industry_summary() -> pd.DataFrame:
    if _use_pg_read():
        return _store().load_industry_summary()
    return pd.DataFrame()


def load_oos_excess_map() -> dict[str, float]:
    if _use_pg_read():
        return _store().load_oos_excess_map()
    return {}


def load_equity_date_spans() -> pd.DataFrame:
    if _use_pg_read():
        return _store().load_equity_date_spans()
    return pd.DataFrame()


def save_app_state(key: str, payload: Any) -> None:
    if _use_pg_write():
        _store().save_app_state(key, payload)


def load_app_state(key: str) -> Any | None:
    if _use_pg_read():
        return _store().load_app_state(key)
    return None


def latest_bar_date(symbol: str) -> date | None:
    if _use_pg_read():
        return _store().max_daily_bar_date(symbol)
    return None


def symbol_data_ready(symbol: str, *, require_features: bool = True) -> bool:
    sym = str(symbol).zfill(6)
    if _use_pg_read():
        store = _store()
        if not store.symbol_has_daily_bars(sym):
            return False
        # 持久特征以 minute_daily 为准；feature_matrix / state_features 运行时重建
        if require_features and not store.has_symbol_series(sym, SERIES_MINUTE_DAILY):
            return False
        return True
    return False


def count_data_ready(*, require_features: bool = True) -> int:
    if _use_pg_read():
        return _store().count_data_ready(require_features=require_features)
    return 0


def count_analyzed() -> int:
    if _use_pg_read():
        return _store().count_analyzed()
    return 0


def symbol_is_analyzed(symbol: str) -> bool:
    sym = str(symbol).zfill(6)
    if _use_pg_read():
        return _store().symbol_is_analyzed(sym)
    return False


def save_portfolio(portfolio_id: str, name: str, metadata: dict, equity: pd.DataFrame) -> None:
    if _use_pg_write():
        _store().save_portfolio(portfolio_id, name, metadata, equity)


def list_portfolios() -> list[dict[str, Any]]:
    if _use_pg_read():
        return _store().list_portfolios()
    return []


def load_portfolio_equity(portfolio_id: str) -> pd.DataFrame:
    if _use_pg_read():
        return _store().load_portfolio_equity(portfolio_id)
    return pd.DataFrame()


def load_portfolio_metadata(portfolio_id: str) -> dict | None:
    if _use_pg_read():
        return _store().load_portfolio_metadata(portfolio_id)
    return None


def load_market_series_df(name: str) -> pd.DataFrame:
    if _use_pg_read():
        return _store().load_market_series_df(name)
    return pd.DataFrame()


def ping() -> bool:
    if not pg_enabled():
        return False
    return _store().ping()


def load_kline_batch(symbols: list[str], *, limit: int | None = 90) -> dict[str, pd.DataFrame]:
    if not _use_pg_read() or not symbols:
        return {}
    return _store().load_kline_batch(symbols, limit=limit)


def init_schema() -> None:
    _store().init_schema()

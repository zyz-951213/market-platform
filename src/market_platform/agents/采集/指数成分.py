from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_MAX_AGE_HOURS = 24 * 7  # 成分调整不频繁，缓存一周

# 中证指数公司公开成分；akshare index_stock_cons_csindex(symbol=…)
INDEX_UNIVERSES: dict[str, dict[str, Any]] = {
    "hs300": {
        "id": "hs300",
        "name": "沪深300",
        "code": "sh000300",
        "csindex_symbol": "000300",
        "min_n": 100,
        "cache_key": "hs300_constituents",
    },
    "csi1000": {
        "id": "csi1000",
        "name": "中证1000",
        "code": "sh000852",
        "csindex_symbol": "000852",
        "min_n": 500,
        "cache_key": "csi1000_constituents",
    },
    "csi2000": {
        "id": "csi2000",
        "name": "中证2000",
        "code": "sh932000",
        "csindex_symbol": "932000",
        "min_n": 1000,
        "cache_key": "csi2000_constituents",
    },
}


def _cache_age_hours(payload: dict[str, Any]) -> float | None:
    raw = payload.get("fetched_at")
    if not raw:
        return None
    try:
        ts = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    return max(0.0, (now - ts.astimezone(timezone.utc)).total_seconds() / 3600.0)


def fetch_index_constituents(csindex_symbol: str, *, min_n: int = 50) -> list[str]:
    """拉取中证指数最新成分代码（6 位）。"""
    import akshare as ak

    df = ak.index_stock_cons_csindex(symbol=str(csindex_symbol))
    col = "成分券代码" if "成分券代码" in df.columns else ("品种代码" if "品种代码" in df.columns else None)
    if col is None:
        raise ValueError(f"unexpected cons columns for {csindex_symbol}: {list(df.columns)}")
    symbols = sorted({str(x).zfill(6) for x in df[col].tolist() if str(x).strip()})
    if len(symbols) < min_n:
        raise ValueError(f"{csindex_symbol} cons too few: {len(symbols)} < {min_n}")
    return symbols


def load_index_constituents(
    index_id: str,
    *,
    max_age_hours: float = _DEFAULT_MAX_AGE_HOURS,
    refresh: bool = False,
) -> set[str]:
    """按指数 id 加载成分集合；带 app_state 缓存。"""
    from market_platform.agents.存储 import data_access as store

    meta = INDEX_UNIVERSES.get(index_id)
    if meta is None:
        raise KeyError(f"unknown index universe: {index_id}")

    cache_key = str(meta["cache_key"])
    cached = store.load_app_state(cache_key)
    if isinstance(cached, dict) and not refresh:
        syms = cached.get("symbols") or []
        age = _cache_age_hours(cached)
        min_n = int(meta["min_n"])
        if isinstance(syms, list) and len(syms) >= min_n and (age is None or age <= max_age_hours):
            return {str(s).zfill(6) for s in syms}

    try:
        symbols = fetch_index_constituents(str(meta["csindex_symbol"]), min_n=int(meta["min_n"]))
    except Exception:
        logger.warning("Fetch %s constituents failed; fallback to cache", index_id, exc_info=True)
        if isinstance(cached, dict) and isinstance(cached.get("symbols"), list):
            return {str(s).zfill(6) for s in cached["symbols"]}
        return set()

    payload = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "as_of": datetime.now().date().isoformat(),
        "index_id": index_id,
        "csindex_symbol": meta["csindex_symbol"],
        "n": len(symbols),
        "symbols": symbols,
    }
    try:
        store.save_app_state(cache_key, payload)
    except Exception:
        logger.warning("Persist %s constituents cache failed", index_id, exc_info=True)
    return set(symbols)


def load_hs300_constituents(
    *,
    max_age_hours: float = _DEFAULT_MAX_AGE_HOURS,
    refresh: bool = False,
) -> set[str]:
    """兼容旧接口。"""
    return load_index_constituents("hs300", max_age_hours=max_age_hours, refresh=refresh)


def list_index_universes() -> list[dict[str, str]]:
    return [
        {
            "id": str(m["id"]),
            "name": str(m["name"]),
            "code": str(m["code"]),
            "label": f"{m['name']}同行业（成分市值加权满仓）",
        }
        for m in INDEX_UNIVERSES.values()
    ]

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from market_platform.agents.分析.批处理.增量 import load_universe_meta
from market_platform.agents.存储 import data_access as store
from market_platform.agents.采集.股票池 import infer_market

_SHANGHAI = ZoneInfo("Asia/Shanghai")
_SUSPENDED_STATE_KEY = "suspended_symbols"


@dataclass
class SymbolFreshness:
    symbol: str
    market: str
    name: str
    sector: str
    latest_bar_date: date | None
    is_ready: bool
    is_stale: bool
    reason: str = ""
    is_suspended: bool = False


def _iter_symbol_rows(data_root: Path) -> list[tuple[str, str, str, str]]:
    registry = store.load_symbols_registry()
    if not registry.empty:
        rows: list[tuple[str, str, str, str]] = []
        for row in registry.itertuples(index=False):
            sym = str(row.symbol).zfill(6)
            rows.append(
                (
                    sym,
                    infer_market(sym) or str(getattr(row, "market", "") or ""),
                    str(getattr(row, "name", "") or ""),
                    str(getattr(row, "sector", "") or getattr(row, "sector_l3", "") or ""),
                )
            )
        return rows

    universe = load_universe_meta(data_root)
    if universe.empty:
        return []
    return [
        (
            str(row.symbol).zfill(6),
            str(getattr(row, "market", "") or infer_market(str(row.symbol).zfill(6))),
            str(getattr(row, "name", "") or ""),
            str(getattr(row, "sector", "") or ""),
        )
        for row in universe.itertuples(index=False)
    ]


def _parse_iso_date(value: object) -> date | None:
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def load_suspended_map() -> dict[str, dict]:
    payload = store.load_app_state(_SUSPENDED_STATE_KEY)
    if not isinstance(payload, dict):
        return {}
    out: dict[str, dict] = {}
    for key, meta in payload.items():
        sym = str(key).zfill(6)
        if isinstance(meta, dict):
            out[sym] = meta
        else:
            out[sym] = {"as_of": str(meta)}
    return out


def save_suspended_map(payload: dict[str, dict]) -> None:
    store.save_app_state(_SUSPENDED_STATE_KEY, payload)


def mark_suspended(
    symbol: str,
    *,
    as_of: date | None,
    expected_date: date,
    name: str = "",
) -> None:
    """拉取确认无更新日线时登记停牌：不算待更新，界面标注停牌。"""
    if as_of is None:
        return
    sym = str(symbol).zfill(6)
    data = load_suspended_map()
    data[sym] = {
        "as_of": as_of.isoformat(),
        "expected_when_marked": expected_date.isoformat(),
        "name": name or str(data.get(sym, {}).get("name") or ""),
        "marked_at": datetime.now(_SHANGHAI).isoformat(timespec="seconds"),
    }
    save_suspended_map(data)


def clear_suspended(symbol: str) -> None:
    sym = str(symbol).zfill(6)
    data = load_suspended_map()
    if sym not in data:
        return
    data.pop(sym, None)
    save_suspended_map(data)


def is_marked_suspended(symbol: str, *, expected_date: date, latest: date | None = None) -> bool:
    """已登记停牌，且尚未复牌（日线仍落后于目标交易日）。"""
    sym = str(symbol).zfill(6)
    meta = load_suspended_map().get(sym)
    if not meta:
        return False
    as_of = _parse_iso_date(meta.get("as_of"))
    if latest is not None and latest >= expected_date:
        return False
    if as_of is not None and latest is not None and latest > as_of:
        # 有了比登记时更新的日线但未到目标日：可能部分恢复，仍按停牌展示，直到追上目标日
        return latest < expected_date
    return latest is None or latest < expected_date


def sync_suspension_after_fetch(
    symbol: str,
    *,
    expected_date: date,
    latest: date | None,
    name: str = "",
) -> str | None:
    """拉取后同步停牌标记。返回 suspended / resumed / None。"""
    if latest is not None and latest >= expected_date:
        if load_suspended_map().get(str(symbol).zfill(6)):
            clear_suspended(symbol)
            return "resumed"
        return None
    if latest is None:
        return None
    mark_suspended(symbol, as_of=latest, expected_date=expected_date, name=name)
    return "suspended"


def _needs_suspend_recheck(meta: dict, *, expected_date: date) -> bool:
    """目标交易日已推进时，允许再拉一次以探测复牌。"""
    marked_expected = _parse_iso_date(meta.get("expected_when_marked"))
    if marked_expected is None:
        return True
    return expected_date > marked_expected


def scan_symbol_freshness(
    data_root: Path,
    *,
    expected_date: date,
    limit: int | None = None,
) -> list[SymbolFreshness]:
    suspended_map = load_suspended_map()
    items: list[SymbolFreshness] = []
    for sym, market, name, sector in _iter_symbol_rows(data_root):
        ready = store.symbol_data_ready(sym, require_features=True)
        latest = store.latest_bar_date(sym)
        if latest is not None and latest >= expected_date and sym in suspended_map:
            clear_suspended(sym)
            suspended_map.pop(sym, None)
        if not ready:
            stale, reason, halted = True, "missing", False
        elif latest is None:
            stale, reason, halted = True, "no_daily", False
        elif latest >= expected_date:
            stale, reason, halted = False, "ok", False
        elif sym in suspended_map:
            # 停牌：数据已对齐到最后交易日，不算待更新
            stale, reason, halted = False, "suspended", True
        else:
            stale, reason, halted = True, "outdated", False
        items.append(
            SymbolFreshness(
                symbol=sym,
                market=market,
                name=name,
                sector=sector,
                latest_bar_date=latest,
                is_ready=ready,
                is_stale=stale,
                reason=reason,
                is_suspended=halted,
            )
        )
        if limit is not None and len(items) >= limit:
            break
    return items


def discover_fetch_candidates(
    data_root: Path,
    *,
    expected_date: date,
    limit: int | None = None,
) -> list[tuple[str, str, str, str]]:
    suspended_map = load_suspended_map()
    stale_outdated: list[tuple[str, str, str, str]] = []
    stale_missing: list[tuple[str, str, str, str]] = []
    recheck_suspended: list[tuple[str, str, str, str]] = []
    for sym, market, name, sector in _iter_symbol_rows(data_root):
        ready = store.symbol_data_ready(sym, require_features=True)
        latest = store.latest_bar_date(sym)
        if ready and latest is not None and latest >= expected_date:
            continue
        item = (sym, market or infer_market(sym), name, sector)
        meta = suspended_map.get(sym)
        if meta and ready and latest is not None:
            if _needs_suspend_recheck(meta, expected_date=expected_date):
                recheck_suspended.append(item)
            # 目标日未变：跳过，避免反复占用拉取配额
            continue
        if not ready or latest is None:
            stale_missing.append(item)
        else:
            stale_outdated.append(item)
        if limit is not None and len(stale_outdated) + len(stale_missing) >= limit:
            break
    combined = stale_outdated + stale_missing + recheck_suspended
    return combined[:limit] if limit is not None else combined


def summarize_freshness(data_root: Path, *, expected_date: date) -> dict:
    rows = scan_symbol_freshness(data_root, expected_date=expected_date)
    if not rows:
        return {
            "tracked": 0,
            "ready": 0,
            "stale": 0,
            "missing": 0,
            "suspended": 0,
            "up_to_date": 0,
            "latest_bar_date": None,
            "expected_trading_day": expected_date.isoformat(),
        }
    ready = [r for r in rows if r.is_ready]
    stale = [r for r in rows if r.is_stale]
    missing = [r for r in rows if r.reason == "missing"]
    suspended = [r for r in rows if r.is_suspended]
    dates = [r.latest_bar_date for r in ready if r.latest_bar_date is not None]
    return {
        "tracked": len(rows),
        "ready": len(ready),
        "stale": len(stale),
        "missing": len(missing),
        "suspended": len(suspended),
        "up_to_date": len(ready) - len(stale),
        "latest_bar_date": max(dates).isoformat() if dates else None,
        "expected_trading_day": expected_date.isoformat(),
    }


def load_status_file(data_root: Path) -> dict:
    payload = store.load_app_state("auto_update_status")
    return payload if isinstance(payload, dict) else {}


def save_status_file(data_root: Path, payload: dict) -> Path:
    store.save_app_state("auto_update_status", payload)
    return Path("auto_update_status")

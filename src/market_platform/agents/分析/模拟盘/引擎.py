"""每日 Top N 模拟盘：T 日收盘信号 → T+1 全换仓执行。"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

import pandas as pd
from market_platform.agents.settings import AppConfig
from market_platform.agents.分析.回测.回测引擎 import BacktestFriction, _trade_cost
from market_platform.agents.分析.回测.风控 import constrain_portfolio_weights
from market_platform.agents.存储 import data_access as store
from market_platform.agents.接口.组合 import _recommend_score, load_universe_df
from market_platform.agents.核心.任务.交易日历 import (
    is_trading_day,
    iter_trading_days,
    latest_expected_trading_day,
    next_trading_day as _calendar_next_trading_day,
    prev_trading_day,
)
from market_platform.agents.核心.通知.分发 import dispatch_notification
from market_platform.agents.核心.路径.项目 import data_dir, profile_path

logger = logging.getLogger(__name__)

PAPER_STATE_KEY = "paper_portfolio_v1"
_bars_cache: dict[str, pd.DataFrame] = {}
_PRICE_TICK = Decimal("0.01")  # A 股成交价最小变动单位：分
_MONEY_TICK = Decimal("0.01")


def _round_trade_price(price: float | Decimal) -> float:
    """A 股成交价按分取整（最多两位小数），四舍五入。"""
    return float(Decimal(str(price)).quantize(_PRICE_TICK, rounding=ROUND_HALF_UP))


def _round_money(amount: float | Decimal) -> float:
    """金额/费用按分取整。"""
    return float(Decimal(str(amount)).quantize(_MONEY_TICK, rounding=ROUND_HALF_UP))


def _clear_bars_cache() -> None:
    _bars_cache.clear()


def _load_bars_cached(symbol: str) -> pd.DataFrame:
    """按标的缓存全量日线，避免回放逐日重复查库。"""
    sym = str(symbol).zfill(6)
    cached = _bars_cache.get(sym)
    if cached is not None:
        return cached
    bars = store.load_daily_bars(sym)
    if bars is None or bars.empty:
        _bars_cache[sym] = pd.DataFrame()
        return _bars_cache[sym]
    out = bars.copy()
    out["date"] = pd.to_datetime(out["date"]).dt.date
    out = out.sort_values("date").reset_index(drop=True)
    _bars_cache[sym] = out
    return out


def _paper_file() -> Any:
    return data_dir() / "paper_portfolio.json"


def _default_state(config: AppConfig) -> dict[str, Any]:
    capital = float(config.paper_trading.initial_capital or config.backtest.initial_capital)
    return {
        "version": 1,
        "updated_at": None,
        "initial_capital": capital,
        "cash": capital,
        "nav": capital,
        "signal_date": None,
        "last_trade_date": None,
        "last_nav_date": None,
        "positions": [],
        "pending": None,
        "nav_curve": [],
        "trades": [],
        "pick_history": [],
        "metrics": {"total_return": 0.0, "max_drawdown": 0.0, "trade_count": 0},
    }


def _sync_cash_ledger(state: dict[str, Any]) -> None:
    """现金 = 初始资金 + 累计成交净额，修正历史 reserved_cash 重复计入。"""
    initial = float(state.get("initial_capital", 0) or 0)
    ledger = initial + sum(float(t.get("net", 0) or 0) for t in state.get("trades") or [])
    state["cash"] = ledger


def load_paper_state(config: AppConfig | None = None) -> dict[str, Any]:
    config = config or AppConfig.from_yaml(profile_path("默认.yaml"))
    payload = store.load_app_state(PAPER_STATE_KEY)
    if isinstance(payload, dict) and payload.get("version") == 1:
        state = {**_default_state(config), **payload}
        _sync_cash_ledger(state)
        state["positions"] = _merge_duplicate_positions(state.get("positions") or [])
        return state
    path = _paper_file()
    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                state = {**_default_state(config), **raw}
                _sync_cash_ledger(state)
                state["positions"] = _merge_duplicate_positions(state.get("positions") or [])
                return state
        except json.JSONDecodeError:
            pass
    return _default_state(config)


def save_paper_state(state: dict[str, Any]) -> dict[str, Any]:
    out = dict(state)
    out["updated_at"] = datetime.now().isoformat(timespec="seconds")
    store.save_app_state(PAPER_STATE_KEY, out)
    path = _paper_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def next_trading_day(d: date) -> date:
    """下一 A 股交易日（含节假日日历）。"""
    return _calendar_next_trading_day(d)


def _limit_pct_for_symbol(symbol: str, default: float = 0.098) -> float:
    """按板块估算涨跌停幅度：主板约 10%，创业/科创约 20%。"""
    sym = str(symbol).zfill(6)
    if sym.startswith(("300", "301", "688", "689")):
        return 0.198
    return float(default)


def _bar_tradable(bar: dict[str, float] | None) -> bool:
    """有有效收盘且非零成交量（停牌 volume=0 不可成交）。"""
    if not bar:
        return False
    if float(bar.get("close", 0) or 0) <= 0:
        return False
    vol = bar.get("volume")
    if vol is not None and float(vol) <= 0:
        return False
    return True


def _parse_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    text = str(value).strip()[:10]
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def _date_str(d: date) -> str:
    return d.isoformat()


def select_top_picks(
    config: AppConfig,
    *,
    top_n: int | None = None,
    output_root: Any = None,
    current_symbols: list[str] | None = None,
) -> list[dict[str, Any]]:
    """按综合评分选取明日买入候选；对现有持仓做缓冲区降换手。"""
    pt = config.paper_trading
    n = int(top_n or pt.top_n)
    buffer = max(int(getattr(pt, "rebalance_buffer", 0) or 0), 0)
    df = load_universe_df(output_root)
    if df.empty:
        return []

    df = df.copy()
    df["recommend_score"] = df.apply(_recommend_score, axis=1)

    if pt.min_suggested_position > 0 and "suggested_position" in df.columns:
        df = df[pd.to_numeric(df["suggested_position"], errors="coerce").fillna(0) >= pt.min_suggested_position]
    if pt.exclude_states and "latest_state" in df.columns:
        df = df[~df["latest_state"].astype(str).isin(pt.exclude_states)]
    if pt.min_recommend_score > 0:
        df = df[df["recommend_score"] >= pt.min_recommend_score]

    ranked = df.sort_values("recommend_score", ascending=False).reset_index(drop=True)
    if ranked.empty:
        return []

    pool = ranked.head(n + buffer)
    current = {str(s).zfill(6) for s in (current_symbols or []) if str(s).strip()}
    selected: list[str] = []
    if current and buffer > 0:
        # 先保留仍在缓冲池内的旧持仓
        for sym in pool["symbol"].astype(str).str.zfill(6).tolist():
            if sym in current and sym not in selected:
                selected.append(sym)
            if len(selected) >= n:
                break
        # 再用高分票补齐
        for sym in pool["symbol"].astype(str).str.zfill(6).tolist():
            if sym not in selected:
                selected.append(sym)
            if len(selected) >= n:
                break
    else:
        selected = pool["symbol"].astype(str).str.zfill(6).head(n).tolist()

    pick_df = ranked[ranked["symbol"].astype(str).str.zfill(6).isin(selected)].copy()
    # 保持评分序，但优先稳定集合
    order = {s: i for i, s in enumerate(selected)}
    pick_df["_ord"] = pick_df["symbol"].astype(str).str.zfill(6).map(order)
    pick_df = pick_df.sort_values("_ord")

    picks: list[dict[str, Any]] = []
    for _, row in pick_df.iterrows():
        sector = str(row.get("sector_l3") or row.get("sector") or "")
        picks.append(
            {
                "symbol": str(row["symbol"]).zfill(6),
                "name": str(row.get("name", "") or ""),
                "sector": sector,
                "weight": 1.0 / max(len(pick_df), 1),
                "latest_state": str(row.get("latest_state", "") or ""),
                "suggested_position": float(row.get("suggested_position", 0) or 0),
                "excess_return": float(row.get("excess_return", 0) or 0),
                "oos_excess_return": float(row.get("oos_excess_return", 0) or 0),
                "strategy_return": float(row.get("strategy_return", 0) or 0),
                "strategy_sharpe": float(row.get("strategy_sharpe", 0) or 0),
                "recommend_score": float(row.get("recommend_score", 0) or 0),
            }
        )

    if pt.apply_risk_constraints:
        picks = constrain_portfolio_weights(picks, config.risk)
    return _enrich_pick_names(picks)


def _enrich_pick_names(picks: list[dict[str, Any]], state: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    if not picks:
        return picks
    symbols = {str(p.get("symbol", "")).zfill(6) for p in picks}
    name_map = _build_symbol_name_map(symbols, state)
    out: list[dict[str, Any]] = []
    for p in picks:
        sym = str(p.get("symbol", "")).zfill(6)
        out.append({**p, "symbol": sym, "name": str(p.get("name") or name_map.get(sym) or "").strip()})
    return out


def _get_bar_on_date(
    symbol: str,
    trade_date: date,
    *,
    exact: bool = False,
) -> dict[str, float] | None:
    """取前复权收盘价。

    exact=True：仅允许 trade_date 当日 K 线（成交用，禁止回退到前收，避免成交价错日）。
    exact=False：无当日则回退到最近前收（盯市用）。
    """
    bars = _load_bars_cached(symbol)
    if bars.empty:
        return None
    if exact:
        hit = bars[bars["date"] == trade_date]
        if hit.empty:
            return None
        row = hit.iloc[-1]
        idx = int(hit.index[-1])
    else:
        prior = bars[bars["date"] <= trade_date]
        if prior.empty:
            return None
        row = prior.iloc[-1]
        idx = int(prior.index[-1])
    close = float(row.get("close", 0) or 0)
    if close <= 0:
        return None
    vol = float(row.get("volume", 0) or 0)
    prev_close = None
    if idx > 0:
        prev_close = float(bars.iloc[idx - 1].get("close", close) or close)
    day_ret = (close / prev_close - 1.0) if prev_close and prev_close > 0 else 0.0
    return {"close": close, "volume": vol, "day_return": day_ret, "bar_date": str(row["date"])}


def _merge_duplicate_positions(positions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """同标的持仓合并股数与加权成本（跌停未卖后又买入时可能重复）。"""
    merged: dict[str, dict[str, Any]] = {}
    for p in positions or []:
        sym = str(p.get("symbol", "")).zfill(6)
        if not sym or sym == "000000":
            continue
        shares = float(p.get("shares", 0) or 0)
        if shares <= 0:
            continue
        cost = float(p.get("avg_cost", 0) or 0)
        if sym not in merged:
            merged[sym] = {**p, "symbol": sym, "shares": shares, "avg_cost": cost}
            continue
        old = merged[sym]
        old_sh = float(old.get("shares", 0) or 0)
        old_cost = float(old.get("avg_cost", 0) or 0)
        total = old_sh + shares
        if total > 0:
            old["avg_cost"] = (old_cost * old_sh + cost * shares) / total
        old["shares"] = total
        if p.get("last_price"):
            old["last_price"] = p.get("last_price")
        if p.get("name") and not old.get("name"):
            old["name"] = p.get("name")
    return list(merged.values())


def _max_drawdown_from_curve(curve: list[dict[str, Any]], initial: float) -> float:
    peak = float(initial) if initial > 0 else 0.0
    max_dd = 0.0
    if peak <= 0 and curve:
        peak = float(curve[0].get("nav", 0) or 0)
    for pt in curve:
        nav = float(pt.get("nav", 0) or 0)
        peak = max(peak, nav)
        dd = nav / peak - 1.0 if peak > 0 else 0.0
        max_dd = min(max_dd, dd)
    return max_dd


def _position_value(positions: list[dict], prices: dict[str, float]) -> float:
    total = 0.0
    for p in positions:
        sym = str(p["symbol"]).zfill(6)
        price = prices.get(sym, float(p.get("last_price", 0) or 0))
        shares = float(p.get("shares", 0) or 0)
        total += shares * price
    return total


def mark_to_market(state: dict[str, Any], trade_date: date) -> dict[str, Any]:
    positions = _merge_duplicate_positions(state.get("positions") or [])
    state["positions"] = positions
    initial = float(state.get("initial_capital", 1) or 1)

    prices: dict[str, float] = {}
    for p in positions:
        sym = str(p["symbol"]).zfill(6)
        bar = _get_bar_on_date(sym, trade_date)
        if bar:
            mark = _round_trade_price(bar["close"])
            prices[sym] = mark
            p["last_price"] = mark
            p["price_date"] = bar.get("bar_date")
        if p.get("avg_cost") is not None:
            p["avg_cost"] = _round_trade_price(float(p.get("avg_cost") or 0))

    stock_value = _position_value(positions, prices) if positions else 0.0
    cash = float(state.get("cash", 0) or 0)
    nav = cash + stock_value
    state["nav"] = nav
    state["positions"] = positions
    state["last_nav_date"] = _date_str(trade_date)
    state.setdefault("metrics", {})["total_return"] = nav / initial - 1.0 if initial else 0.0

    curve = list(state.get("nav_curve") or [])
    # 曲线起点锚定初始资金，使对比图累计收益与「总收益」一致
    if not curve:
        seed = prev_trading_day(trade_date)
        curve.append({"date": _date_str(seed), "nav": initial, "daily_return": 0.0})

    if curve and curve[-1].get("date") == _date_str(trade_date):
        prev_nav = float(curve[-2]["nav"]) if len(curve) >= 2 else initial
        daily_ret = nav / prev_nav - 1.0 if prev_nav > 0 else 0.0
        curve[-1] = {"date": _date_str(trade_date), "nav": nav, "daily_return": daily_ret}
    else:
        prev_nav = float(curve[-1]["nav"]) if curve else initial
        daily_ret = nav / prev_nav - 1.0 if prev_nav > 0 else 0.0
        curve.append({"date": _date_str(trade_date), "nav": nav, "daily_return": daily_ret})
    state["nav_curve"] = curve[-500:]
    state["metrics"]["max_drawdown"] = _max_drawdown_from_curve(state["nav_curve"], initial)
    return state


def _normalize_pending(state: dict[str, Any]) -> None:
    """若换仓日已有成交记录，标记 pending 已执行，避免重复全换仓。"""
    pending = state.get("pending")
    if not pending or pending.get("executed"):
        return
    exec_date = _parse_date(pending.get("execute_date"))
    last_trade = _parse_date(state.get("last_trade_date"))
    if exec_date and last_trade and last_trade >= exec_date:
        pending["executed"] = True
        pending["executed_at"] = _date_str(last_trade)
        state["pending"] = pending


def _replay_positions_through(state: dict[str, Any], as_of: date) -> tuple[float, dict[str, dict[str, Any]]]:
    """按成交记录回放至指定日收盘后的持仓（卖出先于买入；同标的买入累加）。"""
    cash = float(state.get("initial_capital", 0) or 0)
    positions: dict[str, dict[str, Any]] = {}
    trades = sorted(
        state.get("trades") or [],
        key=lambda t: (str(t.get("date", "")), 0 if t.get("side") == "sell" else 1),
    )
    for t in trades:
        td = _parse_date(t.get("date"))
        if td is None or td > as_of:
            continue
        sym = str(t.get("symbol", "")).zfill(6)
        side = str(t.get("side", ""))
        cash += float(t.get("net", 0) or 0)
        if side == "sell":
            positions.pop(sym, None)
        elif side == "buy":
            add_sh = float(t.get("shares", 0) or 0)
            px = float(t.get("price", 0) or 0)
            if add_sh <= 0:
                continue
            if sym in positions:
                old = positions[sym]
                old_sh = float(old.get("shares", 0) or 0)
                old_cost = float(old.get("avg_cost", 0) or 0)
                total = old_sh + add_sh
                avg = (old_cost * old_sh + px * add_sh) / total if total > 0 else px
                old["avg_cost"] = _round_trade_price(avg)
                old["shares"] = total
                old["last_price"] = _round_trade_price(px)
                if t.get("name") and not old.get("name"):
                    old["name"] = t.get("name", "")
            else:
                positions[sym] = {
                    "symbol": sym,
                    "name": t.get("name", ""),
                    "shares": add_sh,
                    "avg_cost": _round_trade_price(px),
                    "last_price": _round_trade_price(px),
                }
    return cash, positions


def _nav_from_positions(cash: float, positions: dict[str, dict[str, Any]], trade_date: date) -> float:
    total = cash
    for p in positions.values():
        sym = str(p["symbol"]).zfill(6)
        bar = _get_bar_on_date(sym, trade_date)
        price = float(bar["close"]) if bar else float(p.get("last_price", 0) or 0)
        total += float(p.get("shares", 0) or 0) * price
    return total


def replay_nav_curve(state: dict[str, Any], *, end_date: date | None = None) -> dict[str, Any]:
    """根据成交记录逐日回放净值曲线，填补缺失交易日。"""
    trades = state.get("trades") or []
    if not trades:
        return state

    end_date = end_date or latest_expected_trading_day()
    trade_dates = [_parse_date(t.get("date")) for t in trades]
    trade_dates = [d for d in trade_dates if d is not None]
    if not trade_dates:
        return state
    first = min(trade_dates)

    initial = float(state.get("initial_capital", 0) or 0)
    seed = prev_trading_day(first)
    curve: list[dict[str, Any]] = [{"date": _date_str(seed), "nav": initial, "daily_return": 0.0}]
    prev_nav = initial
    for day in iter_trading_days(first, end_date):
        cash, positions = _replay_positions_through(state, day)
        nav = _nav_from_positions(cash, positions, day) if positions else cash
        daily_ret = nav / prev_nav - 1.0 if prev_nav > 0 else 0.0
        curve.append({"date": _date_str(day), "nav": nav, "daily_return": daily_ret})
        prev_nav = nav

    state["nav_curve"] = curve[-500:]
    if curve:
        state["nav"] = curve[-1]["nav"]
        state["last_nav_date"] = curve[-1]["date"]
    initial_cap = float(state.get("initial_capital", 1) or 1)
    state.setdefault("metrics", {})["total_return"] = state["nav"] / initial_cap - 1.0 if initial_cap else 0.0
    state["metrics"]["max_drawdown"] = _max_drawdown_from_curve(curve, initial_cap)
    return state


def _fetch_symbol_name_tencent(symbol: str) -> str:
    from market_platform.agents.采集.名称 import fetch_symbol_name_tencent

    return fetch_symbol_name_tencent(symbol)


def _build_symbol_name_map(symbols: set[str], state: dict[str, Any] | None = None) -> dict[str, str]:
    """合并多来源股票名称，缺失时回退腾讯行情接口。"""
    from market_platform.agents.采集.名称 import build_symbol_name_map

    extra: dict[str, str] = {}
    if state:
        for p in state.get("positions") or []:
            sym = str(p.get("symbol") or "").zfill(6)
            label = str(p.get("name") or "").strip()
            if sym and label:
                extra[sym] = label
        for t in state.get("trades") or []:
            sym = str(t.get("symbol") or "").zfill(6)
            label = str(t.get("name") or "").strip()
            if sym and label and sym not in extra:
                extra[sym] = label
        for p in (state.get("pending") or {}).get("holdings") or []:
            sym = str(p.get("symbol") or "").zfill(6)
            label = str(p.get("name") or "").strip()
            if sym and label and sym not in extra:
                extra[sym] = label
    return build_symbol_name_map(symbols, extra=extra, fetch_missing=True, persist=True)


def _apply_symbol_names(state: dict[str, Any], name_map: dict[str, str]) -> None:
    for p in state.get("positions") or []:
        sym = str(p.get("symbol", "")).zfill(6)
        if not str(p.get("name") or "").strip() and name_map.get(sym):
            p["name"] = name_map[sym]
    for t in state.get("trades") or []:
        sym = str(t.get("symbol", "")).zfill(6)
        if not str(t.get("name") or "").strip() and name_map.get(sym):
            t["name"] = name_map[sym]


def enrich_paper_positions(state: dict[str, Any], config: AppConfig | None = None) -> dict[str, Any]:
    """补全持仓名称/行业/状态/权重，并标注目标仓与遗留仓。"""
    config = config or AppConfig.from_yaml(profile_path("默认.yaml"))
    positions = state.get("positions") or []
    if not positions:
        return state

    symbols = {str(p.get("symbol", "")).zfill(6) for p in positions}
    for t in state.get("trades") or []:
        symbols.add(str(t.get("symbol", "")).zfill(6))
    for p in (state.get("pending") or {}).get("holdings") or []:
        symbols.add(str(p.get("symbol", "")).zfill(6))
    name_map = _build_symbol_name_map(symbols, state)
    _apply_symbol_names(state, name_map)

    df = load_universe_df()
    meta_by_sym: dict[str, dict[str, Any]] = {}
    if not df.empty:
        df = df.copy()
        df["recommend_score"] = df.apply(_recommend_score, axis=1)
        for row in df.itertuples(index=False):
            sym = str(row.symbol).zfill(6)
            meta_by_sym[sym] = {
                "name": str(getattr(row, "name", "") or ""),
                "sector": str(getattr(row, "sector_l3", "") or getattr(row, "sector", "") or ""),
                "latest_state": str(getattr(row, "latest_state", "") or ""),
                "recommend_score": float(getattr(row, "recommend_score", 0) or 0),
            }

    last_trade = str(state.get("last_trade_date") or "")
    target_buys = {
        str(t.get("symbol", "")).zfill(6)
        for t in state.get("trades") or []
        if str(t.get("date", "")) == last_trade and str(t.get("side", "")) == "buy"
    }

    nav = float(state.get("nav") or 0)
    enriched: list[dict[str, Any]] = []
    for p in positions:
        sym = str(p.get("symbol", "")).zfill(6)
        meta = meta_by_sym.get(sym, {})
        shares = float(p.get("shares", 0) or 0)
        last_price = float(p.get("last_price", 0) or 0)
        market_value = shares * last_price
        enriched.append(
            {
                **p,
                "symbol": sym,
                "name": str(p.get("name") or meta.get("name") or name_map.get(sym) or ""),
                "sector": str(p.get("sector") or meta.get("sector") or ""),
                "latest_state": str(p.get("latest_state") or meta.get("latest_state") or ""),
                "recommend_score": p.get("recommend_score") if p.get("recommend_score") is not None else meta.get("recommend_score"),
                "market_value": market_value,
                "weight": market_value / nav if nav > 0 else 0.0,
                "position_tag": "target" if sym in target_buys else "legacy",
            }
        )
    enriched.sort(key=lambda x: float(x.get("weight") or 0), reverse=True)
    state["positions"] = enriched

    pending = state.get("pending")
    if pending and pending.get("holdings"):
        pending["holdings"] = _enrich_pick_names(pending.get("holdings") or [], state)
        state["pending"] = pending
    return state


def enrich_paper_trades(state: dict[str, Any]) -> list[dict[str, Any]]:
    """补全成交记录中的股票名称。"""
    trades = recent_paper_trades(state)
    if not trades:
        return []
    symbols = {str(t.get("symbol", "")).zfill(6) for t in trades}
    name_map = _build_symbol_name_map(symbols, state)
    out: list[dict[str, Any]] = []
    for t in trades:
        sym = str(t.get("symbol", "")).zfill(6)
        out.append({**t, "symbol": sym, "name": str(t.get("name") or name_map.get(sym) or "").strip()})
    return out


def recent_paper_trades(state: dict[str, Any], *, limit: int | None = None) -> list[dict[str, Any]]:
    """最近成交：按日期倒序，同一换仓日内先卖后买。默认返回全部成交记录。"""
    trades = list(state.get("trades") or [])
    if not trades:
        return []
    by_date: dict[str, list[dict[str, Any]]] = {}
    for t in trades:
        by_date.setdefault(str(t.get("date", "")), []).append(t)
    ordered: list[dict[str, Any]] = []
    for d in sorted(by_date.keys(), reverse=True):
        day = by_date[d]
        ordered.extend([t for t in day if str(t.get("side", "")) == "sell"])
        ordered.extend([t for t in day if str(t.get("side", "")) == "buy"])
    if limit is not None and limit > 0:
        return ordered[:limit]
    return ordered


def rewrite_paper_trade_prices(state: dict[str, Any], config: AppConfig) -> dict[str, int]:
    """按成交日收盘价（分位取整）重写历史成交价/成本/净额。"""
    friction = BacktestFriction.from_backtest_config(config.backtest)
    rewritten = 0
    missing = 0
    unchanged = 0
    trades = state.get("trades") or []
    for t in trades:
        td = _parse_date(t.get("date"))
        sym = str(t.get("symbol", "")).zfill(6)
        side = str(t.get("side", ""))
        shares = float(t.get("shares", 0) or 0)
        if td is None or shares <= 0 or side not in {"buy", "sell"}:
            continue
        bar = _get_bar_on_date(sym, td, exact=True)
        if not bar:
            # 停牌/缺 K 线的历史脏数据：回退前收仅用于纠错重写，新成交禁止此路径
            bar = _get_bar_on_date(sym, td, exact=False)
            if not bar:
                missing += 1
                continue
        price = _round_trade_price(bar["close"])
        if price <= 0:
            missing += 1
            continue
        gross = _round_money(shares * price)
        if side == "sell":
            cost = _round_money(_trade_cost(0.0, gross, friction))
            net = _round_money(gross - cost)
        else:
            cost = _round_money(_trade_cost(gross, 0.0, friction))
            net = _round_money(-(gross + cost))
        old_px = float(t.get("price", 0) or 0)
        if (
            abs(old_px - price) < 1e-9
            and abs(float(t.get("cost", 0) or 0) - cost) < 1e-9
            and abs(float(t.get("amount", 0) or 0) - gross) < 1e-9
        ):
            unchanged += 1
            continue
        t["price"] = price
        t["amount"] = gross
        t["cost"] = cost
        t["net"] = net
        rewritten += 1
    state["trades"] = trades
    _sync_cash_ledger(state)
    state["cash"] = _round_money(float(state.get("cash", 0) or 0))
    return {"rewritten": rewritten, "unchanged": unchanged, "missing_bar": missing}


def repair_paper_state(
    config: AppConfig | None = None,
    *,
    fetch_bars: bool = True,
    end_date: date | None = None,
    rewrite_trades: bool = True,
) -> dict[str, Any]:
    """修复模拟盘：补持仓行情、重写成交价、回放净值曲线、同步标记价与 pending 状态。"""
    config = config or AppConfig.from_yaml(profile_path("默认.yaml"))
    end_date = end_date or latest_expected_trading_day()
    state = load_paper_state(config)
    _clear_bars_cache()

    symbols: set[str] = set()
    for p in state.get("positions") or []:
        symbols.add(str(p["symbol"]).zfill(6))
    for p in (state.get("pending") or {}).get("holdings") or []:
        symbols.add(str(p["symbol"]).zfill(6))
    for t in state.get("trades") or []:
        symbols.add(str(t.get("symbol", "")).zfill(6))

    fetched = 0
    if fetch_bars and symbols:
        from market_platform.agents.settings import DataConfig
        from market_platform.agents.采集.股票池 import infer_market
        from market_platform.agents.采集.数据源 import fetch_daily_sina, fetch_daily_tencent

        start = (end_date - timedelta(days=30)).strftime("%Y%m%d")
        end = end_date.strftime("%Y%m%d")
        for sym in sorted(symbols):
            market = infer_market(sym) or "sz"
            existing = store.load_daily_bars(sym)
            last = None
            if not existing.empty:
                existing = existing.copy()
                existing["date"] = pd.to_datetime(existing["date"]).dt.date
                last = existing["date"].max()
            if last is not None and last >= end_date:
                continue
            fetch_start = (last - timedelta(days=7)).strftime("%Y%m%d") if last else start
            cfg = DataConfig(symbol=sym, market=market, start_date=fetch_start, end_date=end)
            try:
                new_daily = fetch_daily_tencent(cfg)
            except Exception:
                try:
                    new_daily = fetch_daily_sina(cfg)
                except Exception:
                    logger.warning("Paper repair fetch failed %s", sym)
                    continue
            if new_daily is None or new_daily.empty:
                continue
            if existing is not None and not existing.empty:
                merged = pd.concat([existing, new_daily], ignore_index=True)
                merged["date"] = pd.to_datetime(merged["date"]).dt.date
                merged = merged.drop_duplicates(subset=["date"], keep="last").sort_values("date")
            else:
                merged = new_daily.copy()
                merged["date"] = pd.to_datetime(merged["date"]).dt.date
            store.save_daily_bars(sym, merged)
            fetched += 1
            _bars_cache.pop(sym, None)

    _sync_cash_ledger(state)
    _normalize_pending(state)
    state["positions"] = _merge_duplicate_positions(state.get("positions") or [])
    trade_fix = {"rewritten": 0, "unchanged": 0, "missing_bar": 0}
    if rewrite_trades:
        trade_fix = rewrite_paper_trade_prices(state, config)
    replay_nav_curve(state, end_date=end_date)
    _, replayed = _replay_positions_through(state, end_date)
    state["positions"] = list(replayed.values())
    mark_to_market(state, end_date)

    pending = state.get("pending") or {}
    if pending and not pending.get("executed"):
        held = [str(p.get("symbol", "")).zfill(6) for p in (state.get("positions") or [])]
        refreshed = select_top_picks(config, current_symbols=held)
        if refreshed:
            pending["holdings"] = refreshed
            pending["refreshed_at"] = datetime.now().isoformat(timespec="seconds")
            state["pending"] = pending

    enrich_paper_positions(state, config)
    save_paper_state(state)
    return {
        "end_date": _date_str(end_date),
        "symbols_refreshed": fetched,
        "trade_prices": trade_fix,
        "nav": state.get("nav"),
        "last_nav_date": state.get("last_nav_date"),
        "nav_curve_points": len(state.get("nav_curve") or []),
        "total_return": state.get("metrics", {}).get("total_return"),
        "max_drawdown": state.get("metrics", {}).get("max_drawdown"),
        "positions": len(state.get("positions") or []),
        "pending_executed": bool((state.get("pending") or {}).get("executed")),
    }


def catch_up_paper_trading(
    config: AppConfig | None = None,
    *,
    start_date: date,
    end_date: date | None = None,
    notify: bool = False,
) -> list[dict[str, Any]]:
    """补跑缺失的换仓日：按交易日依次执行 pending → 标记 → 生成下一日清单。"""
    config = config or AppConfig.from_yaml(profile_path("默认.yaml"))
    end_date = end_date or latest_expected_trading_day()
    state = load_paper_state(config)
    last_trade = _parse_date(state.get("last_trade_date"))
    pending = state.get("pending") or {}

    if last_trade and (not pending or pending.get("executed")):
        exec_day = next_trading_day(last_trade)
        if exec_day <= end_date:
            picks = select_top_picks(config)
            if picks:
                state["signal_date"] = _date_str(last_trade)
                state["pending"] = {
                    "signal_date": _date_str(last_trade),
                    "execute_date": _date_str(exec_day),
                    "executed": False,
                    "holdings": picks,
                }
                save_paper_state(state)

    results: list[dict[str, Any]] = []
    for day in iter_trading_days(start_date, end_date):
        out = run_paper_trading_cycle(config, trade_date=day, dry_run=False, notify=notify)
        exec_info = out.get("execution") or {}
        results.append(
            {
                "trade_date": _date_str(day),
                "executed": bool(exec_info),
                "sells": exec_info.get("sells") if isinstance(exec_info, dict) else 0,
                "buys": exec_info.get("buys") if isinstance(exec_info, dict) else 0,
                "tomorrow_picks": len(out.get("tomorrow_picks") or []),
            }
        )
    repair_paper_state(config, fetch_bars=True, end_date=end_date)
    return results


def _execute_full_rebalance(
    state: dict[str, Any],
    picks: list[dict[str, Any]],
    trade_date: date,
    config: AppConfig,
) -> dict[str, Any]:
    friction = BacktestFriction.from_backtest_config(config.backtest)
    pt = config.paper_trading
    invest_ratio = min(float(config.risk.max_portfolio_position), 1.0)

    trades: list[dict[str, Any]] = []
    cash = float(state.get("cash", 0) or 0)
    old_positions = _merge_duplicate_positions(state.get("positions") or [])
    unsold: list[dict[str, Any]] = []

    for p in old_positions:
        sym = str(p["symbol"]).zfill(6)
        shares = float(p.get("shares", 0) or 0)
        if shares <= 0:
            continue
        bar = _get_bar_on_date(sym, trade_date, exact=True)
        if not _bar_tradable(bar):
            logger.warning("Paper sell skipped %s: no tradable bar on %s", sym, trade_date)
            unsold.append(p)
            continue
        lim = _limit_pct_for_symbol(sym, friction.limit_pct)
        if pt.respect_limit_up_down and friction.apply_limit and bar["day_return"] <= -lim:
            logger.info("Paper sell blocked %s limit down on %s", sym, trade_date)
            unsold.append(p)
            continue
        # 成交价 = 当日收盘价（分位）；滑点只在 _trade_cost 计一次
        price = _round_trade_price(bar["close"])
        gross = _round_money(shares * price)
        cost = _round_money(_trade_cost(0.0, gross, friction))
        net = _round_money(gross - cost)
        cash += net
        trades.append(
            {
                "date": _date_str(trade_date),
                "side": "sell",
                "symbol": sym,
                "name": p.get("name", ""),
                "shares": shares,
                "price": price,
                "amount": gross,
                "cost": cost,
                "net": net,
            }
        )

    investable = cash * invest_ratio
    new_positions: list[dict[str, Any]] = list(unsold)
    unsold_syms = {str(p["symbol"]).zfill(6) for p in unsold}

    for pick in picks:
        sym = str(pick["symbol"]).zfill(6)
        weight = float(pick.get("weight", 0) or 0)
        if weight <= 0:
            continue
        # 跌停未卖出的标的保留旧仓，不再叠加买入，避免双持仓
        if sym in unsold_syms:
            continue
        bar = _get_bar_on_date(sym, trade_date, exact=True)
        if not _bar_tradable(bar):
            logger.warning("Paper buy skipped %s: no tradable bar on %s", sym, trade_date)
            continue
        lim = _limit_pct_for_symbol(sym, friction.limit_pct)
        if pt.respect_limit_up_down and friction.apply_limit and bar["day_return"] >= lim:
            logger.info("Paper buy blocked %s limit up on %s", sym, trade_date)
            continue
        buy_price = _round_trade_price(bar["close"])
        if buy_price <= 0:
            continue
        budget = investable * weight
        shares = int(budget / buy_price / 100) * 100
        if shares <= 0:
            continue
        gross = _round_money(shares * buy_price)
        cost = _round_money(_trade_cost(gross, 0.0, friction))
        total = _round_money(gross + cost)
        if total > cash:
            # 预留成本后再估股数；成本随成交额变化，迭代一次即可
            fee_rate = friction.commission + (friction.slippage if friction.apply_slippage else 0.0)
            shares = int(cash / (buy_price * (1.0 + fee_rate)) / 100) * 100
            if shares <= 0:
                continue
            gross = _round_money(shares * buy_price)
            cost = _round_money(_trade_cost(gross, 0.0, friction))
            total = _round_money(gross + cost)
            if total > cash:
                continue
        cash -= total
        mark = _round_trade_price(bar["close"])
        new_positions.append(
            {
                "symbol": sym,
                "name": pick.get("name", ""),
                "sector": pick.get("sector", ""),
                "weight": weight,
                "shares": float(shares),
                "avg_cost": buy_price,
                "last_price": mark,
                "recommend_score": pick.get("recommend_score"),
                "latest_state": pick.get("latest_state", ""),
            }
        )
        trades.append(
            {
                "date": _date_str(trade_date),
                "side": "buy",
                "symbol": sym,
                "name": pick.get("name", ""),
                "shares": float(shares),
                "price": buy_price,
                "amount": gross,
                "cost": cost,
                "net": -total,
            }
        )

    sells = sum(1 for t in trades if t["side"] == "sell")
    buys = sum(1 for t in trades if t["side"] == "buy")
    # 有旧仓或买入清单却 0 成交（假日/全停牌）→ 中止，避免误推进 last_trade_date
    aborted = (not trades) and (bool(old_positions) or bool(picks))
    if aborted:
        logger.warning("Paper rebalance aborted on %s: no fills", trade_date)
        return {
            "trade_date": _date_str(trade_date),
            "sells": 0,
            "buys": 0,
            "trades": [],
            "positions_count": len(old_positions),
            "aborted": True,
            "reason": "no_fills",
        }

    state["cash"] = _round_money(cash)
    state["positions"] = _merge_duplicate_positions(new_positions)
    if trades:
        state["last_trade_date"] = _date_str(trade_date)
        state["trades"] = (state.get("trades") or []) + trades
        state["trades"] = state["trades"][-2000:]
        state.setdefault("metrics", {})["trade_count"] = len(state["trades"])
    mark_to_market(state, trade_date)
    return {
        "trade_date": _date_str(trade_date),
        "sells": sells,
        "buys": buys,
        "trades": trades,
        "positions_count": len(state["positions"]),
        "aborted": False,
    }


def build_paper_digest(
    state: dict[str, Any],
    *,
    picks: list[dict[str, Any]] | None = None,
    execution: dict[str, Any] | None = None,
    signal_date: date | None = None,
) -> tuple[str, str]:
    pt_signal = signal_date or _parse_date(state.get("signal_date"))
    pending = state.get("pending") or {}
    execute_date = pending.get("execute_date") or (next_trading_day(pt_signal).isoformat() if pt_signal else "--")
    signal_str = pt_signal.isoformat() if pt_signal else "--"

    initial = float(state.get("initial_capital", 1) or 1)
    nav = float(state.get("nav", initial) or initial)
    total_ret = nav / initial - 1.0

    lines = [
        f"信号日 {signal_str} → 执行日 {execute_date}",
        f"模拟净值 {nav:,.0f}（{total_ret:+.2%}）· 现金 {float(state.get('cash', 0)):,.0f}",
    ]

    if execution:
        lines.append(
            f"今日换仓：卖出 {execution.get('sells', 0)} 只 · 买入 {execution.get('buys', 0)} 只 · 持仓 {execution.get('positions_count', 0)} 只"
        )

    target = picks or pending.get("holdings") or []
    if target:
        lines.append("")
        lines.append(f"明日买入 Top {len(target)}（综合评分）：")
        for i, row in enumerate(target[:20], 1):
            lines.append(
                f"  {i:2d}. {row.get('symbol')} {row.get('name') or ''} "
                f"评分{float(row.get('recommend_score', 0) or 0):.3f} "
                f"状态{row.get('latest_state') or '--'} "
                f"建议仓位{float(row.get('suggested_position', 0) or 0):.0%} "
                f"权重{float(row.get('weight', 0) or 0):.1%}"
            )

    body = "\n".join(lines)
    change_n = len(target) if target else 0
    title = f"[Market-Agents] 模拟盘 Top{change_n} · 净值 {total_ret:+.2%}"
    return title, body


def run_paper_trading_cycle(
    config: AppConfig | None = None,
    *,
    trade_date: date | None = None,
    dry_run: bool = False,
    notify: bool | None = None,
) -> dict[str, Any]:
    """每日模拟盘：先执行待换仓，再生成明日 Top N 并可选推送。"""
    config = config or AppConfig.from_yaml(profile_path("默认.yaml"))
    pt = config.paper_trading
    if not pt.enabled:
        return {"skipped": True, "reason": "paper_trading.disabled"}

    trade_date = trade_date or latest_expected_trading_day()
    state = load_paper_state(config)
    _normalize_pending(state)
    execution: dict[str, Any] | None = None
    new_picks: list[dict[str, Any]] = []

    pending = state.get("pending")
    if pending and not pending.get("executed"):
        exec_date = _parse_date(pending.get("execute_date"))
        if exec_date and exec_date <= trade_date:
            # 节假日：顺延执行日，不标已执行
            if not is_trading_day(exec_date):
                deferred = next_trading_day(exec_date)
                pending["execute_date"] = _date_str(deferred)
                pending["deferred_from"] = _date_str(exec_date)
                state["pending"] = pending
                logger.info("Paper execute deferred %s → %s (holiday)", exec_date, deferred)
            else:
                picks_to_buy = pending.get("holdings") or []
                if picks_to_buy and not dry_run:
                    execution = _execute_full_rebalance(state, picks_to_buy, exec_date, config)
                    if execution.get("aborted"):
                        deferred = next_trading_day(trade_date)
                        pending["execute_date"] = _date_str(deferred)
                        pending["last_abort"] = execution.get("reason")
                        state["pending"] = pending
                        logger.warning("Paper pending kept; retry on %s", deferred)
                    else:
                        pending["executed"] = True
                        pending["executed_at"] = _date_str(trade_date)
                        state["pending"] = pending
                        state.setdefault("pick_history", []).append(
                            {
                                "signal_date": pending.get("signal_date"),
                                "execute_date": pending.get("execute_date"),
                                "symbols": [p["symbol"] for p in picks_to_buy],
                                "executed": True,
                            }
                        )
                        state["pick_history"] = state["pick_history"][-200:]
                elif picks_to_buy:
                    execution = {"dry_run": True, "would_execute_date": _date_str(exec_date)}

    if not dry_run and is_trading_day(trade_date):
        mark_to_market(state, trade_date)

    last_signal = _parse_date(state.get("signal_date"))
    pending = state.get("pending")
    # 未执行完的 pending 不得被新信号覆盖（假日顺延期间尤其重要）
    if pending and not pending.get("executed"):
        should_generate = False
    else:
        should_generate = last_signal != trade_date
    if should_generate and is_trading_day(trade_date):
        held = [str(p.get("symbol", "")).zfill(6) for p in (state.get("positions") or [])]
        new_picks = select_top_picks(config, current_symbols=held)
        if new_picks:
            exec_day = next_trading_day(trade_date)
            state["signal_date"] = _date_str(trade_date)
            state["pending"] = {
                "signal_date": _date_str(trade_date),
                "execute_date": _date_str(exec_day),
                "executed": False,
                "holdings": new_picks,
                "refreshed_at": datetime.now().isoformat(timespec="seconds"),
            }
            if not dry_run:
                state.setdefault("pick_history", []).append(
                    {
                        "signal_date": _date_str(trade_date),
                        "execute_date": _date_str(exec_day),
                        "symbols": [p["symbol"] for p in new_picks],
                        "executed": False,
                    }
                )
                state["pick_history"] = state["pick_history"][-200:]
    elif pending and not pending.get("executed") and not dry_run:
        # 执行前按最新全市场评分刷新清单（分析追平后成分可能变化），不重复推送
        held = [str(p.get("symbol", "")).zfill(6) for p in (state.get("positions") or [])]
        refreshed = select_top_picks(config, current_symbols=held)
        if refreshed:
            old_syms = [str(p.get("symbol", "")).zfill(6) for p in (pending.get("holdings") or [])]
            new_syms = [str(p.get("symbol", "")).zfill(6) for p in refreshed]
            pending["holdings"] = refreshed
            pending["refreshed_at"] = datetime.now().isoformat(timespec="seconds")
            state["pending"] = pending
            if old_syms != new_syms:
                logger.info(
                    "Paper pending Top%d refreshed: overlap=%d",
                    len(refreshed),
                    len(set(old_syms) & set(new_syms)),
                )

    do_notify = notify if notify is not None else pt.notify_after_cycle
    notify_result: dict[str, Any] | None = None
    if do_notify and new_picks:
        title, body = build_paper_digest(state, picks=new_picks, execution=execution, signal_date=trade_date)
        if not dry_run:
            results = dispatch_notification(config.notify, title, body, dry_run=False)
            notify_result = {
                "title": title,
                "body": body,
                "channels": [{"success": r.success, "channel": r.channel, "detail": r.detail} for r in results],
            }
        else:
            print(f"\n=== {title} ===\n{body}\n")
            notify_result = {"title": title, "body": body, "dry_run": True}

    if not dry_run:
        enrich_paper_positions(state, config)
        save_paper_state(state)

    return {
        "trade_date": _date_str(trade_date),
        "execution": execution,
        "tomorrow_picks": new_picks,
        "state": state,
        "notify": notify_result,
        "dry_run": dry_run,
    }

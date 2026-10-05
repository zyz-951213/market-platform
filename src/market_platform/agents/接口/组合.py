from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal

import numpy as np
import pandas as pd
from market_platform.agents.分析.回测.回测引擎 import _annualized_sharpe, _max_drawdown
from market_platform.agents.分析.信号.每日 import classify_signal_action
from market_platform.agents.settings import AppConfig, RiskConfig
from market_platform.agents.分析.回测.风控 import constrain_portfolio_weights
from market_platform.agents.分析.推断.状态 import MARKET_STATES, POSITION_MAP
from market_platform.agents.存储 import data_access as store
from market_platform.agents.核心.路径.层级 import normalize_sector_hierarchy_df
from market_platform.agents.核心.路径.项目 import data_dir, output_dir
from market_platform.agents.采集.行业.行业层级 import load_effective_hierarchy

WeightMode = Literal["equal", "sharpe_weighted", "risk_parity", "heat_weighted"]
SortBy = Literal["score", "excess_return", "strategy_sharpe", "heat"]

STATE_HEAT = {state: POSITION_MAP[state] for state in MARKET_STATES}


@dataclass
class PortfolioHolding:
    symbol: str
    name: str
    sector: str
    weight: float
    latest_state: str = ""
    suggested_position: float = 0.0
    excess_return: float = 0.0
    strategy_sharpe: float = 0.0
    strategy_return: float = 0.0
    recommend_score: float = 0.0


@dataclass
class PortfolioResult:
    portfolio_id: str
    name: str
    sectors: list[str]
    holdings: list[PortfolioHolding]
    equity_curve: pd.DataFrame
    metrics: dict[str, float]
    weights_by_sector: dict[str, float]
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    def to_dict(self) -> dict:
        eq = self.equity_curve.copy()
        eq["date"] = eq["date"].astype(str)
        return {
            "portfolio_id": self.portfolio_id,
            "name": self.name,
            "sectors": self.sectors,
            "holdings": [asdict(h) for h in self.holdings],
            "metrics": self.metrics,
            "weights_by_sector": self.weights_by_sector,
            "created_at": self.created_at,
            "equity_dates": eq["date"].tolist(),
            "portfolio_equity": eq["portfolio_equity"].tolist(),
            "benchmark_equity": eq["benchmark_equity"].tolist(),
            "portfolio_position": eq["portfolio_position"].tolist(),
        }


def _load_risk_config() -> RiskConfig:
    try:
        from market_platform.agents.核心.路径.项目 import profile_path

        return AppConfig.from_yaml(profile_path("默认.yaml")).risk
    except Exception:
        return RiskConfig()


def _default_output_root() -> Path:
    return output_dir()


def _default_data_root() -> Path:
    return data_dir()


def _merge_hierarchy(df: pd.DataFrame, data_root: Path) -> pd.DataFrame:
    if df.empty:
        return df
    out = normalize_sector_hierarchy_df(df)
    hier = load_effective_hierarchy(data_root)
    if hier.empty:
        return out
    merged = out.merge(
        hier[["symbol", "sector_l1", "sector_l2", "sector_l3"]],
        on="symbol",
        how="left",
        suffixes=("", "_hier"),
    )

    def _meaningful(series: pd.Series) -> pd.Series:
        text = series.astype(str).str.strip()
        return text.ne("") & text.ne("未分类") & text.ne("综合")

    for col in ("sector_l1", "sector_l2", "sector_l3"):
        hier_col = f"{col}_hier"
        if hier_col not in merged.columns:
            continue
        mask = _meaningful(merged[hier_col])
        if mask.any():
            merged.loc[mask, col] = merged.loc[mask, hier_col]
    drop_cols = [c for c in merged.columns if c.endswith("_hier")]
    return normalize_sector_hierarchy_df(merged.drop(columns=drop_cols, errors="ignore"))


def _sector_column(df: pd.DataFrame) -> str:
    return "sector_l3" if "sector_l3" in df.columns else "sector"


def load_universe_df(output_root: Path | None = None) -> pd.DataFrame:
    data_root = _default_data_root()
    df = store.load_symbol_summary()
    if not df.empty and "symbol" in df.columns:
        df["symbol"] = df["symbol"].astype(str).str.zfill(6)

    df = _merge_hierarchy(df, data_root)
    if df.empty:
        return df
    if "status" in df.columns:
        df = df[df["status"] == "ok"]
    for col in ("excess_return", "strategy_sharpe", "strategy_return", "suggested_position"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)
    if "latest_state" not in df.columns:
        df["latest_state"] = "横盘"
    # 样本外超额来自 optimized_positions 产物
    oos_map = store.load_oos_excess_map()
    if oos_map:
        df["oos_excess_return"] = df["symbol"].map(oos_map)
        df["oos_excess_return"] = pd.to_numeric(df["oos_excess_return"], errors="coerce").fillna(0.0)
    elif "oos_excess_return" not in df.columns:
        df["oos_excess_return"] = 0.0
    return df


def list_sectors(    output_root: Path | None = None,
    *,
    sector_l1: str = "",
    sector_l2: str = "",
) -> list[dict]:

    output_root = Path(output_root or _default_output_root())
    registry = load_universe_df(output_root)
    if registry.empty:
        return []

    sector_col = _sector_column(registry)
    df = registry.copy()
    if sector_l1:
        df = df[df["sector_l1"].astype(str) == sector_l1]
    if sector_l2:
        df = df[df["sector_l2"].astype(str) == sector_l2]
    if df.empty:
        return []

    rows: list[dict] = []
    for sector, grp in df.groupby(sector_col, dropna=False):
        name = str(sector).strip()
        if not name or name == "未分类":
            continue
        if "excess_return" in grp.columns:
            excess = pd.to_numeric(grp["excess_return"], errors="coerce").fillna(0.0)
        else:
            excess = pd.Series(0.0, index=grp.index)
        rows.append(
            {
                "sector": name,
                "sector_l3": name,
                "sector_l1": str(grp["sector_l1"].iloc[0]) if "sector_l1" in grp.columns else "",
                "sector_l2": str(grp["sector_l2"].iloc[0]) if "sector_l2" in grp.columns else "",
                "n_symbols": int(len(grp)),
                "mean_excess_return": float(excess.mean()) if len(grp) else None,
                "win_rate": float((excess > 0).mean()) if len(grp) else None,
            }
        )

    return sorted(rows, key=lambda r: (r.get("sector_l1", ""), r.get("sector_l2", ""), r["sector"]))


def _recommend_score(row: pd.Series) -> float:
    """综合评分：偏样本外泛化与绝对收益，弱化「相对自身买持超额」。"""
    heat = STATE_HEAT.get(str(row.get("latest_state", "横盘")), 0.5)
    oos = float(row.get("oos_excess_return", 0) or 0)
    strat = float(row.get("strategy_return", 0) or 0)
    sharpe = float(row.get("strategy_sharpe", 0) or 0)
    # 绝对收益截断，避免极端牛股完全主导
    abs_strength = float(np.clip(strat, -0.5, 2.0))
    return (
        0.40 * oos
        + 0.30 * abs_strength * 0.5
        + 0.20 * max(sharpe, 0.0) / 3.0
        + 0.10 * heat
    )


def recommend_stocks(
    sectors: list[str],
    picks_per_sector: int = 2,
    sort_by: SortBy = "score",
    symbols: list[str] | None = None,
    output_root: Path | None = None,
) -> list[dict]:
    output_root = Path(output_root or _default_output_root())
    df = load_universe_df(output_root)
    if df.empty:
        return []

    if symbols:
        sym_set = {str(s).zfill(6) for s in symbols}
        df = df[df["symbol"].isin(sym_set)]
    elif sectors:
        sector_set = set(sectors)
        sector_col = _sector_column(df)
        df = df[df[sector_col].astype(str).isin(sector_set)]

    if df.empty:
        return []

    df = df.copy()
    df["recommend_score"] = df.apply(_recommend_score, axis=1)
    sector_col = _sector_column(df)

    sort_col = {
        "score": "recommend_score",
        "excess_return": "excess_return",
        "strategy_sharpe": "strategy_sharpe",
        "heat": "suggested_position",
    }[sort_by]

    picks: list[dict] = []
    if sectors and not symbols:
        for sector in sectors:
            sub = df[df[sector_col].astype(str) == sector].sort_values(sort_col, ascending=False)
            for _, row in sub.head(picks_per_sector).iterrows():
                picks.append(_holding_row_to_dict(row))
    else:
        for _, row in df.sort_values(sort_col, ascending=False).iterrows():
            picks.append(_holding_row_to_dict(row))

    return picks


def _row_sector(row: pd.Series) -> str:
    if "sector_l3" in row.index:
        val = str(row.get("sector_l3", "") or "").strip()
        if val:
            return val
    return str(row.get("sector", ""))


def _holding_row_to_dict(row: pd.Series) -> dict:
    sector_name = _row_sector(row)
    return {
        "symbol": str(row["symbol"]).zfill(6),
        "name": str(row.get("name", "")),
        "sector": sector_name,
        "sector_l3": sector_name,
        "sector_l1": str(row.get("sector_l1", "")),
        "sector_l2": str(row.get("sector_l2", "")),
        "latest_state": str(row.get("latest_state", "")),
        "suggested_position": float(row.get("suggested_position", 0) or 0),
        "excess_return": float(row.get("excess_return", 0) or 0),
        "strategy_sharpe": float(row.get("strategy_sharpe", 0) or 0),
        "strategy_return": float(row.get("strategy_return", 0) or 0),
        "recommend_score": float(row.get("recommend_score", _recommend_score(row))),
        "selected": True,
    }


def _compute_weights(
    candidates: list[dict],
    weight_mode: WeightMode,
    equity_returns: dict[str, pd.Series],
) -> list[float]:
    n = len(candidates)
    if n == 0:
        return []

    if weight_mode == "equal":
        return [1.0 / n] * n

    if weight_mode == "sharpe_weighted":
        sharpes = np.array([max(float(c.get("strategy_sharpe", 0) or 0), 0.01) for c in candidates])
        w = sharpes / sharpes.sum()
        return w.tolist()

    if weight_mode == "heat_weighted":
        heats = np.array([max(float(c.get("suggested_position", 0) or 0), 0.05) for c in candidates])
        w = heats / heats.sum()
        return w.tolist()

    # risk_parity: inverse volatility
    vols = []
    for c in candidates:
        sym = str(c["symbol"]).zfill(6)
        ret = equity_returns.get(sym)
        vol = float(ret.std()) if ret is not None and len(ret) > 5 else 0.02
        vols.append(max(vol, 1e-4))
    inv = np.array([1.0 / v for v in vols])
    w = inv / inv.sum()
    return w.tolist()


def _load_equity_returns(symbol: str, output_root: Path) -> pd.DataFrame | None:
    sym = str(symbol).zfill(6)
    eq = store.load_equity_curve(sym)
    if eq.empty:
        return None
    eq = eq.sort_values("date").copy()
    eq["date"] = pd.to_datetime(eq["date"])
    eq["strategy_ret"] = eq["strategy_equity"].pct_change().fillna(0.0)
    eq["benchmark_ret"] = eq["benchmark_equity"].pct_change().fillna(0.0)
    if "position" in eq.columns:
        eq["position"] = eq["position"].astype(float)
    elif "suggested_position" in eq.columns:
        eq["position"] = eq["suggested_position"].astype(float)
    else:
        eq["position"] = 0.5
    return eq[["date", "strategy_ret", "benchmark_ret", "position"]]


def build_portfolio(
    sectors: list[str] | None = None,
    symbols: list[str] | None = None,
    picks_per_sector: int = 2,
    weight_mode: WeightMode = "equal",
    sort_by: SortBy = "score",
    name: str = "",
    output_root: Path | None = None,
    save: bool = True,
) -> PortfolioResult:
    output_root = Path(output_root or _default_output_root())

    if symbols:
        candidates = recommend_stocks([], picks_per_sector=picks_per_sector, symbols=symbols, output_root=output_root)
    else:
        if not sectors:
            raise ValueError("请至少选择一个行业或指定股票列表")
        candidates = recommend_stocks(sectors, picks_per_sector=picks_per_sector, sort_by=sort_by, output_root=output_root)

    if not candidates:
        raise ValueError("未找到可构建组合的标的，请先完成 batch 分析")

    equity_frames: dict[str, pd.DataFrame] = {}
    ret_map: dict[str, pd.Series] = {}
    for c in candidates:
        sym = str(c["symbol"]).zfill(6)
        eq = _load_equity_returns(sym, output_root)
        if eq is not None:
            equity_frames[sym] = eq
            ret_map[sym] = eq["strategy_ret"]

    valid = [c for c in candidates if str(c["symbol"]).zfill(6) in equity_frames]
    if not valid:
        raise ValueError("所选标的均无回测净值数据")

    weights = _compute_weights(valid, weight_mode, ret_map)
    risk = _load_risk_config()
    weighted_candidates = constrain_portfolio_weights(
        [{**c, "weight": float(w)} for c, w in zip(valid, weights)],
        risk,
    )
    valid = weighted_candidates
    weights = [float(c["weight"]) for c in valid]
    merged = None
    for c, w in zip(valid, weights):
        sym = str(c["symbol"]).zfill(6)
        part = equity_frames[sym].copy()
        part = part.rename(
            columns={
                "strategy_ret": f"{sym}_strat",
                "benchmark_ret": f"{sym}_bench",
                "position": f"{sym}_pos",
            }
        )
        merged = part if merged is None else merged.merge(part, on="date", how="outer")

    merged = merged.sort_values("date")
    strat_cols = [c for c in merged.columns if c.endswith("_strat")]
    bench_cols = [c for c in merged.columns if c.endswith("_bench")]
    pos_cols = [c for c in merged.columns if c.endswith("_pos")]
    # 收益/仓位缺失填 0，禁止对日收益 ffill（短历史票会虚增组合收益）
    if strat_cols:
        merged[strat_cols] = merged[strat_cols].fillna(0.0)
    if bench_cols:
        merged[bench_cols] = merged[bench_cols].fillna(0.0)
    if pos_cols:
        merged[pos_cols] = merged[pos_cols].fillna(0.0)

    w_arr = np.array(weights)
    merged["portfolio_return"] = sum(merged[col] * w for col, w in zip(strat_cols, w_arr))
    merged["benchmark_return"] = sum(merged[col] * w for col, w in zip(bench_cols, w_arr))
    merged["portfolio_position"] = sum(merged[col] * w for col, w in zip(pos_cols, w_arr))

    merged["portfolio_equity"] = (1 + merged["portfolio_return"]).cumprod()
    merged["benchmark_equity"] = (1 + merged["benchmark_return"]).cumprod()

    holdings = [
        PortfolioHolding(
            symbol=str(c["symbol"]).zfill(6),
            name=str(c.get("name", "")),
            sector=str(c.get("sector", "")),
            weight=float(w),
            latest_state=str(c.get("latest_state", "")),
            suggested_position=float(c.get("suggested_position", 0) or 0),
            excess_return=float(c.get("excess_return", 0) or 0),
            strategy_sharpe=float(c.get("strategy_sharpe", 0) or 0),
            strategy_return=float(c.get("strategy_return", 0) or 0),
            recommend_score=float(c.get("recommend_score", 0) or 0),
        )
        for c, w in zip(valid, weights)
    ]

    sector_weights: dict[str, float] = {}
    for h in holdings:
        sector_weights[h.sector] = sector_weights.get(h.sector, 0.0) + h.weight

    metrics = _portfolio_metrics(merged)
    portfolio_id = datetime.now().strftime("portfolio_%Y%m%d_%H%M%S")
    sector_list = sorted({h.sector for h in holdings if h.sector})
    display_name = name or f"{'+'.join(sector_list[:3])}{'…' if len(sector_list) > 3 else ''}"

    result = PortfolioResult(
        portfolio_id=portfolio_id,
        name=display_name,
        sectors=sector_list,
        holdings=holdings,
        equity_curve=merged[["date", "portfolio_equity", "benchmark_equity", "portfolio_return", "benchmark_return", "portfolio_position"]],
        metrics=metrics,
        weights_by_sector=sector_weights,
    )

    if save:
        _save_portfolio(result, output_root)
    return result


def _portfolio_metrics(eq: pd.DataFrame) -> dict[str, float]:
    p_ret = eq["portfolio_return"]
    b_ret = eq["benchmark_return"]
    p_eq = eq["portfolio_equity"]
    b_eq = eq["benchmark_equity"]
    total_p = float(p_eq.iloc[-1] - 1)
    total_b = float(b_eq.iloc[-1] - 1)
    return {
        "portfolio_total_return": total_p,
        "benchmark_total_return": total_b,
        "excess_return": total_p - total_b,
        "portfolio_sharpe": _annualized_sharpe(p_ret),
        "benchmark_sharpe": _annualized_sharpe(b_ret),
        "portfolio_max_drawdown": _max_drawdown(p_eq),
        "benchmark_max_drawdown": _max_drawdown(b_eq),
        "avg_position": float(eq["portfolio_position"].mean()),
        "trading_days": float(len(eq)),
    }


def analyze_portfolio_daily(
    holdings: list[PortfolioHolding] | list[dict],
    output_root: Path | None = None,
) -> dict:
    """聚合持仓的最新状态、热度与明日概率，给出当日组合建议。"""
    output_root = Path(output_root or _default_output_root())
    if not holdings:
        raise ValueError("组合持仓为空")

    items: list[dict] = []
    state_probs_acc = {s: 0.0 for s in MARKET_STATES}
    total_w = 0.0

    for raw in holdings:
        if isinstance(raw, PortfolioHolding):
            h = raw
            sym, w = h.symbol, h.weight
            name, sector = h.name, h.sector
            latest_state = h.latest_state
            position = h.suggested_position
        else:
            sym = str(raw["symbol"]).zfill(6)
            w = float(raw.get("weight", 0) or 0)
            name = str(raw.get("name", ""))
            sector = str(raw.get("sector", ""))
            latest_state = str(raw.get("latest_state", ""))
            position = float(raw.get("suggested_position", 0) or 0)

        st = store.load_market_states(sym)
        prev_state = latest_state
        prev_position = position
        if not st.empty:
            st = st.sort_values("date")
            latest = st.iloc[-1]
            latest_state = str(latest.get("market_state", latest_state))
            position = float(latest.get("suggested_position", position))
            if len(st) >= 2:
                prev = st.iloc[-2]
                prev_state = str(prev.get("market_state", prev_state))
                prev_position = float(prev.get("suggested_position", prev_position))

        rebalance_action = classify_signal_action(prev_state, latest_state, prev_position, position)
        position_delta = position - prev_position

        probs = store.load_next_state_probs(sym)
        if not probs.empty:
            col = probs.columns[0]
            for state in MARKET_STATES:
                if state in probs.index:
                    state_probs_acc[state] += w * float(probs.loc[state, col])

        heat = STATE_HEAT.get(latest_state, 0.5)
        items.append({
            "symbol": sym,
            "name": name,
            "sector": sector,
            "weight": w,
            "latest_state": latest_state,
            "prev_state": prev_state,
            "suggested_position": position,
            "prev_position": prev_position,
            "position_delta": position_delta,
            "rebalance_action": rebalance_action,
            "heat": heat,
            "action": _position_action(position, latest_state),
        })
        total_w += w

    if total_w > 0:
        state_probs_acc = {k: v / total_w for k, v in state_probs_acc.items()}

    avg_position = sum(i["suggested_position"] * i["weight"] for i in items) / max(total_w, 1e-9)
    avg_heat = sum(i["heat"] * i["weight"] for i in items) / max(total_w, 1e-9)
    dominant_state = max(state_probs_acc, key=state_probs_acc.get) if state_probs_acc else "横盘"

    return {
        "as_of": datetime.now().strftime("%Y-%m-%d"),
        "portfolio_position": avg_position,
        "portfolio_heat": avg_heat,
        "dominant_tomorrow_state": dominant_state,
        "tomorrow_state_probs": state_probs_acc,
        "portfolio_action": _position_action(avg_position, dominant_state),
        "holdings": sorted(items, key=lambda x: x["weight"], reverse=True),
        "summary": _daily_summary(avg_position, avg_heat, dominant_state, state_probs_acc),
    }


def _position_action(position: float, state: str) -> str:
    if position >= 0.8 or state == "狂热":
        return "积极持仓"
    if position <= 0.1 or state == "恐慌":
        return "防御空仓"
    if position >= 0.5:
        return "适度加仓"
    return "谨慎观望"


def _daily_summary(avg_position: float, avg_heat: float, dominant_state: str, probs: dict[str, float]) -> str:
    top_prob = max(probs.values()) if probs else 0.0
    return (
        f"组合加权仓位 {avg_position:.0%}，热度 {avg_heat:.0%}；"
        f"明日最可能进入「{dominant_state}」（概率 {top_prob:.0%}）。"
        f"建议：{_position_action(avg_position, dominant_state)}。"
    )


def _save_portfolio(result: PortfolioResult, output_root: Path) -> None:
    meta = {
        "portfolio_id": result.portfolio_id,
        "name": result.name,
        "sectors": result.sectors,
        "holdings": [asdict(h) for h in result.holdings],
        "metrics": result.metrics,
        "weights_by_sector": result.weights_by_sector,
        "created_at": result.created_at,
        "weight_mode": "saved",
    }
    store.save_portfolio(result.portfolio_id, result.name, meta, result.equity_curve)


def list_portfolios(output_root: Path | None = None) -> list[dict]:
    items = []
    for row in store.list_portfolios():
        meta = row.get("metadata") or {}
        items.append({
            "portfolio_id": row["portfolio_id"],
            "name": row.get("name") or meta.get("name", row["portfolio_id"]),
            "sectors": meta.get("sectors", []),
            "n_holdings": len(meta.get("holdings", [])),
            "metrics": meta.get("metrics", {}),
            "created_at": str(row.get("created_at", meta.get("created_at", ""))),
        })
    return items


def load_portfolio(portfolio_id: str, output_root: Path | None = None) -> dict:
    row = store.load_portfolio_metadata(portfolio_id)
    if not row:
        raise FileNotFoundError(f"未找到组合 {portfolio_id}")
    meta = row.get("metadata") or {}
    meta["portfolio_id"] = portfolio_id
    meta["name"] = row.get("name", meta.get("name", portfolio_id))
    eq = store.load_portfolio_equity(portfolio_id)
    if not eq.empty:
        meta["equity_dates"] = [str(pd.Timestamp(d).date()) for d in eq["date"]]
        meta["portfolio_equity"] = [float(v) for v in eq["portfolio_equity"]]
        meta["benchmark_equity"] = [float(v) for v in eq["benchmark_equity"]]
        meta["portfolio_position"] = [float(v) for v in eq["portfolio_position"]]
    return meta


def get_portfolio_equity_chart(portfolio_id: str, output_root: Path | None = None) -> dict:
    meta = load_portfolio(portfolio_id, output_root)
    dates = meta.get("equity_dates") or []
    strat_raw = meta.get("portfolio_equity") or []
    bench_raw = meta.get("benchmark_equity") or []
    if not dates or not strat_raw:
        return {
            "dates": [],
            "strategy": [],
            "benchmark": [],
            "excess": [],
            "position": [],
            "empty": True,
        }
    strat = np.array(strat_raw, dtype=float)
    bench = np.array(bench_raw, dtype=float) if bench_raw else np.ones_like(strat)
    pos = meta.get("portfolio_position", [])
    strat_norm = strat / strat[0] if len(strat) and strat[0] else strat
    bench_norm = bench / bench[0] if len(bench) and bench[0] else bench
    excess = strat_norm / bench_norm if len(bench_norm) else strat_norm
    return {
        "dates": dates,
        "strategy": [float(v) for v in strat_norm],
        "benchmark": [float(v) for v in bench_norm],
        "excess": [float(v) for v in excess],
        "position": [float(v) for v in pos],
    }

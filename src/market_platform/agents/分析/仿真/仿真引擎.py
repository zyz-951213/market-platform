from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from market_platform.agents.分析.仿真.冲击 import apply_price_impact
from market_platform.agents.分析.仿真.智能体 import (
    DailyMacroContext,
    HotMoneyAgent,
    InstitutionalAgent,
    MacroAgent,
    MarketContext,
    QuantitativeAgent,
    RetailAgent,
    SimulationParams,
)
from market_platform.agents.分析.推断.智能体角色 import get_agent_columns
from market_platform.agents.核心.工具.读写 import ensure_dir, save_parquet


@dataclass
class SimulationResult:
    minute_path: pd.DataFrame
    daily_metrics: pd.DataFrame
    summary: dict[str, float]

    def save(self, output_dir: Path) -> None:
        ensure_dir(output_dir)
        save_parquet(self.minute_path, output_dir / "simulated_minute.parquet")
        save_parquet(self.daily_metrics, output_dir / "simulation_daily_metrics.parquet")


def _compute_rsi(closes: pd.Series, period: int = 14) -> float:
    if len(closes) < 2:
        return 50.0
    delta = closes.diff().dropna()
    if delta.empty:
        return 50.0
    gain = delta.clip(lower=0).mean()
    loss = (-delta.clip(upper=0)).mean()
    if loss == 0:
        return 100.0 if gain > 0 else 50.0
    rs = gain / loss
    return float(100 - 100 / (1 + rs))


def _normalize_signal(series: pd.Series) -> pd.Series:
    std = series.std()
    if std == 0 or np.isnan(std):
        return pd.Series(0.0, index=series.index)
    return (series / std).clip(-3, 3) / 3.0


def build_daily_macro_map(
    northbound: pd.DataFrame | None = None,
    margin: pd.DataFrame | None = None,
) -> dict:
    """构建 date -> DailyMacroContext 映射。"""
    dates: set = set()
    north_map: dict = {}
    margin_map: dict = {}

    if northbound is not None and not northbound.empty:
        nb = northbound.copy()
        nb["date"] = pd.to_datetime(nb["date"]).dt.date
        nb["northbound_signal"] = _normalize_signal(nb["north_net_inflow"].astype(float))
        north_map = nb.set_index("date")["northbound_signal"].to_dict()
        dates.update(north_map.keys())

    if margin is not None and not margin.empty:
        mg = margin.copy()
        mg["date"] = pd.to_datetime(mg["date"]).dt.date
        mg["margin_signal"] = _normalize_signal(mg["margin_balance_change"].astype(float))
        margin_map = mg.set_index("date")["margin_signal"].to_dict()
        dates.update(margin_map.keys())

    result: dict = {}
    for d in dates:
        result[d] = DailyMacroContext(
            northbound_signal=float(north_map.get(d, 0.0)),
            margin_signal=float(margin_map.get(d, 0.0)),
        )
    return result


def _build_context(
    day_df: pd.DataFrame,
    idx: int,
    price: float,
    lookback: int,
    macro_ctx: DailyMacroContext,
    activities: dict[str, float],
) -> MarketContext:
    row = day_df.iloc[idx]
    hist = day_df.iloc[max(0, idx - lookback) : idx + 1]

    if len(hist) >= 2:
        return_1min = hist["close"].pct_change().iloc[-1]
        if np.isnan(return_1min):
            return_1min = 0.0
    else:
        return_1min = 0.0

    if len(hist) > 1:
        return_5min = hist["close"].iloc[-1] / hist["close"].iloc[0] - 1.0
    else:
        return_5min = 0.0

    vwap = row.get("vwap", np.nan)
    if pd.isna(vwap) or vwap == 0:
        vwap = price
    vwap_deviation = (price - vwap) / vwap if vwap else 0.0

    avg_volume = day_df["volume"].mean()
    if avg_volume <= 0:
        avg_volume = float(row["volume"]) or 1.0

    rsi_window = day_df.iloc[max(0, idx - 14) : idx + 1]["close"]
    rsi = _compute_rsi(rsi_window)

    realized_vol = float(row.get("realized_vol", 0.0) or 0.0)
    volume_spike = float(row.get("volume_spike", 1.0) or 1.0)
    minute_progress = idx / max(len(day_df) - 1, 1)

    return MarketContext(
        price=price,
        return_1min=float(return_1min),
        return_5min=float(return_5min),
        vwap_deviation=float(vwap_deviation),
        volume=float(row["volume"]),
        avg_volume=float(avg_volume),
        rsi=rsi,
        realized_vol=realized_vol,
        volume_spike=volume_spike,
        minute_progress=float(minute_progress),
        northbound_signal=macro_ctx.northbound_signal,
        margin_signal=macro_ctx.margin_signal,
        retail_activity=activities.get("retail_activity", 1.0),
        institutional_activity=activities.get("institutional_activity", 1.0),
        quant_activity=activities.get("quant_activity", 1.0),
        macro_activity=activities.get("macro_activity", 1.0),
        hot_money_activity=activities.get("hot_money_activity", 1.0),
    )


def simulate_trading_day(
    day_df: pd.DataFrame,
    params: SimulationParams,
    macro_ctx: DailyMacroContext | None = None,
    activities: dict[str, float] | None = None,
) -> pd.DataFrame:
    """对单个交易日做分钟级 5-Agent 模拟。"""
    day_df = day_df.sort_values("datetime").reset_index(drop=True)
    macro_ctx = macro_ctx or DailyMacroContext()
    activities = activities or {}

    agents = (
        RetailAgent(params.retail),
        InstitutionalAgent(params.institutional),
        QuantitativeAgent(params.quant),
        MacroAgent(params.macro),
        HotMoneyAgent(params.hot_money),
    )
    order_cols = ("retail_order", "institutional_order", "quant_order", "macro_order", "hot_money_order")

    price = float(day_df.iloc[0]["open"])
    records: list[dict] = []

    for idx in range(len(day_df)):
        row = day_df.iloc[idx]
        ctx = _build_context(day_df, idx, price, params.lookback, macro_ctx, activities)
        orders = [agent.decide(ctx) for agent in agents]
        net_order = sum(orders)

        price = apply_price_impact(price, net_order, ctx.avg_volume, params.impact_lambda)

        record = {
            "datetime": row["datetime"],
            "date": row["date"],
            "actual_close": row["close"],
            "simulated_price": price,
            "net_order_flow": net_order,
            "return_5min": ctx.return_5min,
            "vwap_deviation": ctx.vwap_deviation,
            "rsi": ctx.rsi,
            "minute_progress": ctx.minute_progress,
        }
        for col, val in zip(order_cols, orders, strict=True):
            record[col] = val
        records.append(record)

    return pd.DataFrame(records)


def _normalize_activity(series: pd.Series) -> pd.Series:
    if series.empty:
        return series
    max_val = series.max()
    if max_val <= 0:
        return pd.Series(1.0, index=series.index)
    return series / max_val


def _map_nmf_activity(activity_df: pd.DataFrame) -> dict:
    act = activity_df.copy()
    act["date"] = pd.to_datetime(act["date"]).dt.date
    agent_cols = get_agent_columns(act)

    keys = [
        "retail_activity",
        "institutional_activity",
        "quant_activity",
        "macro_activity",
        "hot_money_activity",
    ]
    for key in keys:
        act[key] = 1.0

    for i, col in enumerate(agent_cols[: len(keys)]):
        act[keys[i]] = _normalize_activity(act[col])

    return act.set_index("date")[keys].to_dict("index")


def run_simulation(
    minute_df: pd.DataFrame,
    params: SimulationParams,
    activity_df: pd.DataFrame | None = None,
    northbound: pd.DataFrame | None = None,
    margin: pd.DataFrame | None = None,
) -> SimulationResult:
    """对完整分钟数据运行 5-Agent 模拟。"""
    minute_df = minute_df.copy()
    if "vwap" not in minute_df.columns:
        minute_df["vwap"] = minute_df["amount"] / minute_df["volume"].replace(0, np.nan)

    activity_map = _map_nmf_activity(activity_df) if activity_df is not None and not activity_df.empty else {}
    macro_map = build_daily_macro_map(northbound, margin)

    default_act = {
        "retail_activity": 1.0,
        "institutional_activity": 1.0,
        "quant_activity": 1.0,
        "macro_activity": 1.0,
        "hot_money_activity": 1.0,
    }

    day_results: list[pd.DataFrame] = []
    for date_val, day_df in minute_df.groupby("date"):
        act = {**default_act, **activity_map.get(date_val, {})}
        macro_ctx = macro_map.get(date_val, DailyMacroContext())
        day_results.append(simulate_trading_day(day_df, params, macro_ctx, act))

    sim_df = pd.concat(day_results, ignore_index=True)
    sim_df["sim_return"] = sim_df.groupby("date")["simulated_price"].pct_change()
    sim_df["actual_return"] = sim_df.groupby("date")["actual_close"].pct_change()

    daily = sim_df.groupby("date").agg(
        actual_close=("actual_close", "last"),
        simulated_close=("simulated_price", "last"),
        net_order_sum=("net_order_flow", "sum"),
        retail_order_sum=("retail_order", "sum"),
        institutional_order_sum=("institutional_order", "sum"),
        quant_order_sum=("quant_order", "sum"),
        macro_order_sum=("macro_order", "sum"),
        hot_money_order_sum=("hot_money_order", "sum"),
    ).reset_index()
    daily["close_error_pct"] = (daily["simulated_close"] - daily["actual_close"]) / daily["actual_close"]

    actual_returns = sim_df["actual_return"].dropna()
    sim_returns = sim_df["sim_return"].dropna()
    order_abs = sim_df["net_order_flow"].abs().sum() + 1e-9

    summary = {
        "days_simulated": float(len(daily)),
        "mean_abs_close_error_pct": float(daily["close_error_pct"].abs().mean()),
        "actual_return_std": float(actual_returns.std()),
        "simulated_return_std": float(sim_returns.std()),
        "return_corr": float(actual_returns.corr(sim_returns)) if len(actual_returns) > 1 else 0.0,
        "macro_order_share": float(sim_df["macro_order"].abs().sum() / order_abs),
        "hot_money_order_share": float(sim_df["hot_money_order"].abs().sum() / order_abs),
    }

    return SimulationResult(minute_path=sim_df, daily_metrics=daily, summary=summary)

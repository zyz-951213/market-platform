"""
归因分析服务 — 后端计算逻辑，供 API 和脚本共用
"""

from __future__ import annotations


from typing import Any, Dict, List

import numpy as np
import pandas as pd

from market_platform.predictor.接口.回测存储 import load_backtest_for_provider, provider_from_model_label
from market_platform.predictor.配置 import load_config, get_config
from market_platform.predictor.存储.数据访问 import load_index_features
from market_platform.predictor.分析.提示词.构建器 import get_evaluation_since

ROLLING_SHARPE_WINDOW = 252  # 全年交易日窗口
ROLLING_ACCURACY_WINDOW = 252


def _classification_slice(signals: pd.DataFrame) -> pd.DataFrame:
    cols = ["date", "actual", "prediction"]
    if not all(c in signals.columns for c in cols):
        return pd.DataFrame()
    out = signals[cols].dropna().copy()
    out["actual"] = out["actual"].astype(int)
    out["prediction"] = out["prediction"].astype(int)
    out["hit"] = out["prediction"] == out["actual"]
    out["dir_hit"] = (out["prediction"] == out["actual"]) & (out["actual"] != 0)
    return out


def _accuracy_metrics(df: pd.DataFrame) -> Dict[str, Any]:
    if df.empty:
        return {}
    n = len(df)
    acc = float(df["hit"].mean())
    nonzero = df["actual"] != 0
    dir_acc = float(df.loc[nonzero, "hit"].mean()) if nonzero.any() else None
    return {
        "n": n,
        "accuracy": round(acc, 4),
        "directional_accuracy": round(dir_acc, 4) if dir_acc is not None else None,
        "pred_up_pct": round(float((df["prediction"] == 1).mean()), 4),
        "pred_down_pct": round(float((df["prediction"] == -1).mean()), 4),
        "pred_flat_pct": round(float((df["prediction"] == 0).mean()), 4),
    }


def _compute_llm_accuracy(signals: pd.DataFrame) -> Dict[str, Any]:
    cls = _classification_slice(signals)
    if cls.empty:
        return {"available": False}

    cls["year"] = cls["date"].dt.year
    primary_since = pd.Timestamp(get_evaluation_since())

    yearly: List[Dict[str, Any]] = []
    for yr, grp in cls.groupby("year"):
        m = _accuracy_metrics(grp)
        if m:
            yearly.append({"year": int(yr), **m})

    rolling = cls["hit"].astype(float).rolling(
        ROLLING_ACCURACY_WINDOW, min_periods=ROLLING_ACCURACY_WINDOW
    ).mean()
    rolling_data: List[Dict[str, Any]] = []
    for i in rolling.dropna().index:
        rolling_data.append({
            "date": str(cls.iloc[i]["date"].date()),
            "accuracy": round(float(rolling.iloc[i]), 4),
        })
    step = max(1, len(rolling_data) // 40)
    rolling_sampled = rolling_data[::step]
    if rolling_data and rolling_sampled[-1] != rolling_data[-1]:
        rolling_sampled.append(rolling_data[-1])

    primary = cls[cls["date"] >= primary_since]
    return {
        "available": True,
        "primary_since": primary_since.strftime("%Y-%m-%d"),
        "full_sample": _accuracy_metrics(cls),
        "primary_sample": _accuracy_metrics(primary),
        "yearly": yearly,
        "rolling_accuracy": {
            "window_days": ROLLING_ACCURACY_WINDOW,
            "mean": round(float(rolling.mean()), 4) if len(rolling.dropna()) else None,
            "median": round(float(rolling.median()), 4) if len(rolling.dropna()) else None,
            "min": round(float(rolling.min()), 4) if len(rolling.dropna()) else None,
            "latest": round(float(rolling.iloc[-1]), 4) if len(rolling.dropna()) else None,
            "series": rolling_sampled[-200:],
        },
    }


def _group_attribution(signals: pd.DataFrame) -> Dict[str, Any] | None:
    """统一夏普模式：进攻 / 防守+现金流 贡献。"""
    if "offense_return" not in signals.columns and "group_w_offense" not in signals.columns:
        return None
    out: Dict[str, Any] = {}
    if "offense_return" in signals.columns:
        off = signals["offense_return"].fillna(0.0).astype(float)
        deff = (
            signals["defensive_return"].fillna(0.0).astype(float)
            if "defensive_return" in signals.columns
            else signals["period_return"].fillna(0.0).astype(float) - off
        )
        out["offense_return"] = round(float((1 + off).prod() - 1), 6)
        out["defensive_return"] = round(float((1 + deff).prod() - 1), 6)
    for col, key in (
        ("group_w_offense", "avg_offense_weight"),
        ("group_w_defense", "avg_defense_weight"),
        ("group_w_cashflow", "avg_cashflow_weight"),
    ):
        if col in signals.columns and signals[col].notna().any():
            out[key] = round(float(signals[col].astype(float).mean()), 4)
    return out or None


def compute_attribution(provider: str | None = None) -> Dict[str, Any]:
    """
    从指定渠道（或 latest）回测 signals 计算归因指标。
    """
    bt = load_backtest_for_provider(provider)
    if not bt or not bt.get("signals"):
        return {"available": False, "llm_provider": provider}

    signals = pd.DataFrame(bt["signals"])
    signals["date"] = pd.to_datetime(signals["date"])
    signals = signals.sort_values("date").reset_index(drop=True)

    signals["ret_mixed"] = signals["period_return"].fillna(0.0).astype(float)
    signals["year"] = signals["date"].dt.year
    signals["month"] = signals["date"].dt.month

    # 基准收益
    cfg = load_config()
    df = load_index_features(cfg["data"]["index_code"])
    if df is not None and not df.empty:
        df["date"] = pd.to_datetime(df["date"])
        df["label_return"] = df["label_return"].fillna(0.0)
        merged = signals[["date"]].merge(df[["date", "label_return"]], on="date", how="left")
        signals["benchmark_ret"] = merged["label_return"].fillna(0.0)
    else:
        signals["benchmark_ret"] = 0.0

    # ── 年度归因 ──
    all_years = sorted(signals["year"].unique())
    yearly: List[Dict[str, Any]] = []
    for yr in all_years:
        yr_data = signals[signals["year"] == yr]
        if len(yr_data) < 10:
            continue
        strat = (1 + yr_data["ret_mixed"]).prod() - 1
        bench = (1 + yr_data["benchmark_ret"]).prod() - 1
        sharpe = yr_data["ret_mixed"].mean() / (yr_data["ret_mixed"].std() + 1e-10) * np.sqrt(252)
        win_rate = (yr_data["ret_mixed"] > 0).mean()
        trades = int(yr_data["ret_mixed"].notna().sum())
        yearly.append({
            "year": int(yr),
            "strategy_ret": round(float(strat), 6),
            "benchmark_ret": round(float(bench), 6),
            "excess": round(float(strat - bench), 6),
            "sharpe": round(float(sharpe), 4),
            "win_rate": round(float(win_rate), 4),
            "trades": trades,
        })

    # ── 涨跌市参与率 ──
    monthly_bench = signals.groupby(["year", "month"])["benchmark_ret"].apply(
        lambda x: (1 + x).prod() - 1
    )
    monthly_strat = signals.groupby(["year", "month"])["ret_mixed"].apply(
        lambda x: (1 + x).prod() - 1
    )
    up = monthly_bench > 0
    down = monthly_bench < 0
    up_capture = float(monthly_strat[up].mean() / monthly_bench[up].mean()) if up.sum() > 0 else 0.0
    down_capture = float(monthly_strat[down].mean() / monthly_bench[down].mean()) if down.sum() > 0 else 0.0

    # ── 滚动夏普（全年窗口）──
    rolling_sharpe_series = signals["ret_mixed"].rolling(
        ROLLING_SHARPE_WINDOW, min_periods=ROLLING_SHARPE_WINDOW
    ).apply(lambda x: x.mean() / (x.std() + 1e-10) * np.sqrt(252))
    rs = rolling_sharpe_series.dropna()
    rolling_sharpe_data: List[Dict[str, Any]] = []
    for i in rs.index:
        rolling_sharpe_data.append({
            "date": str(signals.iloc[i]["date"].date()),
            "sharpe": round(float(rs[i]), 4),
        })
    # 按 20 个点采样避免数据过多
    step = max(1, len(rolling_sharpe_data) // 40)
    rolling_sharpe_sampled = rolling_sharpe_data[::step]
    if rolling_sharpe_data and rolling_sharpe_sampled[-1] != rolling_sharpe_data[-1]:
        rolling_sharpe_sampled.append(rolling_sharpe_data[-1])

    total_ret = bt.get("total_return", 0)
    bench_ret = bt.get("benchmark_total_return", 0)
    excess_ret = total_ret - bench_ret

    return {
        "available": True,
        "model_name": bt.get("model_name", "?"),
        "llm_provider": bt.get("llm_provider")
        or provider_from_model_label(bt.get("model_name"))
        or provider,
        "data_as_of": bt.get("data_as_of") or (
            str(signals["date"].iloc[-1].date()) if len(signals) else None
        ),
        "period": {
            "start": bt.get("benchmark_start_date") or (
                str(signals["date"].iloc[0].date()) if len(signals) else None
            ),
            "end": bt.get("benchmark_end_date") or (
                str(signals["date"].iloc[-1].date()) if len(signals) else None
            ),
        },
        "cash_mgmt_enabled": bool(bt.get("cash_mgmt_enabled", False)),
        "allocation_mode": bt.get("allocation_mode") or (
            str(signals["allocation_mode"].iloc[0])
            if "allocation_mode" in signals.columns and len(signals)
            else "legacy"
        ),
        "evaluation_primary_since": get_evaluation_since(),
        "llm_accuracy": _compute_llm_accuracy(signals),
        "total_return": total_ret,
        "benchmark_total_return": bench_ret,
        "excess_return": excess_ret,
        "cash_return": bt.get("cash_return"),
        "strategy_only_return": bt.get("strategy_only_return"),
        "static_4020_return": bt.get("static_4020_return"),
        "equal_six_return": bt.get("equal_six_return"),
        "group_attribution": _group_attribution(signals),
        "sharpe": bt.get("sharpe_ratio"),
        "max_drawdown": bt.get("max_drawdown"),
        "yearly": yearly,
        "market_participation": {
            "up_months": int(up.sum()),
            "down_months": int(down.sum()),
            "total_months": len(monthly_bench),
            "up_capture": round(up_capture, 4),
            "down_capture": round(down_capture, 4),
            "monthly_win_rate": round(float((monthly_strat > 0).mean()), 4),
        },
        "rolling_sharpe": {
            "window_days": ROLLING_SHARPE_WINDOW,
            "mean": round(float(rs.mean()), 4) if len(rs) > 0 else None,
            "median": round(float(rs.median()), 4) if len(rs) > 0 else None,
            "min": round(float(rs.min()), 4) if len(rs) > 0 else None,
            "latest": round(float(rs.iloc[-1]), 4) if len(rs) > 0 else None,
            "pct_positive": round(float((rs > 0).mean()), 4) if len(rs) > 0 else None,
            "series": rolling_sharpe_sampled[-200:],  # 最多 200 个点
        },
        "worst_drawdown": {
            "max_dd": round(float(bt.get("max_drawdown", 0)), 6),
            "benchmark_max_dd": round(float(bt.get("benchmark_max_drawdown", 0)), 6),
        },
        "best_year": max(yearly, key=lambda y: y["strategy_ret"])["year"] if yearly else None,
        "worst_year": min(yearly, key=lambda y: y["strategy_ret"])["year"] if yearly else None,
    }

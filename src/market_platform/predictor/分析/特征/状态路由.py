"""市场状态路由器（Prompt 用三态规则 + 平滑）。

与闲置资金 ThreeLayerAllocator._classify_regime（9 态）解耦。
deviation_60 特征已是百分比；momentum_60d 为比率。
"""

from __future__ import annotations

from typing import Any, Dict, Optional

import pandas as pd

from market_platform.predictor.配置 import get_config

REGIME_BULL = "bull"
REGIME_BEAR = "bear"
REGIME_RANGE = "range"

REGIME_ZH = {
    REGIME_BULL: "趋势上升",
    REGIME_BEAR: "趋势下降",
    REGIME_RANGE: "震荡",
}


def regime_router_cfg() -> Dict[str, Any]:
    prompt = get_config().get("model", {}).get("llm", {}).get("prompt", {}) or {}
    raw = prompt.get("regime_router", {}) or {}
    return {
        "enabled": bool(raw.get("enabled", False)),
        "smooth_window": max(1, int(raw.get("smooth_window", 5))),
        "bull_deviation": float(raw.get("bull_deviation", 3.0)),
        "bear_deviation": float(raw.get("bear_deviation", -3.0)),
        "include_money_flow_change": bool(raw.get("include_money_flow_change", True)),
        "include_etf": bool(raw.get("include_etf", True)),
        "include_basis": bool(raw.get("include_basis", True)),
    }


def classify_regime(
    row: pd.Series,
    *,
    bull_deviation: Optional[float] = None,
    bear_deviation: Optional[float] = None,
) -> str:
    """单日硬规则：bull / bear / range。"""
    cfg = regime_router_cfg()
    bull_th = float(cfg["bull_deviation"] if bull_deviation is None else bull_deviation)
    bear_th = float(cfg["bear_deviation"] if bear_deviation is None else bear_deviation)

    dev = row.get("deviation_60")
    mom = row.get("momentum_60d")
    if pd.isna(dev) or pd.isna(mom):
        return REGIME_RANGE
    dev_f = float(dev)
    mom_f = float(mom)
    if dev_f > bull_th and mom_f > 0:
        return REGIME_BULL
    if dev_f < bear_th and mom_f < 0:
        return REGIME_BEAR
    return REGIME_RANGE


def classify_regime_series(
    df: pd.DataFrame,
    *,
    bull_deviation: Optional[float] = None,
    bear_deviation: Optional[float] = None,
) -> pd.Series:
    """逐行硬规则序列（与 df 同序；index 与输入对齐）。"""
    out = [
        classify_regime(
            row,
            bull_deviation=bull_deviation,
            bear_deviation=bear_deviation,
        )
        for _, row in df.iterrows()
    ]
    return pd.Series(out, index=df.index, dtype="object")


def smooth_regime(raw: pd.Series, window: Optional[int] = None) -> pd.Series:
    """因果多数票平滑：用含当日的过去 window 日众数；平票保留当前硬标签。"""
    cfg = regime_router_cfg()
    w = max(1, int(cfg["smooth_window"] if window is None else window))
    if w <= 1 or raw.empty:
        return raw.astype(object).copy()

    values = raw.astype(object).tolist()
    smoothed: list[str] = []
    for i in range(len(values)):
        start = max(0, i - w + 1)
        window_vals = values[start : i + 1]
        counts: Dict[str, int] = {}
        for v in window_vals:
            counts[str(v)] = counts.get(str(v), 0) + 1
        best = max(counts.values())
        candidates = [k for k, c in counts.items() if c == best]
        cur = str(values[i])
        smoothed.append(cur if cur in candidates else sorted(candidates)[0])
    return pd.Series(smoothed, index=raw.index, dtype="object")


def regime_series(df: pd.DataFrame, *, smooth: bool = True) -> pd.Series:
    """全表状态序列（因果）。"""
    raw = classify_regime_series(df)
    if not smooth:
        return raw
    return smooth_regime(raw)


def regime_at(
    df: pd.DataFrame,
    date: pd.Timestamp,
    *,
    smooth: bool = True,
) -> str:
    """截至 date（含）的因果状态；date 不在表内则取不晚于 date 的最近一行。"""
    if df is None or df.empty or "date" not in df.columns:
        return REGIME_RANGE
    data = df.sort_values("date").reset_index(drop=True)
    target = pd.Timestamp(date).normalize()
    dates = pd.to_datetime(data["date"]).dt.normalize()
    mask = dates <= target
    if not mask.any():
        return REGIME_RANGE
    sub = data.loc[mask].reset_index(drop=True)
    series = regime_series(sub, smooth=smooth)
    return str(series.iloc[-1])


def regime_zh(regime: str) -> str:
    return REGIME_ZH.get(str(regime), str(regime))


def price_flow_divergence(row: pd.Series) -> Optional[str]:
    """简单价资背离描述；数据不足返回 None。"""
    mom = row.get("momentum_5d")
    mf = row.get("money_flow_change")
    if pd.isna(mom) or pd.isna(mf):
        return None
    mom_f = float(mom)
    mf_f = float(mf)
    if mom_f > 0 and mf_f < 0:
        return "是（价涨资出）"
    if mom_f < 0 and mf_f > 0:
        return "是（价跌资进）"
    return "否"

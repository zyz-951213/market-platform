from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from market_platform.agents.分析.推断.智能体角色 import get_agent_columns


@dataclass
class ResidualReport:
    daily: pd.DataFrame
    anomalies: pd.DataFrame
    explanations: pd.DataFrame


def estimate_baseline_flow(
    agent_activity: pd.DataFrame,
    fund_flow: pd.DataFrame,
) -> pd.DataFrame:
    """线性基线：NMF 活跃度 × 系数 ≈ 主力净流入。"""
    act = agent_activity.copy()
    act["date"] = pd.to_datetime(act["date"]).dt.date
    agent_cols = get_agent_columns(act)

    ff = fund_flow.copy()
    if ff.empty or "date" not in ff.columns or "main_net_inflow" not in ff.columns:
        act["main_net_inflow"] = 0.0
        act["baseline_flow"] = 0.0
        act["residual"] = 0.0
        act["residual_zscore"] = 0.0
        return act.sort_values("date").reset_index(drop=True)

    ff["date"] = pd.to_datetime(ff["date"]).dt.date

    merged = act.merge(ff[["date", "main_net_inflow"]], on="date", how="inner")
    if not agent_cols:
        merged["baseline_flow"] = 0.0
        merged["residual"] = merged["main_net_inflow"]
        return merged

    x = merged[agent_cols].to_numpy(dtype=float)
    y = merged["main_net_inflow"].to_numpy(dtype=float)

    coef, _, _, _ = np.linalg.lstsq(x, y, rcond=None)
    merged["baseline_flow"] = x @ coef
    merged["residual"] = merged["main_net_inflow"] - merged["baseline_flow"]
    merged["residual_zscore"] = (
        (merged["residual"] - merged["residual"].mean()) / (merged["residual"].std() + 1e-9)
    )
    for i, col in enumerate(agent_cols):
        merged[f"beta_{col}"] = coef[i]

    return merged.sort_values("date").reset_index(drop=True)


def detect_anomalies(baseline_df: pd.DataFrame, z_threshold: float = 2.0) -> pd.DataFrame:
    return baseline_df[baseline_df["residual_zscore"].abs() >= z_threshold].copy()


def explain_residuals_rule_based(
    anomalies: pd.DataFrame,
    minute_daily: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """规则解释残差异常（LLM 层可替换此函数）。"""
    if anomalies.empty:
        return pd.DataFrame(columns=["date", "explanation", "llm_prompt"])

    daily = None
    if minute_daily is not None and not minute_daily.empty:
        daily = minute_daily.copy()
        daily["date"] = pd.to_datetime(daily["date"]).dt.date

    rows: list[dict] = []
    for _, row in anomalies.iterrows():
        z = float(row["residual_zscore"])
        direction = "超出" if z > 0 else "低于"
        parts = [
            f"主力净流入 {direction}线性基线 {abs(z):.1f} 个标准差",
        ]

        if daily is not None:
            day = daily[daily["date"] == row["date"]]
            if not day.empty:
                d = day.iloc[0]
                if d.get("volume_spike_max", 0) > 2:
                    parts.append("分钟级放量异常")
                if d.get("tail_volume_ratio", 0) > 0.35:
                    parts.append("尾盘成交量占比偏高")

        explanation = "；".join(parts)
        prompt = (
            f"日期 {row['date']}：实际主力净流入 {row['main_net_inflow']:.0f}，"
            f"基线预测 {row['baseline_flow']:.0f}，残差 z={z:.2f}。"
            f"请结合当日新闻解释 Agent 行为偏移原因。"
        )
        rows.append({"date": row["date"], "explanation": explanation, "llm_prompt": prompt})

    return pd.DataFrame(rows)


def build_residual_report(
    agent_activity: pd.DataFrame,
    fund_flow: pd.DataFrame,
    minute_daily: pd.DataFrame | None = None,
    z_threshold: float = 2.0,
) -> ResidualReport:
    daily = estimate_baseline_flow(agent_activity, fund_flow)
    anomalies = detect_anomalies(daily, z_threshold=z_threshold)
    explanations = explain_residuals_rule_based(anomalies, minute_daily)
    return ResidualReport(daily=daily, anomalies=anomalies, explanations=explanations)

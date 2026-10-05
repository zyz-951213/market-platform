from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from market_platform.agents.分析.推断.智能体角色 import get_agent_columns

MARKET_STATES = ["恐慌", "震荡偏弱", "横盘", "震荡偏强", "狂热"]

# 进攻抬高中间档，恐慌保持空仓（防守底线）
POSITION_MAP = {
    "恐慌": 0.0,
    "震荡偏弱": 0.4,
    "横盘": 0.7,
    "震荡偏强": 1.0,
    "狂热": 1.0,
}

STATE_RANK = {name: i for i, name in enumerate(MARKET_STATES)}
_DEFENSIVE_STATES = frozenset({"恐慌", "震荡偏弱"})
_OFFENSIVE_STATES = frozenset({"震荡偏强", "狂热"})


@dataclass(frozen=True)
class StateConfirmPolicy:
    """不对称确认：进防守快、出防守慢、进进攻快。"""

    default_days: int = 2
    enter_defensive_days: int = 1
    exit_defensive_days: int = 2
    enter_offensive_days: int = 1


def _confirm_need(current: str, target: str, policy: StateConfirmPolicy) -> int:
    cur_r = STATE_RANK.get(current, 2)
    nxt_r = STATE_RANK.get(target, 2)
    if nxt_r < cur_r:
        # 仅「恐慌」即时落地；降到偏弱仍用默认确认，避免过度防守
        if target == "恐慌":
            return max(int(policy.enter_defensive_days), 1)
        return max(int(policy.default_days), 1)
    if nxt_r > cur_r:
        # 离开「恐慌」要粘性确认；离开偏弱/进入进攻要快，恢复仓位
        if current == "恐慌":
            return max(int(policy.exit_defensive_days), 1)
        if target in _OFFENSIVE_STATES or current == "震荡偏弱":
            return max(int(policy.enter_offensive_days), 1)
        return max(int(policy.default_days), 1)
    return max(int(policy.default_days), 1)


def build_state_features(
    agent_activity: pd.DataFrame,
    minute_daily: pd.DataFrame,
    fund_flow: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """合并 Agent 活跃度与日频量价特征，供状态分类使用。"""
    act = agent_activity.copy()
    act["date"] = pd.to_datetime(act["date"]).dt.date

    daily = minute_daily.copy()
    daily["date"] = pd.to_datetime(daily["date"]).dt.date

    merged = act.merge(daily, on="date", how="inner", suffixes=("", "_daily"))

    if fund_flow is not None and not fund_flow.empty:
        ff = fund_flow.copy()
        ff["date"] = pd.to_datetime(ff["date"]).dt.date
        keep = ["date"]
        for col in ("main_net_ratio", "small_net_ratio", "pct_change"):
            if col in ff.columns:
                keep.append(col)
        merged = merged.merge(ff[keep], on="date", how="left")

    agent_cols = get_agent_columns(merged)
    if agent_cols:
        merged["agent_activity_total"] = merged[agent_cols].sum(axis=1)
        merged["agent_activity_max"] = merged[agent_cols].max(axis=1)
        total = merged["agent_activity_total"].replace(0, np.nan)
        shares = merged[agent_cols].div(total, axis=0)
        merged["agent_dispersion"] = shares.std(axis=1).fillna(0.0)
        merged["agent_leader_share"] = shares.max(axis=1).fillna(0.0)
        # 多因子同时活跃：份额超过 15% 的 agent 占比
        merged["bull_agreement"] = (shares.ge(0.15).sum(axis=1) / max(len(agent_cols), 1)).fillna(0.0)
        leader = merged[agent_cols].astype(float).idxmax(axis=1)
        change = leader.ne(leader.shift(fill_value=leader.iloc[0] if len(leader) else ""))
        merged["leader_persistence"] = leader.groupby(change.cumsum()).cumcount() + 1
        merged["leader_persistence"] = merged["leader_persistence"].astype(float)

    return merged.sort_values("date").reset_index(drop=True)


def confirm_state_sequence(
    states: list[str],
    confirm_days: int = 2,
    *,
    policy: StateConfirmPolicy | None = None,
) -> list[str]:
    """状态切换确认。

    - 有 policy：不对称确认（进防守快 / 出防守慢 / 进进攻快）
    - 仅 confirm_days：对称确认（兼容旧逻辑）
    """
    if len(states) <= 1:
        return list(states)

    if policy is None:
        if confirm_days <= 1:
            return list(states)
        need_fixed = max(int(confirm_days), 2)
        out: list[str] = []
        current = states[0]
        pending: str | None = None
        pending_count = 0
        for state in states:
            if state == current:
                pending = None
                pending_count = 0
                out.append(current)
                continue
            if state == pending:
                pending_count += 1
                if pending_count >= need_fixed:
                    current = state
                    pending = None
                    pending_count = 0
                out.append(current)
            else:
                pending = state
                pending_count = 1
                out.append(current)
        return out

    out: list[str] = []
    current = states[0]
    pending: str | None = None
    pending_count = 0
    pending_need = 1
    for state in states:
        if state == current:
            pending = None
            pending_count = 0
            out.append(current)
            continue
        need = _confirm_need(current, state, policy)
        if state == pending:
            pending_count += 1
            if pending_count >= pending_need:
                current = state
                pending = None
                pending_count = 0
            out.append(current)
        else:
            pending = state
            pending_count = 1
            pending_need = need
            # need=1：当日立即切换（加快进防守/进进攻）
            if need <= 1:
                current = state
                pending = None
                pending_count = 0
            out.append(current)
    return out


def _safe_col(df: pd.DataFrame, col: str, default: float = 0.0) -> pd.Series:
    if col in df.columns:
        return df[col].fillna(default).astype(float)
    return pd.Series(default, index=df.index, dtype=float)


def classify_market_states(
    features: pd.DataFrame,
    *,
    confirm_days: int = 1,
    confirm_policy: StateConfirmPolicy | None = None,
) -> pd.DataFrame:
    """基于规则的市场状态分类（路线四 MVP）。"""
    df = features.copy()

    ret_std = _safe_col(df, "return_std")
    vol_spike = _safe_col(df, "volume_spike_max")
    ret_mean = _safe_col(df, "return_mean")
    main_ratio = _safe_col(df, "main_net_ratio")
    agent_max = _safe_col(df, "agent_activity_max")
    dispersion = _safe_col(df, "agent_dispersion")
    persistence = _safe_col(df, "leader_persistence")
    agreement = _safe_col(df, "bull_agreement")

    # 前视修正(评审 R5):原实现用**全样本 max** 归一化,第 t 行的输入携带了未来最大值。
    # 改为扩展窗口(截至当前行的历史最大值),严格因果。
    ret_std_n = ret_std / (ret_std.cummax() + 1e-9)
    vol_spike_n = vol_spike / (vol_spike.cummax() + 1e-9)
    agent_n = agent_max / (agent_max.cummax() + 1e-9)

    states: list[str] = []
    scores: list[float] = []

    for i in range(len(df)):
        # 持续性/多因子共振略抬高多头分；分歧抬高压力分
        bull = float(
            ret_mean.iloc[i] * 100
            + main_ratio.iloc[i] * 0.3
            + agent_n.iloc[i] * 0.2
            + float(agreement.iloc[i]) * 0.25
            + min(float(persistence.iloc[i]), 5.0) * 0.03
        )
        stress = float(
            ret_std_n.iloc[i] * 0.4
            + vol_spike_n.iloc[i] * 0.4
            + max(-ret_mean.iloc[i], 0) * 50
            + float(dispersion.iloc[i]) * 0.15
        )

        if stress > 0.65 and bull < -0.2:
            state, score = "恐慌", stress
        elif stress > 0.45 and bull < 0:
            state, score = "震荡偏弱", stress - bull
        elif bull > 0.8 and stress < 0.35:
            state, score = "狂热", bull
        elif bull > 0.3:
            state, score = "震荡偏强", bull
        elif bull < -0.2 or stress > 0.45:
            state, score = "震荡偏弱", stress
        else:
            state, score = "横盘", 0.0

        states.append(state)
        scores.append(score)

    states = confirm_state_sequence(states, confirm_days=confirm_days, policy=confirm_policy)

    out = df[["date"]].copy() if "date" in df.columns else pd.DataFrame()
    out["market_state"] = states
    out["state_score"] = scores
    out["suggested_position"] = [POSITION_MAP[s] for s in states]
    if "symbol" in df.columns:
        out["symbol"] = df["symbol"].values
    return out


def state_distribution(states_df: pd.DataFrame) -> pd.DataFrame:
    """统计各状态出现频次（使用 states 中实际 suggested_position）。"""
    counts = states_df["market_state"].value_counts().reindex(MARKET_STATES, fill_value=0)
    total = counts.sum() or 1
    avg_pos = states_df.groupby("market_state")["suggested_position"].mean()
    return pd.DataFrame(
        {
            "market_state": counts.index,
            "count": counts.values,
            "ratio": counts.values / total,
            "suggested_position": [
                float(avg_pos.get(s, POSITION_MAP[s])) for s in counts.index
            ],
        }
    )


def compute_state_predictive_metrics(
    states_df: pd.DataFrame,
    daily_bars: pd.DataFrame,
) -> pd.DataFrame:
    """各状态对未来收益的预测力诊断（样本内）。"""
    bars = daily_bars.copy()
    bars["date"] = pd.to_datetime(bars["date"]).dt.date
    bars = bars.sort_values("date")
    bars["forward_1d"] = bars["close"].pct_change().shift(-1)
    bars["forward_5d"] = bars["close"].pct_change(5).shift(-5)

    st = states_df.copy()
    st["date"] = pd.to_datetime(st["date"]).dt.date
    merged = st.merge(bars[["date", "forward_1d", "forward_5d"]], on="date", how="inner")
    total = len(merged) or 1

    rows: list[dict] = []
    for state in MARKET_STATES:
        sub = merged[merged["market_state"] == state]
        if sub.empty:
            rows.append(
                {
                    "market_state": state,
                    "count": 0,
                    "ratio": 0.0,
                    "avg_forward_1d": np.nan,
                    "avg_forward_5d": np.nan,
                    "hit_rate_1d": np.nan,
                    "suggested_position": POSITION_MAP[state],
                }
            )
            continue
        rows.append(
            {
                "market_state": state,
                "count": len(sub),
                "ratio": len(sub) / total,
                "avg_forward_1d": float(sub["forward_1d"].mean()),
                "avg_forward_5d": float(sub["forward_5d"].mean()),
                "hit_rate_1d": float((sub["forward_1d"] > 0).mean()),
                "suggested_position": float(sub["suggested_position"].mean()),
            }
        )
    return pd.DataFrame(rows)

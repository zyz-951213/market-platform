from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from market_platform.agents.分析.推断.状态 import MARKET_STATES, POSITION_MAP, StateConfirmPolicy, confirm_state_sequence
from market_platform.agents.分析.回测.回测引擎 import walk_forward_split_index
from sklearn.mixture import GaussianMixture
from sklearn.preprocessing import StandardScaler

STATE_FEATURE_COLUMNS = [
    "return_mean",
    "return_std",
    "volume_spike_max",
    "realized_vol_mean",
    "agent_activity_max",
    "main_net_ratio",
    "agent_dispersion",
    "agent_leader_share",
    "bull_agreement",
    "leader_persistence",
]

CLUSTER_MAP_METHODS = ("forward_return", "centroid")


@dataclass
class HMMStateResult:
    states: pd.DataFrame
    transition_matrix: pd.DataFrame
    next_state_probs: pd.Series
    model: GaussianMixture


def attach_forward_returns(
    features: pd.DataFrame,
    daily_bars: pd.DataFrame,
    horizon: int = 1,
) -> pd.DataFrame:
    """为特征表附加 forward return（仅用于簇→状态映射与诊断，不进入 GMM 输入）。"""
    out = features.copy()
    bars = daily_bars.copy()
    bars["date"] = pd.to_datetime(bars["date"]).dt.date
    bars["close"] = pd.to_numeric(bars["close"], errors="coerce")
    bars = bars.loc[bars["close"].notna() & (bars["close"] > 0)].sort_values("date")
    h = max(int(horizon), 1)
    bars["forward_return"] = bars["close"].pct_change(h).shift(-h)
    bars["forward_return"] = bars["forward_return"].clip(lower=-0.5, upper=2.0)
    merged = out.merge(bars[["date", "forward_return"]], on="date", how="left")
    return merged.sort_values("date").reset_index(drop=True)


def _available_features(df: pd.DataFrame) -> list[str]:
    return [c for c in STATE_FEATURE_COLUMNS if c in df.columns]


def _map_clusters_to_states(centroids: np.ndarray, feature_names: list[str]) -> list[str]:
    """按 contemporaneous bullish 维度将 GMM 簇映射到语义状态名。"""
    idx_ret = feature_names.index("return_mean") if "return_mean" in feature_names else 0
    idx_vol = feature_names.index("return_std") if "return_std" in feature_names else 0
    scores = centroids[:, idx_ret] * 2.0 - centroids[:, idx_vol]
    order = np.argsort(scores)

    n = len(centroids)
    if n != len(MARKET_STATES):
        return [MARKET_STATES[min(i, len(MARKET_STATES) - 1)] for i in order]

    mapping = [""] * n
    for rank, cluster_id in enumerate(order):
        mapping[cluster_id] = MARKET_STATES[rank]
    return mapping


def _map_clusters_by_forward_return(labels: np.ndarray, forward_returns: pd.Series) -> list[str]:
    """按簇内平均 forward return 从低到高映射五档状态。"""
    n_clusters = int(labels.max()) + 1
    cluster_scores: list[tuple[int, float]] = []
    fwd = forward_returns.to_numpy(dtype=float)
    for cluster_id in range(n_clusters):
        mask = labels == cluster_id
        score = float(np.nanmean(fwd[mask])) if mask.any() else 0.0
        cluster_scores.append((cluster_id, score))

    cluster_scores.sort(key=lambda x: x[1])
    mapping = [""] * n_clusters
    for rank, (cluster_id, _) in enumerate(cluster_scores):
        state_idx = min(rank, len(MARKET_STATES) - 1)
        mapping[cluster_id] = MARKET_STATES[state_idx]
    return mapping


def _build_transition_matrix(state_sequence: list[str]) -> pd.DataFrame:
    n = len(MARKET_STATES)
    counts = np.zeros((n, n))
    idx = {s: i for i, s in enumerate(MARKET_STATES)}

    for cur, nxt in zip(state_sequence[:-1], state_sequence[1:], strict=False):
        if cur in idx and nxt in idx:
            counts[idx[cur], idx[nxt]] += 1

    row_sums = counts.sum(axis=1, keepdims=True)
    probs = np.divide(counts, row_sums, where=row_sums > 0, out=np.full_like(counts, 1.0 / n))
    return pd.DataFrame(probs, index=MARKET_STATES, columns=MARKET_STATES)


def classify_with_hmm(
    features: pd.DataFrame,
    n_states: int = 5,
    random_state: int = 42,
    cluster_map_method: str = "forward_return",
    *,
    confirm_days: int = 1,
    confirm_policy: StateConfirmPolicy | None = None,
    walk_forward_cluster_map: bool = False,
    walk_forward_ratio: float | None = 0.7,
) -> HMMStateResult:
    """GMM 聚类 + Markov 转移矩阵，输出状态与明日概率。"""
    feature_cols = _available_features(features)
    if len(feature_cols) < 2:
        raise ValueError("Need at least 2 state features for HMM classification")

    x = features[feature_cols].fillna(0).to_numpy()
    scaler = StandardScaler()
    x_scaled = scaler.fit_transform(x)

    n_states = min(n_states, len(x_scaled))
    model = GaussianMixture(n_components=n_states, random_state=random_state)

    split = walk_forward_split_index(len(x_scaled), walk_forward_ratio) if walk_forward_cluster_map else None
    if split is not None:
        # 仅用训练段拟合 GMM，再全样本预测，降低标签泄漏
        model.fit(x_scaled[:split])
        labels = model.predict(x_scaled)
    else:
        labels = model.fit_predict(x_scaled)

    method = cluster_map_method.lower()
    if method not in CLUSTER_MAP_METHODS:
        raise ValueError(f"cluster_map_method must be one of {CLUSTER_MAP_METHODS}")

    if method == "forward_return" and "forward_return" in features.columns:
        fwd = features["forward_return"]
        if split is not None:
            cluster_to_state = _map_clusters_by_forward_return(labels[:split], fwd.iloc[:split])
        else:
            cluster_to_state = _map_clusters_by_forward_return(labels, fwd)
    else:
        cluster_to_state = _map_clusters_to_states(model.means_, feature_cols)

    states = [cluster_to_state[int(lbl)] for lbl in labels]
    states = confirm_state_sequence(states, confirm_days=confirm_days, policy=confirm_policy)

    out = features[["date"]].copy() if "date" in features.columns else pd.DataFrame(index=features.index)
    out["market_state"] = states
    out["cluster_id"] = labels
    out["suggested_position"] = [POSITION_MAP[s] for s in states]

    transition = _build_transition_matrix(states)
    last_state = states[-1]
    next_probs = transition.loc[last_state]

    return HMMStateResult(
        states=out,
        transition_matrix=transition,
        next_state_probs=next_probs,
        model=model,
    )

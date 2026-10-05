from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from market_platform.agents.settings import NMFConfig
from market_platform.agents.分析.推断.智能体角色 import (
    infer_agent_names,
    rename_agent_result,
    save_agent_names,
)
from market_platform.agents.分析.特征.日频 import (
    display_feature_name,
    feature_matrix_to_array,
    normalize_feature_matrix,
)
from market_platform.agents.核心.工具.读写 import ensure_dir, save_parquet
from sklearn.decomposition import NMF


@dataclass
class AgentActivityResult:
    """NMF 分解结果。"""

    activity: pd.DataFrame  # date × Agent_i 活跃度 W
    patterns: pd.DataFrame  # Agent_i × feature 行为指纹 H
    reconstruction_error: float
    feature_names: list[str]

    def save(self, output_dir: Path, agent_name_map: dict[str, str] | None = None) -> None:
        ensure_dir(output_dir)
        activity = self.activity
        patterns = self.patterns
        if agent_name_map:
            activity, patterns = rename_agent_result(activity, patterns, agent_name_map)
            save_agent_names(output_dir / "agent_names.json", agent_name_map)
        save_parquet(activity, output_dir / "agent_activity.parquet")
        save_parquet(patterns, output_dir / "agent_patterns.parquet")


def decompose_agent_activity(
    feature_matrix: pd.DataFrame,
    config: NMFConfig,
    normalize: bool = True,
) -> AgentActivityResult:
    """对日频特征矩阵做 NMF，得到 Agent 活跃度与行为模式。"""
    working = feature_matrix
    if normalize:
        working, _ = normalize_feature_matrix(feature_matrix)

    X, feature_names, meta = feature_matrix_to_array(working)

    if X.shape[0] < config.n_components:
        raise ValueError(
            f"Need at least {config.n_components} samples, got {X.shape[0]}"
        )

    model = NMF(
        n_components=config.n_components,
        init="nndsvda",
        max_iter=config.max_iter,
        random_state=config.random_state,
    )
    W = model.fit_transform(X)
    H = model.components_

    agent_cols = [f"Agent_{i}" for i in range(config.n_components)]
    activity = meta.copy()
    for i, col in enumerate(agent_cols):
        activity[col] = W[:, i]

    patterns = pd.DataFrame(H, columns=feature_names)
    patterns.insert(0, "agent", agent_cols)

    return AgentActivityResult(
        activity=activity.sort_values("date").reset_index(drop=True),
        patterns=patterns,
        reconstruction_error=float(model.reconstruction_err_),
        feature_names=feature_names,
    )


def apply_agent_role_names(result: AgentActivityResult) -> tuple[AgentActivityResult, dict[str, str]]:
    """为 NMF 结果赋予功能化 Agent 名称。"""
    mapping = infer_agent_names(result.patterns)
    activity, patterns = rename_agent_result(result.activity, result.patterns, mapping)
    return (
        AgentActivityResult(
            activity=activity,
            patterns=patterns,
            reconstruction_error=result.reconstruction_error,
            feature_names=result.feature_names,
        ),
        mapping,
    )


def label_dominant_features(
    patterns: pd.DataFrame,
    agent_name_map: dict[str, str] | None = None,
    top_k: int = 3,
) -> pd.DataFrame:
    """为每个隐因子标注 dominant 特征（便于事后语义解释）。"""
    rows = []
    feature_cols = [c for c in patterns.columns if c not in {"agent", "agent_id"}]
    for _, row in patterns.iterrows():
        agent_id = str(row.get("agent_id", row["agent"]))
        agent_name = str(row["agent"])
        if agent_name_map and agent_id in agent_name_map:
            agent_name = agent_name_map[agent_id]
        weights = row[feature_cols].astype(float)
        top = weights.nlargest(top_k)
        rows.append(
            {
                "agent_id": agent_id,
                "agent": agent_name,
                "top_features": ", ".join(
                    f"{display_feature_name(k)}({v:.3f})" for k, v in top.items()
                ),
            }
        )
    return pd.DataFrame(rows)

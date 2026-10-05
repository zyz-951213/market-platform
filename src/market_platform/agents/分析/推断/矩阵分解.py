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
    说明: dict | None = None  # 追加式分解的记账(历史保留行/新增行/是否重拟合)

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


def 追加式分解(
    feature_matrix: pd.DataFrame,
    config: NMFConfig,
    *,
    已有活跃度: pd.DataFrame | None = None,
    已有模式: pd.DataFrame | None = None,
    重拟合阈值: int = 250,
    归一方式: str | None = None,
) -> AgentActivityResult:
    """point-in-time 追加式 NMF:历史活跃度**保持原值**,只算新增行。

    为什么需要(评审 R4):历史实现在每次增量分析时对**整个窗口**重新
    `fit_transform`,于是同一根 K 线的"参与者活跃度/市场状态"会被后续数据改写,
    历史统计与回测结果因此不可复现。

    规则(全部严格因果):
    1. 已有活跃度覆盖的历史行:直接沿用,永不重算;
    2. 新增行:用**截至历史末日的基**做非负最小二乘投影(nnls),等价于固定 H 的 W;
    3. 若新增行数 ≥ `重拟合阈值`(或没有可用旧基/特征列不匹配),
       则在**历史窗口**(不含新增行)上重拟合一次基,再用新基投影新增行 ——
       重拟合只影响新增行,历史行依旧保持原值。

    参数 `归一方式` 见 `normalize_feature_matrix`(默认扩展窗口,因果)。
    """
    import numpy as np
    from scipy.optimize import nnls

    working, _ = normalize_feature_matrix(feature_matrix, 归一方式=归一方式)
    X, feature_names, meta = feature_matrix_to_array(working)
    if X.shape[0] < config.n_components:
        raise ValueError(f"Need at least {config.n_components} samples, got {X.shape[0]}")

    n_components = int(config.n_components)
    agent_cols = [f"Agent_{i}" for i in range(n_components)]

    历史行数 = 0
    if 已有活跃度 is not None and not 已有活跃度.empty and "date" in 已有活跃度.columns:
        日期列 = pd.to_datetime(meta["date"])
        历史末日 = pd.to_datetime(已有活跃度["date"]).max()
        历史行数 = int((日期列 <= 历史末日).sum())

    历史活跃度 = None
    历史模式 = None
    if 历史行数 and 已有活跃度 is not None:
        可用列 = [c for c in agent_cols if c in 已有活跃度.columns]
        if len(可用列) == n_components:
            历史活跃度 = 已有活跃度[["date"] + 可用列].head(历史行数).reset_index(drop=True)
            历史模式 = 已有模式

    旧基可用 = (
        历史模式 is not None
        and not 历史模式.empty
        and all(c in 历史模式.columns for c in feature_names)
        and len(历史模式) == n_components
    )
    新增行数 = X.shape[0] - 历史行数
    需要重拟合 = (not 旧基可用) or 新增行数 >= 重拟合阈值 or 历史行数 < n_components

    说明: dict = {
        "历史保留行": 历史行数,
        "新增行": 新增行数,
        "重拟合": bool(需要重拟合),
        "重拟合窗口": 历史行数 if 需要重拟合 else None,
    }

    if 需要重拟合 and 历史行数 >= n_components:
        # 只在本块之前的窗口上拟合(不触碰历史行的既有取值)
        基模型 = NMF(
            n_components=n_components,
            init="nndsvda",
            max_iter=config.max_iter,
            random_state=config.random_state,
        )
        基模型.fit(X[:历史行数])
        H = 基模型.components_
    elif 旧基可用:
        H = 历史模式[feature_names].to_numpy(dtype=float)
    else:
        # 没有历史行可用:退化为一次性分解(首跑)
        基模型 = NMF(
            n_components=n_components,
            init="nndsvda",
            max_iter=config.max_iter,
            random_state=config.random_state,
        )
        W_全 = 基模型.fit_transform(X)
        H = 基模型.components_
        活跃度 = meta.copy()
        for i, col in enumerate(agent_cols):
            活跃度[col] = W_全[:, i]
        模式 = pd.DataFrame(H, columns=feature_names)
        模式.insert(0, "agent", agent_cols)
        return AgentActivityResult(
            activity=活跃度.sort_values("date").reset_index(drop=True),
            patterns=模式,
            reconstruction_error=float(基模型.reconstruction_err_),
            feature_names=feature_names,
            说明={**说明, "重拟合": True, "重拟合窗口": X.shape[0]},
        )

    # 新增行:固定基 H,逐行非负最小二乘投影
    Ht = H.T
    新增活跃度 = np.zeros((max(新增行数, 0), n_components), dtype=float)
    for i in range(max(新增行数, 0)):
        w, _ = nnls(Ht, X[历史行数 + i])
        新增活跃度[i] = w

    新行 = meta.iloc[历史行数:].reset_index(drop=True)
    for i, col in enumerate(agent_cols):
        新行[col] = 新增活跃度[:, i]

    if 历史活跃度 is not None:
        活跃度 = pd.concat([历史活跃度[["date"] + agent_cols], 新行[["date"] + agent_cols]], ignore_index=True)
    else:
        活跃度 = 新行

    模式 = pd.DataFrame(H, columns=feature_names)
    模式.insert(0, "agent", agent_cols)

    误差 = float(np.sqrt(((X[历史行数:] - 新增活跃度 @ H) ** 2).mean())) if 新增行数 else float("nan")
    return AgentActivityResult(
        activity=活跃度.sort_values("date").reset_index(drop=True),
        patterns=模式,
        reconstruction_error=误差,
        feature_names=feature_names,
        说明=说明,
    )

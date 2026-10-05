from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd

# 特征关键词 → 可读功能名（按区分度排序）
ROLE_RULES: list[tuple[str, list[str]]] = [
    ("北向资金", ["north_net"]),
    ("融资融券", ["margin_balance", "margin_"]),
    ("主力资金", ["main_net"]),
    ("分级资金流", ["small_net", "large_net"]),
    ("尾盘量能", ["tail_volume", "open_volume"]),
    ("波动冲击", ["price_impact", "realized_vol", "return_std", "high_low_range"]),
    ("放量异动", ["volume_spike"]),
    ("成交规模", ["volume_total", "amount_total"]),
    ("价格动量", ["pct_change", "return_mean"]),
    ("均价偏离", ["vwap_dev"]),
]

_AGENT_ID_RE = re.compile(r"^Agent_\d+$", re.IGNORECASE)


def _agent_sort_key(name: str) -> int:
    m = re.search(r"(\d+)$", str(name))
    return int(m.group(1)) if m else 0


def build_agent_display_map(activity: pd.DataFrame, sym_dir: Path) -> dict[str, str]:
    """将 Agent_* 列名解析为中文功能名（用于展示）。"""
    cols = get_agent_columns(activity)
    mapping = load_agent_names(sym_dir / "nmf" / "agent_names.json")

    if cols and not any(_AGENT_ID_RE.match(str(c)) for c in cols):
        return {str(c): str(c) for c in cols}

    labels_path = sym_dir / "nmf" / "agent_pattern_labels.csv"
    if labels_path.exists():
        labels = pd.read_csv(labels_path)
        for _, row in labels.iterrows():
            agent_id = str(row.get("agent_id", "")).strip()
            agent_name = str(row.get("agent", "")).strip()
            if agent_id and agent_name:
                mapping[agent_id] = agent_name

    roles = [role for role, _ in ROLE_RULES]
    used = set(mapping.values())
    for col in sorted(cols, key=_agent_sort_key):
        key = str(col)
        if key in mapping:
            continue
        if _AGENT_ID_RE.match(key):
            chosen = next((role for role in roles if role not in used), None)
            if chosen is None:
                chosen = f"交易因子{_agent_sort_key(key)}"
            mapping[key] = chosen
            used.add(chosen)
        else:
            mapping[key] = key
    return mapping


def display_agent_name(name: str, mapping: dict[str, str] | None = None) -> str:
    key = str(name)
    if mapping and key in mapping:
        return mapping[key]
    if _AGENT_ID_RE.match(key):
        idx = _agent_sort_key(key)
        roles = [role for role, _ in ROLE_RULES]
        if idx < len(roles):
            return roles[idx]
        return f"交易因子{idx}"
    return key


def _score_role(weights: pd.Series, keywords: list[str]) -> float:
    score = 0.0
    for feat, weight in weights.items():
        feat_l = str(feat).lower()
        for kw in keywords:
            if kw in feat_l:
                score += float(weight)
                break
    return score


def infer_agent_names(patterns: pd.DataFrame) -> dict[str, str]:
    """根据 NMF 行为指纹为每个隐因子赋予功能化名称。"""
    feature_cols = [c for c in patterns.columns if c not in {"agent", "agent_id"}]
    scored_agents: list[tuple[str, list[tuple[str, float]]]] = []

    for _, row in patterns.iterrows():
        agent_id = str(row.get("agent_id", row["agent"]))
        weights = row[feature_cols].astype(float)
        role_scores = [(_score_role(weights, keywords), role) for role, keywords in ROLE_RULES]
        role_scores.sort(key=lambda x: x[0], reverse=True)
        scored_agents.append((agent_id, [(role, score) for score, role in role_scores if score > 0]))

    # 置信度高的 Agent 优先领取「canonical」名称，避免重复
    scored_agents.sort(key=lambda x: x[1][0][1] if x[1] else 0.0, reverse=True)

    mapping: dict[str, str] = {}
    used: set[str] = set()

    for agent_id, role_scores in scored_agents:
        chosen = None
        for role, score in role_scores:
            if role not in used:
                chosen = role
                break
        if chosen is None and role_scores:
            base = role_scores[0][0]
            n = 2
            while f"{base}{n}" in used:
                n += 1
            chosen = f"{base}{n}"
        if chosen is None:
            suffix = agent_id.split("_")[-1]
            chosen = f"交易因子{suffix}"
        mapping[agent_id] = chosen
        used.add(chosen)

    return mapping


def get_agent_columns(df: pd.DataFrame, role_names: list[str] | None = None) -> list[str]:
    """识别 Agent 活跃度列（兼容 Agent_i 与功能化命名）。"""
    if role_names:
        return [c for c in df.columns if c in role_names]

    agent_like = [c for c in df.columns if _AGENT_ID_RE.match(str(c))]
    if agent_like:
        return agent_like

    canonical = {role for role, _ in ROLE_RULES}
    numbered = {role for role in df.columns if any(str(role).startswith(r) for r in canonical)}
    if numbered:
        return sorted(numbered)

    meta = {
        "date",
        "symbol",
        "symbol_daily",
        "minute_bars",
        "volume_total",
        "amount_total",
        "return_mean",
        "return_std",
        "realized_vol_mean",
        "realized_vol_max",
        "volume_spike_max",
        "volume_spike_mean",
        "vwap_dev_mean",
        "vwap_dev_std",
        "high_low_range_mean",
        "price_impact_mean",
        "price_impact_max",
        "tail_volume_ratio",
        "open_volume_ratio",
        "main_net_ratio",
        "small_net_ratio",
        "pct_change",
        "agent_activity_total",
        "agent_activity_max",
        "forward_return",
        "main_net_inflow",
        "baseline_flow",
        "residual",
        "residual_zscore",
        "market_state",
        "suggested_position",
        "state_score",
    }
    beta_cols = {c for c in df.columns if str(c).startswith("beta_")}
    return [c for c in df.columns if c not in meta and c not in beta_cols and df[c].dtype.kind in "fiu"]


def load_agent_names(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict) and "mapping" in data:
        return {str(k): str(v) for k, v in data["mapping"].items()}
    if isinstance(data, dict):
        return {str(k): str(v) for k, v in data.items() if str(k).startswith("Agent_")}
    return {}


def save_agent_names(path: Path, mapping: dict[str, str]) -> None:
    payload = {
        "mapping": mapping,
        "roles": list(dict.fromkeys(mapping.values())),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def rename_agent_result(activity: pd.DataFrame, patterns: pd.DataFrame, mapping: dict[str, str]) -> tuple[pd.DataFrame, pd.DataFrame]:
    renamed_activity = activity.rename(columns={k: v for k, v in mapping.items() if k in activity.columns})
    renamed_patterns = patterns.copy()
    if "agent_id" not in renamed_patterns.columns:
        renamed_patterns.insert(1, "agent_id", renamed_patterns["agent"])
    renamed_patterns["agent"] = renamed_patterns["agent_id"].map(mapping).fillna(renamed_patterns["agent"])
    return renamed_activity, renamed_patterns

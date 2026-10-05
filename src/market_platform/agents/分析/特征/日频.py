from __future__ import annotations

import numpy as np
import pandas as pd

# NMF 使用的数值列（非负化后输入）
NMF_FEATURE_COLUMNS = [
    # 分钟汇总
    "volume_total",
    "amount_total",
    "return_std",
    "realized_vol_mean",
    "realized_vol_max",
    "volume_spike_max",
    "volume_spike_mean",
    "vwap_dev_std",
    "high_low_range_mean",
    "price_impact_mean",
    "price_impact_max",
    "tail_volume_ratio",
    "open_volume_ratio",
    # 资金流向（弱监督）
    "main_net_inflow_abs",
    "large_net_inflow_abs",
    "small_net_inflow_abs",
    "main_net_ratio_abs",
    "small_net_ratio_abs",
    # 宏观辅助
    "north_net_inflow_abs",
    "margin_balance_change_abs",
]

FEATURE_DISPLAY_NAMES: dict[str, str] = {
    "volume_total": "成交总量",
    "amount_total": "成交总额",
    "return_std": "收益波动",
    "realized_vol_mean": "平均实现波动",
    "realized_vol_max": "最大实现波动",
    "volume_spike_max": "最大放量倍数",
    "volume_spike_mean": "平均放量倍数",
    "vwap_dev_std": "均价偏离波动",
    "high_low_range_mean": "日内振幅",
    "price_impact_mean": "平均价格冲击",
    "price_impact_max": "最大价格冲击",
    "tail_volume_ratio": "尾盘量能占比",
    "open_volume_ratio": "开盘量能占比",
    "main_net_inflow_abs": "主力净流入",
    "large_net_inflow_abs": "大单净流入",
    "small_net_inflow_abs": "小单净流入",
    "main_net_ratio_abs": "主力净占比",
    "small_net_ratio_abs": "小单净占比",
    "north_net_inflow_abs": "北向净流入",
    "margin_balance_change_abs": "融资余额变动",
}


def display_feature_name(name: str) -> str:
    return FEATURE_DISPLAY_NAMES.get(str(name), str(name))


def format_top_features_text(text: str) -> str:
    """将 top_features 中的英文特征名转为中文展示。"""
    import re

    parts: list[str] = []
    for chunk in str(text).split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        m = re.match(r"^(.+?)\(([^)]+)\)$", chunk)
        if m:
            feat, weight = m.group(1).strip(), m.group(2).strip()
            parts.append(f"{display_feature_name(feat)}({weight})")
        else:
            parts.append(display_feature_name(chunk))
    return ", ".join(parts)


def _abs_nonneg(series: pd.Series) -> pd.Series:
    return series.fillna(0).abs()


def build_daily_feature_matrix(
    minute_daily: pd.DataFrame,
    fund_flow: pd.DataFrame,
    northbound: pd.DataFrame,
    margin: pd.DataFrame,
) -> pd.DataFrame:
    """合并多源日频特征，构建 NMF 输入矩阵 X。"""
    base = minute_daily.copy()
    base["date"] = pd.to_datetime(base["date"]).dt.date

    if not fund_flow.empty:
        ff = fund_flow.copy()
        ff["date"] = pd.to_datetime(ff["date"]).dt.date
        ff_cols = [
            "main_net_inflow",
            "large_net_inflow",
            "small_net_inflow",
            "main_net_ratio",
            "small_net_ratio",
        ]
        keep = ["date"] + [c for c in ff_cols if c in ff.columns]
        base = base.merge(ff[keep], on="date", how="left")

    if not northbound.empty:
        nb = northbound.copy()
        nb["date"] = pd.to_datetime(nb["date"]).dt.date
        base = base.merge(nb[["date", "north_net_inflow"]], on="date", how="left")

    if not margin.empty:
        mg = margin.copy()
        mg["date"] = pd.to_datetime(mg["date"]).dt.date
        base = base.merge(mg[["date", "margin_balance_change"]], on="date", how="left")

    # 构造非负特征
    mapping = {
        "main_net_inflow_abs": "main_net_inflow",
        "large_net_inflow_abs": "large_net_inflow",
        "small_net_inflow_abs": "small_net_inflow",
        "main_net_ratio_abs": "main_net_ratio",
        "small_net_ratio_abs": "small_net_ratio",
        "north_net_inflow_abs": "north_net_inflow",
        "margin_balance_change_abs": "margin_balance_change",
    }
    for new_col, src in mapping.items():
        if src in base.columns:
            base[new_col] = _abs_nonneg(base[src])
        else:
            base[new_col] = 0.0

    for col in NMF_FEATURE_COLUMNS:
        if col not in base.columns:
            base[col] = 0.0
        base[col] = base[col].fillna(0).clip(lower=0)

    meta_cols = ["date", "symbol"] if "symbol" in base.columns else ["date"]
    feature_cols = [c for c in NMF_FEATURE_COLUMNS if c in base.columns]
    return base[meta_cols + feature_cols].sort_values("date").reset_index(drop=True)


def feature_matrix_to_array(df: pd.DataFrame) -> tuple[np.ndarray, list[str], pd.DataFrame]:
    """提取 (X, feature_names, meta_df)。"""
    feature_cols = [c for c in NMF_FEATURE_COLUMNS if c in df.columns]
    meta_cols = [c for c in ["date", "symbol"] if c in df.columns]
    X = df[feature_cols].to_numpy(dtype=float)
    meta = df[meta_cols].copy()
    return X, feature_cols, meta


def normalize_feature_matrix(
    df: pd.DataFrame,
    *,
    归一方式: str | None = None,
) -> tuple[pd.DataFrame, dict[str, np.ndarray]]:
    """对 NMF 输入做 log1p + 列归一化，缓解量纲差异。

    **前视修正(评审 R5)**:历史实现用**全样本列 max** 归一,等于把未来信息带进
    每一行输入。现默认改为**扩展窗口(expanding)**:第 t 行只用第 1..t 行的最大值
    归一 —— 严格因果,不改 log1p 语义。

    归一方式:
      expanding(默认) — 逐行扩展窗口最大归一(因果)
      full           — 旧行为:全样本最大归一(有前视,仅用于复现旧结果)
    """
    feature_cols = [c for c in NMF_FEATURE_COLUMNS if c in df.columns]
    meta_cols = [c for c in ["date", "symbol"] if c in df.columns]

    方式 = (归一方式 or "expanding").strip().lower()
    scaled = df[feature_cols].astype(float).copy()
    scaled = np.log1p(scaled)

    scales: dict[str, np.ndarray] = {}
    if 方式 == "full":
        for col in feature_cols:
            col_max = scaled[col].max()
            if col_max > 0:
                scaled[col] = scaled[col] / col_max
            scales[col] = np.array([col_max if col_max > 0 else 1.0])
    else:
        # 扩展窗口:分母 = 截至当前行的历史最大值(无正值时用 1 避免除零)
        for col in feature_cols:
            列 = scaled[col]
            累积最大 = 列.cummax()
            分母 = 累积最大.where(累积最大 > 0, 1.0)
            scaled[col] = 列 / 分母
            scales[col] = 分母.to_numpy()

    out = df[meta_cols].copy()
    out[feature_cols] = scaled
    return out, scales

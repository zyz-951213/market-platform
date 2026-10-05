from __future__ import annotations

import numpy as np
import pandas as pd


def compute_minute_features(minute_df: pd.DataFrame, config: dict | None = None) -> pd.DataFrame:
    """从分钟 K 线提取微观结构代理特征。"""
    cfg = config or {}
    vol_window = int(cfg.get("volume_spike_window", 20))
    rv_window = int(cfg.get("realized_vol_window", 5))

    df = minute_df.copy()
    df = df.sort_values("datetime")

    df["return"] = df["close"].pct_change()
    df["log_return"] = np.log(df["close"]).diff()

    if "vwap" not in df.columns or df["vwap"].isna().all():
        df["vwap"] = df["amount"] / df["volume"].replace(0, np.nan)
    df["vwap_deviation"] = (df["close"] - df["vwap"]) / df["vwap"].replace(0, np.nan)

    df["realized_vol"] = df.groupby("date")["return"].transform(
        lambda s: s.rolling(rv_window, min_periods=2).std()
    )

    vol_mean = df.groupby("date")["volume"].transform(
        lambda s: s.rolling(vol_window, min_periods=5).mean()
    )
    df["volume_spike"] = df["volume"] / vol_mean.replace(0, np.nan)
    df["high_low_range"] = (df["high"] - df["low"]) / df["close"].replace(0, np.nan)

    impact_denom = np.sqrt(df["volume"].replace(0, np.nan))
    df["price_impact"] = df["return"].abs() / impact_denom

    df["volume_imbalance_proxy"] = np.sign(df["return"]) * df["volume"]

    return df.replace([np.inf, -np.inf], np.nan)


def summarize_minute_to_daily(minute_features: pd.DataFrame) -> pd.DataFrame:
    """将分钟特征汇总为日频，供 NMF 使用。"""
    g = minute_features.groupby("date")

    daily = g.agg(
        minute_bars=("datetime", "count"),
        volume_total=("volume", "sum"),
        amount_total=("amount", "sum"),
        return_mean=("return", "mean"),
        return_std=("return", "std"),
        realized_vol_mean=("realized_vol", "mean"),
        realized_vol_max=("realized_vol", "max"),
        volume_spike_max=("volume_spike", "max"),
        volume_spike_mean=("volume_spike", "mean"),
        vwap_dev_mean=("vwap_deviation", "mean"),
        vwap_dev_std=("vwap_deviation", "std"),
        high_low_range_mean=("high_low_range", "mean"),
        price_impact_mean=("price_impact", "mean"),
        price_impact_max=("price_impact", "max"),
    ).reset_index()

    # 尾盘 30 分钟成交量占比（A 股 240 分钟，取最后 30 根）
    tail_ratios: list[float] = []
    for date_val, day_df in minute_features.groupby("date"):
        day_df = day_df.sort_values("datetime")
        total = day_df["volume"].sum()
        tail = day_df.tail(30)["volume"].sum()
        tail_ratios.append(tail / total if total > 0 else np.nan)

    daily["tail_volume_ratio"] = tail_ratios

    # 开盘 30 分钟占比
    open_ratios: list[float] = []
    for _, day_df in minute_features.groupby("date"):
        day_df = day_df.sort_values("datetime")
        total = day_df["volume"].sum()
        head = day_df.head(30)["volume"].sum()
        open_ratios.append(head / total if total > 0 else np.nan)

    daily["open_volume_ratio"] = open_ratios

    if "symbol" in minute_features.columns:
        daily["symbol"] = minute_features["symbol"].iloc[0]

    return daily.sort_values("date").reset_index(drop=True)

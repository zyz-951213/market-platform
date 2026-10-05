"""
K 线重采样 — 日频转周频等
"""

from typing import Optional

import pandas as pd
from loguru import logger

from market_platform.predictor.配置 import get_config


def _aggregate_weekly_news(series: pd.Series, max_items: int = 5) -> Optional[str]:
    items: list[str] = []
    for text in series.dropna():
        for part in str(text).split("；"):
            part = part.strip()
            if part and part not in items:
                items.append(part)
    if not items:
        return None
    return "；".join(items[:max_items])


def resample_bars(df: pd.DataFrame, bar_freq: str | None = None) -> pd.DataFrame:
    """
    将 OHLCV 重采样到指定频率
    - daily: 不处理
    - weekly: 按自然周（周五收盘）聚合
    """
    cfg = get_config().get("data", {})
    bar_freq = bar_freq or cfg.get("bar_freq", "daily")

    if bar_freq == "daily":
        return df

    if bar_freq != "weekly":
        raise ValueError(f"不支持的 bar_freq: {bar_freq}")

    df = df.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()

    agg = {
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    }

    ohlcv = set(agg.keys())
    news_max = int(get_config().get("features", {}).get("external", {}).get("macro_news", {}).get("max_headlines_per_week", 5))
    for col in df.columns:
        if col in ohlcv:
            continue
        if col == "macro_news_summary":
            agg[col] = lambda s: _aggregate_weekly_news(s, max_items=news_max)
        elif col == "macro_news_count":
            agg[col] = "sum"
        elif "flow" in col or col.endswith("_net"):
            agg[col] = "sum"
        else:
            agg[col] = "last"

    weekly = df.resample("W-FRI").agg(agg)
    weekly = weekly.dropna(subset=["close"]).reset_index()

    logger.info(f"📅 日频 → 周频: {len(df)} 日 → {len(weekly)} 周")
    return weekly

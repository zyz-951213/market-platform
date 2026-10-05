"""
数据清洗模块 — 处理缺失值、异常值、对齐时间序列
"""

import pandas as pd
import numpy as np
from loguru import logger


def clean_daily_data(df: pd.DataFrame) -> pd.DataFrame:
    """
    清洗日度数据
    - 处理缺失值
    - 处理异常跳空
    - 确保日期连续性
    """
    df = df.copy()

    # 必备列
    required = ["date", "open", "high", "low", "close", "volume"]
    for col in required:
        if col not in df.columns:
            raise ValueError(f"缺少必要列: {col}")

    # 1. 按日期排序
    df = df.sort_values("date").reset_index(drop=True)

    # 2. 去除全空行
    df = df.dropna(subset=required, how="all")

    # 3. 填充少量缺失（用前值填充）
    df[["open", "high", "low", "close", "volume"]] = df[
        ["open", "high", "low", "close", "volume"]
    ].ffill()

    # 4. 去除异常值（涨跌幅超过 ±30% 的视为数据错误）
    df["pct_chg"] = df["close"].pct_change()
    outlier_mask = df["pct_chg"].abs() > 0.30
    if outlier_mask.any():
        logger.warning(f"⚠️ 发现 {outlier_mask.sum()} 个异常涨跌日（>±30%），已修复")
        df.loc[outlier_mask, ["open", "high", "low", "close"]] = np.nan
        df[["open", "high", "low", "close"]] = df[
            ["open", "high", "low", "close"]
        ].ffill()

    # 5. 补充日期连续性（识别缺失交易日）
    df["date"] = pd.to_datetime(df["date"])
    date_range = pd.date_range(
        start=df["date"].min(), end=df["date"].max(), freq="B"  # 仅交易日
    )
    missing_dates = set(date_range) - set(df["date"])
    if missing_dates:
        logger.info(f"📅 缺失 {len(missing_dates)} 个交易日（非交易日或停牌）")

    logger.info(f"✅ 清洗完成，{len(df)} 条数据")
    return df.drop(columns=["pct_chg"])

"""
特征工程模块 — 从日度行情数据衍生技术指标 & 自定义因子
"""

import pandas as pd
import numpy as np
from loguru import logger

from market_platform.predictor.配置 import get_config
from market_platform.predictor.采集.基本面 import add_fundamental_features


def add_technical_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """添加常见技术指标"""
    df = df.copy()
    close = df["close"]
    high = df["high"]
    low = df["low"]
    volume = df["volume"]

    # ======== 均线 ========
    for period in [5, 10, 20, 60]:
        df[f"sma_{period}"] = close.rolling(period).mean()
        df[f"ema_{period}"] = close.ewm(span=period, adjust=False).mean()

    # ======== RSI ========
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta.where(delta < 0, 0.0))
    avg_gain = gain.rolling(14).mean()
    avg_loss = loss.rolling(14).mean()
    rs = avg_gain / (avg_loss + 1e-10)
    df["rsi_14"] = 100 - (100 / (1 + rs))

    # ======== MACD ========
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    df["macd"] = ema12 - ema26
    df["macd_signal"] = df["macd"].ewm(span=9, adjust=False).mean()
    df["macd_hist"] = df["macd"] - df["macd_signal"]

    # ======== 布林带 ========
    sma20 = close.rolling(20).mean()
    std20 = close.rolling(20).std()
    df["boll_upper"] = sma20 + 2 * std20
    df["boll_middle"] = sma20
    df["boll_lower"] = sma20 - 2 * std20
    df["boll_position"] = (close - df["boll_lower"]) / (df["boll_upper"] - df["boll_lower"] + 1e-10)

    # ======== ATR ========
    tr = pd.concat(
        [high - low, (high - close.shift()).abs(), (low - close.shift()).abs()],
        axis=1,
    ).max(axis=1)
    df["atr_14"] = tr.rolling(14).mean()
    df["atr_pct"] = df["atr_14"] / close * 100  # 相对波动率

    # ======== 成交量均线 ========
    for period in [5, 20]:
        df[f"volume_sma_{period}"] = volume.rolling(period).mean()
    df["volume_ratio"] = volume / (df["volume_sma_20"] + 1e-10)

    return df


def add_custom_factors(df: pd.DataFrame) -> pd.DataFrame:
    """添加自定义衍生因子"""
    df = df.copy()
    close = df["close"]
    volume = df["volume"]

    # 价格动量（N日收益率）
    for period in [1, 5, 10, 20, 60]:
        df[f"momentum_{period}d"] = close.pct_change(period)

    # 波动率
    df["volatility_20d"] = close.pct_change().rolling(20).std()
    df["volatility_60d"] = close.pct_change().rolling(60).std()

    # 成交量变化
    df["volume_change_1d"] = volume.pct_change()

    # 量价关系
    df["money_flow"] = close * volume
    df["money_flow_change"] = df["money_flow"].pct_change()

    # 主动买卖压力（近似）
    df["hl_spread"] = (df["high"] - df["low"]) / close
    df["close_position"] = (close - df["low"]) / (df["high"] - df["low"] + 1e-10)

    # 乖离率 (close vs SMA)
    for period in [5, 20, 60]:
        sma = close.rolling(period).mean()
        df[f"deviation_{period}"] = (close - sma) / (sma + 1e-10) * 100

    return df


def build_soft_label(df: pd.DataFrame, horizon: int = 1, threshold: float = 0.003, sigma: float = 0.005) -> pd.DataFrame:
    """构造软标签（概率分布），替代硬分类。

    用高斯 CDF 将实际收益率映射到 [跌/平/涨] 三个类的概率权重，
    消除阈值附近（如 +0.29% 和 +0.31%）被硬分到不同类的问题。

    参数:
        horizon: 预测未来几天
        threshold: "平"区的半宽（收益率在此区间内主要归为平）
        sigma: 平滑程度，越大边界越模糊

    返回:
        新增三列 label_soft_dn / label_soft_flat / label_soft_up
    """
    df = df.copy()
    from scipy.stats import norm

    future_return = df["close"].shift(-horizon) / df["close"] - 1
    df["label_return"] = future_return

    # 对每个样本计算软标签
    n = len(df)
    soft = np.full((n, 3), 1.0 / 3.0, dtype=np.float32)

    for i in range(n):
        ret = future_return.iloc[i]
        if pd.isna(ret):
            soft[i] = [1.0 / 3.0, 1.0 / 3.0, 1.0 / 3.0]  # 未知，均匀分布
            continue
        r = float(ret)
        # 高斯CDF映射
        up_weight = norm.cdf((r - threshold / 2) / sigma)
        dn_weight = 1.0 - norm.cdf((r + threshold / 2) / sigma)
        flat_weight = 1.0 - up_weight - dn_weight
        total = dn_weight + flat_weight + up_weight
        soft[i] = [dn_weight / total, flat_weight / total, up_weight / total]

    df["label_soft_dn"] = soft[:, 0]
    df["label_soft_flat"] = soft[:, 1]
    df["label_soft_up"] = soft[:, 2]
    return df


def build_label(df: pd.DataFrame, horizon: int = 1, threshold: float = 0.005) -> pd.DataFrame:
    """
    构造标签
    - horizon: 预测未来几天
    - threshold: 涨跌方向阈值（< threshold 算"平"）
    返回:
        label_direction: -1 跌, 0 平, 1 涨
        label_return: 未来 horizon 日收益率
    """
    df = df.copy()
    future_return = df["close"].shift(-horizon) / df["close"] - 1
    df["label_return"] = future_return

    # 三分类
    df["label_direction"] = 0
    df.loc[future_return > threshold, "label_direction"] = 1
    df.loc[future_return < -threshold, "label_direction"] = -1

    return df


def build_week_label(
    df: pd.DataFrame,
    horizon: int = 5,
    threshold: float = 0.0075,
) -> pd.DataFrame:
    """构造周频（未来 N 日）三分类标签：涨/跌/平。"""
    df = df.copy()
    week_ret = df["close"].shift(-horizon) / df["close"] - 1
    df["label_week_return"] = week_ret
    thr = max(float(threshold), 1e-12)
    df["label_week_direction"] = 0
    df.loc[week_ret > thr, "label_week_direction"] = 1
    df.loc[week_ret < -thr, "label_week_direction"] = -1
    df.loc[week_ret.isna(), "label_week_direction"] = pd.NA
    return df


def build_feature_pipeline(df: pd.DataFrame) -> pd.DataFrame:
    """完整的特征工程流水线"""
    logger.info("🔧 开始特征工程...")
    df = add_technical_indicators(df)
    df = add_custom_factors(df)
    if get_config().get("features", {}).get("financial", {}).get("enabled", False):
        df = add_fundamental_features(df)

    label_cfg = get_config().get("features", {}).get("label", {})
    method = label_cfg.get("method", "future_return")
    horizon = label_cfg.get("horizon", 1)
    threshold = label_cfg.get("direction_threshold", 0.005)

    if method != "future_return":
        logger.warning(f"⚠️ 标签方法 '{method}' 尚未实现，使用 future_return")

    logger.info(f"🏷️ 标签配置: horizon={horizon}, threshold={threshold}")
    bar_freq = get_config().get("data", {}).get("bar_freq", "daily")
    unit = "周" if bar_freq == "weekly" else "日"
    logger.info(f"🏷️ 预测目标: 未来 {horizon} {unit}涨跌方向")
    df = build_label(df, horizon=horizon, threshold=threshold)

    week_horizon = int(label_cfg.get("week_horizon", 5))
    week_threshold = float(label_cfg.get("week_direction_threshold", 0.0075))
    if label_cfg.get("week_label_enabled", True):
        df = build_week_label(df, horizon=week_horizon, threshold=week_threshold)
        logger.info(
            f"🏷️ 周频标签: 未来 {week_horizon} {unit}, 三分类平盘带 ±{week_threshold * 100:.2f}%"
        )

    logger.info(f"✅ 特征工程完成，共 {len(df)} 条数据，{len(df.columns)} 列")

    # 软标签（仅当 enabled）
    soft_cfg = label_cfg.get("soft_label", {})
    if soft_cfg.get("enabled", False):
        soft_sigma = float(soft_cfg.get("sigma", 0.005))
        logger.info(f"🏷️ 软标签 enabled: sigma={soft_sigma}")
        df = build_soft_label(df, horizon=horizon, threshold=threshold, sigma=soft_sigma)

    return df

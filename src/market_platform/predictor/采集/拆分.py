"""
训练/验证集划分 — 支持按时间末尾切分，或按市场周期（牛/熊/震荡）切分
"""

from __future__ import annotations


from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from loguru import logger

from market_platform.predictor.配置 import get_config


@dataclass
class MarketSegment:
    """连续的市场周期片段"""
    start_idx: int
    end_idx: int
    regime: str
    start_date: pd.Timestamp
    end_date: pd.Timestamp

    @property
    def length(self) -> int:
        return self.end_idx - self.start_idx + 1


def detect_market_regime(
    df: pd.DataFrame,
    trend_window: int = 120,
    momentum_window: int = 60,
    bull_threshold: float = 0.03,
    bear_threshold: float = -0.03,
) -> pd.Series:
    """
    识别每日市场状态
    - bull: 价格显著高于长期均线且动量为正
    - bear: 价格显著低于长期均线且动量为负
    - sideways: 其余震荡市
    """
    close = df["close"]
    sma = close.rolling(trend_window, min_periods=trend_window // 2).mean()
    trend_dev = close / sma - 1
    momentum = close.pct_change(momentum_window)

    regime = pd.Series("sideways", index=df.index, dtype="object")
    regime[(trend_dev > bull_threshold) & (momentum > 0)] = "bull"
    regime[(trend_dev < bear_threshold) & (momentum < 0)] = "bear"
    return regime


def _build_segments(regime: pd.Series, dates: pd.Series, min_segment_days: int) -> List[MarketSegment]:
    segments: List[MarketSegment] = []
    if regime.empty:
        return segments

    start = 0
    current = regime.iloc[0]
    for i in range(1, len(regime)):
        if regime.iloc[i] != current:
            if i - start >= min_segment_days:
                segments.append(
                    MarketSegment(
                        start_idx=start,
                        end_idx=i - 1,
                        regime=current,
                        start_date=dates.iloc[start],
                        end_date=dates.iloc[i - 1],
                    )
                )
            start = i
            current = regime.iloc[i]

    if len(regime) - start >= min_segment_days:
        segments.append(
            MarketSegment(
                start_idx=start,
                end_idx=len(regime) - 1,
                regime=current,
                start_date=dates.iloc[start],
                end_date=dates.iloc[-1],
            )
        )
    return segments


def _select_cycle_holdout_segments(
    segments: List[MarketSegment],
    target_size: int,
) -> List[MarketSegment]:
    """从不同周期状态中选取完整片段作为验证集，尽量覆盖牛/熊/震荡"""
    if not segments or target_size <= 0:
        return []

    by_regime: Dict[str, List[MarketSegment]] = {"bull": [], "bear": [], "sideways": []}
    for seg in segments:
        by_regime.setdefault(seg.regime, []).append(seg)

    for regime in by_regime:
        by_regime[regime].sort(key=lambda s: s.end_date, reverse=True)

    selected: List[MarketSegment] = []
    current_size = 0

    # 第一轮：每种状态至少保留一个完整周期（若有）
    for regime in ("bull", "bear", "sideways"):
        if by_regime[regime] and current_size < target_size:
            seg = by_regime[regime].pop(0)
            selected.append(seg)
            current_size += seg.length

    # 第二轮：轮询补齐到目标规模
    while current_size < target_size:
        added = False
        for regime in ("bull", "bear", "sideways"):
            if not by_regime[regime]:
                continue
            seg = by_regime[regime].pop(0)
            selected.append(seg)
            current_size += seg.length
            added = True
            if current_size >= target_size:
                break
        if not added:
            break

    return selected


def cycle_tail_train_val_split(
    df: pd.DataFrame,
    val_ratio: float,
    trend_window: int = 120,
    momentum_window: int = 60,
    bull_threshold: float = 0.03,
    bear_threshold: float = -0.03,
    min_segment_days: int = 20,
) -> Tuple[pd.DataFrame, pd.DataFrame, List[MarketSegment]]:
    """按市场周期切分,但**验证集只取历史末尾的连续周期段**(因果安全)。

    与旧 `cycle_train_val_split` 的差别(修正时间泄漏):
    - 旧实现:各类状态各挑一个"最近"的完整周期段,这些段散落在历史各处,
      于是训练集里包含验证期**之后**的数据 → 前视泄漏。
    - 本实现:从最后一个周期段往前累加,直到达到目标规模;验证集是历史的**连续后缀**,
      训练集严格早于验证集,同时仍保留"按牛熊震荡周期取样"的意图。

    返回 (train_df, val_df, holdout_segments);无法切分时回退到时间末尾切分。
    """
    if not 0 < val_ratio < 1:
        raise ValueError(f"val_ratio 需在 (0, 1) 之间，当前: {val_ratio}")

    df = df.sort_values("date").reset_index(drop=True)
    regime = detect_market_regime(
        df,
        trend_window=trend_window,
        momentum_window=momentum_window,
        bull_threshold=bull_threshold,
        bear_threshold=bear_threshold,
    )
    segments = _build_segments(regime, df["date"], min_segment_days)
    if not segments:
        logger.warning("⚠️ 未识别到足够长的市场周期，回退到时间末尾切分")
        train_df, val_df = temporal_train_val_split(df, val_ratio)
        return train_df, val_df, []

    target_size = int(len(df) * val_ratio)
    # 只从末尾往前取:保证被选段落在时间上是连续后缀(段之间可有更早的段被跳过,
    # 因此这里要求"从最后一个段起连续累加",一旦跳过就不再回头取更早的段)。
    selected: List[MarketSegment] = []
    current_size = 0
    for seg in reversed(segments):
        selected.append(seg)
        current_size += seg.length
        if current_size >= target_size:
            break

    if not selected:
        train_df, val_df = temporal_train_val_split(df, val_ratio)
        return train_df, val_df, []

    selected.sort(key=lambda s: s.start_idx)
    val_mask = np.zeros(len(df), dtype=bool)
    for seg in selected:
        val_mask[seg.start_idx : seg.end_idx + 1] = True

    val_df = df.loc[val_mask].reset_index(drop=True)
    train_df = df.loc[~val_mask].reset_index(drop=True)
    if train_df.empty or val_df.empty:
        logger.warning("⚠️ 周期切分导致训练/验证为空，回退到时间末尾切分")
        train_df, val_df = temporal_train_val_split(df, val_ratio)
        return train_df, val_df, []

    # 因果校验:训练集最后一日必须早于验证集第一日
    if train_df["date"].max() > val_df["date"].min():
        logger.warning("⚠️ 检测到训练集跨越验证集起点，回退到时间末尾切分以保证因果")
        train_df, val_df = temporal_train_val_split(df, val_ratio)
        return train_df, val_df, []

    return train_df, val_df, selected


def temporal_train_val_split(df: pd.DataFrame, val_ratio: float) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """按时间顺序切分（验证集取末尾）"""
    if not 0 < val_ratio < 1:
        raise ValueError(f"val_ratio 需在 (0, 1) 之间，当前: {val_ratio}")

    split_idx = int(len(df) * (1 - val_ratio))
    split_idx = max(1, min(split_idx, len(df) - 1))
    train_df = df.iloc[:split_idx].reset_index(drop=True)
    val_df = df.iloc[split_idx:].reset_index(drop=True)
    return train_df, val_df


def cycle_train_val_split(
    df: pd.DataFrame,
    val_ratio: float,
    trend_window: int = 120,
    momentum_window: int = 60,
    bull_threshold: float = 0.03,
    bear_threshold: float = -0.03,
    min_segment_days: int = 20,
) -> Tuple[pd.DataFrame, pd.DataFrame, List[MarketSegment]]:
    """
    按市场周期切分：验证集由完整的牛/熊/震荡周期片段组成，而非简单取时间末尾
    """
    if not 0 < val_ratio < 1:
        raise ValueError(f"val_ratio 需在 (0, 1) 之间，当前: {val_ratio}")

    df = df.sort_values("date").reset_index(drop=True)
    regime = detect_market_regime(
        df,
        trend_window=trend_window,
        momentum_window=momentum_window,
        bull_threshold=bull_threshold,
        bear_threshold=bear_threshold,
    )
    segments = _build_segments(regime, df["date"], min_segment_days)

    if not segments:
        logger.warning("⚠️ 未识别到足够长的市场周期，回退到时间末尾切分")
        train_df, val_df = temporal_train_val_split(df, val_ratio)
        return train_df, val_df, []

    target_size = int(len(df) * val_ratio)
    holdout = _select_cycle_holdout_segments(segments, target_size)

    if not holdout:
        logger.warning("⚠️ 周期 holdout 为空，回退到时间末尾切分")
        train_df, val_df = temporal_train_val_split(df, val_ratio)
        return train_df, val_df, []

    val_mask = np.zeros(len(df), dtype=bool)
    for seg in holdout:
        val_mask[seg.start_idx : seg.end_idx + 1] = True

    val_df = df.loc[val_mask].reset_index(drop=True)
    train_df = df.loc[~val_mask].reset_index(drop=True)

    if train_df.empty or val_df.empty:
        logger.warning("⚠️ 周期切分导致训练/验证为空，回退到时间末尾切分")
        train_df, val_df = temporal_train_val_split(df, val_ratio)
        return train_df, val_df, holdout

    return train_df, val_df, holdout


def train_val_split(
    df: pd.DataFrame,
    val_ratio: Optional[float] = None,
    split_method: Optional[str] = None,
    cycle_cfg: Optional[dict] = None,
) -> Tuple[pd.DataFrame, pd.DataFrame, List[MarketSegment]]:
    """统一入口：根据配置选择切分方式

    split_method:
      temporal    — 时间末尾切分(默认,最保守)
      cycle_tail  — 按周期切分但验证集只取末尾连续周期段(因果安全)
      cycle       — 旧行为:验证集取历史上零散周期段(**有前视泄漏,不推荐**,仅兼容旧实验)
    """
    cfg = get_config()
    split_cfg = cfg.get("model", {}).get("split", {})
    val_ratio = val_ratio if val_ratio is not None else split_cfg.get("val_ratio", 0.2)
    split_method = split_method or split_cfg.get("split_method", "temporal")
    cycle_cfg = cycle_cfg or split_cfg.get("cycle", {})

    cycle_参数 = dict(
        val_ratio=val_ratio,
        trend_window=cycle_cfg.get("trend_window", 120),
        momentum_window=cycle_cfg.get("momentum_window", 60),
        bull_threshold=cycle_cfg.get("bull_threshold", 0.03),
        bear_threshold=cycle_cfg.get("bear_threshold", -0.03),
        min_segment_days=cycle_cfg.get("min_segment_days", 20),
    )

    if split_method == "cycle_tail":
        return cycle_tail_train_val_split(df, **cycle_参数)

    if split_method == "cycle":
        logger.warning(
            "⚠️ split_method=cycle 会把验证集取成历史上零散周期段,训练集因此包含验证期之后的数据"
            "(时间泄漏)。建议改为 cycle_tail;本次按旧行为执行。"
        )
        return cycle_train_val_split(df, **cycle_参数)

    train_df, val_df = temporal_train_val_split(df, val_ratio)
    return train_df, val_df, []


def log_split_summary(
    train_df: pd.DataFrame,
    val_df: pd.DataFrame,
    holdout_segments: Optional[List[MarketSegment]] = None,
    prefix: str = "",
):
    """打印切分摘要"""
    train_range = f"{train_df['date'].min().date()} → {train_df['date'].max().date()}"
    val_range = f"{val_df['date'].min().date()} → {val_df['date'].max().date()}"
    logger.info(
        f"{prefix}训练集: {len(train_df)} 条 ({train_range}) | "
        f"验证集: {len(val_df)} 条 ({val_range})"
    )

    if holdout_segments:
        logger.info(f"{prefix}验证集周期片段（完整牛/熊/震荡周期 holdout）:")
        for seg in sorted(holdout_segments, key=lambda s: s.start_date):
            logger.info(
                f"  - {seg.regime:8s} | {seg.start_date.date()} → {seg.end_date.date()} "
                f"| {seg.length} 天"
            )

        if "label_direction" in val_df.columns:
            regime = detect_market_regime(val_df)
            counts = regime.value_counts()
            logger.info(
                f"{prefix}验证集市场状态覆盖: "
                f"bull={counts.get('bull', 0)}, "
                f"bear={counts.get('bear', 0)}, "
                f"sideways={counts.get('sideways', 0)}"
            )

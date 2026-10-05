from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from market_platform.agents.分析.回测.回测引擎 import BacktestFriction, run_state_backtest, walk_forward_split_index
from market_platform.agents.分析.回测.风控 import RiskConfig
from market_platform.agents.分析.推断.状态 import MARKET_STATES, POSITION_MAP

OBJECTIVE_CHOICES = ("balanced", "excess", "defensive", "trend")


@dataclass
class OptimizeResult:
    position_map: dict[str, float]
    metrics: dict[str, float]
    objective: float
    train_metrics: dict[str, float] | None = None
    oos_metrics: dict[str, float] | None = None
    walk_forward_ratio: float | None = None
    optimize_objective: str = "balanced"


def apply_position_map(states: pd.DataFrame, position_map: dict[str, float]) -> pd.DataFrame:
    out = states.copy()
    out["suggested_position"] = out["market_state"].map(position_map).fillna(0.5)
    return out


def _objective_score(
    metrics: dict[str, float],
    *,
    objective: str = "balanced",
    drawdown_penalty: float = 0.5,
) -> float:
    """balanced / excess / defensive / trend（偏绝对收益与满仓参与）。"""
    sharpe = metrics.get("strategy_sharpe", 0.0)
    max_dd = abs(metrics.get("strategy_max_drawdown", 0.0))
    excess = metrics.get("excess_return", 0.0)
    total = metrics.get("strategy_total_return", 0.0)
    full_ratio = metrics.get("full_position_ratio", 0.0)
    avg_pos = metrics.get("avg_position", 0.0)

    obj = objective.lower()
    if obj == "excess":
        return float(total * 3.0 + excess * 5.0 - drawdown_penalty * max_dd * 5.0)
    if obj == "defensive":
        return float(sharpe + excess * 0.5 - drawdown_penalty * max_dd * 15.0)
    if obj == "trend":
        # 更强调绝对收益与仓位参与；回撤惩罚适中，避免优化器退回空仓防守
        return float(
            total * 6.0
            + excess * 0.5
            + full_ratio * 2.2
            + avg_pos * 1.5
            - drawdown_penalty * max_dd * 2.5
        )
    return float(sharpe + excess * 2.0 - drawdown_penalty * max_dd * 10.0)


def _split_train(
    daily_bars: pd.DataFrame,
    states: pd.DataFrame,
    ratio: float | None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """按日期对齐后再切训练段，避免日线/状态长度不一致时位置切片错位。"""
    bars = daily_bars.copy()
    st = states.copy()
    bars["date"] = pd.to_datetime(bars["date"]).dt.date
    st["date"] = pd.to_datetime(st["date"]).dt.date
    merged = bars.merge(st, on="date", how="inner", suffixes=("", "_state"))
    if merged.empty:
        return bars.iloc[0:0].copy(), st.iloc[0:0].copy()
    merged = merged.sort_values("date").reset_index(drop=True)
    split = walk_forward_split_index(len(merged), ratio)
    if split is None:
        train = merged
    else:
        train = merged.iloc[:split].copy()
    bar_cols = [c for c in bars.columns if c in train.columns]
    state_cols = [c for c in st.columns if c in train.columns]
    return train[bar_cols].copy(), train[state_cols].copy()


def optimize_position_map(
    daily_bars: pd.DataFrame,
    states: pd.DataFrame,
    initial_capital: float = 1_000_000.0,
    commission: float = 0.0003,
    step: float = 0.1,
    drawdown_penalty: float = 0.5,
    objective: str = "balanced",
    min_position_floor: float = 0.0,
    mid_position_floor: float | None = None,
    strong_position_floor: float | None = None,
    walk_forward_ratio: float | None = None,
    friction: BacktestFriction | None = None,
    risk: RiskConfig | None = None,
) -> OptimizeResult:
    """网格搜索单调递增的 5 档仓位映射。"""
    if objective.lower() not in OBJECTIVE_CHOICES:
        raise ValueError(f"optimize_objective must be one of {OBJECTIVE_CHOICES}")

    train_bars, train_states = _split_train(daily_bars, states, walk_forward_ratio)
    floor = float(np.clip(min_position_floor, 0.0, 1.0))
    steps = np.arange(floor, 1.0 + step / 2, step)
    best_map = dict(POSITION_MAP)
    best_score = -np.inf
    train_metrics: dict[str, float] = {}
    obj = objective.lower()

    panic, euphoria = MARKET_STATES[0], MARKET_STATES[-1]
    middle = MARKET_STATES[1:-1]
    # trend：抬高横盘/偏强底仓，保留满仓通道；恐慌仍固定空仓巩固防守
    if obj == "trend":
        flat_floor = float(np.clip(mid_position_floor if mid_position_floor is not None else 0.6, floor, 1.0))
        strong_floor = float(
            np.clip(strong_position_floor if strong_position_floor is not None else 0.9, flat_floor, 1.0)
        )
    else:
        flat_floor = floor
        strong_floor = floor

    for w in steps:
        for f in steps:
            if f < w or f < flat_floor:
                continue
            for s in steps:
                if s < f or s < strong_floor:
                    continue
                pmap = {
                    panic: 0.0,
                    middle[0]: float(w),
                    middle[1]: float(f),
                    middle[2]: float(s),
                    euphoria: 1.0,
                }
                adjusted = apply_position_map(train_states, pmap)
                train_result = run_state_backtest(
                    train_bars,
                    adjusted,
                    initial_capital=initial_capital,
                    commission=commission,
                    friction=friction,
                    risk=risk,
                )
                score = _objective_score(
                    train_result.metrics,
                    objective=obj,
                    drawdown_penalty=drawdown_penalty,
                )
                if score > best_score:
                    best_score = score
                    best_map = pmap
                    train_metrics = train_result.metrics

    full_adjusted = apply_position_map(states, best_map)
    full_result = run_state_backtest(
        daily_bars,
        full_adjusted,
        initial_capital=initial_capital,
        commission=commission,
        friction=friction,
        risk=risk,
    )
    full_score = _objective_score(
        full_result.metrics,
        objective=obj,
        drawdown_penalty=drawdown_penalty,
    )

    oos_metrics: dict[str, float] | None = None
    split = walk_forward_split_index(len(daily_bars), walk_forward_ratio)
    if split is not None:
        test_bars = daily_bars.iloc[split:].copy()
        test_states = states.iloc[split:].copy()
        if not test_bars.empty and not test_states.empty:
            test_adjusted = apply_position_map(test_states, best_map)
            oos_result = run_state_backtest(
                test_bars,
                test_adjusted,
                initial_capital=initial_capital,
                commission=commission,
                friction=friction,
                risk=risk,
            )
            oos_metrics = oos_result.metrics

    return OptimizeResult(
        position_map=best_map,
        metrics=full_result.metrics,
        objective=full_score,
        train_metrics=train_metrics if walk_forward_ratio else None,
        oos_metrics=oos_metrics,
        walk_forward_ratio=walk_forward_ratio,
        optimize_objective=obj,
    )


def save_position_map(path: Path, result: OptimizeResult) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict = {
        "position_map": result.position_map,
        "objective": result.objective,
        "optimize_objective": result.optimize_objective,
        "metrics": result.metrics,
        "metrics_scope": "full_sample",
    }
    if result.train_metrics is not None:
        payload["train_metrics"] = result.train_metrics
    if result.oos_metrics is not None:
        payload["oos_metrics"] = result.oos_metrics
    if result.walk_forward_ratio is not None:
        payload["walk_forward_ratio"] = result.walk_forward_ratio
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def load_position_map(path: Path) -> dict[str, float] | None:
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload.get("position_map", payload)

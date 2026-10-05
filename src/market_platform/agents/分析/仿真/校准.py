from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from market_platform.agents.分析.仿真.仿真引擎 import run_simulation
from market_platform.agents.分析.仿真.智能体 import SimulationParams, params_to_vector, vector_to_params
from scipy.optimize import minimize


@dataclass
class CalibrationResult:
    params: SimulationParams
    loss: float
    moments: dict[str, float]


def compute_moments(returns: pd.Series) -> dict[str, float]:
    returns = returns.dropna()
    if len(returns) < 2:
        return {"mean": 0.0, "std": 0.0, "autocorr": 0.0, "kurtosis": 0.0}

    return {
        "mean": float(returns.mean()),
        "std": float(returns.std()),
        "autocorr": float(returns.autocorr(lag=1)) if len(returns) > 2 else 0.0,
        "kurtosis": float(returns.kurtosis()),
    }


def _moment_loss(actual: dict[str, float], simulated: dict[str, float]) -> float:
    keys = ["mean", "std", "autocorr", "kurtosis"]
    loss = 0.0
    for key in keys:
        a, s = actual[key], simulated[key]
        denom = max(abs(a), 1e-6)
        loss += ((s - a) / denom) ** 2
    return loss


def calibrate_simulation(
    minute_df: pd.DataFrame,
    initial: SimulationParams | None = None,
    activity_df: pd.DataFrame | None = None,
    lookback: int = 5,
    maxiter: int = 50,
) -> CalibrationResult:
    """通过统计矩匹配校准 Agent 参数。"""
    initial = initial or SimulationParams(lookback=lookback)
    x0 = params_to_vector(initial)

    bounds = [
        (0.1, 5.0),        # retail momentum
        (1e4, 1e6),        # retail order size
        (0.0005, 0.01),    # chase threshold
        (0.1, 5.0),        # inst reversion
        (1e4, 1e6),        # inst order size
        (0.0005, 0.02),    # vwap threshold
        (0.01, 1.0),       # impact lambda
    ]

    actual_returns = minute_df.sort_values("datetime")["close"].pct_change()
    actual_moments = compute_moments(actual_returns)

    def objective(vec: np.ndarray) -> float:
        params = vector_to_params(vec, lookback=lookback, template=initial)
        result = run_simulation(minute_df, params, activity_df)
        sim_returns = result.minute_path["sim_return"]
        sim_moments = compute_moments(sim_returns)
        return _moment_loss(actual_moments, sim_moments)

    opt = minimize(
        objective,
        x0,
        method="L-BFGS-B",
        bounds=bounds,
        options={"maxiter": maxiter},
    )
    best_params = vector_to_params(opt.x, lookback=lookback, template=initial)
    final_result = run_simulation(minute_df, best_params, activity_df)
    sim_moments = compute_moments(final_result.minute_path["sim_return"])

    return CalibrationResult(
        params=best_params,
        loss=float(opt.fun),
        moments={
            **{f"actual_{k}": v for k, v in actual_moments.items()},
            **{f"sim_{k}": v for k, v in sim_moments.items()},
        },
    )

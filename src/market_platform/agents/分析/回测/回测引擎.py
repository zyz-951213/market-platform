from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from market_platform.agents.分析.回测.风控 import RiskConfig, drawdown_position_scale
from market_platform.agents.核心.工具.读写 import ensure_dir, save_parquet


@dataclass
class BacktestFriction:
    """A 股日频回测摩擦：双边佣金、卖出印花税、滑点、涨跌停无法成交。"""

    commission: float = 0.0003
    stamp_tax: float = 0.0005
    slippage: float = 0.0005
    limit_pct: float = 0.098
    apply_limit: bool = True
    apply_stamp_tax: bool = True
    apply_slippage: bool = True

    @classmethod
    def from_backtest_config(cls, cfg) -> BacktestFriction:
        return cls(
            commission=float(cfg.commission),
            stamp_tax=float(cfg.stamp_tax) if cfg.apply_stamp_tax else 0.0,
            slippage=float(cfg.slippage) if cfg.apply_slippage else 0.0,
            limit_pct=float(cfg.limit_pct),
            apply_limit=bool(cfg.apply_limit),
            apply_stamp_tax=bool(cfg.apply_stamp_tax),
            apply_slippage=bool(cfg.apply_slippage),
        )


@dataclass
class BacktestResult:
    equity_curve: pd.DataFrame
    metrics: dict[str, float]

    def save(self, output_dir: Path) -> None:
        ensure_dir(output_dir)
        save_parquet(self.equity_curve, output_dir / "equity_curve.parquet")


def _max_drawdown(equity: pd.Series) -> float:
    peak = equity.cummax()
    dd = equity / peak - 1.0
    return float(dd.min())


def _annualized_sharpe(returns: pd.Series, periods_per_year: int = 252) -> float:
    r = returns.dropna()
    if len(r) < 2 or r.std() == 0:
        return 0.0
    return float(r.mean() / r.std() * np.sqrt(periods_per_year))


def _trade_cost(buy_turnover: float, sell_turnover: float, friction: BacktestFriction) -> float:
    slip = friction.slippage if friction.apply_slippage else 0.0
    stamp = friction.stamp_tax if friction.apply_stamp_tax else 0.0
    comm = friction.commission
    return buy_turnover * (comm + slip) + sell_turnover * (comm + slip + stamp)


def _simulate_executed_positions(
    target_positions: np.ndarray,
    market_returns: np.ndarray,
    *,
    volume: np.ndarray | None,
    friction: BacktestFriction,
    risk: RiskConfig | None = None,
    initial_capital: float = 1_000_000.0,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """按涨跌停/停牌约束调仓，返回执行仓位、策略收益、成本、被阻挡次数。"""
    n = len(target_positions)
    executed = np.zeros(n, dtype=float)
    strategy_returns = np.zeros(n, dtype=float)
    costs = np.zeros(n, dtype=float)
    blocked = np.zeros(n, dtype=bool)

    prev_exec = 0.0
    limit_pct = float(friction.limit_pct)
    equity = float(initial_capital)
    peak_equity = float(initial_capital)
    rk = risk or RiskConfig(enabled=False)
    dd_scale = 1.0

    for i in range(n):
        dd_scale = drawdown_position_scale(equity, peak_equity, rk, prev_scale=dd_scale)
        target = float(target_positions[i]) * dd_scale
        if rk.enabled:
            target = min(target, rk.max_portfolio_position)
        delta = target - prev_exec
        blocked_flag = False

        if volume is not None and float(volume[i]) <= 0:
            delta = 0.0
            blocked_flag = True
        elif friction.apply_limit and i > 0:
            day_ret = float(market_returns[i])
            if delta > 0 and day_ret >= limit_pct:
                delta = 0.0
                blocked_flag = True
            elif delta < 0 and day_ret <= -limit_pct:
                delta = 0.0
                blocked_flag = True

        buy = max(delta, 0.0)
        sell = max(-delta, 0.0)
        day_cost = _trade_cost(buy, sell, friction)
        day_return = float(market_returns[i])

        strategy_returns[i] = prev_exec * day_return - day_cost
        costs[i] = day_cost
        blocked[i] = blocked_flag

        prev_exec = prev_exec + delta
        executed[i] = prev_exec
        equity *= 1.0 + strategy_returns[i]
        peak_equity = max(peak_equity, equity)

    return executed, strategy_returns, costs, blocked


def run_state_backtest(
    daily_bars: pd.DataFrame,
    states: pd.DataFrame,
    initial_capital: float = 1_000_000.0,
    commission: float = 0.0003,
    friction: BacktestFriction | None = None,
    risk: RiskConfig | None = None,
) -> BacktestResult:
    """按市场状态建议仓位做日频回测（T-1 状态 → T 日收益）。"""
    fr = friction or BacktestFriction(commission=commission)
    if friction is None:
        fr.commission = commission

    bars = daily_bars.copy()
    bars["date"] = pd.to_datetime(bars["date"]).dt.date
    if "close" not in bars.columns:
        raise ValueError("daily_bars must contain close")

    st = states.copy()
    st["date"] = pd.to_datetime(st["date"]).dt.date
    if "suggested_position" not in st.columns:
        raise ValueError("states must contain suggested_position")

    state_cols = ["date", "suggested_position"]
    if "market_state" in st.columns:
        state_cols.append("market_state")
    df = bars.merge(st[state_cols], on="date", how="inner")
    df = df.sort_values("date").reset_index(drop=True)
    # 防御：剔除非正收盘价后再算收益，并限制极端日收益（脏复权/跳空）
    close = pd.to_numeric(df["close"], errors="coerce")
    df = df.loc[close.notna() & (close > 0)].copy()
    df["market_return"] = df["close"].pct_change().fillna(0.0)
    df["market_return"] = df["market_return"].clip(lower=-0.5, upper=1.0)

    # T-1 状态决定 T 日目标仓位；首日从空仓开始调仓
    df["target_position"] = df["suggested_position"].shift(1).fillna(0.0)
    volume = df["volume"].to_numpy(dtype=float) if "volume" in df.columns else None

    executed, strat_rets, costs, blocked = _simulate_executed_positions(
        df["target_position"].to_numpy(dtype=float),
        df["market_return"].to_numpy(dtype=float),
        volume=volume,
        friction=fr,
        risk=risk,
        initial_capital=initial_capital,
    )

    df["position"] = executed
    df["target_position"] = df["target_position"].astype(float)
    df["position_change"] = np.abs(np.diff(np.concatenate([[0.0], executed])))
    df["transaction_cost"] = costs
    df["limit_blocked"] = blocked
    df["strategy_return"] = strat_rets
    df["benchmark_return"] = df["market_return"]

    df["strategy_equity"] = initial_capital * (1 + df["strategy_return"]).cumprod()
    df["benchmark_equity"] = initial_capital * (1 + df["benchmark_return"]).cumprod()

    if df.empty:
        metrics = {
            "strategy_total_return": 0.0,
            "benchmark_total_return": 0.0,
            "excess_return": 0.0,
            "strategy_sharpe": 0.0,
            "benchmark_sharpe": 0.0,
            "strategy_max_drawdown": 0.0,
            "benchmark_max_drawdown": 0.0,
            "trading_days": 0.0,
            "avg_position": 0.0,
            "full_position_ratio": 0.0,
            "total_transaction_cost": 0.0,
            "avg_daily_transaction_cost": 0.0,
            "limit_blocked_days": 0.0,
            "risk_enabled": float(1.0 if risk and risk.enabled else 0.0),
            "friction_enabled": float(
                fr.apply_limit or fr.apply_stamp_tax or fr.apply_slippage or fr.stamp_tax > 0 or fr.slippage > 0
            ),
        }
        return BacktestResult(equity_curve=df, metrics=metrics)

    strat_total = df["strategy_equity"].iloc[-1] / initial_capital - 1
    bench_total = df["benchmark_equity"].iloc[-1] / initial_capital - 1

    metrics = {
        "strategy_total_return": float(strat_total),
        "benchmark_total_return": float(bench_total),
        "excess_return": float(strat_total - bench_total),
        "strategy_sharpe": _annualized_sharpe(df["strategy_return"]),
        "benchmark_sharpe": _annualized_sharpe(df["benchmark_return"]),
        "strategy_max_drawdown": _max_drawdown(df["strategy_equity"]),
        "benchmark_max_drawdown": _max_drawdown(df["benchmark_equity"]),
        "trading_days": float(len(df)),
        "avg_position": float(df["position"].mean()),
        "full_position_ratio": float((df["position"] >= 0.9).mean()),
        "total_transaction_cost": float(costs.sum()),
        "avg_daily_transaction_cost": float(costs.mean()),
        "limit_blocked_days": float(blocked.sum()),
        "risk_enabled": float(1.0 if risk and risk.enabled else 0.0),
        "friction_enabled": float(
            fr.apply_limit or fr.apply_stamp_tax or fr.apply_slippage or fr.stamp_tax > 0 or fr.slippage > 0
        ),
    }

    return BacktestResult(equity_curve=df, metrics=metrics)


def walk_forward_split_index(length: int, ratio: float | None) -> int | None:
    """返回训练集截止下标（不含）；ratio 无效时返回 None。"""
    if ratio is None or ratio <= 0 or ratio >= 1 or length < 40:
        return None
    split = max(int(length * ratio), 30)
    return min(split, length - 10)


def run_walk_forward_oos_backtest(
    daily_bars: pd.DataFrame,
    states: pd.DataFrame,
    walk_forward_ratio: float | None,
    initial_capital: float = 1_000_000.0,
    commission: float = 0.0003,
    friction: BacktestFriction | None = None,
    risk: RiskConfig | None = None,
) -> dict[str, float] | None:
    """在 walk-forward 测试窗（后段）上单独回测，得到样本外指标。"""
    bars = daily_bars.copy()
    st = states.copy()
    bars["date"] = pd.to_datetime(bars["date"]).dt.date
    st["date"] = pd.to_datetime(st["date"]).dt.date
    merged = bars.merge(st, on="date", how="inner", suffixes=("", "_state"))
    if merged.empty:
        return None
    merged = merged.sort_values("date").reset_index(drop=True)
    split = walk_forward_split_index(len(merged), walk_forward_ratio)
    if split is None:
        return None
    test = merged.iloc[split:].copy()
    if test.empty:
        return None
    bar_cols = [c for c in bars.columns if c in test.columns]
    state_cols = [c for c in st.columns if c in test.columns]
    return run_state_backtest(
        test[bar_cols],
        test[state_cols],
        initial_capital=initial_capital,
        commission=commission,
        friction=friction,
        risk=risk,
    ).metrics

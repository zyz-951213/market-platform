from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pandas as pd
from market_platform.agents.分析.回测.仓位优化 import load_position_map
from market_platform.agents.核心.工具.读写 import load_parquet
from market_platform.agents.核心.路径.输出 import resolve_symbol_dir


@dataclass
class DailyDigest:
    symbol: str
    market_state: str
    suggested_position: float
    strategy_return: float
    benchmark_return: float
    next_state_probs: dict[str, float] | None
    anomaly_count: int
    title: str
    body: str


@dataclass
class BatchDigest:
    as_of: date
    rows: list[DailyDigest]
    title: str
    body: str


def _load_optional(path: Path) -> pd.DataFrame | None:
    return load_parquet(path) if path.exists() else None


def build_daily_digest(
    output_root: Path,
    symbol: str,
) -> DailyDigest | None:
    """从 outputs 目录组装日更推送摘要。"""
    base = resolve_symbol_dir(output_root, symbol)
    if base is None:
        return None
    states_path = base / "state" / "market_states.parquet"
    if not states_path.exists():
        return None

    states = load_parquet(states_path)
    latest = states.iloc[-1]

    strategy_return = 0.0
    benchmark_return = 0.0
    eq_path = base / "backtest" / "equity_curve.parquet"
    if eq_path.exists():
        eq = load_parquet(eq_path)
        if len(eq) > 1:
            strategy_return = eq["strategy_equity"].iloc[-1] / eq["strategy_equity"].iloc[0] - 1
            benchmark_return = eq["benchmark_equity"].iloc[-1] / eq["benchmark_equity"].iloc[0] - 1

    next_probs: dict[str, float] | None = None
    prob_path = base / "state" / "next_state_probs.csv"
    if prob_path.exists():
        probs = pd.read_csv(prob_path, index_col=0)
        col = probs.columns[0] if len(probs.columns) else "probability"
        next_probs = {str(k): float(v) for k, v in probs[col].items()}

    anomalies = _load_optional(base / "residual" / "anomalies.parquet")
    anomaly_count = len(anomalies) if anomalies is not None else 0

    opt_map = load_position_map(base / "backtest" / "optimized_positions.json")
    opt_hint = f"\n优化仓位映射: {opt_map}" if opt_map else ""

    prob_lines = ""
    if next_probs:
        prob_lines = "\n".join(f"  - {k}: {v:.0%}" for k, v in next_probs.items())

    title = f"[Market-Agents] {symbol} | {latest['market_state']} | 仓位 {latest['suggested_position']:.0%}"
    body = (
        f"标的: {symbol}\n"
        f"市场状态: {latest['market_state']}\n"
        f"建议仓位: {latest['suggested_position']:.0%}\n"
        f"策略累计收益: {strategy_return:.2%}\n"
        f"基准累计收益: {benchmark_return:.2%}\n"
        f"残差异常日: {anomaly_count}\n"
        f"{opt_hint}\n"
        f"明日状态概率:\n{prob_lines or '  (无)'}"
    ).strip()

    return DailyDigest(
        symbol=symbol,
        market_state=str(latest["market_state"]),
        suggested_position=float(latest["suggested_position"]),
        strategy_return=float(strategy_return),
        benchmark_return=float(benchmark_return),
        next_state_probs=next_probs,
        anomaly_count=anomaly_count,
        title=title,
        body=body,
    )


def build_batch_digest(output_root: Path, as_of: date | None = None) -> BatchDigest | None:
    """从 outputs 目录组装多标的批量推送摘要。"""
    as_of = as_of or date.today()
    summary_csv = output_root / "daily_batch_summary.csv"
    if summary_csv.exists():
        summary = pd.read_csv(summary_csv, dtype={"symbol": str})
        symbols = [s.zfill(6) if s.isdigit() else s for s in summary["symbol"].tolist()]
    else:
        batch_csv = output_root / "batch_summary.csv"
        if batch_csv.exists():
            summary = pd.read_csv(batch_csv, dtype={"symbol": str})
            symbols = [s.zfill(6) if s.isdigit() else s for s in summary["symbol"].tolist()]
        else:
            symbols = sorted([p.name for p in output_root.iterdir() if p.is_dir() and p.name.isdigit()])

    digests: list[DailyDigest] = []
    for sym in symbols:
        digest = build_daily_digest(output_root, sym)
        if digest:
            digests.append(digest)

    if not digests:
        return None

    lines = [
        f"{d.symbol} | {d.market_state} | 仓位 {d.suggested_position:.0%} | 策略 {d.strategy_return:+.2%}"
        for d in digests
    ]
    title = f"[Market-Agents] 批量日更 {as_of.isoformat()} ({len(digests)} 标的)"
    body = f"日期: {as_of.isoformat()}\n\n" + "\n".join(lines)
    return BatchDigest(as_of=as_of, rows=digests, title=title, body=body)

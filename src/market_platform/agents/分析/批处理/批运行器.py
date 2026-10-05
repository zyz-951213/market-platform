from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from pathlib import Path

import pandas as pd
from market_platform.agents.settings import AppConfig
from market_platform.agents.分析.回测.仓位优化 import load_position_map
from market_platform.agents.分析.回测.回测引擎 import BacktestFriction, run_state_backtest
from market_platform.agents.分析.批处理.行业汇总 import save_industry_summary
from market_platform.agents.分析.推断.状态 import build_state_features, classify_market_states
from market_platform.agents.分析.推断.矩阵分解 import (
    apply_agent_role_names,
    decompose_agent_activity,
    label_dominant_features,
)
from market_platform.agents.分析.推断.隐马尔可夫 import classify_with_hmm
from market_platform.agents.核心.工具.读写 import ensure_dir, load_parquet, save_parquet
from market_platform.agents.核心.路径.数据 import data_dir_for_config, resolve_data_dir
from market_platform.agents.核心.路径.输出 import config_symbol_output_dir
from market_platform.agents.采集.管道 import DataPipeline


def _symbol_data_dir(config: AppConfig) -> Path:
    d = config.data
    return data_dir_for_config(
        d.root,
        d.symbol,
        sector=d.sector,
        name=d.name,
        group_by_sector=d.group_by_sector,
        name_as_folder=d.name_as_folder,
    )

logger = logging.getLogger(__name__)


@dataclass
class SymbolSpec:
    symbol: str
    market: str
    sector: str = ""
    name: str = ""


@dataclass
class BatchRow:
    symbol: str
    market: str
    latest_state: str
    suggested_position: float
    strategy_return: float
    benchmark_return: float
    excess_return: float
    strategy_sharpe: float
    anomalies: int
    status: str
    oos_strategy_return: float | None = None
    oos_benchmark_return: float | None = None
    oos_excess_return: float | None = None
    oos_strategy_sharpe: float | None = None
    walk_forward_ratio: float | None = None
    raw_suggested_position: float | None = None
    risk_note: str = ""


def _config_for_symbol(base: AppConfig, spec: SymbolSpec) -> AppConfig:
    return replace(
        base,
        data=replace(
            base.data,
            symbol=spec.symbol,
            market=spec.market,
            sector=spec.sector,
            name=spec.name,
        ),
    )


def _infer_market(symbol: str) -> str:
    return "sh" if symbol.startswith("6") else "sz"


def parse_symbol_specs(symbols: list[str], markets: list[str] | None = None) -> list[SymbolSpec]:
    specs: list[SymbolSpec] = []
    for i, sym in enumerate(symbols):
        sym = sym.strip()
        if not sym:
            continue
        market = markets[i] if markets and i < len(markets) else _infer_market(sym)
        specs.append(SymbolSpec(symbol=sym, market=market))
    return specs


def run_symbol_pipeline(config: AppConfig, demo: bool = False) -> BatchRow:
    symbol = config.data.symbol
    market = config.data.market
    out_base = config_symbol_output_dir(config)
    try:
        pipeline = DataPipeline(config)
        pipeline.run_fetch(demo=demo)
        pipeline.run_features()
        sym_dir = resolve_data_dir(config.data.root, symbol) or _symbol_data_dir(config)
        feature_matrix = load_parquet(sym_dir / "daily_feature_matrix.parquet")
        nmf_result = decompose_agent_activity(feature_matrix, config.nmf)
        nmf_result, name_map = apply_agent_role_names(nmf_result)
        nmf_dir = ensure_dir(out_base / "nmf")
        nmf_result.save(nmf_dir, agent_name_map=name_map)
        label_dominant_features(nmf_result.patterns, name_map).to_csv(
            nmf_dir / "agent_pattern_labels.csv", index=False
        )

        minute_daily = load_parquet(sym_dir / "minute_daily_features.parquet")
        fund_path = sym_dir / "fund_flow.parquet"
        fund_flow = load_parquet(fund_path) if fund_path.exists() else pd.DataFrame()

        features = build_state_features(nmf_result.activity, minute_daily, fund_flow)
        if config.state.method.lower() == "hmm":
            states = classify_with_hmm(
                features,
                n_states=config.state.n_states,
                confirm_days=config.state.state_confirm_days,
                confirm_policy=config.state.confirm_policy(),
                walk_forward_cluster_map=config.state.walk_forward_cluster_map,
                walk_forward_ratio=config.backtest.walk_forward_ratio,
            ).states
        else:
            states = classify_market_states(
                features,
                confirm_days=config.state.state_confirm_days,
                confirm_policy=config.state.confirm_policy(),
            )

        opt_map = load_position_map(out_base / "backtest" / "optimized_positions.json")
        if opt_map:
            states["suggested_position"] = states["market_state"].map(opt_map).fillna(0.5)

        state_dir = ensure_dir(out_base / "state")
        save_parquet(states, state_dir / "market_states.parquet")

        daily = load_parquet(sym_dir / "daily_bars.parquet")
        bt = run_state_backtest(
            daily,
            states,
            config.backtest.initial_capital,
            config.backtest.commission,
        friction=BacktestFriction.from_backtest_config(config.backtest),
        risk=config.risk,
    )
        bt.save(ensure_dir(out_base / "backtest"))

        latest = states.iloc[-1]
        anomalies = 0
        residual_path = out_base / "residual" / "anomalies.parquet"
        if residual_path.exists():
            anomalies = len(load_parquet(residual_path))

        return BatchRow(
            symbol=symbol,
            market=market,
            latest_state=str(latest["market_state"]),
            suggested_position=float(latest["suggested_position"]),
            strategy_return=float(bt.metrics["strategy_total_return"]),
            benchmark_return=float(bt.metrics["benchmark_total_return"]),
            excess_return=float(bt.metrics["excess_return"]),
            strategy_sharpe=float(bt.metrics["strategy_sharpe"]),
            anomalies=anomalies,
            status="ok",
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Batch failed for %s: %s", symbol, exc)
        return BatchRow(
            symbol=symbol,
            market=market,
            latest_state="",
            suggested_position=0.0,
            strategy_return=0.0,
            benchmark_return=0.0,
            excess_return=0.0,
            strategy_sharpe=0.0,
            anomalies=0,
            status=f"error: {exc}",
        )


def run_batch(
    base_config: AppConfig,
    specs: list[SymbolSpec],
    demo: bool = False,
) -> pd.DataFrame:
    rows: list[BatchRow] = []
    for spec in specs:
        logger.info("Batch processing %s.%s", spec.symbol, spec.market)
        cfg = _config_for_symbol(base_config, spec)
        rows.append(run_symbol_pipeline(cfg, demo=demo))

    records: list[dict] = []
    for spec, row in zip(specs, rows, strict=True):
        rec = row.__dict__.copy()
        rec["sector"] = spec.sector
        rec["name"] = spec.name
        records.append(rec)

    df = pd.DataFrame(records)
    out_root = ensure_dir(base_config.output.root)
    out_path = out_root / "batch_summary.parquet"
    save_parquet(df, out_path)
    df.to_csv(out_root / "batch_summary.csv", index=False)
    save_industry_summary(df, out_root)
    logger.info("Batch summary -> %s", out_path)
    return df

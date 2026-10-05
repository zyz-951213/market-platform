from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

import pandas as pd
from market_platform.agents.settings import AppConfig
from market_platform.agents.分析.回测.仓位优化 import apply_position_map, optimize_position_map
from market_platform.agents.分析.回测.回测引擎 import BacktestFriction, run_state_backtest, run_walk_forward_oos_backtest
from market_platform.agents.分析.回测.风控 import apply_risk_to_states, latest_risk_snapshot
from market_platform.agents.分析.批处理.批运行器 import BatchRow
from market_platform.agents.分析.批处理.行业汇总 import save_industry_summary
from market_platform.agents.分析.推断.状态 import build_state_features, classify_market_states
from market_platform.agents.分析.推断.矩阵分解 import (
    apply_agent_role_names,
    decompose_agent_activity,
    label_dominant_features,
    追加式分解,
)
from market_platform.agents.分析.推断.隐马尔可夫 import attach_forward_returns, classify_with_hmm
from market_platform.agents.存储 import data_access as store
from market_platform.agents.采集.股票池 import infer_market, load_universe_csv
from market_platform.agents.采集.行业.行业映射 import load_sector_map, lookup_sector

logger = logging.getLogger(__name__)

UNIVERSE_SUMMARY_NAME = "universe_batch_summary.csv"


@dataclass
class AnalyzeRow:
    symbol: str
    market: str
    name: str
    sector: str
    status: str
    latest_state: str = ""
    suggested_position: float = 0.0
    strategy_return: float = 0.0
    excess_return: float = 0.0
    error: str = ""


def _config_for_symbol(
    base: AppConfig,
    symbol: str,
    market: str,
    name: str = "",
    sector: str = "",
) -> AppConfig:
    return replace(
        base,
        data=replace(
            base.data,
            symbol=symbol,
            market=market,
            name=name,
            sector=sector,
        ),
    )


def is_data_ready(data_root: Path, symbol: str, *, require_features: bool = True) -> bool:
    return store.symbol_data_ready(str(symbol).zfill(6), require_features=require_features)


def is_analyzed(output_root: Path, symbol: str) -> bool:
    return store.symbol_is_analyzed(str(symbol).zfill(6))


def _feature_matrix_from_minute_daily(
    minute_daily: pd.DataFrame,
    fund_flow: pd.DataFrame,
) -> pd.DataFrame:
    """由持久化的 minute_daily 现算 feature_matrix（不落库）。"""
    from market_platform.agents.分析.特征.日频 import build_daily_feature_matrix

    north = store.load_market_series_df("northbound")
    margin = store.load_market_series_df("margin")
    return build_daily_feature_matrix(
        minute_daily=minute_daily,
        fund_flow=fund_flow,
        northbound=north,
        margin=margin,
    )


def _rebuild_features_from_daily(
    config: AppConfig,
    daily: pd.DataFrame,
    fund_flow: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """由日线重建 minute_daily（落库）+ feature_matrix（仅内存）。"""
    from market_platform.agents.分析.特征.分钟 import compute_minute_features, summarize_minute_to_daily
    from market_platform.agents.采集.数据源 import expand_daily_to_minute_proxy

    sym = str(config.data.symbol).zfill(6)
    minute = expand_daily_to_minute_proxy(daily, config.data.symbol)
    minute_feat = compute_minute_features(minute, config.features.minute)
    minute_daily = summarize_minute_to_daily(minute_feat)
    store.save_series(sym, store.SERIES_MINUTE_DAILY, minute_daily)
    matrix = _feature_matrix_from_minute_daily(minute_daily, fund_flow)
    return matrix, minute_daily


def load_universe_meta(data_root: Path) -> pd.DataFrame:
    path = data_root / "universe.csv"
    if not path.exists():
        return pd.DataFrame(columns=["symbol", "name", "market", "sector"])
    df = load_universe_csv(path)
    if "market" not in df.columns:
        df["market"] = df["symbol"].astype(str).map(infer_market)
    if "sector" not in df.columns:
        sectors = load_sector_map(data_root)
        if not sectors.empty:
            df = df.merge(sectors[["symbol", "sector"]], on="symbol", how="left")
    df["sector"] = df.get("sector", pd.Series([""] * len(df))).fillna("").replace("", "未分类")
    return df


def discover_pending(
    data_root: Path,
    output_root: Path,
    *,
    universe: pd.DataFrame | None = None,
    skip_analyzed: bool = True,
    limit: int | None = None,
) -> list[tuple[str, str, str, str]]:
    """返回待分析列表 [(symbol, market, name, sector), ...]，已分析标的自动跳过。"""
    universe = universe if universe is not None else load_universe_meta(data_root)
    pending: list[tuple[str, str, str, str]] = []
    seen: set[str] = set()

    if not universe.empty:
        candidates = universe
    else:
        registry = store.load_symbols_registry()
        if not registry.empty:
            candidates = registry
        else:
            candidates = pd.DataFrame(
                {"symbol": [p.name for p in data_root.iterdir() if p.is_dir() and p.name.isdigit()]}
            )

    for row in candidates.itertuples(index=False):
        sym = str(row.symbol).zfill(6)
        if sym in seen:
            continue
        if not is_data_ready(data_root, sym):
            continue
        if skip_analyzed and is_analyzed(output_root, sym):
            continue
        market = str(getattr(row, "market", infer_market(sym)))
        name = str(getattr(row, "name", ""))
        sector = str(getattr(row, "sector", "") or lookup_sector(data_root, sym, name=name))
        pending.append((sym, market, name, sector))
        seen.add(sym)
        if limit is not None and len(pending) >= limit:
            break

    return pending


def run_symbol_analysis_from_cache(
    config: AppConfig,
    *,
    optimize: bool = True,
) -> BatchRow:
    """基于 PostgreSQL 缓存数据做轻量分析。"""
    symbol = config.data.symbol
    market = config.data.market
    sym = str(symbol).zfill(6)

    fund_flow = store.load_fund_flow(sym)
    daily = store.load_daily_bars(sym)
    if daily.empty:
        raise FileNotFoundError(f"未找到标的 {symbol} 的日线数据")

    minute_daily = store.load_series(sym, store.SERIES_MINUTE_DAILY)
    # 拉取曾用 --no-features、日线变长/变短、或脏价清洗后长度错位 → 按全日线重建
    # 也覆盖：日线被外部 SQL/公共层补齐后，分钟日特征日期落后但行数仍落在长度容差内
    min_len = max(60, int(len(daily) * 0.85))
    max_len = max(min_len, int(len(daily) * 1.15)) + 5
    daily_max = None
    minute_max = None
    if not daily.empty and "date" in daily.columns:
        daily_max = pd.to_datetime(daily["date"]).max().date()
    if not minute_daily.empty and "date" in minute_daily.columns:
        minute_max = pd.to_datetime(minute_daily["date"]).max().date()
    need_rebuild = (
        minute_daily.empty
        or len(minute_daily) < min_len
        or len(minute_daily) > max_len
        or (daily_max is not None and (minute_max is None or minute_max < daily_max))
    )
    if need_rebuild:
        feature_matrix, minute_daily = _rebuild_features_from_daily(config, daily, fund_flow)
    else:
        feature_matrix = _feature_matrix_from_minute_daily(minute_daily, fund_flow)
    if feature_matrix.empty or minute_daily.empty:
        raise FileNotFoundError(f"未找到标的 {symbol} 的特征数据")

    # point-in-time 追加式分解(评审 R4):历史活跃度保持原值,只算新增行,
    # 避免"每次增量重拟合导致历史状态被改写"。开关见 state/nmf 配置。
    nmf_result = _分解行为活跃度(sym, feature_matrix, config, store)
    nmf_result, name_map = apply_agent_role_names(nmf_result)
    labels = label_dominant_features(nmf_result.patterns, name_map)
    store.save_nmf_outputs(sym, nmf_result.activity, nmf_result.patterns, agent_names=name_map, labels=labels)

    features = build_state_features(nmf_result.activity, minute_daily, fund_flow)
    features = attach_forward_returns(features, daily, horizon=config.state.forward_horizon_days)

    if config.state.method.lower() == "hmm":
        hmm = classify_with_hmm(
            features,
            n_states=config.state.n_states,
            cluster_map_method=config.state.cluster_map_method,
            confirm_days=config.state.state_confirm_days,
            confirm_policy=config.state.confirm_policy(),
            walk_forward_cluster_map=config.state.walk_forward_cluster_map,
            walk_forward_ratio=config.backtest.walk_forward_ratio,
        )
        states = hmm.states
        store.save_transition_matrix(sym, hmm.transition_matrix)
        store.save_next_state_probs(sym, hmm.next_state_probs)
    else:
        states = classify_market_states(
            features,
            confirm_days=config.state.state_confirm_days,
            confirm_policy=config.state.confirm_policy(),
        )

    if optimize:
        friction = BacktestFriction.from_backtest_config(config.backtest)
        opt = optimize_position_map(
            daily,
            states,
            initial_capital=config.backtest.initial_capital,
            commission=config.backtest.commission,
            step=config.backtest.optimize_step,
            drawdown_penalty=config.backtest.drawdown_penalty,
            objective=config.backtest.optimize_objective,
            min_position_floor=config.backtest.min_position_floor,
            mid_position_floor=getattr(config.backtest, "mid_position_floor", 0.6),
            strong_position_floor=getattr(config.backtest, "strong_position_floor", 0.9),
            walk_forward_ratio=config.backtest.walk_forward_ratio,
            friction=friction,
            risk=config.risk,
        )
        states = apply_position_map(states, opt.position_map)
        store.save_optimized_positions(
            sym,
            {
                "position_map": opt.position_map,
                "objective": opt.objective,
                "optimize_objective": config.backtest.optimize_objective,
                "metrics": opt.metrics,
                "metrics_scope": "full_sample",
                "train_metrics": opt.train_metrics,
                "oos_metrics": opt.oos_metrics,
                "walk_forward_ratio": opt.walk_forward_ratio,
                "friction": {
                    "commission": friction.commission,
                    "stamp_tax": friction.stamp_tax,
                    "slippage": friction.slippage,
                    "limit_pct": friction.limit_pct,
                    "apply_limit": friction.apply_limit,
                },
                "risk": {
                    "enabled": config.risk.enabled,
                    "panic_max_position": config.risk.panic_max_position,
                    "max_portfolio_position": config.risk.max_portfolio_position,
                },
            },
        )
        oos_metrics = opt.oos_metrics
    else:
        friction = BacktestFriction.from_backtest_config(config.backtest)
        oos_metrics = run_walk_forward_oos_backtest(
            daily,
            states,
            config.backtest.walk_forward_ratio,
            initial_capital=config.backtest.initial_capital,
            commission=config.backtest.commission,
            friction=friction,
            risk=config.risk,
        )

    states = apply_risk_to_states(states, config.risk)

    friction = BacktestFriction.from_backtest_config(config.backtest)
    bt = run_state_backtest(
        daily,
        states,
        config.backtest.initial_capital,
        config.backtest.commission,
        friction=friction,
        risk=config.risk,
    )
    equity = bt.equity_curve.copy()
    if "state_score" in states.columns:
        score = states[["date", "state_score"]].copy()
        score["date"] = pd.to_datetime(score["date"]).dt.date
        equity["date"] = pd.to_datetime(equity["date"]).dt.date
        equity = equity.merge(score.drop_duplicates("date"), on="date", how="left")
    store.save_equity_curve(sym, equity)

    latest = states.iloc[-1]
    risk_snap = latest_risk_snapshot(states)

    def _finite(value: object, default: float = 0.0) -> float:
        try:
            out = float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return default
        if out != out or out in (float("inf"), float("-inf")):
            return default
        return out

    def _finite_opt(value: object) -> float | None:
        if value is None:
            return None
        try:
            out = float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None
        if out != out or out in (float("inf"), float("-inf")):
            return None
        return out

    return BatchRow(
        symbol=symbol,
        market=market,
        latest_state=str(latest["market_state"]),
        suggested_position=_finite(latest["suggested_position"]),
        strategy_return=_finite(bt.metrics["strategy_total_return"]),
        benchmark_return=_finite(bt.metrics["benchmark_total_return"]),
        excess_return=_finite(bt.metrics["excess_return"]),
        strategy_sharpe=_finite(bt.metrics["strategy_sharpe"]),
        anomalies=0,
        status="ok",
        oos_strategy_return=_finite_opt(oos_metrics["strategy_total_return"]) if oos_metrics else None,
        oos_benchmark_return=_finite_opt(oos_metrics["benchmark_total_return"]) if oos_metrics else None,
        oos_excess_return=_finite_opt(oos_metrics["excess_return"]) if oos_metrics else None,
        oos_strategy_sharpe=_finite_opt(oos_metrics["strategy_sharpe"]) if oos_metrics else None,
        walk_forward_ratio=config.backtest.walk_forward_ratio,
        raw_suggested_position=_finite(risk_snap.get("raw_suggested_position", latest["suggested_position"])),
        risk_note=str(risk_snap.get("risk_note", "") or ""),
    )


def _batch_row_to_record(row: BatchRow, name: str = "", sector: str = "") -> dict:
    return {
        "symbol": row.symbol,
        "sector": sector,
        "name": name,
        "latest_state": row.latest_state,
        "suggested_position": row.suggested_position,
        "strategy_return": row.strategy_return,
        "benchmark_return": row.benchmark_return,
        "excess_return": row.excess_return,
        "strategy_sharpe": row.strategy_sharpe,
        "oos_strategy_return": row.oos_strategy_return,
        "oos_benchmark_return": row.oos_benchmark_return,
        "oos_excess_return": row.oos_excess_return,
        "oos_strategy_sharpe": row.oos_strategy_sharpe,
        "walk_forward_ratio": row.walk_forward_ratio,
        "raw_suggested_position": row.raw_suggested_position,
        "risk_note": row.risk_note,
        "anomalies": row.anomalies,
        "status": row.status,
    }


def append_universe_summary(rows: list[dict], output_root: Path) -> Path:

    new_df = pd.DataFrame(rows)
    new_df["symbol"] = new_df["symbol"].astype(str).str.zfill(6)
    old = store.load_symbol_summary()
    if not old.empty:
        merged = pd.concat([old[~old["symbol"].isin(new_df["symbol"])], new_df], ignore_index=True)
    else:
        merged = new_df
    merged = merged.drop_duplicates(subset=["symbol"], keep="last").sort_values(["sector", "symbol"])
    store.save_symbol_summary_rows(merged.to_dict(orient="records"))
    save_industry_summary(merged, output_root)
    return Path(UNIVERSE_SUMMARY_NAME)


def run_incremental_batch(
    base_config: AppConfig,
    pending: list[tuple[str, str, str, str]],
    *,
    optimize: bool = True,
) -> pd.DataFrame:
    results: list[dict] = []
    progress_rows: list[AnalyzeRow] = []

    for sym, market, name, sector in pending:
        logger.info("Analyzing %s.%s [%s] %s", sym, market, sector, name)
        cfg = _config_for_symbol(base_config, sym, market, name, sector)
        try:
            row = run_symbol_analysis_from_cache(cfg, optimize=optimize)
            rec = _batch_row_to_record(row, name=name, sector=sector)
            rec["path"] = f"{sector}/{name}" if name else sym
            results.append(rec)
            progress_rows.append(
                AnalyzeRow(
                    symbol=sym,
                    market=market,
                    name=name,
                    sector=sector,
                    status="ok",
                    latest_state=row.latest_state,
                    suggested_position=row.suggested_position,
                    strategy_return=row.strategy_return,
                    excess_return=row.excess_return,
                )
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Analyze failed %s: %s", sym, exc)
            progress_rows.append(
                AnalyzeRow(
                    symbol=sym,
                    market=market,
                    name=name,
                    sector=sector,
                    status="error",
                    error=str(exc),
                )
            )

    if results:
        append_universe_summary(results, base_config.output.root)

    _save_analyze_progress(base_config.data.root, progress_rows)
    return pd.DataFrame([r.__dict__ for r in progress_rows])


def _save_analyze_progress(data_root: Path, rows: list[AnalyzeRow]) -> None:
    if not rows:
        return
    new_df = pd.DataFrame([r.__dict__ for r in rows])
    new_df["symbol"] = new_df["symbol"].astype(str).str.zfill(6)
    old_payload = store.load_app_state("analyze_progress") or []
    old_df = pd.DataFrame(old_payload)
    if not old_df.empty:
        old_df["symbol"] = old_df["symbol"].astype(str).str.zfill(6)
        merged = pd.concat([old_df[~old_df["symbol"].isin(new_df["symbol"])], new_df], ignore_index=True)
    else:
        merged = new_df
    merged = merged.drop_duplicates(subset=["symbol"], keep="last")
    store.save_app_state("analyze_progress", merged.to_dict(orient="records"))


def analyze_status(data_root: Path, output_root: Path) -> dict:
    universe = load_universe_meta(data_root)
    total = len(universe) if not universe.empty else 0

    # 单次聚合计数，避免对全市场逐票 EXISTS
    data_ready = store.count_data_ready(require_features=True)
    analyzed = store.count_analyzed()
    prog_payload = store.load_app_state("analyze_progress") or []
    prog = pd.DataFrame(prog_payload)
    errors = int((prog["status"] == "error").sum()) if not prog.empty and "status" in prog.columns else 0

    pending = max(data_ready - analyzed, 0)
    sector_map = load_sector_map(data_root)
    return {
        "universe_total": total,
        "data_ready": data_ready,
        "analyzed": analyzed,
        "pending": pending,
        "analyze_errors": errors,
        "sector_count": int(sector_map["sector"].nunique()) if not sector_map.empty and "sector" in sector_map.columns else 0,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }

def _分解行为活跃度(sym: str, feature_matrix, config, store):
    """按配置选择分解方式:追加式(默认,point-in-time)或全窗口重拟合(旧行为)。

    评审 R4:历史实现在每次增量分析时对整窗 `fit_transform`,同一根 K 线的活跃度会被
    后续数据改写。默认走追加式(历史行保持原值,只算新增行);置
    `nmf.append_only: false` 可退回旧行为(仅用于复现旧结果)。
    """
    if not bool(getattr(config.nmf, "append_only", True)):
        return decompose_agent_activity(feature_matrix, config.nmf)

    已有活跃度 = None
    已有模式 = None
    try:
        已有活跃度 = store.load_agent_activity(sym)
        已有模式 = store.load_agent_patterns(sym)
    except Exception as exc:  # noqa: BLE001 — 无历史输出时退化为首跑分解
        logger.info("无历史 NMF 输出(%s),按首跑分解: %s", type(exc).__name__, sym)

    return 追加式分解(
        feature_matrix,
        config.nmf,
        已有活跃度=已有活跃度,
        已有模式=已有模式,
        重拟合阈值=int(getattr(config.nmf, "refit_every_rows", 250)),
    )

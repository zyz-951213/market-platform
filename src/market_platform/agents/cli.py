from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
from market_platform.agents.settings import AppConfig
from market_platform.agents.分析.仿真.仿真引擎 import run_simulation
from market_platform.agents.分析.仿真.参数加载 import resolve_simulation_params
from market_platform.agents.分析.仿真.校准 import calibrate_simulation
from market_platform.agents.分析.可视化.绘图 import plot_agent_activity, plot_backtest, plot_states
from market_platform.agents.分析.回测.仓位优化 import (
    apply_position_map,
    load_position_map,
    optimize_position_map,
    save_position_map,
)
from market_platform.agents.分析.回测.回测引擎 import BacktestFriction, run_state_backtest
from market_platform.agents.分析.批处理.批运行器 import parse_symbol_specs, run_batch
from market_platform.agents.分析.推断.残差 import (
    detect_anomalies,
    estimate_baseline_flow,
)
from market_platform.agents.分析.推断.状态 import (
    build_state_features,
    classify_market_states,
    compute_state_predictive_metrics,
    state_distribution,
)
from market_platform.agents.分析.推断.矩阵分解 import (
    apply_agent_role_names,
    decompose_agent_activity,
    label_dominant_features,
)
from market_platform.agents.分析.推断.隐马尔可夫 import attach_forward_returns, classify_with_hmm
from market_platform.agents.核心.大模型.客户端 import get_llm_client
from market_platform.agents.核心.大模型.解释器 import explain_residuals_with_llm
from market_platform.agents.核心.工具.读写 import ensure_dir, load_parquet, save_parquet
from market_platform.agents.核心.路径.项目 import profile_path
from market_platform.agents.核心.通知.分发 import dispatch_notification
from market_platform.agents.核心.通知.摘要 import build_batch_digest, build_daily_digest
from market_platform.agents.采集.健康检查 import check_data_sources
from market_platform.agents.采集.管道 import DataPipeline

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("market_platform.agents")


def _default_config_path() -> Path:
    return profile_path("默认.yaml")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Multi-agent market simulation pipeline")
    parser.add_argument(
        "--config",
        type=Path,
        default=_default_config_path(),
        help="Path to YAML config",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    fetch_p = sub.add_parser("fetch", help="Fetch raw market data via akshare")
    fetch_p.add_argument("--refresh", action="store_true", help="Force re-fetch")
    fetch_p.add_argument("--demo", action="store_true", help="Use synthetic demo data (offline)")

    feat_p = sub.add_parser("features", help="Build daily feature matrix from cached data")
    feat_p.add_argument("--refresh", action="store_true", help="Re-fetch before building features")
    feat_p.add_argument("--demo", action="store_true", help="Use synthetic demo data (offline)")

    nmf_p = sub.add_parser("nmf", help="Run NMF decomposition on feature matrix")
    nmf_p.add_argument("--refresh", action="store_true", help="Rebuild feature matrix first")
    nmf_p.add_argument("--plot", action="store_true", help="Save activity plot")
    nmf_p.add_argument("--demo", action="store_true", help="Use synthetic demo data (offline)")

    run_p = sub.add_parser("run", help="Run full pipeline: fetch -> features -> nmf")
    run_p.add_argument("--demo", action="store_true", help="Use synthetic demo data (offline)")
    run_p.add_argument("--simulate", action="store_true", help="Also run 5-agent simulation")
    run_p.add_argument("--classify", action="store_true", help="Also run market state classification")
    run_p.add_argument("--residual", action="store_true", help="Also run residual analysis")
    run_p.add_argument("--llm-explain", action="store_true", help="Use LLM for residual explanations")
    run_p.add_argument("--backtest", action="store_true", help="Also run state-based backtest")

    news_p = sub.add_parser("news", help="Fetch stock news")
    news_p.add_argument("--demo", action="store_true", help="Use synthetic demo news")

    sim_p = sub.add_parser("simulate", help="Run 5-agent minute-level simulation")
    sim_p.add_argument("--plot", action="store_true", help="Save simulation comparison plot")
    sim_p.add_argument("--use-nmf", action="store_true", help="Weight agents with NMF activity")
    sim_p.add_argument(
        "--no-calibrated",
        action="store_true",
        help="Ignore calibrated_params.json, use YAML config only",
    )

    cal_p = sub.add_parser("calibrate", help="Calibrate simulation params via moment matching")
    cal_p.add_argument("--use-nmf", action="store_true", help="Use NMF activity during calibration")

    cls_p = sub.add_parser("classify", help="Classify daily market states (route 4)")
    cls_p.add_argument("--plot", action="store_true", help="Save state timeline plot")
    cls_p.add_argument("--rules", action="store_true", help="Force rule-based classifier")

    res_p = sub.add_parser("residual", help="Baseline vs actual flow residual analysis")
    res_p.add_argument("--llm", action="store_true", help="Use LLM explanations (Ollama if configured)")

    bt_p = sub.add_parser("backtest", help="Backtest state-based position strategy")
    bt_p.add_argument("--plot", action="store_true", help="Save equity curve plot")
    bt_p.add_argument("--no-optimized", action="store_true", help="Ignore optimized position map")

    opt_p = sub.add_parser("optimize", help="Grid-search optimal state position mapping")
    opt_p.add_argument("--plot", action="store_true", help="Plot backtest with optimized positions")

    batch_p = sub.add_parser("batch", help="Run pipeline for multiple symbols")
    batch_p.add_argument("--demo", action="store_true", help="Use demo data")
    batch_p.add_argument(
        "--symbols",
        type=str,
        default=None,
        help="Comma-separated symbols, e.g. 000001,600000",
    )

    sub.add_parser("fetch-check", help="Check akshare data source connectivity")

    fix_p = sub.add_parser("修复日行情", help="以 公共.日行情 为源修复 行为分析.日行情 的口径(股/元)与缺失日")
    fix_p.add_argument("--since", default=None)
    fix_p.add_argument("--until", default=None)
    fix_p.add_argument("--dry-run", action="store_true")

    notify_p = sub.add_parser("notify", help="Send daily digest via webhook/email")
    notify_p.add_argument("--dry-run", action="store_true", help="Print message only")
    notify_p.add_argument("--batch", action="store_true", help="Send batch digest for all symbols")

    paper_p = sub.add_parser("paper", help="Run daily paper-trading cycle (Top20 rebalance)")
    paper_p.add_argument("--dry-run", action="store_true", help="Preview without saving or sending")
    paper_p.add_argument("--no-notify", action="store_true", help="Skip webhook/email push")

    web_p = sub.add_parser("web", help="Launch FastAPI web dashboard (ECharts)")
    web_p.add_argument("--host", default="0.0.0.0")
    web_p.add_argument("--port", type=int, default=8766)

    auto_p = sub.add_parser("auto-update", help="Auto fetch stale data and run incremental analysis")
    auto_p.add_argument("--config", type=Path, default=profile_path("自动更新.yaml"))
    auto_p.add_argument("--fetch-limit", type=int, default=None)
    auto_p.add_argument("--analyze-limit", type=int, default=None)
    auto_p.add_argument("--refresh-list", action="store_true")
    auto_p.add_argument("--no-market-refresh", action="store_true")
    auto_p.add_argument("--no-features", action="store_true")
    auto_p.add_argument("--no-optimize", action="store_true")
    auto_p.add_argument("--status", action="store_true")
    auto_p.add_argument("--loop", action="store_true")
    auto_p.add_argument("--interval", type=int, default=None)

    pg_p = sub.add_parser("pg", help="PostgreSQL schema init / status")
    pg_sub = pg_p.add_subparsers(dest="pg_command", required=True)
    pg_sub.add_parser("init", help="Create tables (schema.sql)")
    pg_sub.add_parser("status", help="Check database connectivity")

    return parser


def run_nmf(config: AppConfig, refresh: bool = False, plot: bool = False) -> None:
    pipeline = DataPipeline(config)
    feature_matrix = pipeline.load_or_build_feature_matrix(refresh=refresh)

    result = decompose_agent_activity(feature_matrix, config.nmf)
    result, agent_name_map = apply_agent_role_names(result)
    out_dir = ensure_dir(config.output.root / config.data.symbol / "nmf")
    result.save(out_dir, agent_name_map=agent_name_map)

    labels = label_dominant_features(result.patterns)
    labels.to_csv(out_dir / "agent_pattern_labels.csv", index=False)

    logger.info("NMF reconstruction error: %.4f", result.reconstruction_error)
    logger.info("Saved agent activity -> %s", out_dir)
    print("\n=== 市场参与者行为模式 ===")
    print(labels.to_string(index=False))

    if plot:
        plot_agent_activity(result.activity, out_dir / "agent_activity.png")


def _load_minute_data(config: AppConfig) -> pd.DataFrame:
    minute_path = config.data.root / config.data.symbol / "minute_bars.parquet"
    if not minute_path.exists():
        raise FileNotFoundError(
            f"Minute data not found at {minute_path}. Run `market-agents fetch` first."
        )
    return load_parquet(minute_path)


def _load_nmf_activity(config: AppConfig) -> pd.DataFrame | None:
    activity_path = config.output.root / config.data.symbol / "nmf" / "agent_activity.parquet"
    if activity_path.exists():
        return load_parquet(activity_path)
    return None


def _load_optional_parquet(path: Path) -> pd.DataFrame:
    return load_parquet(path) if path.exists() else pd.DataFrame()


def _load_auxiliary_daily(config: AppConfig) -> tuple[pd.DataFrame, pd.DataFrame]:
    symbol_dir = config.data.root / config.data.symbol
    north = _load_optional_parquet(symbol_dir / "northbound.parquet")
    margin = _load_optional_parquet(symbol_dir / "margin.parquet")
    return north, margin


def run_simulate(
    config: AppConfig,
    plot: bool = False,
    use_nmf: bool = False,
    prefer_calibrated: bool | None = None,
) -> None:
    minute_df = _load_minute_data(config)
    use_calibrated = (
        config.simulation.prefer_calibrated if prefer_calibrated is None else prefer_calibrated
    )
    params, source = resolve_simulation_params(config, prefer_calibrated=use_calibrated)
    activity = _load_nmf_activity(config) if use_nmf else None
    north, margin = _load_auxiliary_daily(config)

    result = run_simulation(minute_df, params, activity, north, margin)
    out_dir = ensure_dir(config.output.root / config.data.symbol / "simulation")
    result.save(out_dir)

    logger.info("Simulation params source: %s", source)
    logger.info("Simulation summary: %s", result.summary)
    print(f"\n=== 模拟参数来源: {source} ===")
    print("\n=== 模拟结果摘要 ===")
    for key, val in result.summary.items():
        print(f"  {key}: {val:.4f}")

    if plot:
        _plot_simulation(result.minute_path, out_dir / "simulation_vs_actual.png")


def run_calibrate(config: AppConfig, use_nmf: bool = False) -> None:
    minute_df = _load_minute_data(config)
    activity = _load_nmf_activity(config) if use_nmf else None
    initial = config.simulation.to_params()

    logger.info("Starting calibration (this may take a minute)...")
    cal = calibrate_simulation(
        minute_df,
        initial=initial,
        activity_df=activity,
        lookback=config.simulation.lookback,
    )

    out_dir = ensure_dir(config.output.root / config.data.symbol / "simulation")

    payload = {
        "loss": cal.loss,
        "moments": cal.moments,
        "params": {
            "impact_lambda": cal.params.impact_lambda,
            "lookback": cal.params.lookback,
            "retail_momentum_sensitivity": cal.params.retail.momentum_sensitivity,
            "retail_base_order_size": cal.params.retail.base_order_size,
            "retail_chase_threshold": cal.params.retail.chase_threshold,
            "institutional_reversion_sensitivity": cal.params.institutional.reversion_sensitivity,
            "institutional_base_order_size": cal.params.institutional.base_order_size,
            "institutional_vwap_threshold": cal.params.institutional.vwap_entry_threshold,
            "quant_signal_sensitivity": cal.params.quant.signal_sensitivity,
            "quant_base_order_size": cal.params.quant.base_order_size,
            "quant_rsi_oversold": cal.params.quant.rsi_oversold,
            "quant_rsi_overbought": cal.params.quant.rsi_overbought,
            "quant_vol_spike_threshold": cal.params.quant.vol_spike_threshold,
            "macro_signal_sensitivity": cal.params.macro.signal_sensitivity,
            "macro_base_order_size": cal.params.macro.base_order_size,
            "hot_money_signal_sensitivity": cal.params.hot_money.signal_sensitivity,
            "hot_money_base_order_size": cal.params.hot_money.base_order_size,
        },
    }
    (out_dir / "calibrated_params.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")

    north, margin = _load_auxiliary_daily(config)
    result = run_simulation(minute_df, cal.params, activity, north, margin)
    result.save(out_dir)

    print("\n=== 校准结果 ===")
    print(f"  loss: {cal.loss:.4f}")
    for key, val in cal.moments.items():
        print(f"  {key}: {val:.6f}")
    logger.info("Saved calibrated params -> %s", out_dir / "calibrated_params.json")


def run_classify(config: AppConfig, plot: bool = False, force_rules: bool = False) -> None:
    symbol_dir = config.data.root / config.data.symbol
    activity = load_parquet(config.output.root / config.data.symbol / "nmf" / "agent_activity.parquet")
    minute_daily = load_parquet(symbol_dir / "minute_daily_features.parquet")

    fund_path = symbol_dir / "fund_flow.parquet"
    fund_flow = load_parquet(fund_path) if fund_path.exists() else pd.DataFrame()

    daily = load_parquet(symbol_dir / "daily_bars.parquet")
    features = build_state_features(activity, minute_daily, fund_flow)
    features = attach_forward_returns(
        features,
        daily,
        horizon=config.state.forward_horizon_days,
    )
    out_dir = ensure_dir(config.output.root / config.data.symbol / "state")
    save_parquet(features, out_dir / "state_features.parquet")

    use_hmm = config.state.method.lower() == "hmm" and not force_rules
    if use_hmm:
        hmm_result = classify_with_hmm(
            features,
            n_states=config.state.n_states,
            cluster_map_method=config.state.cluster_map_method,
            confirm_days=config.state.state_confirm_days,
            confirm_policy=config.state.confirm_policy(),
            walk_forward_cluster_map=config.state.walk_forward_cluster_map,
            walk_forward_ratio=config.backtest.walk_forward_ratio,
        )
        states = hmm_result.states
        hmm_result.transition_matrix.to_csv(out_dir / "transition_matrix.csv")
        hmm_result.next_state_probs.to_csv(out_dir / "next_state_probs.csv", header=["probability"])

        print("\n=== 明日状态概率 (HMM) ===")
        for state, prob in hmm_result.next_state_probs.items():
            print(f"  {state}: {prob:.2%}")
    else:
        states = classify_market_states(
            features,
            confirm_days=config.state.state_confirm_days,
            confirm_policy=config.state.confirm_policy(),
        )

    dist = state_distribution(states)
    save_parquet(states, out_dir / "market_states.parquet")
    dist.to_csv(out_dir / "state_distribution.csv", index=False)
    compute_state_predictive_metrics(states, daily).to_csv(
        out_dir / "state_predictive_metrics.csv", index=False
    )

    print("\n=== 市场状态分布 ===")
    print(dist.to_string(index=False))

    latest = states.iloc[-1]
    print(
        f"\n最新交易日 {latest['date']}: "
        f"{latest['market_state']} | 建议仓位 {latest['suggested_position']:.0%}"
    )

    if plot:
        plot_states(states, out_dir / "market_states.png")


def run_residual(config: AppConfig, use_llm: bool = False) -> None:
    symbol_dir = config.data.root / config.data.symbol
    activity = load_parquet(config.output.root / config.data.symbol / "nmf" / "agent_activity.parquet")
    fund_flow = load_parquet(symbol_dir / "fund_flow.parquet")
    minute_daily = load_parquet(symbol_dir / "minute_daily_features.parquet")

    news_path = symbol_dir / "news.parquet"
    news = load_parquet(news_path) if news_path.exists() else pd.DataFrame()

    daily = estimate_baseline_flow(activity, fund_flow)
    anomalies = detect_anomalies(daily, z_threshold=config.state.residual_z_threshold)

    llm_enabled = use_llm or config.llm.provider.lower() == "ollama"
    client = get_llm_client(
        provider=config.llm.provider if llm_enabled else "rules",
        model=config.llm.model,
        base_url=config.llm.base_url,
    )
    explanations = explain_residuals_with_llm(
        anomalies,
        news_df=news,
        minute_daily=minute_daily,
        client=client,
        use_llm=llm_enabled,
    )

    from market_platform.agents.分析.推断.残差 import ResidualReport

    report = ResidualReport(daily=daily, anomalies=anomalies, explanations=explanations)

    out_dir = ensure_dir(config.output.root / config.data.symbol / "residual")
    save_parquet(report.daily, out_dir / "residual_daily.parquet")
    save_parquet(report.anomalies, out_dir / "anomalies.parquet")
    if not report.explanations.empty:
        report.explanations.to_csv(out_dir / "explanations.csv", index=False)

    print("\n=== 残差分析 ===")
    print(f"  异常日数量: {len(report.anomalies)} / {len(report.daily)}")
    if not report.explanations.empty:
        print("\n最近异常日解释:")
        cols = ["date", "explanation", "llm_explanation", "explanation_source"]
        show = [c for c in cols if c in report.explanations.columns]
        print(report.explanations[show].tail(5).to_string(index=False))
    logger.info("Saved residual report -> %s", out_dir)


def _states_with_positions(config: AppConfig, states: pd.DataFrame) -> pd.DataFrame:
    opt_map = load_position_map(
        config.output.root / config.data.symbol / "backtest" / "optimized_positions.json"
    )
    if opt_map:
        return apply_position_map(states, opt_map)
    return states


def run_backtest(config: AppConfig, plot: bool = False, use_optimized: bool = True) -> None:
    symbol_dir = config.data.root / config.data.symbol
    daily = load_parquet(symbol_dir / "daily_bars.parquet")
    states_path = config.output.root / config.data.symbol / "state" / "market_states.parquet"

    if not states_path.exists():
        raise FileNotFoundError(
            f"Market states not found at {states_path}. Run `market-agents classify` first."
        )
    states = load_parquet(states_path)
    if use_optimized:
        states = _states_with_positions(config, states)

    result = run_state_backtest(
        daily,
        states,
        initial_capital=config.backtest.initial_capital,
        commission=config.backtest.commission,
        friction=BacktestFriction.from_backtest_config(config.backtest),
        risk=config.risk,
    )
    out_dir = ensure_dir(config.output.root / config.data.symbol / "backtest")
    result.save(out_dir)

    print("\n=== 回测结果 ===")
    for key, val in result.metrics.items():
        if "return" in key or "drawdown" in key:
            print(f"  {key}: {val:.2%}" if abs(val) < 10 else f"  {key}: {val:.4f}")
        else:
            print(f"  {key}: {val:.4f}")

    if plot:
        plot_backtest(result.equity_curve, out_dir / "equity_curve.png")


def run_optimize(config: AppConfig, plot: bool = False) -> None:
    symbol_dir = config.data.root / config.data.symbol
    daily = load_parquet(symbol_dir / "daily_bars.parquet")
    states_path = config.output.root / config.data.symbol / "state" / "market_states.parquet"
    if not states_path.exists():
        raise FileNotFoundError("Run `market-agents classify` before optimize.")

    states = load_parquet(states_path)
    friction = BacktestFriction.from_backtest_config(config.backtest)
    result = optimize_position_map(
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
    out_dir = ensure_dir(config.output.root / config.data.symbol / "backtest")
    save_position_map(out_dir / "optimized_positions.json", result)

    print("\n=== 仓位映射优化结果 ===")
    print(f"  objective: {result.objective:.4f}")
    for state, pos in result.position_map.items():
        print(f"  {state}: {pos:.0%}")
    print("\n=== 优化后回测（全样本） ===")
    for key, val in result.metrics.items():
        if "return" in key or "drawdown" in key:
            print(f"  {key}: {val:.2%}")
        else:
            print(f"  {key}: {val:.4f}")

    if result.train_metrics:
        print("\n=== 训练窗回测 ===")
        for key, val in result.train_metrics.items():
            if "return" in key or "drawdown" in key:
                print(f"  {key}: {val:.2%}")
            else:
                print(f"  {key}: {val:.4f}")

    if result.oos_metrics:
        ratio = result.walk_forward_ratio
        print(f"\n=== 样本外回测（后 {100 - int((ratio or 0) * 100)}%） ===")
        for key, val in result.oos_metrics.items():
            if "return" in key or "drawdown" in key:
                print(f"  {key}: {val:.2%}")
            else:
                print(f"  {key}: {val:.4f}")

    if plot:
        adjusted = apply_position_map(states, result.position_map)
        bt = run_state_backtest(
            daily,
            adjusted,
            config.backtest.initial_capital,
            config.backtest.commission,
            friction=friction,
            risk=config.risk,
        )
        plot_backtest(bt.equity_curve, out_dir / "equity_curve_optimized.png")


def run_batch_cmd(config: AppConfig, demo: bool = False, symbols_arg: str | None = None) -> None:
    if symbols_arg:
        specs = parse_symbol_specs([s.strip() for s in symbols_arg.split(",") if s.strip()])
    elif config.batch.symbols:
        specs = [parse_symbol_specs([s.symbol], [s.market])[0] for s in config.batch.symbols]
    else:
        raise ValueError("Provide --symbols or batch.symbols in config file")

    df = run_batch(config, specs, demo=demo)
    print("\n=== 批量分析汇总 ===")
    print(df.to_string(index=False))


def run_fetch_check(config: AppConfig) -> None:
    result = check_data_sources(config)
    print("\n=== 数据源探测 ===")
    print(f"  标的: {result.symbol}")
    print(f"  日线: {'✓' if result.daily_ok else '✗'} ({result.daily_rows} 行, source={result.daily_source or '-'})")
    print(
        f"  分钟线: {'✓' if result.minute_ok else '✗'} "
        f"({result.minute_rows} 行, source={result.minute_source or '-'})"
    )
    print(f"  资金流向: {'✓' if result.fund_flow_ok else '✗'} (source={result.fund_flow_source or '-'})")
    print(f"  新闻: {'✓' if result.news_ok else '✗'}")
    if result.errors:
        print("\n错误详情:")
        for err in result.errors[:8]:
            print(f"  - {err}")
    if not result.minute_ok and result.daily_ok:
        print("\n提示: 可启用 fallback_daily_proxy 用日线展开代理分钟线。")


def run_notify(config: AppConfig, dry_run: bool = False, batch: bool = False) -> None:
    from datetime import date

    if batch:
        digest = build_batch_digest(config.output.root, as_of=date.today())
        if digest is None:
            raise FileNotFoundError("No batch output found. Run daily_update --batch first.")
        print("\n=== 批量推送摘要 ===")
        print(digest.body)
        if dry_run:
            return
        results = dispatch_notification(config.notify, digest.title, digest.body, dry_run=False)
        if not results:
            logger.warning("Notify disabled or no channels configured.")
            return
        ok = any(r.success for r in results)
        print("推送成功" if ok else f"推送失败: {[r.detail for r in results]}")
        return

    digest = build_daily_digest(config.output.root, config.data.symbol)
    if digest is None:
        raise FileNotFoundError("No analysis output found. Run daily_update or classify first.")

    print("\n=== 推送摘要 ===")
    print(digest.body)

    if dry_run:
        return

    if not config.notify.enabled:
        logger.warning("Notify disabled. Set notify.enabled=true in config.")
        return

    results = dispatch_notification(config.notify, digest.title, digest.body, dry_run=False)
    if not results:
        logger.warning("No notify channels configured (webhook_url or email).")
        return
    ok = any(r.success for r in results)
    print("推送成功" if ok else f"推送失败: {[r.detail for r in results]}")


def run_paper(config: AppConfig, *, dry_run: bool = False, notify: bool = True) -> None:
    from market_platform.agents.分析.模拟盘.引擎 import run_paper_trading_cycle

    result = run_paper_trading_cycle(config, dry_run=dry_run, notify=notify)
    if result.get("skipped"):
        print(f"模拟盘已跳过: {result.get('reason')}")
        return
    picks = result.get("tomorrow_picks") or []
    execution = result.get("execution")
    print(f"\n=== 模拟盘周期 trade_date={result.get('trade_date')} ===")
    if execution:
        print(f"换仓执行: 卖 {execution.get('sells', 0)} / 买 {execution.get('buys', 0)}")
    print(f"明日 Top{len(picks)} 已生成")
    state = result.get("state") or {}
    nav = float(state.get("nav", 0) or 0)
    initial = float(state.get("initial_capital", 1) or 1)
    print(f"模拟净值 {nav:,.0f} ({nav / initial - 1:+.2%})")
    if dry_run:
        print("(dry-run: 未保存状态、未推送)")


def run_web(host: str = "0.0.0.0", port: int = 8766) -> None:
    import uvicorn
    from market_platform.agents.接口.应用 import app

    logger.info("Starting API server at http://%s:%d (frontend: serve separately or SERVE_FRONTEND=1)", host, port)
    uvicorn.run(app, host=host, port=port)


def _plot_simulation(sim_df, path: Path) -> None:
    daily = sim_df.groupby("date").agg(
        actual=("actual_close", "last"),
        simulated=("simulated_price", "last"),
    ).reset_index()

    fig, ax = plt.subplots(figsize=(12, 5))
    ax.plot(daily["date"], daily["actual"], label="Actual", linewidth=1.5)
    ax.plot(daily["date"], daily["simulated"], label="Simulated", linewidth=1.5, alpha=0.85)
    ax.set_title("Daily Close: Actual vs Simulated (5 Agents)")
    ax.set_xlabel("Date")
    ax.set_ylabel("Price")
    ax.legend()
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)
    logger.info("Saved plot -> %s", path)


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    config = AppConfig.from_yaml(args.config)

    pipeline = DataPipeline(config)

    if args.command == "修复日行情":
        from market_platform.agents.脚本.运维.修复应用日行情 import main as 修复主函数

        修复参数 = []
        if args.since:
            修复参数 += ["--since", args.since]
        if args.until:
            修复参数 += ["--until", args.until]
        if args.dry_run:
            修复参数.append("--dry-run")
        raise SystemExit(修复主函数(修复参数))

    if args.command == "fetch":
        paths = pipeline.run_fetch(
            demo=getattr(args, "demo", False),
            fallback_daily_proxy=config.data.fallback_daily_proxy,
        )
        for name, p in paths.items():
            logger.info("%s -> %s", name, p)

    elif args.command == "features":
        if args.refresh:
            pipeline.run_fetch(demo=getattr(args, "demo", False))
        fm = pipeline.run_features()
        logger.info("Built feature matrix with %d rows", len(fm))

    elif args.command == "nmf":
        run_nmf(config, refresh=args.refresh, plot=args.plot)

    elif args.command == "run":
        pipeline.run_fetch(demo=args.demo)
        pipeline.run_features()
        run_nmf(config, plot=True)
        if args.simulate:
            run_simulate(config, plot=True, use_nmf=True)
        if args.classify or args.backtest:
            run_classify(config, plot=True)
        if args.residual:
            run_residual(config, use_llm=args.llm_explain)
        if args.backtest:
            run_backtest(config, plot=True)

    elif args.command == "news":
        path = pipeline.run_fetch_news(demo=args.demo)
        logger.info("News saved -> %s", path)

    elif args.command == "simulate":
        run_simulate(
            config,
            plot=args.plot,
            use_nmf=args.use_nmf,
            prefer_calibrated=not args.no_calibrated,
        )

    elif args.command == "calibrate":
        run_calibrate(config, use_nmf=args.use_nmf)

    elif args.command == "classify":
        run_classify(config, plot=args.plot, force_rules=args.rules)

    elif args.command == "residual":
        run_residual(config, use_llm=args.llm)

    elif args.command == "backtest":
        run_backtest(config, plot=args.plot, use_optimized=not args.no_optimized)

    elif args.command == "optimize":
        run_optimize(config, plot=args.plot)

    elif args.command == "batch":
        run_batch_cmd(config, demo=args.demo, symbols_arg=args.symbols)

    elif args.command == "fetch-check":
        run_fetch_check(config)

    elif args.command == "notify":
        run_notify(config, dry_run=args.dry_run, batch=args.batch)

    elif args.command == "paper":
        run_paper(config, dry_run=args.dry_run, notify=not args.no_notify)

    elif args.command == "web":
        run_web(host=args.host, port=args.port)
    elif args.command == "auto-update":
        import time

        import yaml
        from market_platform.agents.核心.任务.运行器 import run_auto_update, update_status

        config = AppConfig.from_yaml(args.config)
        raw = yaml.safe_load(args.config.read_text(encoding="utf-8")) or {}
        auto = raw.get("auto_update", {}) if isinstance(raw.get("auto_update", {}), dict) else {}

        if args.status:
            status = update_status(config, rescan=True)
            for key, value in status.items():
                print(f"{key}: {value}")
            return

        def _run_once() -> int:
            try:
                result = run_auto_update(
                    config,
                    fetch_limit=args.fetch_limit or int(auto.get("fetch_limit", 50)),
                    analyze_limit=args.analyze_limit or int(auto.get("analyze_limit", 50)),
                    refresh_universe_list=args.refresh_list or bool(auto.get("refresh_universe_list", False)),
                    refresh_market_data=not args.no_market_refresh,
                    build_features=not args.no_features,
                    optimize=not args.no_optimize,
                )
            except RuntimeError as exc:
                logger.warning("%s", exc)
                return 2
            print(
                f"[auto-update] fetch {result.fetch_ok}/{result.fetch_attempted} | "
                f"analyze {result.analyze_ok}/{result.analyze_attempted} | "
                f"stale {result.stale_before} | {result.duration_seconds:.1f}s"
            )
            return 0 if result.fetch_error == 0 and result.analyze_error == 0 else 1

        if not args.loop:
            raise SystemExit(_run_once())
        interval = args.interval or int(auto.get("loop_interval_seconds", 3600))
        logger.info("Auto-update loop interval=%ds", interval)
        while True:
            code = _run_once()
            if code == 2:
                logger.info("Skipped run because another update is active")
            time.sleep(interval)

    elif args.command == "pg":
        from market_platform.agents.存储 import data_access
        from market_platform.agents.存储.存储配置 import load_database_settings

        settings = load_database_settings()
        if args.pg_command == "status":
            ok = data_access.ping()
            print(f"enabled: {settings.enabled}")
            print(f"dsn: {settings.dsn}")
            print(f"connected: {ok}")
            raise SystemExit(0 if ok else 1)
        if args.pg_command == "init":
            if not settings.dsn:
                raise SystemExit("未配置 database.dsn")
            data_access.init_schema()
            print("PostgreSQL schema initialized.")
            return


if __name__ == "__main__":
    main()

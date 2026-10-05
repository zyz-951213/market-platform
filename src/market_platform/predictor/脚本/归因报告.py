"""
策略归因分析 — 分段收益、涨跌市参与率、年度夏普

用法: python 后端/脚本/归因报告.py
"""

from __future__ import annotations


import numpy as np
import pandas as pd
from loguru import logger

from market_platform.predictor.配置 import get_config, load_config
from market_platform.predictor.接口.回测存储 import load_latest_backtest
from market_platform.predictor.接口.归因 import compute_attribution


def run_attribution():
    attr = compute_attribution()
    if not attr.get("available"):
        logger.error("❌ 无回测数据，请先运行回测")
        return

    bt = load_latest_backtest()
    signals = pd.DataFrame(bt["signals"])
    signals["date"] = pd.to_datetime(signals["date"])
    signals = signals.sort_values("date").reset_index(drop=True)

    # 混合收益（策略+现金）和策略净收益
    signals["ret_mixed"] = signals["period_return"].fillna(0.0).astype(float)

    # 读取累计数据：需要从回测引擎中获得，这里近似用 rolling
    signals["year"] = signals["date"].dt.year
    signals["month"] = signals["date"].dt.month

    # 基准收益
    cfg = load_config()
    df_path = Path(__file__).resolve().parent.parent / "data" / "processed" / f"{cfg['data']['index_code']}_with_features.parquet"
    if df_path.exists():
        df = pd.read_parquet(df_path)
        df["date"] = pd.to_datetime(df["date"])
        df["label_return"] = df["label_return"].fillna(0.0)
        merged = signals[["date"]].merge(df[["date", "label_return"]], on="date", how="left")
        signals["benchmark_ret"] = merged["label_return"].fillna(0.0)
    else:
        signals["benchmark_ret"] = 0.0

    print("=" * 65)
    print("📊 策略归因报告")
    print("=" * 65)
    print(f"\n模型: {bt.get('model_name', '?')}")
    print(f"回测区间: {bt.get('benchmark_start_date')} → {bt.get('benchmark_end_date')}")
    print(f"样本外天数: {len(signals)}")
    print(f"保存时间: {bt.get('saved_at')}")
    print()

    # === 1. 年度归因 ===
    print("━" * 65)
    print("📅 年度归因")
    print("━" * 65)
    print(f"{'年份':>6} {'策略收益':>10} {'基准收益':>10} {'超额':>10} {'闲置收益':>10} {'夏普':>8} {'胜率':>8} {'调仓':>6}")
    print("-" * 70)

    all_years = sorted(signals["year"].unique())
    total_strat = 1.0
    total_bench = 1.0

    for yr in all_years:
        yr_data = signals[signals["year"] == yr]
        if len(yr_data) < 10:
            continue
        strat_ret = (1 + yr_data["ret_mixed"]).prod() - 1
        bench_ret = (1 + yr_data["benchmark_ret"]).prod() - 1
        excess = strat_ret - bench_ret
        sharpe = yr_data["ret_mixed"].mean() / (yr_data["ret_mixed"].std() + 1e-10) * np.sqrt(252)
        win_rate = (yr_data["ret_mixed"] > 0).mean()
        trades = int((yr_data["ret_mixed"].abs() > 1e-6).sum())

        total_strat *= (1 + strat_ret)
        total_bench *= (1 + bench_ret)

        print(f"{yr:>6} {strat_ret:>+9.2%} {bench_ret:>+9.2%} {excess:>+9.2%} {'--':>10} {sharpe:>7.2f} {win_rate:>7.1%} {trades:>5}")

    # 总计
    total_strat_ret = total_strat - 1
    total_bench_ret = total_bench - 1
    print("-" * 70)
    print(f"{'总计':>6} {total_strat_ret:>+9.2%} {total_bench_ret:>+9.2%} {total_strat_ret - total_bench_ret:>+9.2%}")

    print()

    # === 2. 涨跌市参与率 ===
    print("━" * 65)
    print("📈 涨跌市参与率")
    print("━" * 65)

    # 用月度收益判断基准涨跌
    monthly_bench = signals.groupby(["year", "month"])["benchmark_ret"].apply(lambda x: (1 + x).prod() - 1)
    monthly_strat = signals.groupby(["year", "month"])["ret_mixed"].apply(lambda x: (1 + x).prod() - 1)

    up_months = monthly_bench > 0
    down_months = monthly_bench < 0

    up_capture = monthly_strat[up_months].mean() / monthly_bench[up_months].mean() if up_months.sum() > 0 else 0
    down_capture = monthly_strat[down_months].mean() / monthly_bench[down_months].mean() if down_months.sum() > 0 else 0

    print(f"  上涨月份: {up_months.sum()} / {len(monthly_bench)} ({up_months.mean():.1%})")
    print(f"  下跌月份: {down_months.sum()} / {len(monthly_bench)} ({down_months.mean():.1%})")
    print(f"  上涨参与率: {up_capture:.2f}x (策略月均 / 基准月均)")
    print(f"  下跌保护率: {down_capture:.2f}x (<1 表示跌得比基准少)")
    print(f"  月度胜率: {(monthly_strat > 0).mean():.1%}")
    print()

    # === 3. 滚动夏普 ===
    print("━" * 65)
    print("📉 滚动夏普（全年窗口）")
    print("━" * 65)

    rolling_sharpe = signals["ret_mixed"].rolling(252, min_periods=252).apply(
        lambda x: x.mean() / (x.std() + 1e-10) * np.sqrt(252)
    )
    win_1y = rolling_sharpe.dropna()
    if len(win_1y) > 0:
        recent_sharpe = rolling_sharpe.iloc[-1]
        min_sharpe = rolling_sharpe.min()
        print(f"  全期夏普:                {bt.get('sharpe_ratio', 0):.4f}")
        print(f"  滚动夏普均值(1年):       {win_1y.mean():.4f}")
        print(f"  滚动夏普中位数(1年):     {win_1y.median():.4f}")
        print(f"  滚动夏普最小值:          {min_sharpe:.4f}")
        print(f"  最近 1 年夏普:           {recent_sharpe:.4f}")
        pct_positive = (win_1y > 0).mean()
        print(f"  滚动夏普 > 0 占比:        {pct_positive:.1%}")
    print()

    # === 4. 逐年超额来源 ===
    print("━" * 65)
    print("💰 超额来源（闲置资金 vs 方向判断）")
    print("━" * 65)
    cash_ret = bt.get("cash_return", 0)
    strat_only = bt.get("strategy_only_return", 0)
    print(f"  混合总收益: {bt.get('total_return', 0)*100:.2f}%")
    print(f"    其中策略净收益: {strat_only*100:.2f}%")
    print(f"    其中闲置资金:   {cash_ret*100:.2f}%")
    print(f"  闲置资金贡献占比: {cash_ret / (1 + bt.get('total_return', 1) - 1) * 100:.1f}%")
    print()

    # === 5. 最大回撤区间 ===
    print("━" * 65)
    print("⚠️ 最大回撤区间")
    print("━" * 65)
    equity = (1 + signals["ret_mixed"]).cumprod()
    peak = equity.cummax()
    dd = (equity - peak) / peak
    min_dd_idx = dd.idxmin()
    if not np.isnan(min_dd_idx):
        min_dd = dd[min_dd_idx]
        min_start = signals.iloc[:min_dd_idx+1].loc[
            signals.iloc[:min_dd_idx+1]["date"] >= signals.iloc[:min_dd_idx+1].loc[
                (equity.iloc[:min_dd_idx+1] == peak.iloc[:min_dd_idx+1]).idxmax(), "date"
            ]
        ]
        print(f"  最大回撤: {min_dd:.2%}")
        print(f"  回撤开始: {signals.iloc[dd.idxmax()]['date'].date()}")
        print(f"  回撤谷底: {signals.iloc[min_dd_idx]['date'].date()}")

    # === 6. 最佳/最差年度 ===
    print()
    ann_returns = {}
    for yr in all_years:
        yr_data = signals[signals["year"] == yr]
        ann_returns[yr] = (1 + yr_data["ret_mixed"]).prod() - 1
    best_yr = max(ann_returns, key=ann_returns.get) if ann_returns else None
    worst_yr = min(ann_returns, key=ann_returns.get) if ann_returns else None
    if best_yr:
        print(f"  最佳年度: {best_yr} ({ann_returns[best_yr]:+.2%})")
    if worst_yr:
        print(f"  最差年度: {worst_yr} ({ann_returns[worst_yr]:+.2%})")

    llm = attr.get("llm_accuracy", {})
    if llm.get("available"):
        print()
        print("━" * 65)
        print(f"🧠 LLM 分类准确率（主区间 {llm.get('primary_since')} 起）")
        print("━" * 65)
        ps = llm.get("primary_sample", {})
        fs = llm.get("full_sample", {})
        print(f"  主区间准确率:     {ps.get('accuracy', 0):.1%}  (n={ps.get('n', 0)})")
        print(f"  主区间方向准确率: {ps.get('directional_accuracy', 0):.1%}")
        print(f"  主区间预测涨占比: {ps.get('pred_up_pct', 0):.1%}")
        print(f"  全样本准确率:     {fs.get('accuracy', 0):.1%}  (n={fs.get('n', 0)})")
        ra = llm.get("rolling_accuracy", {})
        if ra.get("latest") is not None:
            print(f"  滚动准确率(252日): 最新 {ra['latest']:.1%}  均值 {ra.get('mean', 0):.1%}")

    print()
    print("=" * 65)
    print("✅ 报告完成")
    print("=" * 65)


if __name__ == "__main__":
    run_attribution()

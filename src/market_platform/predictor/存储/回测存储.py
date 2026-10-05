"""回测结果持久化"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

from market_platform.predictor.核心.路径.项目 import data_dir

import hashlib
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from market_platform.predictor.配置 import get_bar_meta, load_config
from market_platform.predictor.分析.回测.引擎 import BacktestResult
from market_platform.predictor.分析.指标.分类指标 import compute_classification_metrics
from market_platform.predictor.存储.数据访问 import (
    load_index_features,
    load_latest_backtest as dal_load_latest_backtest,
    list_backtest_runs as dal_list_backtest_runs,
    load_backtest_run as dal_load_backtest_run,
    load_backtest_by_model_name as dal_load_backtest_by_model_name,
    save_backtest_payload,
)


def _store_dir() -> Path:
    path = data_dir() / "cache" / "backtest"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _processed_path() -> Path:
    cfg = load_config()
    return data_dir() / "processed" / f"{cfg['data']['index_code']}_with_features.parquet"


_DATA_AS_OF_CACHE: dict[str, Any] = {"t": 0.0, "v": None}
_FP_CACHE: dict[str, Any] = {"t": 0.0, "v": None}


def data_as_of() -> Optional[str]:
    now = time.time()
    if _DATA_AS_OF_CACHE["v"] is not None and now - float(_DATA_AS_OF_CACHE["t"]) < 60:
        return _DATA_AS_OF_CACHE["v"]
    df = load_index_features(load_config()["data"]["index_code"])
    if df is None or df.empty or "date" not in df.columns:
        return None
    val = pd.Timestamp(df["date"].max()).strftime("%Y-%m-%d")
    _DATA_AS_OF_CACHE.update({"t": now, "v": val})
    return val


def strategy_fingerprint() -> str:
    """当前策略 + Prompt + 校准配置指纹"""
    now = time.time()
    if _FP_CACHE["v"] is not None and now - float(_FP_CACHE["t"]) < 60:
        return _FP_CACHE["v"]
    cfg = load_config()
    payload = {
        "strategy": cfg.get("backtest", {}).get("strategy", {}),
        "cash_management": cfg.get("cash_management", {}),
        "direction_threshold": cfg.get("features", {}).get("label", {}).get("direction_threshold"),
        "prompt_version": cfg.get("model", {}).get("llm", {}).get("prompt", {}).get("version"),
        "cash_allocation": cfg.get("cash_management", {}).get("allocation", {}),
        "cash_mode": cfg.get("cash_management", {}).get("mode", "fixed"),
        "calibration": cfg.get("model", {}).get("llm", {}).get("calibration", {}),
        "llm_provider": cfg.get("model", {}).get("llm", {}).get("provider", "api"),
        "backtest_start_date": cfg.get("data", {}).get("start_date"),
        "backtest_bar_freq": cfg.get("data", {}).get("bar_freq"),
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    val = hashlib.sha256(raw.encode()).hexdigest()[:16]
    _FP_CACHE.update({"t": now, "v": val})
    return val


def expected_model_label(provider: str | None = None) -> str:
    """与 run_pipeline 回测标签一致：LLM-API[+DualFreq]@<完整model名>"""
    from market_platform.predictor.分析.推断.语言模型预测 import provider_model_id

    cfg = load_config()
    strategy_cfg = cfg.get("backtest", {}).get("strategy", {})
    dual_freq = strategy_cfg.get("dual_freq_gate", False)
    tags: List[str] = []
    if dual_freq:
        tags.append("DualFreq")
    base = "LLM-API"
    label = base + ("+" + "+".join(tags) if tags else "")
    llm_cfg = cfg.get("model", {}).get("llm", {})
    prov = provider or llm_cfg.get("active_api") or "deepseek"
    model = provider_model_id(prov)
    return f"{label}@{model}"


def provider_from_model_label(model_name: str | None) -> str | None:
    """从回测 model_name 反查渠道 id（兼容旧 @provider 与新 @model）。"""
    if not model_name or "@" not in str(model_name):
        return None
    suffix = str(model_name).rsplit("@", 1)[-1]
    cfg = load_config().get("model", {}).get("llm", {})
    providers = cfg.get("providers") or {}
    if suffix in providers:
        return suffix
    for name, pcfg in providers.items():
        if (pcfg or {}).get("model") == suffix:
            return name
    return suffix


def backtest_staleness_reasons(
    backtest: Optional[Dict[str, Any]],
    *,
    provider: str | None = None,
) -> List[str]:
    if not backtest:
        return ["no_backtest"]
    reasons: List[str] = []
    meta = get_bar_meta()
    bt_freq = backtest.get("bar_freq")
    if not bt_freq and meta["bar_freq"] == "daily":
        bt_freq = "weekly" if backtest.get("signals") and len(backtest["signals"]) < 500 else None
    if bt_freq and bt_freq != meta["bar_freq"]:
        reasons.append("freq_mismatch")
    # 按「正在查看的渠道」比对；未指定时从 model_name 的 @provider 推断，避免切渠道误报 mismatch
    view_provider = provider or provider_from_model_label(backtest.get("model_name"))
    expected = expected_model_label(provider=view_provider)
    if backtest.get("model_name") != expected:
        reasons.append("model_mismatch")
    if backtest.get("strategy_fingerprint") != strategy_fingerprint():
        reasons.append("strategy_outdated")
    current_as_of = data_as_of()
    if current_as_of and backtest.get("data_as_of") and backtest.get("data_as_of") != current_as_of:
        reasons.append("data_outdated")
    return reasons


def backtest_stale_message(
    reasons: List[str],
    backtest: Optional[Dict[str, Any]] = None,
    *,
    provider: str | None = None,
) -> Optional[str]:
    if not reasons:
        return None
    parts: List[str] = []
    if "no_backtest" in reasons:
        return "暂无回测缓存，请运行: market-predictor pipeline -- --step backtest --model llm"
    if "freq_mismatch" in reasons:
        parts.append("回测频率与当前配置不一致")
    if "model_mismatch" in reasons:
        saved = backtest.get("model_name") if backtest else "未知"
        view_provider = provider or provider_from_model_label(
            backtest.get("model_name") if backtest else None
        )
        parts.append(f"回测模型为 {saved}，期望为 {expected_model_label(provider=view_provider)}")
    if "strategy_outdated" in reasons:
        saved_at = backtest.get("saved_at") if backtest else None
        parts.append(
            f"策略或 Prompt 已更新{'（缓存于 ' + saved_at + '）' if saved_at else ''}；"
            "定时更新只做增量，全量请手动 pipeline backtest"
        )
    if "data_outdated" in reasons:
        parts.append(
            f"特征数据已更新至 {data_as_of()}，回测仍基于 {backtest.get('data_as_of') if backtest else '未知'}"
        )
    parts.append("可运行: market-predictor update --once（仅增量，不跑全量 LLM）")
    return "；".join(parts)


def save_backtest_result(
    result: BacktestResult,
    model_name: str,
    *,
    is_latest: bool = True,
) -> Path:
    saved_at = datetime.now().isoformat(timespec="seconds")
    fingerprint = strategy_fingerprint()
    provider = provider_from_model_label(model_name) or "default"
    stamp = saved_at.replace(":", "").replace("-", "")
    run_id = f"{fingerprint}_{provider}_{stamp}"
    from market_platform.predictor.分析.推断.语言模型预测 import provider_model_id

    payload: Dict[str, Any] = {
        "run_id": run_id,
        "model_name": model_name,
        "bar_freq": load_config().get("data", {}).get("bar_freq", "daily"),
        "strategy_fingerprint": fingerprint,
        "data_as_of": data_as_of(),
        "saved_at": saved_at,
        "accuracy": result.accuracy,
        "total_return": result.total_return,
        "annualized_return": result.annualized_return,
        "sharpe_ratio": result.sharpe_ratio,
        "max_drawdown": result.max_drawdown,
        "win_rate": result.win_rate,
        "total_trades": result.total_trades,
        "periods_in_market": result.periods_in_market,
        "benchmark_total_return": result.benchmark_total_return,
        "benchmark_max_drawdown": result.benchmark_max_drawdown,
        "excess_return": result.excess_return,
        "benchmark_start_date": str(result.benchmark_start_date.date())
        if result.benchmark_start_date is not None else None,
        "benchmark_end_date": str(result.benchmark_end_date.date())
        if result.benchmark_end_date is not None else None,
        "avg_position": result.avg_position,
        "cash_mgmt_enabled": result.cash_mgmt_enabled,
        "cash_return": result.cash_return,
        "strategy_only_return": result.strategy_only_return,
        "allocation_mode": getattr(result, "allocation_mode", "legacy"),
        "static_4020_return": getattr(result, "static_4020_return", None),
        "equal_six_return": getattr(result, "equal_six_return", None),
        "llm_provider": provider,
        "llm_model": provider_model_id(provider),
        "prompt_version": load_config().get("model", {}).get("llm", {}).get("prompt", {}).get("version"),
        "llm_call_count": int(getattr(result, "llm_call_count", 0) or 0),
        # TASK-003:把"丢弃样本"与"口径降级"写进回测记录,让披露随数据留存
        # (此前只活在日志里;评审 R10/R11)
        "dropped_bars": int(getattr(result, "丢弃bar数", 0) or 0),
        "failed_train_blocks": int(getattr(result, "训练失败块数", 0) or 0),
        "failed_predict_bars": int(getattr(result, "预测失败bar数", 0) or 0),
        "unified_fallback_reason": getattr(result, "统一夏普降级原因", None),
        "weekly_decision": bool(
            (load_config().get("backtest", {}).get("strategy", {}).get("weekly_decision") or {}).get("enabled", False)
        ),
    }
    week_accuracy = float(result.accuracy)
    if not result.daily_signals.empty:
        signal_records = result.daily_signals.assign(
            date=lambda d: pd.to_datetime(d["date"]).dt.strftime("%Y-%m-%d")
        ).to_dict(orient="records")
        payload["signals"] = signal_records
        payload.update(compute_classification_metrics(signal_records))
        # 周频硬切：主准确率为 week_end 口径，勿被全日分类指标覆盖
        payload["day_accuracy"] = payload.get("accuracy")
        payload["accuracy"] = week_accuracy
        payload["week_accuracy"] = week_accuracy

    latest = _store_dir() / "latest.json"
    save_backtest_payload(payload, is_latest=is_latest)
    return latest


def list_backtest_runs(limit: int = 30) -> List[Dict[str, Any]]:
    return dal_list_backtest_runs(limit=limit)


def load_backtest_run(run_id: str) -> Optional[Dict[str, Any]]:
    return dal_load_backtest_run(run_id)


def compare_backtest_runs(run_id_a: str, run_id_b: str) -> Dict[str, Any]:
    """对比两次回测的指标与净值序列（轻量，供 Web 叠加曲线）"""
    a = load_backtest_run(run_id_a)
    b = load_backtest_run(run_id_b)
    if not a or not b:
        return {"available": False}

    def _equity_from_signals(bt: Dict) -> List[float]:
        signals = bt.get("signals") or []
        if not signals:
            return []
        df = pd.DataFrame(signals).sort_values("date")
        rets = df["period_return"].fillna(0.0).astype(float)
        return (1 + rets).cumprod().round(6).tolist()

    return {
        "available": True,
        "a": {
            "run_id": run_id_a,
            "saved_at": a.get("saved_at"),
            "metrics": {
                "annualized_return": a.get("annualized_return"),
                "max_drawdown": a.get("max_drawdown"),
                "sharpe_ratio": a.get("sharpe_ratio"),
                "total_trades": a.get("total_trades"),
                "total_return": a.get("total_return"),
                "excess_return": a.get("excess_return"),
                "avg_position": a.get("avg_position"),
                "cash_mgmt_enabled": a.get("cash_mgmt_enabled", False),
                "cash_return": a.get("cash_return"),
                "strategy_only_return": a.get("strategy_only_return"),
            },
            "equity": _equity_from_signals(a),
            "dates": [s["date"] for s in a.get("signals", [])],
        },
        "b": {
            "run_id": run_id_b,
            "saved_at": b.get("saved_at"),
            "metrics": {
                "annualized_return": b.get("annualized_return"),
                "max_drawdown": b.get("max_drawdown"),
                "sharpe_ratio": b.get("sharpe_ratio"),
                "total_trades": b.get("total_trades"),
                "total_return": b.get("total_return"),
                "excess_return": b.get("excess_return"),
                "avg_position": b.get("avg_position"),
                "cash_mgmt_enabled": b.get("cash_mgmt_enabled", False),
                "cash_return": b.get("cash_return"),
                "strategy_only_return": b.get("strategy_only_return"),
            },
            "equity": _equity_from_signals(b),
            "dates": [s["date"] for s in b.get("signals", [])],
        },
    }


def load_latest_backtest() -> Optional[Dict[str, Any]]:
    return dal_load_latest_backtest()


def load_backtest_for_provider(provider: str | None) -> Optional[Dict[str, Any]]:
    """按渠道加载最近一次回测;provider 为空则取 is_latest。

    **串号提示(评审 C5)**:找不到该渠道时会依次回退"旧标签"→"最近一次回测",
    这可能展示**别的渠道**的回测结果。现在每次回退都会打日志,并在返回值上标注
    `provider_mismatch=True` 与 `requested_provider`,避免前端把它当成本渠道结果。
    """
    if not provider:
        return load_latest_backtest()
    labeled = expected_model_label(provider=provider)
    bt = dal_load_backtest_by_model_name(labeled)
    if bt:
        return bt

    # 兼容旧标签(无 @provider)
    legacy = labeled.rsplit("@", 1)[0]
    bt = dal_load_backtest_by_model_name(legacy)
    if bt:
        logger.warning(
            "回测渠道回退: 未找到 %s,改用旧标签 %s 的回测(provider_mismatch)",
            labeled, legacy,
        )
        return _标注渠道不符(bt, provider, 原因=f"使用旧标签 {legacy}")

    bt = load_latest_backtest()
    if bt:
        logger.warning(
            "回测渠道回退: 未找到 %s 及其旧标签,改用最近一次回测(provider_mismatch)",
            labeled,
        )
        return _标注渠道不符(bt, provider, 原因="使用最近一次回测")
    return bt


def _标注渠道不符(bt: Dict[str, Any], provider: str, *, 原因: str) -> Dict[str, Any]:
    """在回测结果上标注"这不是你请求的渠道"(评审 C5)。"""
    out = dict(bt)
    out["provider_mismatch"] = True
    out["requested_provider"] = provider
    out["provider_mismatch_reason"] = 原因
    return out


def list_backtest_channel_summaries() -> Dict[str, Any]:
    """各 LLM 渠道最近一次回测指标摘要（不含净值曲线），供页面多渠道对比。"""
    from market_platform.predictor.分析.推断.语言模型预测 import list_member_providers, provider_model_id
    from market_platform.predictor.存储.数据访问 import load_backtest_metrics_by_model_name

    rows: List[Dict[str, Any]] = []
    for name in list_member_providers():
        labeled = expected_model_label(provider=name)
        metrics = load_backtest_metrics_by_model_name(labeled)
        if not metrics:
            legacy = labeled.rsplit("@", 1)[0]
            metrics = load_backtest_metrics_by_model_name(legacy)
        item: Dict[str, Any] = {
            "provider": name,
            "model": provider_model_id(name),
            "model_name": labeled,
            "available": bool(metrics),
        }
        if metrics:
            item.update({
                "run_id": metrics.get("run_id"),
                "is_latest": bool(metrics.get("is_latest")),
                "saved_at": metrics.get("saved_at"),
                "data_as_of": metrics.get("data_as_of"),
                "total_return": metrics.get("total_return"),
                "annualized_return": metrics.get("annualized_return"),
                "sharpe_ratio": metrics.get("sharpe_ratio"),
                "max_drawdown": metrics.get("max_drawdown"),
                "win_rate": metrics.get("win_rate"),
                "accuracy": metrics.get("accuracy"),
                "directional_accuracy": metrics.get("directional_accuracy"),
                "avg_position": metrics.get("avg_position"),
                "total_trades": metrics.get("total_trades"),
                "cash_return": metrics.get("cash_return"),
                "strategy_only_return": metrics.get("strategy_only_return"),
                "benchmark_total_return": metrics.get("benchmark_total_return"),
                "period": {
                    "start": metrics.get("benchmark_start_date"),
                    "end": metrics.get("benchmark_end_date"),
                },
            })
        rows.append(item)

    ranked = sorted(
        [r for r in rows if r.get("available") and r.get("total_return") is not None],
        key=lambda r: float(r["total_return"]),
        reverse=True,
    )
    for i, r in enumerate(ranked, start=1):
        r["rank"] = i
    return {
        "channels": rows,
        "ranked": ranked,
        "n_available": sum(1 for r in rows if r.get("available")),
    }


def _periods_per_year() -> int:
    meta = get_bar_meta()
    return 52 if meta.get("bar_freq") == "weekly" else 252


def compute_metrics_from_signals(
    signals: List[dict],
    *,
    index_df: Optional[pd.DataFrame] = None,
) -> Dict[str, Any]:
    """从合并后的 signal 序列重算回测指标。"""
    if not signals:
        return {}

    df = pd.DataFrame(signals).sort_values("date").reset_index(drop=True)
    period_returns = df["period_return"].fillna(0.0).astype(float).values
    positions = df.get("position", df.get("holding_position", pd.Series([0] * len(df))))
    positions = positions.fillna(0.0).astype(float).values

    periods_per_year = _periods_per_year()
    metrics: Dict[str, Any] = {}

    preds = df["prediction"].astype(int).tolist()
    actuals = df["actual"].astype(int).tolist()
    metrics["accuracy"] = sum(p == a for p, a in zip(preds, actuals)) / len(preds)

    trade_eps = float(
        (load_config().get("backtest") or {}).get("strategy", {}).get("trade_epsilon", 0.02)
        or 0.02
    )
    # 与回测引擎 unified_sharpe 一致：六资产分袖权重 L1 变动计调仓
    sleeve_keys = ("csi300", "nasdaq", "gold", "bond", "mm", "repo")
    has_unified_sleeves = all(f"cash_w_{k}" in df.columns for k in sleeve_keys)
    if has_unified_sleeves:
        sleeve = (
            df[[f"cash_w_{k}" for k in sleeve_keys]]
            .apply(pd.to_numeric, errors="coerce")
            .fillna(0.0)
        )
        sleeve_l1 = sleeve.diff().abs().sum(axis=1)
        # 首日相对零权重（与引擎 prev_sleeve_weights={} 一致）
        sleeve_l1.iloc[0] = float(sleeve.iloc[0].abs().sum())
        metrics["total_trades"] = int((sleeve_l1 > trade_eps).sum())
    else:
        metrics["total_trades"] = int(
            sum(
                1
                for i in range(1, len(positions))
                if abs(positions[i] - positions[i - 1]) > trade_eps
            )
        )
    metrics["periods_in_market"] = float(sum(1 for p in positions if p > trade_eps) / max(len(positions), 1))
    metrics["avg_position"] = float(np.mean(positions)) if len(positions) else 0.0

    active_pnl = [r for r, p in zip(period_returns, positions) if p != 0]
    metrics["win_rate"] = (
        sum(1 for r in active_pnl if r > 0) / len(active_pnl) if active_pnl else 0.0
    )

    finite = period_returns[np.isfinite(period_returns)]
    if len(finite) > 0:
        metrics["total_return"] = float(np.prod(1 + finite) - 1)
        n_periods = len(finite)
        metrics["annualized_return"] = (1 + metrics["total_return"]) ** (
            periods_per_year / n_periods
        ) - 1
        metrics["sharpe_ratio"] = float(
            finite.mean() / (finite.std() + 1e-10)
        ) * np.sqrt(periods_per_year)
        cum_ret = np.cumprod(1 + finite)
        running_max = np.maximum.accumulate(cum_ret)
        metrics["max_drawdown"] = float(((cum_ret - running_max) / running_max).min())

    if index_df is not None and not index_df.empty and len(df) >= 2:
        feat = index_df.copy()
        feat["date"] = pd.to_datetime(feat["date"]).dt.strftime("%Y-%m-%d")
        sig_dates = df["date"].astype(str).tolist()
        sub = feat[feat["date"].isin(sig_dates)].sort_values("date")
        if len(sub) >= 2 and "close" in sub.columns:
            start_close = float(sub.iloc[0]["close"])
            end_close = float(sub.iloc[-1]["close"])
            bar_ret = sub["close"].pct_change().dropna()
            metrics["benchmark_start_date"] = str(sub.iloc[0]["date"])[:10]
            metrics["benchmark_end_date"] = str(sub.iloc[-1]["date"])[:10]
            metrics["benchmark_total_return"] = end_close / start_close - 1 if start_close else 0.0
            n_bars = len(bar_ret)
            metrics["benchmark_annualized_return"] = (
                (1 + metrics["benchmark_total_return"]) ** (periods_per_year / n_bars) - 1
                if n_bars > 0 else 0.0
            )
            cum_bh = (1 + bar_ret).cumprod()
            metrics["benchmark_max_drawdown"] = float(
                ((cum_bh - cum_bh.cummax()) / cum_bh.cummax()).min()
            )
            metrics["excess_return"] = metrics.get("total_return", 0.0) - metrics["benchmark_total_return"]

    # 增量保存路径也需补齐闲置资金指标，否则图表/归因会把 cash_mgmt 当成关闭
    cash_cfg = load_config().get("cash_management", {})
    cash_enabled = bool(cash_cfg.get("enabled", False))
    has_cash_weights = "cash_w_gold" in df.columns and df["cash_w_gold"].notna().any()
    metrics["cash_mgmt_enabled"] = cash_enabled and has_cash_weights
    if metrics["cash_mgmt_enabled"]:
        try:
            from market_platform.predictor.分析.资金.闲置管理 import (
                compute_cash_return,
                fetch_cash_assets,
            )

            dates = pd.to_datetime(df["date"])
            cash_returns = fetch_cash_assets(dates.min(), dates.max())
            cash_pnls = []
            strategy_only = []
            for _, row in df.iterrows():
                pos = float(
                    row["holding_position"]
                    if "holding_position" in row and pd.notna(row.get("holding_position"))
                    else row.get("position", 0) or 0
                )
                day_weights = {
                    "gold": float(row.get("cash_w_gold") or 0),
                    "bond": float(row.get("cash_w_bond") or 0),
                    "nasdaq": float(row.get("cash_w_nasdaq") or 0),
                    "repo": float(row.get("cash_w_repo") or 0),
                    "mm": float(row.get("cash_w_mm") or 0),
                }
                c = float(
                    compute_cash_return(
                        pos, row, cash_returns, date_idx=row["date"], weights=day_weights,
                    )
                )
                cash_pnls.append(c)
                strategy_only.append(float(row.get("period_return") or 0) - c)
            metrics["cash_return"] = float(np.prod(1.0 + np.asarray(cash_pnls)) - 1.0)
            metrics["strategy_only_return"] = float(
                np.prod(1.0 + np.asarray(strategy_only)) - 1.0
            )
        except Exception:
            metrics["cash_mgmt_enabled"] = cash_enabled
            metrics.setdefault("cash_return", None)
            metrics.setdefault("strategy_only_return", None)

    return metrics


def merge_backtest_signals(
    old_signals: List[dict],
    new_signals: List[dict],
    *,
    split_date: str,
) -> List[dict]:
    """保留 split_date 之前的旧信号，之后用新回测结果覆盖。"""
    kept = [s for s in old_signals if str(s.get("date", ""))[:10] < split_date]
    replaced = [s for s in new_signals if str(s.get("date", ""))[:10] >= split_date]
    merged = kept + replaced
    merged.sort(key=lambda s: str(s.get("date", "")))
    return merged


def save_backtest_from_signals(
    signals: List[dict],
    model_name: str,
    *,
    incremental: bool = False,
    llm_cache_stats: Optional[dict] = None,
    is_latest: bool = True,
) -> Path:
    """将 signal 列表持久化（增量合并后的完整序列）。"""
    saved_at = datetime.now().isoformat(timespec="seconds")
    fingerprint = strategy_fingerprint()
    provider = provider_from_model_label(model_name) or "default"
    stamp = saved_at.replace(":", "").replace("-", "")
    run_id = f"{fingerprint}_{provider}_{stamp}"
    index_df = load_index_features(load_config()["data"]["index_code"])
    metrics = compute_metrics_from_signals(signals, index_df=index_df)

    payload: Dict[str, Any] = {
        "run_id": run_id,
        "model_name": model_name,
        "bar_freq": load_config().get("data", {}).get("bar_freq", "daily"),
        "strategy_fingerprint": fingerprint,
        "data_as_of": data_as_of(),
        "saved_at": saved_at,
        "incremental": incremental,
        "llm_provider": provider,
        "signals": signals,
        **metrics,
    }
    if llm_cache_stats:
        payload["llm_cache_stats"] = llm_cache_stats
    payload.update(compute_classification_metrics(signals))
    save_backtest_payload(payload, is_latest=is_latest)
    return _store_dir() / "latest.json"

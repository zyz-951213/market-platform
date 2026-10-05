"""市场综合研判服务"""

from __future__ import annotations

from market_platform.predictor.核心.路径.项目 import data_dir

from pathlib import Path
from typing import Any, Dict, Optional
import time

import pandas as pd
import numpy as np
from loguru import logger

from market_platform.predictor.配置 import get_config, get_bar_meta
from market_platform.predictor.分析.推断.语言模型预测 import LLMApiPredictor
from market_platform.predictor.存储.数据访问 import load_index_features, load_latest_backtest_metrics
from market_platform.predictor.接口.回测存储 import (
    backtest_stale_message as format_backtest_stale_message,
    backtest_staleness_reasons,
    expected_model_label,
)
from market_platform.predictor.接口.分类指标 import compute_classification_metrics
from market_platform.predictor.接口.策略摘要 import build_strategy_summary
from market_platform.predictor.接口.自动更新 import fetch_auto_update_status


def processed_path() -> Path:
    cfg = get_config()
    return data_dir() / "processed" / f"{cfg['data']['index_code']}_with_features.parquet"


_FEATURE_DF_CACHE: dict[str, Any] = {"key": None, "mtime": None, "df": None}


def load_feature_df() -> pd.DataFrame:
    """加载特征表；进程内短缓存，避免 overview/charts 重复打库。"""
    cfg = get_config()
    code = cfg["data"]["index_code"]
    # parquet 回退路径的 mtime；PG 主路径用空 mtime + 60s TTL
    path = processed_path()
    mtime = path.stat().st_mtime if path.exists() else None
    cache_key = code
    now = time.time()
    hit = _FEATURE_DF_CACHE
    if (
        hit.get("df") is not None
        and hit.get("key") == cache_key
        and hit.get("mtime") == mtime
        and (mtime is not None or now - float(hit.get("loaded_at") or 0) < 60)
    ):
        return hit["df"]

    df = load_index_features(code)
    if df is None or df.empty:
        raise FileNotFoundError("特征数据不存在，请先运行: market-predictor pipeline -- --step data")
    out = df.sort_values("date").reset_index(drop=True)
    _FEATURE_DF_CACHE.update({"key": cache_key, "mtime": mtime, "df": out, "loaded_at": now})
    return out


def get_overview() -> Dict[str, Any]:
    df = load_feature_df()
    labeled = df.dropna(subset=["label_direction"])
    cfg = get_config()
    meta = get_bar_meta()
    latest = df.iloc[-1]
    backtest = load_latest_backtest_metrics()
    stale_reasons = backtest_staleness_reasons(backtest)
    backtest_stale = len(stale_reasons) > 0
    stale_message = format_backtest_stale_message(stale_reasons, backtest)
    llm_cfg = cfg.get("model", {}).get("llm", {})
    from market_platform.predictor.分析.推断.语言模型预测 import list_llm_channels, list_member_providers

    providers = list_member_providers()
    channels = list_llm_channels()
    active_api = llm_cfg.get("active_api", providers[0] if providers else "deepseek")
    active_model = (llm_cfg.get("providers", {}).get(active_api) or {}).get("model")
    # is_latest 回测渠道（「主渠道 latest」应对齐此，而非仅看 active_api）
    latest_provider = None
    latest_model = None
    if backtest:
        from market_platform.predictor.接口.回测存储 import provider_from_model_label

        latest_provider = backtest.get("llm_provider") or provider_from_model_label(
            backtest.get("model_name")
        )
        latest_model = backtest.get("llm_model") or (
            (llm_cfg.get("providers", {}).get(latest_provider) or {}).get("model")
            if latest_provider else None
        )
        if not latest_model and backtest.get("model_name") and "@" in str(backtest["model_name"]):
            latest_model = str(backtest["model_name"]).rsplit("@", 1)[-1]
    return {
        "index_code": cfg["data"]["index_code"],
        "bar_freq": meta["bar_freq"],
        "bar_meta": meta,
        "total_bars": len(df),
        "labeled_bars": len(labeled),
        "feature_columns": len(df.columns),
        "date_range": {
            "start": pd.Timestamp(df["date"].min()).strftime("%Y-%m-%d"),
            "end": pd.Timestamp(df["date"].max()).strftime("%Y-%m-%d"),
        },
        "data_as_of": pd.Timestamp(df["date"].max()).strftime("%Y-%m-%d"),
        "latest": {
            "date": pd.Timestamp(latest["date"]).strftime("%Y-%m-%d"),
            "close": float(latest["close"]) if pd.notna(latest.get("close")) else None,
            "label_direction": int(latest["label_direction"])
            if pd.notna(latest.get("label_direction")) else None,
            "spx_ret_1d": float(latest["spx_ret_1d"])
            if pd.notna(latest.get("spx_ret_1d")) else None,
            "has_news": bool(
                pd.notna(latest.get("macro_news_summary"))
                and str(latest.get("macro_news_summary")).strip()
            ),
            "tf_proba_up": float(latest["tf_proba_up"])
            if pd.notna(latest.get("tf_proba_up")) else None,
            "tf_proba_flat": float(latest["tf_proba_flat"])
            if pd.notna(latest.get("tf_proba_flat")) else None,
            "tf_proba_down": float(latest["tf_proba_down"])
            if pd.notna(latest.get("tf_proba_down")) else None,
            "tf_pred_direction": int(latest["tf_pred_direction"])
            if pd.notna(latest.get("tf_pred_direction")) else None,
        },
        "llm_provider": "api",
        "active_api": active_api,
        "active_model": active_model,
        "latest_backtest_provider": latest_provider,
        "latest_backtest_model": latest_model,
        "llm_providers": providers,
        "llm_channels": channels,
        "backtest": backtest,
        "backtest_stale": backtest_stale,
        "backtest_stale_reasons": stale_reasons,
        "backtest_stale_message": stale_message,
        "expected_backtest_model": expected_model_label(),
        "strategy_summary": build_strategy_summary(),
        "auto_update": fetch_auto_update_status(),
    }


def get_price_series(
    limit: Optional[int] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
) -> Dict[str, Any]:
    df = load_feature_df().copy()
    df["date"] = pd.to_datetime(df["date"])

    scope = "full"
    if start or end:
        scope = "range"
        if start:
            df = df[df["date"] >= pd.Timestamp(start)]
        if end:
            df = df[df["date"] <= pd.Timestamp(end)]
    elif limit is not None and limit > 0:
        scope = "recent"
        df = df.tail(limit)

    df = df.copy()
    df["date"] = df["date"].dt.strftime("%Y-%m-%d")
    return {
        "dates": df["date"].tolist(),
        "close": [float(x) if pd.notna(x) else None for x in df["close"]],
        "label_return": [float(x) if pd.notna(x) else None for x in df.get("label_return", [])],
        "date_range": {
            "start": df["date"].iloc[0] if len(df) else None,
            "end": df["date"].iloc[-1] if len(df) else None,
        },
        "total_bars": len(df),
        "scope": scope,
    }


def run_deepseek_analysis(date: Optional[str] = None) -> Dict[str, Any]:
    """使用当前 active_api 渠道做 live 研判（CLI 用，不经 Web API）。"""
    return run_channel_analysis(date=date)


def get_llm_channel_catalog() -> Dict[str, Any]:
    """前端渠道选择器用：列出可调 API 成员渠道 + 完整 model 名。"""
    from market_platform.predictor.分析.推断.语言模型预测 import list_llm_channels, list_member_providers

    cfg = get_config().get("model", {}).get("llm", {})
    providers = list_member_providers()
    active = cfg.get("active_api", providers[0] if providers else "deepseek")
    items = []
    for ch in list_llm_channels():
        pcfg = cfg.get("providers", {}).get(ch["id"], {}) or {}
        items.append({
            "id": ch["id"],
            "model": ch["model"],
            "label": ch["label"],
            "base_url": pcfg.get("base_url"),
            "is_active": ch["is_active"],
        })
    return {"active_api": active, "providers": items}


def run_channel_analysis(
    date: Optional[str] = None,
    *,
    provider: Optional[str] = None,
    use_cache: Optional[bool] = None,
) -> Dict[str, Any]:
    from market_platform.predictor.分析.推断.集合元模型 import MetaEnsemblePredictor, is_ensemble_provider
    from market_platform.predictor.分析.提示词.构建器 import LABEL_TO_TEXT

    df = load_feature_df()
    meta = get_bar_meta()
    channel = provider or get_config().get("model", {}).get("llm", {}).get("active_api")

    if is_ensemble_provider(channel):
        predictor = MetaEnsemblePredictor()
        predictor.bind_dataframe(df)
        predictor.train(df)
        row = df.iloc[-1:]
        if date:
            sub = df[pd.to_datetime(df["date"]) <= pd.Timestamp(date)]
            row = sub.iloc[-1:] if len(sub) else row
        pred, proba, week_pred = predictor.predict_bundle(row)
        conf = float(np.max(proba))
        result = {
            "date": pd.Timestamp(row.iloc[0]["date"]).strftime("%Y-%m-%d"),
            "direction": LABEL_TO_TEXT.get(int(pred), str(pred)),
            "confidence": conf,
            "proba": {
                "跌": float(proba[0]),
                "平": float(proba[1]),
                "涨": float(proba[2]),
            },
            "week_direction": LABEL_TO_TEXT.get(int(week_pred), None) if week_pred is not None else None,
            "meta": getattr(predictor, "_last_meta", {}),
            "source": "ensemble",
        }
        result["bar_freq"] = meta["bar_freq"]
        result["bar_unit"] = meta["bar_unit"]
        result["predict_horizon"] = meta["predict_horizon"]
        result["channel"] = "ensemble"
        logger.info(
            f"🧩 集合元模型研判 {result['date']}: {result['direction']} "
            f"(conf={result['confidence']:.2f}, rule={result.get('meta', {}).get('rule')})"
        )
        return result

    predictor = LLMApiPredictor(provider=provider)
    predictor.bind_dataframe(df)
    result = predictor.analyze_latest(df, date=date)
    if use_cache is False:
        pass  # analyze_latest 内部走 analyze_prompt，如需强制刷新可扩展
    result["bar_freq"] = meta["bar_freq"]
    result["bar_unit"] = meta["bar_unit"]
    result["predict_horizon"] = meta["predict_horizon"]
    result["weekly_decision"] = bool(meta.get("weekly_decision"))
    result["decision_freq"] = meta.get("decision_freq")
    result["channel"] = predictor.provider_name
    logger.info(
        f"☁️ LLM[{predictor.provider_name}] 研判 {result['date']}: {result['direction']} "
        f"(conf={result['confidence']:.2f})"
    )
    return result


def get_backtest_charts(provider: Optional[str] = None) -> Dict[str, Any]:
    """构建回测可视化序列（净值、回撤、分布）"""
    from market_platform.predictor.接口.回测存储 import load_backtest_for_provider, provider_from_model_label

    backtest = load_backtest_for_provider(provider)
    if not backtest or not backtest.get("signals"):
        return {"available": False}

    signals = pd.DataFrame(backtest["signals"])
    signals["date"] = pd.to_datetime(signals["date"])
    signals = signals.sort_values("date").reset_index(drop=True)

    features = load_feature_df()[["date", "label_return", "close"]].copy()
    features["date"] = pd.to_datetime(features["date"])
    merged = signals.merge(features, on="date", how="left")

    # 图表基准用收盘价净值，保证最新交易日（尚无 label_return）也会反映涨跌
    # 策略 period_return / 准确率仍按 label 体系计算，不在此改动
    close = merged["close"].astype(float)
    if close.notna().sum() >= 2 and float(close.iloc[0]) > 0:
        benchmark_equity = (close / float(close.iloc[0])).ffill()
        benchmark_ret = close.pct_change().fillna(0.0)
    else:
        benchmark_ret = merged["label_return"].fillna(0.0).astype(float)
        benchmark_equity = (1 + benchmark_ret).cumprod()

    strategy_ret = merged["period_return"].fillna(0.0).astype(float)
    strategy_equity = (1 + strategy_ret).cumprod()

    def _drawdown(equity: pd.Series) -> pd.Series:
        peak = equity.cummax()
        return (equity - peak) / peak

    strategy_dd = _drawdown(strategy_equity)
    benchmark_dd = _drawdown(benchmark_equity.ffill())
    dates = merged["date"].dt.strftime("%Y-%m-%d").tolist()

    label_map = {-1: "跌", 0: "平", 1: "涨"}
    pred_dist = merged["prediction"].value_counts().to_dict()
    actual_dist = merged["actual"].value_counts().to_dict()
    hit = (merged["prediction"] == merged["actual"]).sum()

    confusion = {label_map.get(a, str(a)): {} for a in (-1, 0, 1)}
    ct = pd.crosstab(
        merged["actual"].astype(int),
        merged["prediction"].astype(int),
        dropna=False,
    )
    for actual_i, row in ct.iterrows():
        actual = label_map.get(int(actual_i), str(actual_i))
        confusion.setdefault(actual, {})
        for pred_i, cnt in row.items():
            if int(cnt) <= 0:
                continue
            pred = label_map.get(int(pred_i), str(pred_i))
            confusion[actual][pred] = int(cnt)

    cls_metrics = compute_classification_metrics(backtest.get("signals") or [])
    meta = get_bar_meta()
    backtest_freq = backtest.get("bar_freq")
    if not backtest_freq and meta["bar_freq"] == "daily" and len(signals) < 500:
        backtest_freq = "weekly"
    stale_reasons = backtest_staleness_reasons(backtest, provider=provider)
    stale_message = format_backtest_stale_message(stale_reasons, backtest, provider=provider)
    freq_mismatch = "freq_mismatch" in stale_reasons
    if "holding_position" in merged.columns:
        pos_series = merged["holding_position"]
    else:
        # 兼容旧缓存：当日收盘实际仓 ≈ 下一交易日开盘仓
        pos_series = merged["position"].shift(-1).fillna(merged["position"])

    cash_mgmt_enabled = bool(backtest.get("cash_mgmt_enabled", False))
    allocation_mode = str(
        backtest.get("allocation_mode")
        or ((merged["allocation_mode"].iloc[0] if "allocation_mode" in merged.columns and len(merged) else "legacy"))
        or "legacy"
    ).lower()
    unified_mode = allocation_mode in {"unified_sharpe", "sharpe", "unified"}
    strategy_only_equity = None
    cash_assets = None
    cash_returns = None
    if cash_mgmt_enabled:
        try:
            from market_platform.predictor.分析.资金.闲置管理 import (
                build_cash_asset_curves,
                fetch_all_sleeve_returns,
                fetch_cash_assets,
            )

            cash_start = merged["date"].min()
            cash_end = merged["date"].max()
            if unified_mode:
                cash_returns = fetch_all_sleeve_returns(cash_start, cash_end, index_df=merged)
                # 统一账本不再拆「策略净收益」曲线
                strategy_only_equity = None
            else:
                cash_returns = fetch_cash_assets(cash_start, cash_end)
                from market_platform.predictor.分析.资金.闲置管理 import (
                    CASH_ASSET_SPECS,
                    align_cash_returns_to_dates,
                    get_cash_mgmt_active_start,
                    normalized_cash_weights,
                )

                aligned = align_cash_returns_to_dates(merged["date"], cash_returns)
                active_start = get_cash_mgmt_active_start(cash_returns)
                default_w = normalized_cash_weights()
                pos_f = pos_series.astype(float).fillna(0.0).clip(0.0, 1.0).reset_index(drop=True)
                idle = (1.0 - pos_f).clip(lower=0.0)
                cash_pnls = pd.Series(0.0, index=merged.index, dtype=float)
                if active_start is not None:
                    active_mask = pd.to_datetime(merged["date"]).reset_index(drop=True) >= pd.Timestamp(active_start)
                else:
                    active_mask = pd.Series(True, index=range(len(merged)))
                for key, ret_col, *_ in CASH_ASSET_SPECS:
                    if ret_col not in aligned.columns:
                        continue
                    if all(c in merged.columns for c in ("cash_w_gold", "cash_w_nasdaq", "cash_w_repo", "cash_w_mm")):
                        wcol = {
                            "gold": "cash_w_gold",
                            "bond": "cash_w_bond",
                            "nasdaq": "cash_w_nasdaq",
                            "repo": "cash_w_repo",
                            "mm": "cash_w_mm",
                        }[key]
                        w = merged[wcol].astype(float).fillna(float(default_w.get(key, 0.0))).reset_index(drop=True)
                    else:
                        w = pd.Series(float(default_w.get(key, 0.0)), index=range(len(merged)))
                    asset_ret = aligned[ret_col].astype(float).fillna(0.0).reset_index(drop=True)
                    cash_pnls = cash_pnls + idle * w * asset_ret * active_mask.astype(float)
                strategy_only_ret = strategy_ret.reset_index(drop=True) - cash_pnls
                strategy_only_equity = (1 + strategy_only_ret).cumprod().round(6).tolist()
            cash_assets = build_cash_asset_curves(
                merged["date"],
                cash_returns,
                positions=None if unified_mode else pos_series,
                unified=unified_mode,
            )
            if cash_assets and cash_assets.get("enabled"):
                summary = build_strategy_summary()
                avg_pos = backtest.get("avg_position")
                cash_assets["strategy"] = {
                    "headline": summary.get("headline"),
                    "philosophy": summary.get("philosophy"),
                    "tags": summary.get("tags", []),
                    "base_position": summary.get("base_position"),
                    "bull_regime_base": summary.get("bull_regime_base"),
                    "bear_regime_base": summary.get("bear_regime_base"),
                    "rebalance_interval_days": summary.get("rebalance_interval_days"),
                    "min_hold_periods": summary.get("min_hold_periods"),
                    "dual_freq_gate": summary.get("dual_freq_gate"),
                    "weekly_decision": summary.get("weekly_decision"),
                    "position_cycle": summary.get("position_cycle"),
                    "prompt_version": summary.get("prompt_version"),
                    "prompt_label": summary.get("prompt_label"),
                    "active_model": summary.get("active_model"),
                    "regime_router": summary.get("regime_router"),
                    "classification": summary.get("classification"),
                    "return_calibration": summary.get("return_calibration"),
                    "notes": summary.get("notes", []),
                    "allocations": summary.get("cash_management", {}).get("allocations", []),
                    "sleeves": summary.get("cash_management", {}).get("sleeves", []),
                    "allocation_mode": summary.get("allocation_mode"),
                }
                cash_assets["backtest_stats"] = {
                    "avg_position": avg_pos,
                    "avg_idle": (1.0 - float(avg_pos)) if avg_pos is not None else None,
                    "strategy_only_return": backtest.get("strategy_only_return"),
                    "cash_return": backtest.get("cash_return"),
                    "total_return": backtest.get("total_return"),
                    "model_name": backtest.get("model_name"),
                    "static_4020_return": backtest.get("static_4020_return"),
                    "equal_six_return": backtest.get("equal_six_return"),
                }
        except Exception as exc:
            logger.warning(f"闲置资金曲线构建失败: {exc}")

    from market_platform.predictor.分析.资金.闲置管理 import build_fund_allocation_history

    weight_history = None
    if cash_mgmt_enabled and "cash_w_gold" in merged.columns:
        if merged["cash_w_gold"].notna().any():
            pos_f = pos_series.astype(float).fillna(0.0).clip(0.0, 1.0).reset_index(drop=True)
            weight_history = []
            for loc in range(len(merged)):
                r = merged.iloc[loc]
                row_w = {
                    "gold": float(r["cash_w_gold"]) if pd.notna(r.get("cash_w_gold")) else 0.0,
                    "bond": float(r["cash_w_bond"]) if "cash_w_bond" in merged.columns and pd.notna(r.get("cash_w_bond")) else 0.0,
                    "nasdaq": float(r["cash_w_nasdaq"]) if pd.notna(r.get("cash_w_nasdaq")) else 0.0,
                    "repo": float(r["cash_w_repo"]) if pd.notna(r.get("cash_w_repo")) else 0.0,
                    "mm": float(r["cash_w_mm"]) if pd.notna(r.get("cash_w_mm")) else 0.0,
                }
                if unified_mode:
                    if "cash_w_csi300" in merged.columns and pd.notna(r.get("cash_w_csi300")):
                        row_w["csi300"] = float(r["cash_w_csi300"])
                    else:
                        row_w["csi300"] = float(pos_f.iloc[loc]) if loc < len(pos_f) else 0.0
                weight_history.append(row_w)

    allocation_history = build_fund_allocation_history(
        merged["date"],
        pos_series,
        cash_mgmt_enabled=cash_mgmt_enabled,
        cash_returns=cash_returns if cash_mgmt_enabled else None,
        weight_history=weight_history,
        absolute_cash_weights=unified_mode,
        strategy_label="沪深300" if unified_mode else None,
    )

    return {
        "available": True,
        "bar_freq": meta["bar_freq"],
        "backtest_bar_freq": backtest_freq,
        "freq_mismatch": freq_mismatch,
        "stale": len(stale_reasons) > 0,
        "stale_reasons": stale_reasons,
        "freq_warning": stale_message,
        "model_name": backtest.get("model_name"),
        "llm_provider": backtest.get("llm_provider") or provider_from_model_label(
            backtest.get("model_name")
        ),
        "saved_at": backtest.get("saved_at"),
        "period": {
            "start": backtest.get("benchmark_start_date"),
            "end": backtest.get("benchmark_end_date"),
        },
        "metrics": {
            "accuracy": backtest.get("accuracy"),
            "directional_accuracy": backtest.get("directional_accuracy") or cls_metrics.get("directional_accuracy"),
            "flat_recall": backtest.get("flat_recall") or cls_metrics.get("flat_recall"),
            "high_conf_accuracy": backtest.get("high_conf_accuracy") or cls_metrics.get("high_conf_accuracy"),
            "pred_flat_rate": backtest.get("pred_flat_rate") or cls_metrics.get("pred_flat_rate"),
            "total_return": backtest.get("total_return"),
            "benchmark_total_return": backtest.get("benchmark_total_return"),
            "excess_return": backtest.get("excess_return"),
            "annualized_return": backtest.get("annualized_return"),
            "sharpe_ratio": backtest.get("sharpe_ratio"),
            "max_drawdown": backtest.get("max_drawdown"),
            "benchmark_max_drawdown": backtest.get("benchmark_max_drawdown"),
            "win_rate": backtest.get("win_rate"),
            "total_trades": backtest.get("total_trades"),
            "periods_in_market": backtest.get("periods_in_market"),
            "avg_position": backtest.get("avg_position"),
            "cash_mgmt_enabled": backtest.get("cash_mgmt_enabled", False),
            "cash_return": backtest.get("cash_return"),
            "strategy_only_return": backtest.get("strategy_only_return"),
            "allocation_mode": allocation_mode,
            "static_4020_return": backtest.get("static_4020_return"),
            "equal_six_return": backtest.get("equal_six_return"),
        },
        "dates": dates,
        "strategy_equity": strategy_equity.round(6).tolist(),
        "strategy_only_equity": strategy_only_equity,
        "cash_assets": cash_assets,
        "allocation_history": allocation_history,
        "benchmark_equity": benchmark_equity.round(6).tolist(),
        "strategy_drawdown": strategy_dd.round(6).tolist(),
        "benchmark_drawdown": benchmark_dd.round(6).tolist(),
        "position": pos_series.round(4).tolist(),
        "prediction_dist": {label_map.get(int(k), str(k)): int(v) for k, v in pred_dist.items()},
        "actual_dist": {label_map.get(int(k), str(k)): int(v) for k, v in actual_dist.items()},
        "confusion": confusion,
        "hit_count": int(hit),
        "total_count": len(merged),
        "strategy_summary": build_strategy_summary(),
    }


def build_prompt_preview(date: Optional[str] = None) -> Dict[str, Any]:
    from market_platform.predictor.分析.提示词.构建器 import build_preview_payload

    df = load_feature_df()
    payload = build_preview_payload(df, date=date)
    # 兼容旧字段
    payload["date"] = payload["meta"]["date"]
    payload["requested_date"] = payload["meta"].get("requested_date")
    payload["date_aligned"] = payload["meta"].get("date_aligned")
    return payload

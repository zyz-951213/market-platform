"""Daily auto-update: data refresh + incremental backtest + paper trading."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import pandas as pd
from loguru import logger

from market_platform.predictor.配置 import get_config
from market_platform.predictor.存储.数据访问 import load_auto_update_state, save_auto_update_state
from market_platform.predictor.接口.回测存储 import (
    backtest_staleness_reasons,
    data_as_of,
    expected_model_label,
    load_latest_backtest,
    merge_backtest_signals,
    save_backtest_from_signals,
    strategy_fingerprint,
)
from market_platform.predictor.脚本.运行管道 import run_llm_backtest, step_data


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_trading_day() -> bool:
    return datetime.now().weekday() < 5


def _needs_full_backtest(reasons: List[str], *, allow_full: bool) -> bool:
    """
    定时更新默认禁止全量。
    仅当没有任何历史信号（冷启动）且 allow_full=True 时才允许全量；
    strategy_outdated / model_mismatch 等一律走增量（历史 LLM 读库）。
    """
    if not allow_full:
        return False
    # 冷启动：库里还没有可合并的信号
    return "no_backtest" in reasons


def _rewind_date(df: pd.DataFrame, last_signal_date: str, rewind_bars: int) -> str:
    dates = pd.to_datetime(df["date"])
    last_ts = pd.Timestamp(last_signal_date)
    hits = df.index[dates <= last_ts]
    if len(hits) == 0:
        return pd.Timestamp(df.iloc[max(0, len(df) - rewind_bars)]["date"]).strftime("%Y-%m-%d")
    anchor = int(hits[-1])
    start_idx = max(0, anchor - rewind_bars)
    return pd.Timestamp(df.iloc[start_idx]["date"]).strftime("%Y-%m-%d")


def run_incremental_backtest() -> Dict[str, Any]:
    """增量回测：历史 LLM 读 DB 缓存，仅新 bar 调 API，signal 合并写库。"""
    cfg = get_config()
    auto_cfg = cfg.get("auto_update", {})
    rewind_bars = int(auto_cfg.get("rewind_bars", 126))
    allow_full = bool(auto_cfg.get("allow_full_backtest", False))
    model_label = expected_model_label()

    latest = load_latest_backtest()
    old_signals = (latest or {}).get("signals") or []
    if not old_signals:
        if not allow_full:
            logger.warning("无历史回测信号，且 allow_full_backtest=false：跳过全量，请手动 pipeline backtest")
            return {
                "mode": "incremental",
                "status": "skipped",
                "message": "无历史信号且禁止全量；请手动: market-predictor pipeline -- --step backtest --model llm",
            }
        logger.info("无历史回测信号，冷启动全量回测（历史 LLM 仍走 DB 缓存）")
        result = run_llm_backtest("llm", save=True)
        return {
            "mode": "full",
            "status": "ok" if result else "failed",
            "llm_cache_stats": getattr(result, "llm_cache_stats", None) if result else None,
        }

    from market_platform.predictor.接口.市场分析 import load_feature_df

    df = load_feature_df().dropna(subset=["label_direction"]).reset_index(drop=True)
    last_signal_date = max(str(s["date"])[:10] for s in old_signals)
    current = data_as_of()
    if current and last_signal_date >= current:
        return {"mode": "incremental", "status": "skipped", "message": "回测信号已是最新"}

    split_date = _rewind_date(df, last_signal_date, rewind_bars)
    logger.info(
        "增量回测: 保留 {} 前信号，自 {} 重放（API 仅 {} 之后）",
        split_date,
        split_date,
        last_signal_date,
    )

    result = run_llm_backtest(
        "llm",
        test_start_date=split_date,
        save=False,
        api_cutoff_date=last_signal_date,
    )
    if result is None or result.daily_signals.empty:
        return {"mode": "incremental", "status": "failed", "message": "回测未产生信号"}

    new_records = result.daily_signals.assign(
        date=lambda d: pd.to_datetime(d["date"]).dt.strftime("%Y-%m-%d")
    ).to_dict(orient="records")
    merged = merge_backtest_signals(old_signals, new_records, split_date=split_date)
    stats = getattr(result, "llm_cache_stats", None)

    save_backtest_from_signals(
        merged,
        model_label,
        incremental=True,
        llm_cache_stats=stats,
    )
    new_bars = sum(1 for s in new_records if str(s["date"])[:10] > last_signal_date)
    logger.info(
        "增量回测完成: 合并 {} 条信号，新增 {} 根 K 线，LLM cache hits/misses={}",
        len(merged),
        new_bars,
        stats,
    )
    return {
        "mode": "incremental",
        "status": "ok",
        "split_date": split_date,
        "last_signal_date": last_signal_date,
        "data_as_of": current,
        "total_signals": len(merged),
        "new_bars": new_bars,
        "llm_cache_stats": stats,
    }


def run_daily_update(*, force: bool = False, skip_paper: bool = False) -> Dict[str, Any]:
    """收盘后自动更新：拉数 → 增量回测（默认禁止全量）→ 模拟实盘。"""
    cfg = get_config()
    auto_cfg = cfg.get("auto_update", {})
    if not auto_cfg.get("enabled", True):
        return {"status": "disabled", "message": "自动更新未启用"}

    if not force and not _is_trading_day():
        return {"status": "skipped", "message": "非交易日"}

    report: Dict[str, Any] = {
        "started_at": _utc_now_iso(),
        "force": force,
    }

    try:
        if auto_cfg.get("refresh_data", True):
            logger.info("自动更新 Step 1: 刷新行情与特征")
            # 定时任务强制对照数据源，避免 max_stale 误判导致「跑了但没更新」
            step_data(force_refresh=True)
            report["data_refreshed"] = True
            report["data_as_of"] = data_as_of()

        latest = load_latest_backtest()
        reasons = backtest_staleness_reasons(latest)
        report["stale_reasons_before"] = reasons
        allow_full = bool(auto_cfg.get("allow_full_backtest", False))
        use_incremental = bool(auto_cfg.get("incremental_backtest", True))

        if not reasons and not force:
            report["backtest"] = {"status": "skipped", "message": "回测已是最新"}
        elif (not use_incremental) and allow_full:
            logger.warning("incremental_backtest=false 且 allow_full_backtest=true：执行全量回测")
            result = run_llm_backtest("llm", save=True)
            report["backtest"] = {
                "mode": "full",
                "status": "ok" if result else "failed",
                "llm_cache_stats": getattr(result, "llm_cache_stats", None) if result else None,
            }
        elif _needs_full_backtest(reasons, allow_full=allow_full) and not (latest or {}).get("signals"):
            logger.info("自动更新 Step 2: 冷启动全量回测（仅无历史信号时）")
            result = run_llm_backtest("llm", save=True)
            report["backtest"] = {
                "mode": "full",
                "status": "ok" if result else "failed",
                "llm_cache_stats": getattr(result, "llm_cache_stats", None) if result else None,
            }
        else:
            if reasons and set(reasons) & {"strategy_outdated", "model_mismatch", "freq_mismatch"}:
                logger.info(
                    "自动更新 Step 2: 增量回测（忽略全量触发 {}，禁止定时全量）",
                    reasons,
                )
            else:
                logger.info("自动更新 Step 2: 增量回测")
            report["backtest"] = run_incremental_backtest()

        if not skip_paper and auto_cfg.get("run_paper_trading", True):
            from market_platform.predictor.分析.推断.集合元模型 import list_member_providers
            from market_platform.predictor.接口.模拟盘 import run_paper_trading_cycle

            logger.info("自动更新 Step 3: 模拟实盘（全渠道）")
            by_provider = {}
            for prov in list_member_providers():
                by_provider[prov] = run_paper_trading_cycle(
                    refresh_data=False,
                    provider=prov,
                )
            statuses = {r.get("status") for r in by_provider.values()}
            report["paper_trading"] = {
                "status": "ok" if statuses <= {"ok", "skipped", "disabled"} else "error",
                "by_provider": by_provider,
            }

        report["status"] = "ok"
        report["stale_reasons_after"] = backtest_staleness_reasons(load_latest_backtest())
        report["finished_at"] = _utc_now_iso()

    except Exception as exc:
        logger.exception("自动更新失败: {}", exc)
        report["status"] = "error"
        report["error"] = str(exc)
        report["finished_at"] = _utc_now_iso()

    history = load_auto_update_state().get("history", [])
    history.append({k: v for k, v in report.items() if k != "history"})
    report["history"] = history[-30:]
    save_auto_update_state(report)
    return report


def get_auto_update_status() -> Dict[str, Any]:
    from market_platform.predictor.存储.数据访问 import load_latest_backtest_metrics

    cfg = get_config().get("auto_update", {})
    state = load_auto_update_state()
    latest = load_latest_backtest_metrics()
    reasons = backtest_staleness_reasons(latest)
    return {
        "enabled": bool(cfg.get("enabled", True)),
        "config": {
            "schedule_time": cfg.get("schedule_time", "16:45"),
            "refresh_data": bool(cfg.get("refresh_data", True)),
            "incremental_backtest": bool(cfg.get("incremental_backtest", True)),
            "allow_full_backtest": bool(cfg.get("allow_full_backtest", False)),
            "rewind_bars": int(cfg.get("rewind_bars", 126)),
            "run_paper_trading": bool(cfg.get("run_paper_trading", True)),
        },
        "needs_update": len(reasons) > 0,
        "stale_reasons": reasons,
        "data_as_of": data_as_of(),
        "backtest_data_as_of": latest.get("data_as_of") if latest else None,
        "strategy_fingerprint": strategy_fingerprint(),
        "last_run": state,
    }

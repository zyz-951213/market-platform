#!/usr/bin/env python3
"""批量补齐最新交易日日线到数据库，并刷新模拟盘。"""

from __future__ import annotations

import time
from datetime import timedelta

import pandas as pd
from market_platform.agents.settings import AppConfig, DataConfig
from market_platform.agents.核心.路径.项目 import profile_path, data_dir
from market_platform.agents.核心.任务.运行器 import update_status
from market_platform.agents.核心.任务.交易日历 import latest_expected_trading_day
from market_platform.agents.核心.任务.陈旧检测 import discover_fetch_candidates
from market_platform.agents.分析.模拟盘.引擎 import (
    load_paper_state,
    save_paper_state,
    mark_to_market,
    run_paper_trading_cycle,
)
from market_platform.agents.分析.批处理.增量 import discover_pending, run_incremental_batch
from market_platform.agents.存储 import data_access as store
from market_platform.agents.采集.股票池 import infer_market
from market_platform.agents.采集.数据源 import fetch_daily_tencent, fetch_daily_sina, expand_daily_to_minute_proxy
from market_platform.agents.分析.特征.分钟 import compute_minute_features, summarize_minute_to_daily


def _merge_by_date(old: pd.DataFrame, new: pd.DataFrame) -> pd.DataFrame:
    if old is None or old.empty:
        out = new.copy() if new is not None else pd.DataFrame()
    elif new is None or new.empty:
        out = old.copy()
    else:
        out = pd.concat([old, new], ignore_index=True)
    if out.empty or "date" not in out.columns:
        return out
    out = out.copy()
    out["date"] = pd.to_datetime(out["date"])
    out = out.drop_duplicates(subset=["date"], keep="last").sort_values("date")
    out["date"] = out["date"].dt.date
    return out


def _fetch_daily(sym: str, market: str, start: str, end: str) -> pd.DataFrame:
    cfg = DataConfig(symbol=sym, market=market, start_date=start, end_date=end)
    try:
        return fetch_daily_tencent(cfg)
    except Exception:
        return fetch_daily_sina(cfg)


def _rebuild_features(sym: str, daily: pd.DataFrame) -> None:
    minute = expand_daily_to_minute_proxy(daily, sym)
    from market_platform.agents.settings import FeatureConfig

    minute_feat = compute_minute_features(minute, FeatureConfig().minute)
    minute_daily = summarize_minute_to_daily(minute_feat)
    store.save_series(sym, store.SERIES_MINUTE_DAILY, minute_daily)


def refresh_one(sym: str, market: str, expected, *, build_features: bool = True) -> str:
    market = infer_market(sym) or market or "sz"
    existing = store.load_daily_bars(sym)
    if existing.empty:
        start = "20230101"
    else:
        existing = existing.copy()
        existing["date"] = pd.to_datetime(existing["date"]).dt.date
        last = existing["date"].max()
        if last >= expected:
            return "skipped"
        # 多回溯几天，避免短窗口数据源异常
        start = (last - timedelta(days=7)).strftime("%Y%m%d")
    end = expected.strftime("%Y%m%d")
    try:
        new_daily = _fetch_daily(sym, market, start, end)
    except Exception as exc:
        return f"error:{exc}"
    if new_daily is None or new_daily.empty:
        return "empty"
    daily = _merge_by_date(existing, new_daily)
    store.save_daily_bars(sym, daily)
    if build_features:
        try:
            _rebuild_features(sym, daily)
        except Exception as exc:
            return f"bars_ok_feat_err:{exc}"
    return "ok"


def main() -> None:
    print("=== sync start", time.strftime("%Y-%m-%d %H:%M:%S"), flush=True)
    expected = latest_expected_trading_day()
    print("expected_trading_day", expected, flush=True)
    root = data_dir()
    config = AppConfig.from_yaml(profile_path("自动更新.yaml"))

    total_ok = total_err = total_skip = 0
    batch = 0
    while True:
        batch += 1
        cands = discover_fetch_candidates(root, expected_date=expected, limit=500)
        if not cands:
            print("no more stale candidates", flush=True)
            break
        print(f"\n=== batch {batch} candidates={len(cands)} ===", flush=True)
        for i, (sym, mkt, name, _sector) in enumerate(cands, 1):
            status = refresh_one(sym, mkt, expected, build_features=True)
            if status == "ok":
                total_ok += 1
            elif status == "skipped":
                total_skip += 1
            else:
                total_err += 1
            if i % 25 == 0 or status.startswith("error") or status.startswith("bars_ok"):
                print(f"  [{i}/{len(cands)}] {sym} {name} {status} | ok={total_ok} err={total_err}", flush=True)
            time.sleep(0.15)
        st = update_status(config, rescan=True)
        print(
            f"status after batch: stale={st.get('stale')} up_to_date={st.get('up_to_date')} "
            f"analyze_pending={st.get('analyze_pending')}",
            flush=True,
        )
        # analyze a slice after each fetch batch
        pending = discover_pending(root, config.output.root, limit=200, skip_analyzed=True)
        if pending:
            print(f"analyzing {len(pending)} ...", flush=True)
            df = run_incremental_batch(config, pending, optimize=True)
            ok = int((df["status"] == "ok").sum()) if not df.empty else 0
            print(f"analyze ok={ok}/{len(pending)}", flush=True)
        if int(st.get("stale") or 0) <= 30:
            break
        if batch >= 30:
            print("hit batch limit", flush=True)
            break

    print("\n=== paper remarek ===", flush=True)
    paper_cfg = AppConfig.from_yaml(profile_path("默认.yaml"))
    state = load_paper_state(paper_cfg)
    mark_to_market(state, expected)
    save_paper_state(state)
    result = run_paper_trading_cycle(paper_cfg, notify=False)
    s = result.get("state") or {}
    print(
        "paper nav",
        round(s.get("nav", 0)),
        "positions",
        len(s.get("positions") or []),
        "picks",
        len(result.get("tomorrow_picks") or []),
        flush=True,
    )
    st = update_status(config, rescan=True)
    print("final stale", st.get("stale"), "up_to_date", st.get("up_to_date"), flush=True)
    print("totals ok", total_ok, "err", total_err, "skip", total_skip, flush=True)
    print("=== sync done", time.strftime("%Y-%m-%d %H:%M:%S"), flush=True)


if __name__ == "__main__":
    main()

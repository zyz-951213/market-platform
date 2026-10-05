#!/usr/bin/env python3
"""补齐日线到最新交易日，并重算净值/状态/参与者曲线。"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import timedelta
from pathlib import Path

import pandas as pd
from market_platform.agents.settings import AppConfig, DataConfig, FeatureConfig
from market_platform.agents.核心.路径.项目 import profile_path, data_dir
from market_platform.agents.核心.任务.交易日历 import latest_expected_trading_day
from market_platform.agents.核心.任务.陈旧检测 import discover_fetch_candidates, scan_symbol_freshness
from market_platform.agents.核心.任务.运行器 import update_status
from market_platform.agents.分析.批处理.增量 import run_symbol_analysis_from_cache, append_universe_summary, _batch_row_to_record
from market_platform.agents.分析.模拟盘.引擎 import load_paper_state, save_paper_state, mark_to_market, run_paper_trading_cycle
from market_platform.agents.分析.特征.分钟 import compute_minute_features, summarize_minute_to_daily
from market_platform.agents.存储 import data_access as store
from market_platform.agents.采集.股票池 import infer_market
from market_platform.agents.采集.数据源 import fetch_daily_tencent, fetch_daily_sina, expand_daily_to_minute_proxy


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
        start = (last - timedelta(days=7)).strftime("%Y%m%d")
    end = expected.strftime("%Y%m%d")
    try:
        new_daily = _fetch_daily(sym, market, start, end)
    except Exception as exc:
        return f"error:{type(exc).__name__}"
    from market_platform.agents.核心.任务.陈旧检测 import sync_suspension_after_fetch

    if new_daily is None or new_daily.empty:
        latest = store.latest_bar_date(sym)
        sync_suspension_after_fetch(sym, expected_date=expected, latest=latest)
        return "empty"
    daily = _merge_by_date(existing, new_daily)
    store.save_daily_bars(sym, daily)
    latest = None
    if not daily.empty and "date" in daily.columns:
        latest = pd.to_datetime(daily["date"]).max().date()
    halt = sync_suspension_after_fetch(sym, expected_date=expected, latest=latest)
    if build_features:
        try:
            _rebuild_features(sym, daily)
        except Exception as exc:
            return f"bars_ok_feat_err:{type(exc).__name__}"
    if halt == "suspended":
        return "suspended"
    return "ok"


def curve_lag_symbols(expected) -> list[tuple[str, str, str, str]]:
    """日线已到 expected，但净值/状态曲线仍落后的标的。"""
    rows = scan_symbol_freshness(data_dir(), expected_date=expected)
    out: list[tuple[str, str, str, str]] = []
    for r in rows:
        if r.is_stale:
            continue
        eq = store.load_equity_curve(r.symbol)
        st = store.load_market_states(r.symbol)
        need = False
        if eq.empty or st.empty:
            need = True
        else:
            eq_last = pd.to_datetime(eq["date"]).max().date()
            st_last = pd.to_datetime(st["date"]).max().date()
            if eq_last < expected or st_last < expected:
                need = True
        if need:
            out.append((r.symbol, r.market or infer_market(r.symbol), r.name or "", r.sector or ""))
    return out


def analyze_one(item: tuple[str, str, str, str], config: AppConfig) -> dict:
    sym, market, name, sector = item
    from market_platform.agents.分析.批处理.增量 import _config_for_symbol

    cfg = _config_for_symbol(config, sym, market, name, sector)
    try:
        row = run_symbol_analysis_from_cache(cfg, optimize=True)
        rec = _batch_row_to_record(row, name=name, sector=sector)
        rec["path"] = f"{sector}/{name}" if name else sym
        return {"status": "ok", "rec": rec, "symbol": sym}
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "symbol": sym, "error": str(exc)}


def main() -> None:
    print("=== curve sync start", time.strftime("%Y-%m-%d %H:%M:%S"), flush=True)
    expected = latest_expected_trading_day()
    print("expected_trading_day", expected, flush=True)
    root = data_dir()
    config = AppConfig.from_yaml(profile_path("默认.yaml"))
    status_cfg = AppConfig.from_yaml(profile_path("自动更新.yaml"))

    fetch_workers = 8
    analyze_workers = 4
    fetch_ok = fetch_err = 0
    analyze_ok = analyze_err = 0

    # ---- Phase A: parallel fetch remaining stale bars ----
    while True:
        cands = discover_fetch_candidates(root, expected_date=expected, limit=800)
        if not cands:
            print("fetch: no more stale", flush=True)
            break
        print(f"\n=== fetch batch candidates={len(cands)} workers={fetch_workers} ===", flush=True)
        with ThreadPoolExecutor(max_workers=fetch_workers) as pool:
            futs = {
                pool.submit(refresh_one, sym, mkt, expected, build_features=True): (sym, name)
                for sym, mkt, name, _sector in cands
            }
            done = 0
            for fut in as_completed(futs):
                sym, name = futs[fut]
                done += 1
                try:
                    status = fut.result()
                except Exception as exc:  # noqa: BLE001
                    status = f"error:{exc}"
                if status == "ok":
                    fetch_ok += 1
                elif status != "skipped":
                    fetch_err += 1
                if done % 50 == 0 or status.startswith("error"):
                    print(
                        f"  fetch [{done}/{len(cands)}] {sym} {name} {status} | ok={fetch_ok} err={fetch_err}",
                        flush=True,
                    )
        # 日线已写入 Postgres；重扫并落库状态，网站导航栏即可看到真实待更新数
        st = update_status(status_cfg, rescan=True)
        print(
            f"bars status(synced to DB/site): stale={st.get('stale')} up_to_date={st.get('up_to_date')}",
            flush=True,
        )

        # interleaved: re-analyze a slice of lagging curves so site updates sooner
        lag = curve_lag_symbols(expected)
        if lag:
            chunk = lag[:200]
            print(f"=== interleaved analyze {len(chunk)}/{len(lag)} lagging curves ===", flush=True)
            results: list[dict] = []
            with ThreadPoolExecutor(max_workers=analyze_workers) as pool:
                futs = {pool.submit(analyze_one, item, config): item[0] for item in chunk}
                done = 0
                for fut in as_completed(futs):
                    done += 1
                    out = fut.result()
                    if out["status"] == "ok":
                        analyze_ok += 1
                        results.append(out["rec"])
                    else:
                        analyze_err += 1
                    if done % 25 == 0 or out["status"] != "ok":
                        print(
                            f"  analyze [{done}/{len(chunk)}] {out.get('symbol')} {out['status']} "
                            f"| ok={analyze_ok} err={analyze_err}",
                            flush=True,
                        )
            if results:
                # 汇总表写入数据库，总览/模拟盘评分立刻可读
                path = append_universe_summary(results, config.output.root)
                print(f"  universe summary synced ({len(results)} rows) -> {path}", flush=True)
            update_status(status_cfg, rescan=True)

        if int(st.get("stale") or 0) <= 20:
            break

    # ---- Phase B: finish all lagging curves ----
    while True:
        lag = curve_lag_symbols(expected)
        if not lag:
            print("curves: all up to date", flush=True)
            break
        chunk = lag[:400]
        print(f"\n=== finalize analyze {len(chunk)}/{len(lag)} ===", flush=True)
        results = []
        with ThreadPoolExecutor(max_workers=analyze_workers) as pool:
            futs = {pool.submit(analyze_one, item, config): item[0] for item in chunk}
            done = 0
            for fut in as_completed(futs):
                done += 1
                out = fut.result()
                if out["status"] == "ok":
                    analyze_ok += 1
                    results.append(out["rec"])
                else:
                    analyze_err += 1
                if done % 40 == 0:
                    print(
                        f"  analyze [{done}/{len(chunk)}] ok={analyze_ok} err={analyze_err}",
                        flush=True,
                    )
        if results:
            path = append_universe_summary(results, config.output.root)
            print(f"  universe summary synced ({len(results)} rows) -> {path}", flush=True)
            update_status(status_cfg, rescan=True)
        if len(lag) <= len(chunk):
            # one more scan
            lag2 = curve_lag_symbols(expected)
            if len(lag2) >= len(lag) - 5 and analyze_err > 0:
                print("remaining lag mostly errors, stop", len(lag2), flush=True)
                break

    print("\n=== paper remarek + site status ===", flush=True)
    paper_cfg = AppConfig.from_yaml(profile_path("默认.yaml"))
    state = load_paper_state(paper_cfg)
    mark_to_market(state, expected)
    save_paper_state(state)
    result = run_paper_trading_cycle(paper_cfg, notify=False)
    s = result.get("state") or {}
    print("paper nav", round(s.get("nav", 0)), "positions", len(s.get("positions") or []), flush=True)

    st = update_status(status_cfg, rescan=True)
    lag = curve_lag_symbols(expected)
    print(
        "final bars stale",
        st.get("stale"),
        "up_to_date",
        st.get("up_to_date"),
        "curves_lag",
        len(lag),
        flush=True,
    )
    print(
        "totals fetch_ok",
        fetch_ok,
        "fetch_err",
        fetch_err,
        "analyze_ok",
        analyze_ok,
        "analyze_err",
        analyze_err,
        flush=True,
    )
    print("=== curve sync done", time.strftime("%Y-%m-%d %H:%M:%S"), flush=True)


if __name__ == "__main__":
    main()

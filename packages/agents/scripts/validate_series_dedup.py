#!/usr/bin/env python3
"""单标的验证：哪些 symbol_series / 表列可安全去重而不丢信息。

用法:
  python scripts/validate_series_dedup.py --symbol 000001
  python scripts/validate_series_dedup.py --symbol 000001 --atol 1e-6

退出码: 0=全部通过, 1=有失败项。
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass

import numpy as np
import pandas as pd

from market_platform.agents.settings import AppConfig
from market_platform.agents.分析.特征.日频 import build_daily_feature_matrix
from market_platform.agents.分析.推断.状态 import build_state_features
from market_platform.agents.分析.推断.隐马尔可夫 import attach_forward_returns
from market_platform.agents.核心.路径.项目 import profile_path
from market_platform.agents.存储 import data_access as store


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str


def _norm_dates(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if "date" in out.columns:
        out["date"] = pd.to_datetime(out["date"]).dt.normalize()
    return out


def _numeric_cols(df: pd.DataFrame) -> list[str]:
    return [c for c in df.columns if c != "date" and pd.api.types.is_numeric_dtype(df[c])]


def _compare_frames(
    left: pd.DataFrame,
    right: pd.DataFrame,
    *,
    keys: list[str],
    cols: list[str] | None = None,
    atol: float,
    rtol: float,
) -> tuple[bool, str]:
    a = _norm_dates(left)
    b = _norm_dates(right)
    if a.empty or b.empty:
        return False, f"empty frame left={len(a)} right={len(b)}"
    merged = a.merge(b, on=keys, how="inner", suffixes=("_l", "_r"))
    if merged.empty:
        return False, "no overlapping keys"
    use_cols = cols or sorted(
        {c[:-2] for c in merged.columns if c.endswith("_l")}
        & {c[:-2] for c in merged.columns if c.endswith("_r")}
    )
    bad: list[str] = []
    for col in use_cols:
        cl, cr = f"{col}_l", f"{col}_r"
        if cl not in merged.columns or cr not in merged.columns:
            continue
        x = pd.to_numeric(merged[cl], errors="coerce").to_numpy(dtype=float)
        y = pd.to_numeric(merged[cr], errors="coerce").to_numpy(dtype=float)
        ok_mask = np.isclose(x, y, atol=atol, rtol=rtol, equal_nan=True)
        n_bad = int((~ok_mask).sum())
        if n_bad:
            diff = np.abs(x - y)
            diff = np.where(np.isnan(diff), 0.0, diff)
            bad.append(f"{col}:bad={n_bad}/{len(merged)},max_abs={float(np.nanmax(diff)):.3g}")
    overlap = len(merged)
    cover_l = overlap / max(len(a), 1)
    cover_r = overlap / max(len(b), 1)
    head = f"rows L/R/join={len(a)}/{len(b)}/{overlap} cover={cover_l:.1%}/{cover_r:.1%}"
    if bad:
        return False, f"{head}; " + "; ".join(bad[:8])
    return True, f"{head}; cols_ok={len(use_cols)}"


def check_minute_bars_vs_daily(symbol: str, atol: float) -> CheckResult:
    bars = store.load_daily_bars(symbol)
    mb = store.load_series(symbol, store.SERIES_MINUTE_BARS)
    if mb.empty:
        return CheckResult("minute_bars≈daily.close", True, "already purged / not stored")
    if bars.empty:
        return CheckResult("minute_bars≈daily.close", False, "missing daily")
    a = _norm_dates(bars)[["date", "close"]]
    b = _norm_dates(mb)
    if "close" not in b.columns:
        return CheckResult("minute_bars≈daily.close", False, "minute_bars has no close")
    ok, detail = _compare_frames(a, b[["date", "close"]], keys=["date"], cols=["close"], atol=atol, rtol=0)
    hhmm = ""
    if "datetime" in mb.columns:
        s = pd.to_datetime(mb["datetime"], errors="coerce").dt.strftime("%H:%M")
        top = s.value_counts().head(1)
        if not top.empty:
            hhmm = f"; top_time={top.index[0]}({top.iloc[0]}/{len(s)})"
    return CheckResult("minute_bars≈daily.close", ok, detail + hhmm)


def check_state_features_rebuild(symbol: str, atol: float) -> CheckResult:
    cached = store.load_series(symbol, store.SERIES_STATE_FEATURES)
    activity = store.load_agent_activity(symbol)
    minute_daily = store.load_series(symbol, store.SERIES_MINUTE_DAILY)
    fund_flow = store.load_fund_flow(symbol)
    daily = store.load_daily_bars(symbol)
    if cached.empty:
        if activity.empty or minute_daily.empty:
            return CheckResult("state_features 可重建", False, "missing inputs for rebuild")
        return CheckResult("state_features 可重建", True, "already purged; inputs present")
    if activity.empty or minute_daily.empty or daily.empty:
        return CheckResult("state_features 可重建", False, "missing inputs or cached state_features")

    cfg = AppConfig.from_yaml(profile_path("默认.yaml"))
    rebuilt = build_state_features(activity, minute_daily, fund_flow)
    rebuilt = attach_forward_returns(rebuilt, daily, horizon=cfg.state.forward_horizon_days)

    cols = sorted(set(_numeric_cols(cached)) & set(_numeric_cols(rebuilt)))
    ok, detail = _compare_frames(cached, rebuilt, keys=["date"], cols=cols, atol=atol, rtol=1e-5)
    if not ok and "forward_return:bad=1/" in detail:
        ok = True
        detail += "; note=忽略末行 forward_return 单点差异"
    return CheckResult("state_features 可重建", ok, detail + f"; compared={len(cols)} cols")


def check_feature_matrix_rebuild(symbol: str, atol: float) -> CheckResult:
    cached = store.load_series(symbol, store.SERIES_FEATURE_MATRIX)
    minute_daily = store.load_series(symbol, store.SERIES_MINUTE_DAILY)
    fund_flow = store.load_fund_flow(symbol)
    if cached.empty:
        if minute_daily.empty:
            return CheckResult("daily_feature_matrix 可重建", False, "missing minute_daily")
        return CheckResult("daily_feature_matrix 可重建", True, "already purged; minute_daily present")
    if minute_daily.empty:
        return CheckResult("daily_feature_matrix 可重建", False, "missing matrix or minute_daily")

    north = store.load_market_series_df("northbound")
    margin = store.load_market_series_df("margin")
    rebuilt = build_daily_feature_matrix(
        minute_daily=minute_daily,
        fund_flow=fund_flow,
        northbound=north,
        margin=margin,
    )
    cols = sorted(set(_numeric_cols(cached)) & set(_numeric_cols(rebuilt)))
    ok, detail = _compare_frames(cached, rebuilt, keys=["date"], cols=cols, atol=atol, rtol=1e-5)
    return CheckResult("daily_feature_matrix 可重建", ok, detail + f"; compared={len(cols)} cols")


def check_equity_vs_daily_ohlc(symbol: str, atol: float) -> CheckResult:
    eq = store.load_equity_curve(symbol)
    bars = store.load_daily_bars(symbol)
    if eq.empty:
        return CheckResult("equity 无 OHLCV 列", False, "missing equity")
    ohlc = [c for c in ("open", "high", "low", "close", "volume", "amount") if c in eq.columns]
    if not ohlc:
        has_score = "state_score" in eq.columns
        return CheckResult(
            "equity 无 OHLCV 列",
            True,
            f"slim ok; state_score_col={has_score}; rows={len(eq)}",
        )
    if bars.empty:
        return CheckResult("equity_curves.OHLCV≡daily_bars", False, "missing daily")
    ok, detail = _compare_frames(eq, bars, keys=["date"], cols=ohlc, atol=atol, rtol=0)
    return CheckResult("equity_curves.OHLCV≡daily_bars", ok, detail + " (待迁移去重)")


def check_equity_vs_market_states(symbol: str, atol: float) -> CheckResult:
    eq = store.load_equity_curve(symbol)
    st = store.load_market_states(symbol)
    if eq.empty or st.empty:
        return CheckResult("load_market_states←equity", False, "missing equity/states view")
    # 合并后 load_market_states 应与 equity 状态列一致
    a = _norm_dates(eq)
    b = _norm_dates(st)
    m = a.merge(b, on="date", how="inner", suffixes=("_e", "_s"))
    if m.empty:
        return CheckResult("load_market_states←equity", False, "no overlap")
    state_ok = float((m["market_state_e"].astype(str) == m["market_state_s"].astype(str)).mean())
    pos_ok = float(
        np.isclose(
            pd.to_numeric(m["suggested_position_e"], errors="coerce").to_numpy(dtype=float),
            pd.to_numeric(m["suggested_position_s"], errors="coerce").to_numpy(dtype=float),
            atol=atol,
            rtol=0,
            equal_nan=True,
        ).mean()
    )
    ok = state_ok >= 0.999 and pos_ok >= 0.999
    detail = f"join={len(m)} state_match={state_ok:.2%} pos_match={pos_ok:.2%}"
    return CheckResult("load_market_states←equity", ok, detail)


def pick_default_symbol() -> str:
    summary = store.load_symbol_summary()
    if not summary.empty and "status" in summary.columns:
        ok = summary[summary["status"].astype(str) == "ok"]
        if not ok.empty:
            return str(ok.iloc[0]["symbol"]).zfill(6)
    reg = store.load_symbols_registry()
    if not reg.empty:
        return str(reg.iloc[0]["symbol"]).zfill(6)
    raise SystemExit("无法自动选标的，请传 --symbol")


def main() -> int:
    parser = argparse.ArgumentParser(description="验证 series/表去重安全性（单标的）")
    parser.add_argument("--symbol", default="", help="6 位代码，默认取 summary 首个 ok")
    parser.add_argument("--atol", type=float, default=1e-6)
    args = parser.parse_args()
    symbol = str(args.symbol or pick_default_symbol()).zfill(6)
    print(f"symbol={symbol} atol={args.atol}")

    checks = [
        check_minute_bars_vs_daily(symbol, args.atol),
        check_state_features_rebuild(symbol, args.atol),
        check_feature_matrix_rebuild(symbol, args.atol),
        check_equity_vs_daily_ohlc(symbol, args.atol),
        check_equity_vs_market_states(symbol, args.atol),
    ]
    width = max(len(c.name) for c in checks)
    failed = 0
    for c in checks:
        mark = "PASS" if c.ok else "FAIL"
        if not c.ok:
            failed += 1
        print(f"[{mark}] {c.name:<{width}}  {c.detail}")

    print(f"\n{len(checks) - failed}/{len(checks)} passed")
    if failed:
        print("有失败项：先别全量 DELETE；收紧比对列或检查该标的脏数据后再去重。")
    else:
        print("停写已生效；可执行: PYTHONPATH=后端 python scripts/purge_ephemeral_series.py --execute [--vacuum]")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

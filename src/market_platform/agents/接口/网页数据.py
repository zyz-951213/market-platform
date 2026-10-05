from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from market_platform.agents.settings import AppConfig
from market_platform.agents.分析.信号.每日 import classify_signal_action
from market_platform.agents.分析.回测.风控 import latest_risk_snapshot
from market_platform.agents.分析.批处理.增量 import load_universe_meta
from market_platform.agents.分析.推断.智能体角色 import (
    display_agent_name,
    get_agent_columns,
)
from market_platform.agents.分析.特征.日频 import format_top_features_text
from market_platform.agents.存储 import data_access as store
from market_platform.agents.核心.路径.层级 import CLASSIFICATION_STANDARD, normalize_sector_hierarchy_df
from market_platform.agents.核心.路径.项目 import data_dir, output_dir
from market_platform.agents.采集.行业.行业层级 import (
    build_sector_tree,
    ensure_sector_hierarchy,
    load_effective_hierarchy,
)


def _default_data_root() -> Path:
    return data_dir()


def _fmt_date_series(dates) -> list[str]:
    return [str(pd.Timestamp(d).date()) for d in dates]


def _pct(v: float | None) -> float | None:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    return float(v)


def _json_safe(value):
    """把 pandas/numpy 的 NaN、Inf、Timestamp 等转成 JSON 可序列化值。"""
    if value is None:
        return None
    if isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if pd.isna(value) or value == float("inf") or value == float("-inf"):
            return None
        return value
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    # numpy 标量
    item = getattr(value, "item", None)
    if callable(item):
        try:
            return _json_safe(item())
        except Exception:
            pass
    return value


def _sanitize_records(df: pd.DataFrame) -> list[dict]:
    if df.empty:
        return []
    # float 列无法真正存 None，to_dict 后仍可能是 nan；逐字段清洗
    return [
        {k: _json_safe(v) for k, v in row.items()}
        for row in df.to_dict(orient="records")
    ]


def get_health() -> dict:
    return {"status": "ok", "service": "market-agents"}


_BATCH_SLIM_COLUMNS = (
    "symbol",
    "sector",
    "sector_l1",
    "sector_l2",
    "sector_l3",
    "name",
    "latest_state",
    "strategy_return",
    "benchmark_return",
    "excess_return",
    "oos_excess_return",
    "oos_strategy_return",
    "walk_forward_ratio",
    "suggested_position",
)


def _load_summary_df() -> pd.DataFrame:
    from market_platform.agents.采集.名称 import fill_dataframe_names

    df = store.load_symbol_summary()
    # 本地多源补名；不在每次请求打远端，避免拖慢列表接口
    return fill_dataframe_names(df, fetch_missing=False, persist=False)


def _merge_hierarchy(df: pd.DataFrame, data_root: Path) -> pd.DataFrame:
    if df.empty:
        return df
    out = normalize_sector_hierarchy_df(df)
    hier = load_effective_hierarchy(data_root)
    if hier.empty:
        return out
    merged = out.merge(
        hier[["symbol", "sector_l1", "sector_l2", "sector_l3"]],
        on="symbol",
        how="left",
        suffixes=("", "_hier"),
    )

    def _meaningful(series: pd.Series) -> pd.Series:
        text = series.astype(str).str.strip()
        return text.ne("") & text.ne("未分类") & text.ne("综合")

    for col in ("sector_l1", "sector_l2", "sector_l3"):
        hier_col = f"{col}_hier"
        if hier_col not in merged.columns:
            continue
        mask = _meaningful(merged[hier_col])
        if mask.any():
            merged.loc[mask, col] = merged.loc[mask, hier_col]
    drop_cols = [c for c in merged.columns if c.endswith("_hier")]
    return normalize_sector_hierarchy_df(merged.drop(columns=drop_cols, errors="ignore"))


def _filter_summary_df(
    df: pd.DataFrame,
    *,
    sector: str = "",
    sector_l1: str = "",
    sector_l2: str = "",
    sector_l3: str = "",
    q: str = "",
    sort_by: str = "symbol",
    sort_order: str = "asc",
    data_root: Path | None = None,
) -> pd.DataFrame:
    if df.empty:
        return df
    out = df.copy()
    if data_root is not None:
        out = _merge_hierarchy(out, data_root)
    if "status" in out.columns:
        out = out[out["status"] == "ok"]
    l1 = sector_l1 or sector
    if l1:
        col = "sector_l1" if "sector_l1" in out.columns else "sector"
        out = out[out[col].astype(str) == l1]
    if sector_l2:
        col = "sector_l2" if "sector_l2" in out.columns else "sector"
        out = out[out[col].astype(str) == sector_l2]
    if sector_l3:
        col = "sector_l3" if "sector_l3" in out.columns else "sector_l2"
        out = out[out[col].astype(str) == sector_l3]
    if q:
        needle = q.strip().lower()
        if needle:
            mask = (
                out["symbol"].astype(str).str.contains(needle, case=False, na=False)
                | out.get("name", pd.Series(dtype=str)).astype(str).str.contains(needle, case=False, na=False)
            )
            out = out[mask]
    sort_col = sort_by if sort_by in out.columns else "symbol"
    if sort_col in out.columns:
        ascending = str(sort_order).lower() == "asc"
        out = out.sort_values(sort_col, ascending=ascending, na_position="last")
    return out.reset_index(drop=True)


def _symbol_meta(sym: str) -> dict[str, str]:
    from market_platform.agents.采集.名称 import build_symbol_name_map

    summary = store.load_symbol_summary()
    if summary.empty:
        name_map = build_symbol_name_map([sym], fetch_missing=True, persist=True)
        return {"sector": "", "name": name_map.get(sym, ""), "path": ""}
    hit = summary[summary["symbol"].astype(str).str.zfill(6) == sym]
    if hit.empty:
        name_map = build_symbol_name_map([sym], fetch_missing=True, persist=True)
        return {"sector": "", "name": name_map.get(sym, ""), "path": ""}
    row = hit.iloc[0]
    name = str(row.get("name", "") or "").strip()
    if not name:
        name = build_symbol_name_map([sym], fetch_missing=True, persist=True).get(sym, "")
    return {
        "sector": str(row.get("sector", "")),
        "name": name,
        "path": str(row.get("path", "")),
    }


def get_sector_tree(
    output_root: Path | None = None,
    data_root: Path | None = None,
) -> dict:
    data_root = Path(data_root or _default_data_root())
    summary = _filter_summary_df(_load_summary_df(), data_root=data_root)
    if summary.empty:
        return {"tree": [], "analyzed_count": 0}

    ok_symbols = sorted(summary["symbol"].astype(str).str.zfill(6).unique().tolist())
    ensure_sector_hierarchy(data_root, ok_symbols)

    hier = load_effective_hierarchy(data_root)
    hier = hier[hier["symbol"].astype(str).str.zfill(6).isin(set(ok_symbols))].copy()
    if "name" not in hier.columns or hier["name"].fillna("").astype(str).str.strip().eq("").all():
        hier = hier.drop(columns=["name"], errors="ignore")
    hier = hier.merge(summary[["symbol", "name"]], on="symbol", how="left", suffixes=("", "_sum"))
    blank = hier["name"].fillna("").astype(str).str.strip().eq("") if "name" in hier.columns else pd.Series(True, index=hier.index)
    if "name_sum" in hier.columns and blank.any():
        hier.loc[blank, "name"] = hier.loc[blank, "name_sum"]
    hier = hier.drop(columns=[c for c in hier.columns if c.endswith("_sum")], errors="ignore")

    tree = build_sector_tree(hier)
    return {"tree": tree, "analyzed_count": len(ok_symbols)}


def get_classification(data_root: Path | None = None) -> dict:
    data_root = Path(data_root or _default_data_root())
    hier = load_effective_hierarchy(data_root)
    if hier.empty:
        summary = _load_summary_df()
        if not summary.empty and "sector" in summary.columns:
            cols = [c for c in ("symbol", "sector", "sector_l1", "sector_l2", "sector_l3") if c in summary.columns]
            hier = normalize_sector_hierarchy_df(summary[cols].copy())
    meta_path = data_root / "classification_standard.json"
    standard = CLASSIFICATION_STANDARD.copy()
    if meta_path.exists():
        try:
            standard = json.loads(meta_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {
        "standard": standard,
        "l1_count": int(hier["sector_l1"].nunique()) if not hier.empty else 0,
        "l2_count": int(hier["sector_l2"].nunique()) if not hier.empty else 0,
        "l3_count": int(hier["sector_l3"].nunique()) if not hier.empty and "sector_l3" in hier.columns else 0,
        "symbol_count": len(hier),
        "path_pattern": standard.get("path_pattern", "{sector_l1}/{sector_l2}/{name}/"),
    }


def get_overview(
    output_root: Path | None = None,
    data_root: Path | None = None,
) -> dict:
    import time

    global _OVERVIEW_CACHE
    now = time.monotonic()
    cached = _OVERVIEW_CACHE.get("payload")
    ts = float(_OVERVIEW_CACHE.get("ts") or 0.0)
    if isinstance(cached, dict) and now - ts < _OVERVIEW_CACHE_TTL_SEC:
        return cached

    data_root = Path(data_root or _default_data_root())

    batch = _load_summary_df()
    industry = store.load_industry_summary()
    universe = load_universe_meta(data_root) if (data_root / "universe.csv").exists() else store.load_symbols_registry()
    progress_payload = store.load_app_state("fetch_progress") or []
    progress = pd.DataFrame(progress_payload)
    data_index = store.load_symbols_registry()

    if not batch.empty and "status" in batch.columns:
        analyzed_count = int((batch["status"] == "ok").sum())
    else:
        analyzed_count = len(batch)

    ok_fetch = int((progress["status"] == "ok").sum()) if not progress.empty and "status" in progress.columns else 0
    skip_fetch = int((progress["status"] == "skipped").sum()) if not progress.empty and "status" in progress.columns else 0

    result = {
        "analyzed_count": analyzed_count,
        "data_symbol_count": len(data_index) if not data_index.empty else len(universe),
        "universe_count": len(universe),
        "fetch_ok": ok_fetch,
        "fetch_skipped": skip_fetch,
        "batch_rows": len(batch),
        "industry_rows": len(industry),
        "data_root": str(data_root),
        "has_batch_summary": not batch.empty,
        "has_industry_summary": not industry.empty,
        "classification": get_classification(data_root),
        "update": get_update_status(data_root=data_root),
    }
    _OVERVIEW_CACHE = {"ts": now, "payload": result}
    return result


def get_update_status(
    output_root: Path | None = None,
    data_root: Path | None = None,
    *,
    rescan: bool = False,
) -> dict:
    from dataclasses import replace

    from market_platform.agents.settings import DataConfig, OutputConfig
    from market_platform.agents.核心.任务.调度器 import (
        auto_update_enabled,
        get_auto_update_scheduler,
        load_auto_update_settings,
    )
    from market_platform.agents.核心.任务.运行器 import update_status

    data_root = Path(data_root or _default_data_root())
    config = AppConfig(
        data=replace(DataConfig(), root=data_root),
        output=replace(OutputConfig(), root=Path(output_root or output_dir())),
    )
    settings = load_auto_update_settings()
    status = update_status(config, rescan=rescan)
    scheduler = get_auto_update_scheduler()
    status["scheduler_enabled"] = auto_update_enabled(settings)
    status["scheduler_active"] = scheduler.is_active
    if scheduler.is_running:
        status["is_running"] = True
    try:
        from market_platform.agents.分析.模拟盘.引擎 import load_paper_state

        paper_state = load_paper_state(config)
        status["paper_updated_at"] = paper_state.get("updated_at")
        status["paper_last_trade_date"] = paper_state.get("last_trade_date")
    except Exception:  # noqa: BLE001
        status.setdefault("paper_updated_at", None)
        status.setdefault("paper_last_trade_date", None)
    return status


def list_symbols(
    output_root: Path | None = None,
    data_root: Path | None = None,
    *,
    include_data_only: bool = False,
    q: str = "",
    sector: str = "",
    sector_l1: str = "",
    sector_l2: str = "",
    sector_l3: str = "",
    limit: int = 20,
    all_symbols: bool = False,
) -> list[dict]:
    data_root = Path(data_root or _default_data_root())
    df = _filter_summary_df(
        _load_summary_df(),
        sector=sector,
        sector_l1=sector_l1,
        sector_l2=sector_l2,
        sector_l3=sector_l3,
        q=q,
        sort_by="symbol",
        data_root=data_root,
    )

    if all_symbols:
        page = df
    else:
        limit = max(1, min(int(limit), 200))
        page = df.head(limit)

    entries = [
        {
            "symbol": str(row["symbol"]).zfill(6),
            "sector": str(row.get("sector", "")),
            "name": str(row.get("name", "")),
            "has_analysis": True,
            "output_path": str(row.get("path", "")),
        }
        for _, row in page.iterrows()
    ]

    if include_data_only:
        data_index = store.load_symbols_registry()
        known = {e["symbol"] for e in entries}
        for _, row in data_index.iterrows():
            sym = str(row["symbol"]).zfill(6)
            if sym in known:
                continue
            entries.append(
                {
                    "symbol": sym,
                    "sector": str(row.get("sector", "")),
                    "name": str(row.get("name", "")),
                    "has_analysis": False,
                    "has_data": True,
                    "output_path": "",
                }
            )
    return sorted(entries, key=lambda x: x["symbol"])


def get_symbol_summary(symbol: str, output_root: Path | None = None) -> dict:
    from market_platform.agents.核心.任务.陈旧检测 import is_marked_suspended
    from market_platform.agents.核心.任务.交易日历 import latest_expected_trading_day

    sym = str(symbol).zfill(6)
    states = store.load_market_states(sym)
    if states.empty:
        raise FileNotFoundError(f"未找到标的 {sym} 的分析输出")

    latest = states.iloc[-1]
    risk_snap = latest_risk_snapshot(states)
    bar_date = store.latest_bar_date(sym)
    expected = latest_expected_trading_day()
    suspended = is_marked_suspended(sym, expected_date=expected, latest=bar_date)
    metrics: dict[str, float | str | int | None | bool] = {
        "symbol": sym,
        "latest_state": str(latest.get("market_state", "")),
        "suggested_position": _pct(latest.get("suggested_position")),
        "raw_suggested_position": _pct(risk_snap.get("raw_suggested_position")),
        "risk_note": risk_snap.get("risk_note") or "",
        "risk_adjusted": bool(risk_snap.get("risk_adjusted")),
        "trading_days": len(states),
        "latest_bar_date": bar_date.isoformat() if bar_date else None,
        "is_suspended": suspended,
    }

    eq = store.load_equity_curve(sym)
    if not eq.empty and len(eq) > 1:
        metrics["strategy_return"] = _pct(eq["strategy_equity"].iloc[-1] / eq["strategy_equity"].iloc[0] - 1)
        metrics["benchmark_return"] = _pct(eq["benchmark_equity"].iloc[-1] / eq["benchmark_equity"].iloc[0] - 1)
        metrics["excess_return"] = _pct(
            float(metrics["strategy_return"] or 0) - float(metrics["benchmark_return"] or 0)
        )

    opt = store.load_optimized_positions(sym)
    if opt:
        metrics["position_map"] = opt.get("position_map", {})
        if opt.get("walk_forward_ratio") is not None:
            metrics["walk_forward_ratio"] = float(opt["walk_forward_ratio"])
        if opt.get("metrics_scope"):
            metrics["metrics_scope"] = opt["metrics_scope"]
        oos = opt.get("oos_metrics") or {}
        if oos:
            metrics["oos_strategy_return"] = _pct(oos.get("strategy_total_return"))
            metrics["oos_benchmark_return"] = _pct(oos.get("benchmark_total_return"))
            metrics["oos_excess_return"] = _pct(oos.get("excess_return"))
            metrics["oos_strategy_sharpe"] = float(oos.get("strategy_sharpe", 0) or 0)

    agent_names = store.load_agent_names(sym)
    if agent_names:
        metrics["agent_names"] = agent_names

    meta = _symbol_meta(sym)
    if meta:
        metrics["sector"] = meta.get("sector", "")
        metrics["name"] = meta.get("name", "")
        if meta.get("path"):
            metrics["rel_path"] = meta["path"]

    hier = load_effective_hierarchy(_default_data_root())
    if not hier.empty:
        row = hier[hier["symbol"].astype(str).str.zfill(6) == sym]
        if not row.empty:
            metrics["sector_l1"] = str(row.iloc[0].get("sector_l1", "") or metrics.get("sector", ""))
            metrics["sector_l2"] = str(row.iloc[0].get("sector_l2", "") or metrics["sector_l1"])
            metrics["sector_l3"] = str(row.iloc[0].get("sector_l3", "") or metrics["sector_l2"])
        elif metrics.get("sector"):
            metrics["sector_l1"] = metrics["sector"]
            metrics["sector_l2"] = metrics["sector"]
            metrics["sector_l3"] = metrics["sector"]

    return metrics


def get_equity_chart(symbol: str, output_root: Path | None = None) -> dict:
    sym = str(symbol).zfill(6)
    eq = store.load_equity_curve(sym)
    if eq.empty:
        raise FileNotFoundError(f"标的 {symbol} 无回测净值")

    eq = eq.sort_values("date")
    dates = _fmt_date_series(eq["date"])
    strat = eq["strategy_equity"].astype(float).replace([np.inf, -np.inf], np.nan)
    bench = eq["benchmark_equity"].astype(float).replace([np.inf, -np.inf], np.nan)
    # 脏价导致净值断档时，向前填充便于前端连线；首点仍空则置基准起点
    strat = strat.ffill()
    bench = bench.ffill()
    s0 = float(strat.iloc[0]) if len(strat) and pd.notna(strat.iloc[0]) and float(strat.iloc[0]) != 0 else 1.0
    b0 = float(bench.iloc[0]) if len(bench) and pd.notna(bench.iloc[0]) and float(bench.iloc[0]) != 0 else 1.0
    strat_norm = strat / s0
    bench_norm = bench / b0
    with np.errstate(divide="ignore", invalid="ignore"):
        excess_norm = strat_norm / bench_norm.replace(0.0, np.nan)
    excess_norm = excess_norm.replace([np.inf, -np.inf], np.nan)
    if "position" in eq.columns:
        position = eq["position"].astype(float)
    else:
        position = eq.get("suggested_position", pd.Series([0.5] * len(eq))).astype(float)

    def _series(vals) -> list[float | None]:
        return [_json_safe(float(v) if pd.notna(v) else None) for v in vals]

    return {
        "dates": dates,
        "strategy": _series(strat_norm),
        "benchmark": _series(bench_norm),
        "excess": _series(excess_norm),
        "position": _series(position),
        "strategy_abs": _series(strat),
        "benchmark_abs": _series(bench),
    }


def get_states_chart(symbol: str, output_root: Path | None = None) -> dict:
    sym = str(symbol).zfill(6)
    states = store.load_market_states(sym)
    if states.empty:
        raise FileNotFoundError(f"标的 {symbol} 无状态数据")

    state_order = ["恐慌", "震荡偏弱", "横盘", "震荡偏强", "狂热"]
    dates = _fmt_date_series(states["date"])
    return {
        "dates": dates,
        "states": [str(s) for s in states["market_state"]],
        "positions": [float(v) for v in states["suggested_position"]],
        "state_order": state_order,
        "state_values": [state_order.index(s) if s in state_order else 2 for s in states["market_state"]],
    }


def get_agents_chart(symbol: str, output_root: Path | None = None) -> dict:
    sym = str(symbol).zfill(6)
    act = store.load_agent_activity(sym)
    if act.empty:
        raise FileNotFoundError(f"标的 {symbol} 无参与者活跃度数据")

    act = act.sort_values("date")
    agent_cols = get_agent_columns(act)
    name_map = store.load_agent_names(sym)
    display_map = {col: display_agent_name(col, name_map) for col in agent_cols}
    dates = _fmt_date_series(act["date"])
    series = {
        display_map.get(col, col): [float(v) for v in act[col]]
        for col in agent_cols
    }
    return {"dates": dates, "series": series, "agent_labels": display_map}


def get_state_probs(symbol: str, output_root: Path | None = None) -> dict:
    sym = str(symbol).zfill(6)
    probs = store.load_next_state_probs(sym)
    if probs.empty:
        return {"states": [], "probabilities": []}

    col = probs.columns[0] if len(probs.columns) else "probability"
    return {
        "states": [str(i) for i in probs.index],
        "probabilities": [float(v) for v in probs[col]],
    }


def get_batch_summary(
    output_root: Path | None = None,
    *,
    limit: int = 20,
    offset: int = 0,
    sector: str = "",
    sector_l1: str = "",
    sector_l2: str = "",
    sector_l3: str = "",
    q: str = "",
    sort_by: str = "symbol",
    sort_order: str = "asc",
    all_rows: bool = False,
    slim: bool = True,
) -> dict:
    data_root = _default_data_root()
    df = _filter_summary_df(
        _load_summary_df(),
        sector=sector,
        sector_l1=sector_l1,
        sector_l2=sector_l2,
        sector_l3=sector_l3,
        q=q,
        sort_by=sort_by,
        sort_order=sort_order,
        data_root=data_root,
    )
    if df.empty:
        return {
            "rows": [],
            "total": 0,
            "limit": limit,
            "offset": offset,
            "sort_by": sort_by,
            "sort_order": sort_order,
        }

    total = len(df)
    offset = max(0, int(offset))
    if all_rows:
        page = df
        limit = total
    else:
        limit = max(1, min(int(limit), 5000))
        page = df.iloc[offset : offset + limit]

    cols = [c for c in _BATCH_SLIM_COLUMNS if c in page.columns]
    if not slim:
        cols = list(page.columns)
    if cols:
        page = page[cols]

    return {
        "rows": _sanitize_records(page),
        "total": total,
        "limit": limit,
        "offset": offset,
        "sort_by": sort_by,
        "sort_order": sort_order,
    }


_INDUSTRY_CACHE: dict[str, dict[str, object]] = {}
_INDUSTRY_CACHE_TTL_SEC = 120.0
_YEARS_CACHE: dict[str, object] = {"ts": 0.0, "years": []}
_OVERVIEW_CACHE: dict[str, object] = {"ts": 0.0, "payload": None}
_OVERVIEW_CACHE_TTL_SEC = 30.0
_SIGNALS_CACHE: dict[str, dict[str, object]] = {}
_SIGNALS_CACHE_TTL_SEC = 30.0


def _parse_industry_year(year: str | int | None) -> int | None:
    """None/'all'/'' → 全样本；否则返回日历年。"""
    if year is None:
        return None
    text = str(year).strip().lower()
    if text in {"", "all", "full", "none"}:
        return None
    try:
        y = int(text)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"无效年份: {year}") from exc
    if y < 1990 or y > 2100:
        raise ValueError(f"无效年份: {year}")
    return y


def _cached_equity_years() -> list[int]:
    import time

    from market_platform.agents.存储 import data_access as store

    now = time.monotonic()
    ts = float(_YEARS_CACHE.get("ts") or 0.0)
    years = _YEARS_CACHE.get("years") or []
    if isinstance(years, list) and years and now - ts < _INDUSTRY_CACHE_TTL_SEC:
        return [int(y) for y in years]
    years = store.list_equity_curve_years()
    _YEARS_CACHE["ts"] = now
    _YEARS_CACHE["years"] = years
    return years


def get_industry_summary(
    output_root: Path | None = None,
    *,
    year: str | int | None = None,
) -> dict:
    import time
    from datetime import datetime

    from market_platform.agents.分析.批处理.行业汇总 import build_enriched_industry_summary

    # 显式 year=all → 全样本；缺省参数 → 最近日历年
    if year is None:
        years = _cached_equity_years()
        current = datetime.now().year
        if current in years:
            parsed: int | None = current
        elif years:
            parsed = int(years[0])
        else:
            parsed = None
    else:
        parsed = _parse_industry_year(year)

    cache_key = "all" if parsed is None else str(parsed)
    now = time.monotonic()
    cached_entry = _INDUSTRY_CACHE.get(cache_key)
    if cached_entry is not None:
        cached = cached_entry.get("payload")
        ts = float(cached_entry.get("ts") or 0.0)
        if isinstance(cached, dict) and now - ts < _INDUSTRY_CACHE_TTL_SEC:
            return cached

    payload = build_enriched_industry_summary(year=parsed)
    rows = [
        {k: _json_safe(v) for k, v in (row or {}).items()}
        for row in (payload.get("rows") or [])
    ]
    for row in rows:
        for key, val in list(row.items()):
            if key.endswith("_industry_n") and isinstance(val, float) and val == int(val):
                row[key] = int(val)
    result = {
        "rows": rows,
        "year": payload.get("year"),
        "years": payload.get("years") or _cached_equity_years(),
        "period": payload.get("period") or ("all" if parsed is None else str(parsed)),
        "period_label": payload.get("period_label") or ("全样本" if parsed is None else f"{parsed}年"),
        "index_name": payload.get("index_name") or "沪深300",
        "index_code": payload.get("index_code") or "sh000300",
        "weight_mode": payload.get("weight_mode") or "circ_mv",
        "weight_label": payload.get("weight_label") or "流通市值加权（收益 p2–p98 截尾）",
        "winsorize": payload.get("winsorize") or "p2_p98",
        "winsorize_label": payload.get("winsorize_label") or "全市场收益 p2–p98 截尾",
        "index_benchmark": payload.get("index_benchmark") or "hs300",
        "index_benchmark_label": payload.get("index_benchmark_label")
        or "沪深300同行业（成分市值加权满仓）",
        "index_benchmarks": payload.get("index_benchmarks") or [],
        "sector_level": payload.get("sector_level") or "citic_l1",
        "sector_level_label": payload.get("sector_level_label") or "中信一级",
    }
    _INDUSTRY_CACHE[cache_key] = {"ts": now, "payload": result}
    return result


def get_agent_labels(symbol: str, output_root: Path | None = None) -> dict:
    sym = str(symbol).zfill(6)
    act = store.load_agent_activity(sym)
    if act.empty:
        raise FileNotFoundError(f"未找到标的 {symbol}")

    name_map = store.load_agent_names(sym)
    display_map = {col: display_agent_name(col, name_map) for col in get_agent_columns(act)}
    labels_df = store.load_agent_labels(sym)
    if labels_df.empty:
        return {"rows": [{"agent": name, "top_features": ""} for name in display_map.values()]}

    rows = []
    for _, row in labels_df.iterrows():
        agent_id = str(row.get("agent_id", row.get("agent", "")))
        rows.append(
            {
                "agent": display_agent_name(agent_id, name_map),
                "top_features": format_top_features_text(str(row.get("top_features", ""))),
            }
        )
    return {"rows": rows}


def get_kline_batch(
    symbols: list[str],
    *,
    limit: int = 90,
    data_root: Path | None = None,
) -> dict:
    """批量返回日 K 线（open, close, low, high）。

    limit<=0：返回上市至今全历史；否则返回最近 limit 根（上限 8000）。
    """
    take_all = int(limit) <= 0
    eff_limit: int | None = None if take_all else max(20, min(int(limit), 8000))
    out: dict[str, dict] = {}
    batch = store.load_kline_batch(symbols, limit=eff_limit)
    for sym, df in batch.items():
        if df.empty:
            continue
        bars: list[list[float]] = []
        for row in df.itertuples(index=False):
            bars.append(
                [
                    float(getattr(row, "open", 0) or 0),
                    float(getattr(row, "close", 0) or 0),
                    float(getattr(row, "low", 0) or 0),
                    float(getattr(row, "high", 0) or 0),
                ]
            )
        out[sym] = {"dates": _fmt_date_series(df["date"]), "bars": bars}
    return {"symbols": out}


def _build_signal_row(
    sym: str,
    meta: dict,
    *,
    signal_date,
    market_state: str,
    prev_state: str,
    suggested_position: float,
    prev_position: float,
) -> dict:
    delta = float(suggested_position) - float(prev_position)
    action = classify_signal_action(prev_state, market_state, prev_position, suggested_position)
    return {
        "symbol": sym,
        "name": meta.get("name", ""),
        "sector": meta.get("sector", ""),
        "sector_l1": meta.get("sector_l1", ""),
        "sector_l2": meta.get("sector_l2", ""),
        "sector_l3": meta.get("sector_l3", ""),
        "signal_date": str(signal_date) if signal_date is not None else None,
        "market_state": market_state,
        "prev_state": prev_state,
        "suggested_position": _pct(suggested_position),
        "prev_position": _pct(prev_position),
        "position_delta": _pct(delta),
        "action": action,
        "oos_excess_return": meta.get("oos_excess_return"),
    }


def get_daily_signals(
    output_root: Path | None = None,
    data_root: Path | None = None,
    *,
    limit: int = 30,
    sector_l1: str = "",
    sector_l2: str = "",
    sector_l3: str = "",
    q: str = "",
    include_hold: bool = False,
    portfolio_id: str = "",
) -> dict:
    """汇总最新交易日信号：相邻两日仓位/状态变化 + 最新组合调仓摘要。"""
    import time

    from market_platform.agents.接口 import portfolio as portfolio_api

    cache_key = "|".join(
        [
            str(int(limit)),
            str(sector_l1 or ""),
            str(sector_l2 or ""),
            str(sector_l3 or ""),
            str(q or ""),
            "1" if include_hold else "0",
            str(portfolio_id or ""),
        ]
    )
    now = time.monotonic()
    hit = _SIGNALS_CACHE.get(cache_key)
    if hit and now - float(hit.get("ts") or 0.0) < _SIGNALS_CACHE_TTL_SEC:
        payload = hit.get("payload")
        if isinstance(payload, dict):
            return payload

    data_root = Path(data_root or _default_data_root())
    summary = _filter_summary_df(
        _merge_hierarchy(_load_summary_df(), data_root),
        sector_l1=sector_l1,
        sector_l2=sector_l2,
        sector_l3=sector_l3,
        q=q,
        data_root=data_root,
    )
    if "status" in summary.columns:
        summary = summary[summary["status"].astype(str) == "ok"]

    meta_by_symbol: dict[str, dict] = {}
    for row in summary.itertuples(index=False):
        sym = str(row.symbol).zfill(6)
        meta_by_symbol[sym] = {
            "name": str(getattr(row, "name", "") or ""),
            "sector": str(getattr(row, "sector", "") or ""),
            "sector_l1": str(getattr(row, "sector_l1", "") or ""),
            "sector_l2": str(getattr(row, "sector_l2", "") or ""),
            "sector_l3": str(getattr(row, "sector_l3", "") or ""),
            "oos_excess_return": getattr(row, "oos_excess_return", None),
        }

    rows: list[dict] = []
    signal_date = None
    # 有筛选时按标的列表查，避免全表扫；无筛选走 DISTINCT ON 全市场
    recent = store.load_market_state_recent(
        tail=2,
        symbols=list(meta_by_symbol) if (sector_l1 or sector_l2 or sector_l3 or q) else None,
    )

    if not recent.empty and meta_by_symbol:
        recent = recent.copy()
        recent["symbol"] = recent["symbol"].astype(str).str.zfill(6)
        recent = recent[recent["symbol"].isin(meta_by_symbol)]
        recent["date"] = pd.to_datetime(recent["date"]).dt.date

        for sym, grp in recent.groupby("symbol"):
            grp = grp.sort_values("date")
            meta = meta_by_symbol.get(sym, {})
            latest = grp.iloc[-1]
            prev = grp.iloc[-2] if len(grp) >= 2 else latest
            curr_state = str(latest.get("market_state", "") or meta.get("latest_state", ""))
            prev_state = str(prev.get("market_state", "") or curr_state)
            curr_pos = float(latest.get("suggested_position", 0) or 0)
            prev_pos = float(prev.get("suggested_position", curr_pos) or curr_pos)
            sig_date = latest["date"]
            if signal_date is None or sig_date > signal_date:
                signal_date = sig_date
            rows.append(
                _build_signal_row(
                    sym,
                    meta,
                    signal_date=sig_date,
                    market_state=curr_state,
                    prev_state=prev_state,
                    suggested_position=curr_pos,
                    prev_position=prev_pos,
                )
            )
    elif meta_by_symbol:
        for sym, meta in meta_by_symbol.items():
            st = store.load_market_states(sym)
            if st.empty:
                continue
            st = st.sort_values("date")
            latest = st.iloc[-1]
            prev = st.iloc[-2] if len(st) >= 2 else latest
            sig_date = pd.to_datetime(latest["date"]).date()
            if signal_date is None or sig_date > signal_date:
                signal_date = sig_date
            rows.append(
                _build_signal_row(
                    sym,
                    meta,
                    signal_date=sig_date,
                    market_state=str(latest.get("market_state", "")),
                    prev_state=str(prev.get("market_state", "")),
                    suggested_position=float(latest.get("suggested_position", 0) or 0),
                    prev_position=float(prev.get("suggested_position", 0) or 0),
                )
            )

    add_count = sum(1 for r in rows if r["action"] == "加仓")
    reduce_count = sum(1 for r in rows if r["action"] == "减仓")
    state_change_count = sum(1 for r in rows if r["action"] in ("状态切换", "换状态"))
    hold_count = sum(1 for r in rows if r["action"] == "持有")
    changed_count = add_count + reduce_count + state_change_count

    def _sort_key(item: dict) -> float:
        delta = item.get("position_delta")
        return abs(float(delta)) if delta is not None else 0.0

    changes = [r for r in rows if r["action"] != "持有"]
    changes.sort(key=_sort_key, reverse=True)
    if not include_hold:
        display_rows = changes[: max(1, min(int(limit), 200))]
    else:
        all_sorted = sorted(rows, key=_sort_key, reverse=True)
        display_rows = all_sorted[: max(1, min(int(limit), 200))]

    adds = [r for r in rows if r["action"] == "加仓"]
    adds.sort(key=_sort_key, reverse=True)
    reduces = [r for r in rows if r["action"] == "减仓"]
    reduces.sort(key=_sort_key, reverse=True)
    state_switches = [r for r in rows if r["action"] in ("状态切换", "换状态")]
    state_switches.sort(
        key=lambda x: (str(x.get("prev_state") or ""), str(x.get("market_state") or "")),
    )
    # 前端分页浏览，但不必一次传回上千条；摘要计数仍用全量
    list_cap = max(50, min(int(limit) * 5, 100))
    adds = adds[:list_cap]
    reduces = reduces[:list_cap]
    state_switches = state_switches[:list_cap]

    portfolio_block = None
    try:
        portfolios = portfolio_api.list_portfolios(Path(output_root) if output_root else None)
        target = None
        if portfolio_id:
            target = next((p for p in portfolios if p.get("portfolio_id") == portfolio_id), None)
        elif portfolios:
            target = sorted(portfolios, key=lambda x: str(x.get("created_at", "")), reverse=True)[0]
        if target:
            meta = portfolio_api.load_portfolio(
                str(target["portfolio_id"]),
                Path(output_root) if output_root else None,
            )
            daily = portfolio_api.analyze_portfolio_daily(
                meta.get("holdings", []),
                Path(output_root) if output_root else None,
            )
            rebalance = [
                h
                for h in daily.get("holdings", [])
                if h.get("rebalance_action") in ("加仓", "减仓", "状态切换", "换状态")
            ]
            portfolio_block = {
                "portfolio_id": target["portfolio_id"],
                "name": target.get("name") or meta.get("name") or target["portfolio_id"],
                "portfolio_action": daily.get("portfolio_action"),
                "portfolio_position": daily.get("portfolio_position"),
                "portfolio_heat": daily.get("portfolio_heat"),
                "dominant_tomorrow_state": daily.get("dominant_tomorrow_state"),
                "summary": daily.get("summary"),
                "rebalance": rebalance,
                "holdings": daily.get("holdings", []),
            }
    except (FileNotFoundError, ValueError):
        portfolio_block = None

    result = {
        "signal_date": str(signal_date) if signal_date is not None else None,
        "execution_note": "T 日收盘信号 → T+1 日执行",
        "summary": {
            "total_analyzed": len(rows),
            "changed_count": changed_count,
            "add_count": add_count,
            "reduce_count": reduce_count,
            "state_change_count": state_change_count,
            "hold_count": hold_count,
        },
        "changes": display_rows,
        "high_conviction": adds,
        "defensive": reduces,
        "state_changes": state_switches,
        "portfolio": portfolio_block,
    }
    _SIGNALS_CACHE[cache_key] = {"ts": now, "payload": result}
    return result

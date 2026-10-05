from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from market_platform.agents.采集.行业.中信行业 import SHENWAN_TO_CITIC_L1

INDEX_CODE = "sh000300"
INDEX_NAME = "沪深300"
DEFAULT_INDEX_BENCH_ID = "hs300"
# 与采集.指数成分.INDEX_UNIVERSES 对齐的同行业基准
INDEX_BENCH_SPECS: tuple[dict[str, str], ...] = (
    {"id": "hs300", "name": "沪深300", "code": "sh000300", "prefix": "hs300"},
    {"id": "csi1000", "name": "中证1000", "code": "sh000852", "prefix": "csi1000"},
    {"id": "csi2000", "name": "中证2000", "code": "sh932000", "prefix": "csi2000"},
)
# 中信一级白名单（约 29 个）；行业汇总只按这些聚合
CITIC_L1 = frozenset(SHENWAN_TO_CITIC_L1.values())
_SKIP_SECTORS = frozenset({"", "未分类", "nan", "None"})
_LEVEL_MARK_RE = re.compile(r"[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩIVXivx]+$")
WEIGHT_MODE = "circ_mv"  # 行业内按流通市值加权，与沪深300市值加权口径更对齐


def _strip_level_mark(name: str) -> str:
    return _LEVEL_MARK_RE.sub("", str(name or "").strip()).strip()


def _build_child_to_l1(hier: pd.DataFrame) -> dict[str, str]:
    """从层级表建 二级/三级 → 一级 映射，便于脏标签回落。"""
    mapping: dict[str, str] = {}
    if hier.empty:
        return mapping
    for _, row in hier.iterrows():
        l1 = str(row.get("sector_l1", "") or "").strip()
        if l1 not in CITIC_L1:
            continue
        for col in ("sector_l2", "sector_l3"):
            child = str(row.get(col, "") or "").strip()
            if not child or child in CITIC_L1 or child in _SKIP_SECTORS:
                continue
            mapping.setdefault(child, l1)
            stripped = _strip_level_mark(child)
            if stripped and stripped not in CITIC_L1:
                mapping.setdefault(stripped, l1)
    return mapping


def _resolve_citic_l1(name: str, child_to_l1: dict[str, str]) -> str | None:
    text = str(name or "").strip()
    if not text or text in _SKIP_SECTORS:
        return None
    if text in CITIC_L1:
        return text
    if text in child_to_l1:
        return child_to_l1[text]
    stripped = _strip_level_mark(text)
    if stripped in CITIC_L1:
        return stripped
    if stripped in child_to_l1:
        return child_to_l1[stripped]
    # 申万一级别名
    mapped = SHENWAN_TO_CITIC_L1.get(text) or SHENWAN_TO_CITIC_L1.get(stripped)
    if mapped in CITIC_L1:
        return mapped
    return None


def _outcome_counts(values: pd.Series) -> tuple[int, int, int]:
    """按符号统计跑赢 / 跑输 / 持平只数（等权计数，不含缺失）。"""
    v = pd.to_numeric(values, errors="coerce").dropna()
    if v.empty:
        return 0, 0, 0
    win = int((v > 0).sum())
    lose = int((v < 0).sum())
    flat = int((v == 0).sum())
    return win, lose, flat


def _weighted_mean(values: pd.Series, weights: pd.Series) -> float | None:
    v = pd.to_numeric(values, errors="coerce")
    w = pd.to_numeric(weights, errors="coerce")
    mask = v.notna() & w.notna() & (w > 0)
    if not mask.any():
        return None
    vv = v[mask].astype(float)
    ww = w[mask].astype(float)
    total = float(ww.sum())
    if total <= 0:
        return None
    return float((vv * ww).sum() / total)


def _winsorize_market(values: pd.Series, *, lower: float = 0.02, upper: float = 0.98) -> pd.Series:
    """全市场截面分位截尾，压住复利脏点。"""
    from market_platform.agents.存储.行情清洗 import winsorize_series

    return winsorize_series(values, lower=lower, upper=upper)


def _weighted_positive_rate(values: pd.Series, weights: pd.Series) -> float | None:
    v = pd.to_numeric(values, errors="coerce")
    w = pd.to_numeric(weights, errors="coerce")
    mask = v.notna() & w.notna() & (w > 0)
    if not mask.any():
        return None
    ww = w[mask].astype(float)
    total = float(ww.sum())
    if total <= 0:
        return None
    pos = (v[mask].astype(float) > 0).astype(float)
    return float((pos * ww).sum() / total)


def _attach_weights(df: pd.DataFrame) -> pd.DataFrame:
    """附加流通市值权重；缺失时组内回退等权。"""
    from market_platform.agents.采集.市值 import load_circ_mv_map

    out = df.copy()
    caps = load_circ_mv_map()
    if "symbol" in out.columns:
        out["symbol"] = out["symbol"].astype(str).str.zfill(6)
        out["_circ_mv"] = out["symbol"].map(caps)
    else:
        out["_circ_mv"] = pd.NA
    return out


def _group_weights(grp: pd.DataFrame) -> pd.Series:
    w = pd.to_numeric(grp.get("_circ_mv"), errors="coerce")
    if w is None or not w.notna().any() or not (w.fillna(0) > 0).any():
        return pd.Series(1.0, index=grp.index)
    filled = w.copy()
    missing = filled.isna() | (filled <= 0)
    if missing.any() and (~missing).any():
        # 缺失市值：用有市值样本的中位流通市值，避免完全丢掉
        filled.loc[missing] = float(filled.loc[~missing].median())
    elif missing.any():
        filled.loc[missing] = 1.0
    return filled


def _attach_citic_l1(summary_df: pd.DataFrame, data_root: Path | None = None) -> pd.DataFrame:
    """强制落到中信一级（约 29 个）；二级脏标签经层级表回落，无法识别则归「综合」。"""
    from market_platform.agents.核心.路径.层级 import normalize_sector_hierarchy_df
    from market_platform.agents.核心.路径.项目 import resolve_data_root
    from market_platform.agents.采集.行业.行业层级 import load_effective_hierarchy

    out = normalize_sector_hierarchy_df(summary_df.copy())
    if "symbol" not in out.columns:
        out["sector"] = "综合"
        out["sector_l1"] = "综合"
        return out
    out["symbol"] = out["symbol"].astype(str).str.zfill(6)

    root = Path(data_root) if data_root is not None else resolve_data_root()
    hier = load_effective_hierarchy(root)
    child_to_l1 = _build_child_to_l1(hier)

    hier_l1: dict[str, str] = {}
    hier_l2: dict[str, str] = {}
    hier_l3: dict[str, str] = {}
    if not hier.empty:
        tmp = hier.copy()
        tmp["symbol"] = tmp["symbol"].astype(str).str.zfill(6)
        tmp["sector_l1"] = tmp["sector_l1"].astype(str).str.strip()
        tmp = tmp[tmp["sector_l1"].isin(CITIC_L1)]
        hier_l1 = dict(zip(tmp["symbol"], tmp["sector_l1"], strict=False))
        if "sector_l2" in tmp.columns:
            hier_l2 = {
                s: str(v).strip()
                for s, v in zip(tmp["symbol"], tmp["sector_l2"], strict=False)
                if str(v).strip()
            }
        if "sector_l3" in tmp.columns:
            hier_l3 = {
                s: str(v).strip()
                for s, v in zip(tmp["symbol"], tmp["sector_l3"], strict=False)
                if str(v).strip()
            }

    resolved = out["symbol"].map(hier_l1)
    for key in ("sector_l1", "sector", "sector_l2", "sector_l3"):
        if key not in out.columns:
            continue
        still = resolved.isna()
        if not still.any():
            break
        resolved.loc[still] = out.loc[still, key].map(
            lambda x, m=child_to_l1: _resolve_citic_l1(str(x or ""), m)
        )
    out["sector_l1"] = resolved.fillna("综合").astype(str)
    base_l2 = out["sector_l2"] if "sector_l2" in out.columns else out["sector_l1"]
    base_l3 = out["sector_l3"] if "sector_l3" in out.columns else base_l2
    out["sector_l2"] = out["symbol"].map(hier_l2).fillna(base_l2).fillna(out["sector_l1"]).astype(str)
    out["sector_l3"] = out["symbol"].map(hier_l3).fillna(base_l3).fillna(out["sector_l2"]).astype(str)
    out["sector"] = out["sector_l1"]
    return out


def aggregate_industry_summary(
    summary_df: pd.DataFrame,
    *,
    data_root: Path | None = None,
) -> pd.DataFrame:
    """按中信一级行业聚合；行业内流通市值加权（字段名保留 median_* 兼容前端）。"""
    if summary_df.empty:
        return pd.DataFrame()

    # 已由 build_enriched 预处理时跳过，避免流通市值缓存/联网重复开销
    if "_circ_mv" in summary_df.columns and "sector_l1" in summary_df.columns:
        prepared = summary_df.copy()
        if "sector" not in prepared.columns:
            prepared["sector"] = prepared["sector_l1"]
    else:
        prepared = _attach_citic_l1(summary_df, data_root=data_root)
        prepared = _attach_weights(prepared)
    if "sector" not in prepared.columns:
        return pd.DataFrame()

    ok = prepared[prepared["status"] == "ok"].copy() if "status" in prepared.columns else prepared.copy()
    if ok.empty:
        return pd.DataFrame()

    # 全市场截面截尾，避免长历史脏复利绑架市值加权行业均值
    for col in (
        "excess_return",
        "strategy_return",
        "benchmark_return",
        "oos_excess_return",
        "strategy_sharpe",
    ):
        if col in ok.columns:
            ok[col] = _winsorize_market(ok[col])

    grouped = ok.groupby("sector", dropna=False)
    rows: list[dict] = []
    for sector, grp in grouped:
        name = str(sector).strip()
        if name in _SKIP_SECTORS or name not in CITIC_L1:
            continue
        weights = _group_weights(grp)
        excess = grp["excess_return"] if "excess_return" in grp.columns else pd.Series(dtype=float)
        sharpe = (
            pd.to_numeric(grp["strategy_sharpe"], errors="coerce")
            .replace([float("inf"), float("-inf")], pd.NA)
            if "strategy_sharpe" in grp.columns
            else pd.Series(dtype=float)
        )
        strat = grp["strategy_return"] if "strategy_return" in grp.columns else pd.Series(dtype=float)
        bench = grp["benchmark_return"] if "benchmark_return" in grp.columns else pd.Series(dtype=float)
        pos = grp["suggested_position"] if "suggested_position" in grp.columns else pd.Series(dtype=float)

        # median_* 存流通市值加权均值，便于与沪深300市值加权对照
        w_excess = _weighted_mean(excess, weights)
        n_win, n_lose, n_flat = _outcome_counts(excess)
        n_total = n_win + n_lose + n_flat
        row: dict[str, Any] = {
            "sector": name,
            "n_symbols": len(grp),
            "weight_mode": WEIGHT_MODE,
            "mean_excess_return": w_excess,
            "median_excess_return": w_excess,
            "win_rate": _weighted_positive_rate(excess, weights),
            "count_win_rate": (n_win / n_total) if n_total else None,
            "n_win": n_win,
            "n_lose": n_lose,
            "n_flat": n_flat,
            "mean_strategy_return": _weighted_mean(strat, weights),
            "mean_benchmark_return": _weighted_mean(bench, weights),
            "mean_strategy_sharpe": _weighted_mean(sharpe, weights) or 0.0,
            "mean_suggested_position": _weighted_mean(pos, weights),
        }
        if "oos_excess_return" in grp.columns:
            oos = grp["oos_excess_return"]
            w_oos = _weighted_mean(oos, weights)
            if w_oos is not None:
                row["median_oos_excess_return"] = w_oos
                row["mean_oos_excess_return"] = w_oos
            o_win, o_lose, o_flat = _outcome_counts(oos)
            row["oos_n_win"] = o_win
            row["oos_n_lose"] = o_lose
            row["oos_n_flat"] = o_flat
            o_total = o_win + o_lose + o_flat
            row["oos_win_rate"] = (o_win / o_total) if o_total else None
        rows.append(row)

    if not rows:
        return pd.DataFrame()

    out = pd.DataFrame(rows).sort_values("median_excess_return", ascending=True, na_position="last")
    return out.reset_index(drop=True)


def save_industry_summary(summary_df: pd.DataFrame, out_root) -> None:
    """写入行业聚合到 PostgreSQL。"""
    from market_platform.agents.存储 import data_access as store

    industry_df = aggregate_industry_summary(summary_df)
    if industry_df.empty:
        return
    # DB schema 仅含基础列；扩展字段由 API 即时计算
    base_cols = [
        "sector",
        "n_symbols",
        "mean_excess_return",
        "median_excess_return",
        "win_rate",
        "mean_strategy_return",
        "mean_benchmark_return",
        "mean_strategy_sharpe",
        "mean_suggested_position",
    ]
    store.save_industry_summary(industry_df[[c for c in base_cols if c in industry_df.columns]])


def _to_date(value: object) -> date | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return pd.to_datetime(value).date()
    except Exception:
        return None


def _index_span_return(closes: dict[str, float], start: date, end: date) -> float | None:
    if not closes or start is None or end is None or start > end:
        return None
    start_px = None
    end_px = None
    # 向前找开盘价、向后找收盘价
    d = start
    while d <= end:
        key = d.isoformat()
        if key in closes:
            start_px = closes[key]
            break
        d += timedelta(days=1)
    d = end
    while d >= start:
        key = d.isoformat()
        if key in closes:
            end_px = closes[key]
            break
        d -= timedelta(days=1)
    if start_px is None or end_px is None or start_px <= 0:
        return None
    return float(end_px / start_px - 1.0)


def _enrich_one_index_industry(
    out: pd.DataFrame,
    prep: pd.DataFrame,
    *,
    constituents: set[str],
    prefix: str,
) -> pd.DataFrame:
    """写入 {prefix}_industry_return / _n / _index_excess_return，以及相对袖套的胜负只数。"""
    ret_col = f"{prefix}_industry_return"
    n_col = f"{prefix}_industry_n"
    excess_col = f"{prefix}_index_excess_return"
    win_col = f"{prefix}_n_win"
    lose_col = f"{prefix}_n_lose"
    flat_col = f"{prefix}_n_flat"
    rate_col = f"{prefix}_win_rate"

    if not constituents:
        for col in (ret_col, n_col, excess_col, win_col, lose_col, flat_col, rate_col):
            out[col] = None
        return out

    flag = f"_in_{prefix}"
    work = prep.copy()
    work[flag] = work["symbol"].isin(constituents)

    bench_map: dict[str, float] = {}
    n_map: dict[str, int] = {}
    win_map: dict[str, int] = {}
    lose_map: dict[str, int] = {}
    flat_map: dict[str, int] = {}
    for sector, grp in work.groupby("sector", dropna=False):
        name = str(sector).strip()
        if name in _SKIP_SECTORS:
            continue
        sleeve = grp[grp[flag]]
        if sleeve.empty:
            continue
        weights = _group_weights(sleeve)
        ret = _weighted_mean(sleeve["benchmark_return"], weights)
        if ret is None:
            continue
        bench_map[name] = ret
        n_map[name] = int(len(sleeve))
        # 行业内全部股票：策略收益 vs 该指数同行业袖套
        diff = pd.to_numeric(grp["strategy_return"], errors="coerce") - float(ret)
        w, l, f = _outcome_counts(diff)
        win_map[name] = w
        lose_map[name] = l
        flat_map[name] = f

    def _map_or_none(mapping: dict[str, Any], sector: object) -> Any:
        key = str(sector)
        return mapping[key] if key in mapping else None

    out[ret_col] = [_map_or_none(bench_map, s) for s in out["sector"]]
    out[n_col] = [_map_or_none(n_map, s) for s in out["sector"]]
    out[win_col] = [_map_or_none(win_map, s) for s in out["sector"]]
    out[lose_col] = [_map_or_none(lose_map, s) for s in out["sector"]]
    out[flat_col] = [_map_or_none(flat_map, s) for s in out["sector"]]
    rates: list[float | None] = []
    for s in out["sector"]:
        key = str(s)
        if key not in win_map:
            rates.append(None)
            continue
        w, l, f = win_map[key], lose_map[key], flat_map[key]
        total = w + l + f
        rates.append((w / total) if total else None)
    out[rate_col] = rates

    strat = pd.to_numeric(out.get("mean_strategy_return"), errors="coerce")
    bench = pd.Series(
        [bench_map.get(str(s), float("nan")) for s in out["sector"]],
        index=out.index,
        dtype=float,
    )
    excess = strat - bench
    out[excess_col] = [None if pd.isna(v) else float(v) for v in excess]
    return out


def _enrich_index_industry_benchmarks(industry: pd.DataFrame, prepared: pd.DataFrame) -> pd.DataFrame:
    """对沪深300 / 中证1000 / 中证2000 分别做「成分 ∩ 行业」市值加权满仓基准。"""
    from market_platform.agents.采集.指数成分 import load_index_constituents

    out = industry.copy()
    if out.empty or prepared.empty or "sector" not in prepared.columns:
        return out

    prep = prepared.copy()
    prep["symbol"] = prep["symbol"].astype(str).str.zfill(6)
    prep["benchmark_return"] = _winsorize_market(
        pd.to_numeric(prep.get("benchmark_return"), errors="coerce")
    )
    prep["strategy_return"] = _winsorize_market(
        pd.to_numeric(prep.get("strategy_return"), errors="coerce")
    )

    for spec in INDEX_BENCH_SPECS:
        cons = load_index_constituents(spec["id"])
        out = _enrich_one_index_industry(out, prep, constituents=cons, prefix=spec["prefix"])

    # 兼容旧字段：median_index_excess_return = 默认指数（沪深300）同行业超额
    default_excess = f"{DEFAULT_INDEX_BENCH_ID}_index_excess_return"
    default_ret = f"{DEFAULT_INDEX_BENCH_ID}_industry_return"
    default_n = f"{DEFAULT_INDEX_BENCH_ID}_industry_n"
    out["median_index_excess_return"] = out.get(default_excess)
    out["mean_index_excess_return"] = out.get(default_excess)
    # 兼容旧列名
    if "hs300_industry_return" not in out.columns and default_ret in out.columns:
        out["hs300_industry_return"] = out[default_ret]
    if "hs300_industry_n" not in out.columns and default_n in out.columns:
        out["hs300_industry_n"] = out[default_n]
    return out


def _index_benchmark_meta() -> list[dict[str, str]]:
    return [
        {
            "id": s["id"],
            "name": s["name"],
            "code": s["code"],
            "prefix": s["prefix"],
            "label": f"{s['name']}同行业（成分市值加权满仓）",
            "return_key": f"{s['prefix']}_industry_return",
            "n_key": f"{s['prefix']}_industry_n",
            "excess_key": f"{s['prefix']}_index_excess_return",
            "n_win_key": f"{s['prefix']}_n_win",
            "n_lose_key": f"{s['prefix']}_n_lose",
            "n_flat_key": f"{s['prefix']}_n_flat",
            "win_rate_key": f"{s['prefix']}_win_rate",
        }
        for s in INDEX_BENCH_SPECS
    ]


def build_enriched_industry_summary(*, year: int | None = None) -> dict[str, Any]:
    """行业口径：加权超额 / 样本外加权 / 相对各指数同行业。

    year=None：全样本（symbol_summary 累计收益）；
    year=YYYY：该日历年权益曲线首末净值收益。
    """
    from market_platform.agents.存储 import data_access as store

    years = store.list_equity_curve_years()
    benches = _index_benchmark_meta()
    period_label = "全样本" if year is None else f"{int(year)}年"
    empty = {
        "rows": [],
        "year": year,
        "years": years,
        "period": "all" if year is None else str(int(year)),
        "period_label": period_label,
        "index_name": INDEX_NAME,
        "index_code": INDEX_CODE,
        "weight_mode": WEIGHT_MODE,
        "weight_label": "流通市值加权（收益 p2–p98 截尾）",
        "winsorize": "p2_p98",
        "winsorize_label": "全市场收益 p2–p98 截尾",
        "index_benchmark": DEFAULT_INDEX_BENCH_ID,
        "index_benchmark_label": f"{INDEX_NAME}同行业（成分市值加权满仓）",
        "index_benchmarks": benches,
        "sector_level": "citic_l1",
        "sector_level_label": "中信一级",
    }
    summary = store.load_symbol_summary()
    if summary.empty:
        return empty

    df = summary.copy()
    df["symbol"] = df["symbol"].astype(str).str.zfill(6)
    if "status" in df.columns:
        df = df[df["status"].astype(str) == "ok"]
    if df.empty:
        return empty

    if year is not None:
        yearly = store.load_equity_returns_for_year(int(year))
        if yearly.empty:
            return {**empty, "rows": []}
        keep = ["symbol"] + [c for c in ("name", "sector", "sector_l1", "sector_l2", "sector_l3", "status", "suggested_position", "strategy_sharpe") if c in df.columns]
        df = df[keep].merge(yearly, on="symbol", how="inner")
        # 年度视图不做全样本 OOS
        df["oos_excess_return"] = pd.NA
    else:
        df["excess_return"] = pd.to_numeric(df.get("excess_return"), errors="coerce")
        df["strategy_return"] = pd.to_numeric(df.get("strategy_return"), errors="coerce")
        df["benchmark_return"] = pd.to_numeric(df.get("benchmark_return"), errors="coerce")
        oos_map = store.load_oos_excess_map()
        df["oos_excess_return"] = df["symbol"].map(oos_map)

    prepared = _attach_citic_l1(df)
    prepared = _attach_weights(prepared)

    industry = aggregate_industry_summary(prepared)
    if industry.empty:
        return {**empty, "rows": []}

    industry = _enrich_index_industry_benchmarks(industry, prepared)

    ensure_cols = [
        "median_excess_return",
        "median_oos_excess_return",
        "median_index_excess_return",
        "n_win",
        "n_lose",
        "n_flat",
        "count_win_rate",
        "oos_n_win",
        "oos_n_lose",
        "oos_n_flat",
        "oos_win_rate",
        "hs300_industry_return",
        "hs300_industry_n",
        "hs300_index_excess_return",
        "hs300_n_win",
        "hs300_n_lose",
        "hs300_n_flat",
        "hs300_win_rate",
        "csi1000_industry_return",
        "csi1000_industry_n",
        "csi1000_index_excess_return",
        "csi1000_n_win",
        "csi1000_n_lose",
        "csi1000_n_flat",
        "csi1000_win_rate",
        "csi2000_industry_return",
        "csi2000_industry_n",
        "csi2000_index_excess_return",
        "csi2000_n_win",
        "csi2000_n_lose",
        "csi2000_n_flat",
        "csi2000_win_rate",
    ]
    for col in ensure_cols:
        if col not in industry.columns:
            industry[col] = pd.NA

    int_keys = {
        "n_win",
        "n_lose",
        "n_flat",
        "oos_n_win",
        "oos_n_lose",
        "oos_n_flat",
        "n_symbols",
    }
    for s in INDEX_BENCH_SPECS:
        pfx = s["prefix"]
        int_keys.update(
            {
                f"{pfx}_industry_n",
                f"{pfx}_n_win",
                f"{pfx}_n_lose",
                f"{pfx}_n_flat",
            }
        )
    records: list[dict[str, Any]] = []
    for row in industry.to_dict(orient="records"):
        clean: dict[str, Any] = {}
        for k, v in row.items():
            if v is None:
                clean[k] = None
            elif isinstance(v, float) and (pd.isna(v) or v in (float("inf"), float("-inf"))):
                clean[k] = None
            elif isinstance(v, (int, float)) and not isinstance(v, bool):
                raw = v.item() if hasattr(v, "item") else v
                if k in int_keys and isinstance(raw, float) and raw == int(raw):
                    clean[k] = int(raw)
                else:
                    clean[k] = raw
            else:
                try:
                    clean[k] = None if pd.isna(v) else v
                except (TypeError, ValueError):
                    clean[k] = v
        records.append(clean)
    return {
        "rows": records,
        "year": year,
        "years": years,
        "period": "all" if year is None else str(int(year)),
        "period_label": period_label,
        "index_name": INDEX_NAME,
        "index_code": INDEX_CODE,
        "weight_mode": WEIGHT_MODE,
        "weight_label": "流通市值加权（收益 p2–p98 截尾）",
        "winsorize": "p2_p98",
        "winsorize_label": "全市场收益 p2–p98 截尾",
        "index_benchmark": DEFAULT_INDEX_BENCH_ID,
        "index_benchmark_label": f"{INDEX_NAME}同行业（成分市值加权满仓）",
        "index_benchmarks": benches,
        "sector_level": "citic_l1",
        "sector_level_label": "中信一级",
    }

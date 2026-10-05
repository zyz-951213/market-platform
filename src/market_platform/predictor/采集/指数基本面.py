"""
沪深300 成分股财报聚合 — 按财报研究维度汇总指数基本面
"""

from __future__ import annotations

from market_platform.predictor.核心.路径.项目 import data_dir

import time
from pathlib import Path
from typing import Dict, List

import numpy as np
import pandas as pd
from loguru import logger

from market_platform.predictor.配置 import get_config
from market_platform.predictor.存储.数据访问 import load_series_df, save_series_df

# 财报字段 → 因子名（对应 config dimensions）
STOCK_METRIC_MAP: Dict[str, str] = {
    "roe": "净资产收益率(%)",
    "roa": "总资产净利润率(%)",
    "gross_margin": "销售毛利率(%)",
    "net_margin": "销售净利率(%)",
    "revenue_growth": "主营业务收入增长率(%)",
    "profit_growth": "净利润增长率(%)",
    "debt_ratio": "资产负债率(%)",
    "current_ratio": "流动比率",
    "quick_ratio": "速动比率",
    "ocf_per_share": "每股经营性现金流(元)",
    "ocf_to_profit": "经营现金净流量与净利润的比率(%)",
    "asset_turnover": "总资产周转率(次)",
}

INDEX_CODE_MAP = {
    "000300.SH": "000300",
    "000001.SH": "000001",
    "399001.SZ": "399001",
}


def _index_fund_cfg() -> dict:
    return get_config().get("features", {}).get("financial", {}).get("index_fundamentals", {})


def _cache_dir() -> Path:
    path = data_dir() / "raw" / "stock_fundamentals"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _aggregate_cache_path(index_code: str) -> Path:
    name = INDEX_CODE_MAP.get(index_code, "000300")
    return data_dir() / "raw" / f"index_fundamentals_{name}.parquet"


def _weighted_mean(values: pd.Series, weights: pd.Series) -> float:
    mask = values.notna() & weights.notna()
    if not mask.any():
        return np.nan
    v = values[mask].astype(float)
    w = weights[mask].astype(float)
    return float((v * w).sum() / w.sum())


def fetch_constituent_weights(index_code: str, top_n: int) -> pd.DataFrame:
    import akshare as ak

    symbol = INDEX_CODE_MAP.get(index_code, "000300")
    logger.info(f"📡 拉取指数 {symbol} 成分股权重 (top {top_n})...")
    df = ak.index_stock_cons_weight_csindex(symbol=symbol)
    df = df.rename(columns={"成分券代码": "code", "权重": "weight"})
    df["code"] = df["code"].astype(str).str.zfill(6)
    df["weight"] = pd.to_numeric(df["weight"], errors="coerce") / 100.0
    df = df.sort_values("weight", ascending=False).head(top_n)
    return df[["code", "weight"]].reset_index(drop=True)


EM_METRIC_MAP: Dict[str, str] = {
    "roe": "ROE_DILUTED",
    "roa": "JROA",
    "gross_margin": "GROSS_PROFIT_RATIO",
    "net_margin": "NET_PROFIT_RATIO",
    "revenue_growth": "TOTALOPERATEREVETZ",
    "profit_growth": "PARENTNETPROFITTZ",
    "ocf_per_share": "PER_NETCASH",
}


def _to_em_symbol(code: str) -> str:
    if code.startswith("6"):
        return f"{code}.SH"
    return f"{code}.SZ"


def _fetch_stock_fundamentals_sina(code: str, start_year: str) -> pd.DataFrame:
    import akshare as ak

    raw = ak.stock_financial_analysis_indicator(symbol=code, start_year=start_year)
    if "日期" not in raw.columns:
        raise KeyError("日期")
    raw["report_date"] = pd.to_datetime(raw["日期"])
    rows = []
    for _, row in raw.iterrows():
        item = {"report_date": row["report_date"], "code": code}
        for factor, col in STOCK_METRIC_MAP.items():
            item[factor] = pd.to_numeric(row.get(col), errors="coerce")
        rows.append(item)
    return pd.DataFrame(rows).sort_values("report_date").drop_duplicates("report_date", keep="last")


def _fetch_stock_fundamentals_em(code: str) -> pd.DataFrame:
    import akshare as ak

    em_code = _to_em_symbol(code)
    raw = ak.stock_financial_analysis_indicator_em(symbol=em_code, indicator="盈利能力")
    raw["report_date"] = pd.to_datetime(raw["REPORT_DATE"])
    rows = []
    for _, row in raw.iterrows():
        item = {"report_date": row["report_date"], "code": code}
        for factor, col in EM_METRIC_MAP.items():
            item[factor] = pd.to_numeric(row.get(col), errors="coerce")
        rows.append(item)
    return pd.DataFrame(rows).sort_values("report_date").drop_duplicates("report_date", keep="last")


def _aggregate_cache_name(index_code: str) -> str:
    name = INDEX_CODE_MAP.get(index_code, "000300")
    return f"index_fundamentals_{name}.parquet"


def fetch_stock_fundamentals(code: str, start_year: str) -> pd.DataFrame:
    cache_name = f"stock_fundamentals/{code}_{start_year}.parquet"
    cached = load_series_df(cache_name)
    if cached is not None and not cached.empty:
        return cached

    try:
        df = _fetch_stock_fundamentals_sina(code, start_year)
    except Exception:
        df = _fetch_stock_fundamentals_em(code)

    save_series_df(cache_name, df)
    return df


def aggregate_index_fundamentals(index_code: str | None = None) -> pd.DataFrame:
    """按权重聚合成分股季报，输出季频指数基本面序列"""
    cfg = _index_fund_cfg()
    if not cfg.get("enabled", False):
        return pd.DataFrame()

    index_code = index_code or get_config().get("data", {}).get("index_code", "000300.SH")
    cache_name = _aggregate_cache_name(index_code)
    if not cfg.get("force_refresh", False):
        cached = load_series_df(cache_name)
        if cached is not None and not cached.empty:
            logger.info(f"📂 指数财报聚合缓存: {cache_name} ({len(cached)} 季)")
            return cached

    top_n = int(cfg.get("top_constituents", 50))
    start_year = str(cfg.get("start_year", 2010))
    weights = fetch_constituent_weights(index_code, top_n)

    all_rows: List[dict] = []
    for i, row in weights.iterrows():
        code = row["code"]
        weight = row["weight"]
        try:
            stock_df = fetch_stock_fundamentals(code, start_year)
            for _, q in stock_df.iterrows():
                rec = {"report_date": q["report_date"], "code": code, "weight": weight}
                for factor in STOCK_METRIC_MAP:
                    rec[factor] = q.get(factor)
                all_rows.append(rec)
        except Exception as e:
            logger.warning(f"⚠️ 成分股 {code} 财报获取失败: {e}")
        if (i + 1) % 10 == 0:
            time.sleep(0.2)

    if not all_rows:
        logger.warning("⚠️ 指数财报聚合无数据")
        return pd.DataFrame()

    panel = pd.DataFrame(all_rows)
    agg_rows = []
    for report_date, group in panel.groupby("report_date"):
        rec = {"report_date": report_date}
        for factor in STOCK_METRIC_MAP:
            rec[f"idx_{factor}_w"] = _weighted_mean(group[factor], group["weight"])
            rec[f"idx_{factor}_med"] = float(group[factor].median(skipna=True))
        agg_rows.append(rec)

    result = pd.DataFrame(agg_rows).sort_values("report_date").reset_index(drop=True)
    save_series_df(cache_name, result)
    logger.info(f"💾 指数财报聚合完成: {cache_name} ({len(result)} 季, top {top_n} 成分)")
    return result


def expand_quarterly_to_daily(
    qdf: pd.DataFrame,
    start: pd.Timestamp,
    end: pd.Timestamp,
    lag_days: int = 45,
) -> pd.DataFrame:
    """季报 → 日频，按公告滞后向前填充"""
    if qdf.empty:
        return pd.DataFrame()

    qdf = qdf.copy()
    qdf["date"] = pd.to_datetime(qdf["report_date"]) + pd.Timedelta(days=lag_days)
    value_cols = [c for c in qdf.columns if c not in {"report_date", "date"}]
    daily_index = pd.date_range(start, end, freq="D")
    daily = qdf.set_index("date")[value_cols].sort_index()
    daily = daily.reindex(daily_index.union(daily.index)).sort_index().ffill()
    daily = daily.reindex(daily_index).reset_index().rename(columns={"index": "date"})
    return daily

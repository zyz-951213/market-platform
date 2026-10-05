"""
财务因子 — 按财报研究维度组织，配置驱动
维度: 盈利能力 / 成长 / 估值 / 偿债 / 现金流 / 宏观流动性 / 资金面
"""

from __future__ import annotations

from market_platform.predictor.核心.路径.项目 import data_dir

import re
from typing import Callable, Dict, List

import numpy as np
import pandas as pd
from loguru import logger

from market_platform.predictor.配置 import get_config
from market_platform.predictor.采集.指数基本面 import aggregate_index_fundamentals, expand_quarterly_to_daily
from market_platform.predictor.存储.数据访问 import load_series_df, save_series_df

INDEX_VALUATION_NAMES = {
    "000300.SH": "沪深300",
    "000001.SH": "上证指数",
    "399001.SZ": "深证成指",
}

VALUATION_RAW_COLUMNS = [
    "pe_ttm", "pe_static", "pe_median", "pe_ttm_eq",
    "pb", "pb_median", "pb_eq", "dividend_yield",
]

MACRO_RAW_COLUMNS = [
    "m2_yoy", "m1_yoy", "ppi_yoy", "pmi_mfg", "pmi_non_mfg",
    "lpr_1y", "lpr_5y", "social_financing",
]

MARGIN_RAW_COLUMNS = ["margin_balance", "margin_buy"]
SHIBOR_RAW_COLUMNS = ["shibor_on"]
NORTH_FLOW_RAW_COLUMNS = ["north_flow_net"]

INDEX_FUND_RAW_COLUMNS = [
    "idx_roe_w", "idx_roa_w", "idx_gross_margin_w", "idx_net_margin_w",
    "idx_revenue_growth_w", "idx_profit_growth_w",
    "idx_debt_ratio_w", "idx_current_ratio_w", "idx_quick_ratio_w",
    "idx_ocf_per_share_w", "idx_ocf_to_profit_w", "idx_asset_turnover_w",
]


def get_financial_config() -> dict:
    return get_config().get("features", {}).get("financial", {})


def is_financial_enabled() -> bool:
    return get_financial_config().get("enabled", False)


def get_financial_sources() -> dict:
    return get_financial_config().get("sources", {})


def _factors_from_dimensions(key: str) -> List[str]:
    dimensions = get_financial_config().get("dimensions", {})
    factors: List[str] = []
    for dim in dimensions.values():
        if isinstance(dim, dict) and dim.get("enabled", True):
            factors.extend(dim.get(key, []))
    return factors


def get_raw_factors() -> List[str]:
    fin = get_financial_config()
    if fin.get("dimensions"):
        return _factors_from_dimensions("raw_factors")
    return fin.get("raw_factors", [])


def get_derived_factors() -> List[str]:
    fin = get_financial_config()
    if fin.get("dimensions"):
        return _factors_from_dimensions("derived_factors")
    return fin.get("derived_factors", [])


def _needs_any(raw_factors: List[str], columns: List[str]) -> bool:
    return any(c in raw_factors for c in columns)


def _index_valuation_name() -> str:
    code = get_config().get("data", {}).get("index_code", "000300.SH")
    return INDEX_VALUATION_NAMES.get(code, "沪深300")


def _parse_cn_month(series: pd.Series) -> pd.Series:
    def _one(val) -> pd.Timestamp | pd.NaT:
        if pd.isna(val):
            return pd.NaT
        text = str(val)
        m = re.search(r"(\d{4})年(\d{1,2})月", text)
        if m:
            return pd.Timestamp(year=int(m.group(1)), month=int(m.group(2)), day=1)
        if text.isdigit() and len(text) == 6:
            return pd.Timestamp(year=int(text[:4]), month=int(text[4:6]), day=1)
        return pd.to_datetime(val, errors="coerce")

    return series.map(_one)


def fetch_index_valuation() -> pd.DataFrame:
    import akshare as ak

    name = _index_valuation_name()
    logger.info(f"📡 拉取 {name} 估值数据 (PE/PB/股息率)...")

    pe = ak.stock_index_pe_lg(symbol=name)
    pe = pe.rename(columns={
        "日期": "date",
        "滚动市盈率": "pe_ttm",
        "静态市盈率": "pe_static",
        "滚动市盈率中位数": "pe_median",
        "等权滚动市盈率": "pe_ttm_eq",
    })
    pe = pe[["date", "pe_ttm", "pe_static", "pe_median", "pe_ttm_eq"]]

    pb = ak.stock_index_pb_lg(symbol=name)
    pb = pb.rename(columns={
        "日期": "date",
        "市净率": "pb",
        "市净率中位数": "pb_median",
        "等权市净率": "pb_eq",
    })
    pb = pb[["date", "pb", "pb_median", "pb_eq"]]

    df = pe.merge(pb, on="date", how="outer")
    df["date"] = pd.to_datetime(df["date"])

    try:
        symbol = get_config().get("data", {}).get("index_code", "000300.SH").split(".")[0]
        div = ak.stock_zh_index_value_csindex(symbol=symbol)
        div = div.rename(columns={"日期": "date", "股息率2": "dividend_yield"})
        div["date"] = pd.to_datetime(div["date"])
        div["dividend_yield"] = pd.to_numeric(div["dividend_yield"], errors="coerce")
        df = df.merge(div[["date", "dividend_yield"]], on="date", how="left")
    except Exception as e:
        logger.warning(f"⚠️ 股息率获取失败: {e}")

    for col in VALUATION_RAW_COLUMNS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    cache_name = f"valuation_{name}.parquet"
    save_series_df(cache_name, df)
    logger.info(f"💾 估值数据已缓存: {cache_name} ({len(df)} 条)")
    return df


def fetch_macro_data() -> pd.DataFrame:
    import akshare as ak

    logger.info("📡 拉取宏观流动性数据 (M2/PPI/PMI/LPR/社融)...")
    frames = []

    m2 = ak.macro_china_money_supply()
    m2 = m2.rename(columns={
        "月份": "month",
        "货币和准货币(M2)-同比增长": "m2_yoy",
        "货币(M1)-同比增长": "m1_yoy",
    })
    m2["date"] = _parse_cn_month(m2["month"])
    frames.append(m2[["date", "m2_yoy", "m1_yoy"]])

    ppi = ak.macro_china_ppi()
    ppi = ppi.rename(columns={"月份": "month", "当月同比增长": "ppi_yoy"})
    ppi["date"] = _parse_cn_month(ppi["month"])
    frames.append(ppi[["date", "ppi_yoy"]])

    pmi = ak.macro_china_pmi()
    pmi = pmi.rename(columns={
        "月份": "month",
        "制造业-指数": "pmi_mfg",
        "非制造业-指数": "pmi_non_mfg",
    })
    pmi["date"] = _parse_cn_month(pmi["month"])
    frames.append(pmi[["date", "pmi_mfg", "pmi_non_mfg"]])

    lpr = ak.macro_china_lpr()
    lpr = lpr.rename(columns={"TRADE_DATE": "date", "LPR1Y": "lpr_1y", "LPR5Y": "lpr_5y"})
    lpr["date"] = pd.to_datetime(lpr["date"])
    frames.append(lpr[["date", "lpr_1y", "lpr_5y"]])

    sf = ak.macro_china_shrzgm()
    sf = sf.rename(columns={"月份": "month", "社会融资规模增量": "social_financing"})
    sf["date"] = _parse_cn_month(sf["month"])
    frames.append(sf[["date", "social_financing"]])

    macro = frames[0]
    for part in frames[1:]:
        macro = macro.merge(part, on="date", how="outer")
    macro = macro.sort_values("date").reset_index(drop=True)
    for col in MACRO_RAW_COLUMNS:
        if col in macro.columns:
            macro[col] = pd.to_numeric(macro[col], errors="coerce")

    save_series_df("macro_liquidity.parquet", macro)
    logger.info(f"💾 宏观数据已缓存: macro_liquidity.parquet ({len(macro)} 条)")
    return macro


def fetch_margin_data() -> pd.DataFrame:
    import akshare as ak

    logger.info("📡 拉取融资融券数据...")
    df = ak.stock_margin_sse()
    df = df.rename(columns={
        "信用交易日期": "date",
        "融资余额": "margin_balance",
        "融资买入额": "margin_buy",
    })
    df["date"] = pd.to_datetime(df["date"].astype(str), format="%Y%m%d", errors="coerce")
    for col in MARGIN_RAW_COLUMNS:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df[["date"] + MARGIN_RAW_COLUMNS].sort_values("date").reset_index(drop=True)

    save_series_df("margin_sse.parquet", df)
    return df


def fetch_shibor_overnight() -> pd.DataFrame:
    import akshare as ak

    logger.info("📡 拉取 Shibor 隔夜利率...")
    df = ak.rate_interbank(market="上海银行同业拆借市场", symbol="Shibor人民币", indicator="隔夜")
    df = df.rename(columns={"报告日": "date", "利率": "shibor_on"})
    df["date"] = pd.to_datetime(df["date"])
    df["shibor_on"] = pd.to_numeric(df["shibor_on"], errors="coerce")
    df = df[["date", "shibor_on"]].sort_values("date").reset_index(drop=True)

    save_series_df("shibor_on.parquet", df)
    return df


def fetch_north_flow() -> pd.DataFrame:
    import akshare as ak

    logger.info("📡 拉取北向资金数据...")
    df = ak.stock_hsgt_hist_em(symbol="北向资金")
    df = df.rename(columns={"日期": "date", "当日成交净买额": "north_flow_net"})
    df["date"] = pd.to_datetime(df["date"])
    df["north_flow_net"] = pd.to_numeric(df["north_flow_net"], errors="coerce")
    df = df[["date", "north_flow_net"]].sort_values("date").reset_index(drop=True)

    save_series_df("north_flow.parquet", df)
    logger.info(f"💾 北向资金已缓存: north_flow.parquet ({len(df)} 条)")
    return df


def _load_cached(path_suffix: str) -> pd.DataFrame | None:
    return load_series_df(path_suffix)


def _expand_monthly_to_daily(monthly: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    value_cols = [c for c in monthly.columns if c != "date"]
    daily_index = pd.date_range(start, end, freq="D")
    daily = monthly.set_index("date")[value_cols].sort_index()
    daily = daily.reindex(daily_index.union(daily.index)).sort_index().ffill()
    return daily.reindex(daily_index).reset_index().rename(columns={"index": "date"})


def _merge_on_date(df: pd.DataFrame, extra: pd.DataFrame, cols: List[str]) -> pd.DataFrame:
    use_cols = ["date"] + [c for c in cols if c in extra.columns]
    extra = extra[use_cols].copy()
    extra["date"] = pd.to_datetime(extra["date"])
    return df.merge(extra, on="date", how="left")


def merge_fundamental_data(df: pd.DataFrame) -> pd.DataFrame:
    """按 config 合并各维度原始因子"""
    if not is_financial_enabled():
        return df

    sources = get_financial_sources()
    raw_factors = get_raw_factors()
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"])
    start, end = df["date"].min(), df["date"].max()

    if sources.get("valuation", False) and _needs_any(raw_factors, VALUATION_RAW_COLUMNS):
        val = _load_cached(f"valuation_{_index_valuation_name()}.parquet")
        if val is None:
            try:
                val = fetch_index_valuation()
            except Exception as e:
                logger.warning(f"⚠️ 估值数据获取失败: {e}")
                val = None
        if val is not None:
            df = _merge_on_date(df, val, VALUATION_RAW_COLUMNS)
            logger.info(f"🔗 估值因子: {df['pe_ttm'].notna().sum()}/{len(df)} 日有数据")

    if sources.get("index_fundamentals", False) and _needs_any(raw_factors, INDEX_FUND_RAW_COLUMNS):
        try:
            qdf = aggregate_index_fundamentals()
            lag = int(get_financial_config().get("index_fundamentals", {}).get("announcement_lag_days", 45))
            daily_fund = expand_quarterly_to_daily(qdf, start, end, lag_days=lag)
            if not daily_fund.empty:
                df = _merge_on_date(df, daily_fund, INDEX_FUND_RAW_COLUMNS)
                logger.info(f"🔗 指数财报因子: {df['idx_roe_w'].notna().sum()}/{len(df)} 日有数据")
        except Exception as e:
            logger.warning(f"⚠️ 指数财报聚合失败: {e}")

    if sources.get("macro", False) and _needs_any(raw_factors, MACRO_RAW_COLUMNS):
        macro = _load_cached("macro_liquidity.parquet")
        if macro is None:
            try:
                macro = fetch_macro_data()
            except Exception as e:
                logger.warning(f"⚠️ 宏观数据获取失败: {e}")
                macro = None
        if macro is not None:
            macro_daily = _expand_monthly_to_daily(macro, start, end)
            df = _merge_on_date(df, macro_daily, MACRO_RAW_COLUMNS)
            logger.info(f"🔗 宏观流动性: {df['m2_yoy'].notna().sum()}/{len(df)} 日有数据")

    if sources.get("margin", False) and _needs_any(raw_factors, MARGIN_RAW_COLUMNS):
        margin = _load_cached("margin_sse.parquet")
        if margin is None:
            try:
                margin = fetch_margin_data()
            except Exception as e:
                logger.warning(f"⚠️ 融资融券获取失败: {e}")
                margin = None
        if margin is not None:
            df = _merge_on_date(df, margin, MARGIN_RAW_COLUMNS)
            logger.info(f"🔗 融资融券: {df['margin_balance'].notna().sum()}/{len(df)} 日有数据")

    if sources.get("shibor", False) and _needs_any(raw_factors, SHIBOR_RAW_COLUMNS):
        shibor = _load_cached("shibor_on.parquet")
        if shibor is None:
            try:
                shibor = fetch_shibor_overnight()
            except Exception as e:
                logger.warning(f"⚠️ Shibor 获取失败: {e}")
                shibor = None
        if shibor is not None:
            df = _merge_on_date(df, shibor, SHIBOR_RAW_COLUMNS)

    if sources.get("north_flow", False) and _needs_any(raw_factors, NORTH_FLOW_RAW_COLUMNS):
        north = _load_cached("north_flow.parquet")
        if north is None:
            try:
                north = fetch_north_flow()
            except Exception as e:
                logger.warning(f"⚠️ 北向资金获取失败: {e}")
                north = None
        if north is not None:
            df = _merge_on_date(df, north, NORTH_FLOW_RAW_COLUMNS)

    return df


def _build_derived_registry(window: int) -> Dict[str, Callable[[pd.DataFrame], pd.Series]]:
    """财务衍生因子注册表"""

    def _pct(col):
        return lambda d: d[col].pct_change().replace([np.inf, -np.inf], 0)

    def _ma(col, n):
        return lambda d: d[col].rolling(n).mean()

    def _deviation(col, n=20):
        def fn(d):
            ma = d[col].rolling(n).mean()
            return (d[col] - ma) / (ma + 1e-10)
        return fn

    def _percentile(col):
        return lambda d: d[col].rolling(window, min_periods=20).apply(
            lambda x: pd.Series(x).rank(pct=True).iloc[-1], raw=False
        )

    registry: Dict[str, Callable[[pd.DataFrame], pd.Series]] = {
        # 估值
        "pe_ttm_ma20": _ma("pe_ttm", 20),
        "pe_ttm_deviation": _deviation("pe_ttm", 20),
        "pe_ttm_change": _pct("pe_ttm"),
        "pe_ttm_percentile": _percentile("pe_ttm"),
        "pb_ma20": _ma("pb", 20),
        "pb_deviation": _deviation("pb", 20),
        "pb_change": _pct("pb"),
        "pb_percentile": _percentile("pb"),
        "pe_pb_ratio": lambda d: d["pe_ttm"] / (d["pb"] + 1e-10),
        "pe_vs_median": lambda d: d["pe_ttm"] - d["pe_median"],
        "pb_vs_median": lambda d: d["pb"] - d["pb_median"],
        "earnings_yield": lambda d: 1.0 / (d["pe_ttm"] + 1e-10),
        "dividend_yield_ma20": _ma("dividend_yield", 20),
        "peg_proxy": lambda d: d["pe_ttm"] / (d["idx_profit_growth_w"].abs() + 1e-10),
        # 盈利能力
        "idx_roe_w_change": _pct("idx_roe_w"),
        "idx_roe_w_ma4q": _ma("idx_roe_w", 63),
        "idx_net_margin_w_change": _pct("idx_net_margin_w"),
        # 成长（财报已含同比，做平滑）
        "idx_revenue_growth_w_ma2q": _ma("idx_revenue_growth_w", 63),
        "idx_profit_growth_w_ma2q": _ma("idx_profit_growth_w", 63),
        # 偿债
        "idx_debt_ratio_w_change": _pct("idx_debt_ratio_w"),
        "idx_current_ratio_w_deviation": _deviation("idx_current_ratio_w", 63),
        # 现金流
        "idx_ocf_to_profit_w_deviation": _deviation("idx_ocf_to_profit_w", 63),
        "idx_ocf_per_share_w_change": _pct("idx_ocf_per_share_w"),
        # 宏观流动性
        "m2_yoy_change": lambda d: d["m2_yoy"].diff(),
        "pmi_mfg_ma3": _ma("pmi_mfg", 63),
        "ppi_yoy_change": lambda d: d["ppi_yoy"].diff(),
        "lpr_spread": lambda d: d["lpr_5y"] - d["lpr_1y"],
        "social_financing_ma3": _ma("social_financing", 63),
        # 利率
        "shibor_ma10": _ma("shibor_on", 10),
        "shibor_change": lambda d: d["shibor_on"].diff(),
        "shibor_high": lambda d: (d["shibor_on"] > d["shibor_on"].rolling(60).quantile(0.8)).astype(int),
        # 资金面
        "margin_balance_change": _pct("margin_balance"),
        "margin_balance_ma20": _ma("margin_balance", 20),
        "north_flow_ma_5": lambda d: d["north_flow_net"].fillna(0).rolling(5).mean(),
        "north_flow_ma_20": lambda d: d["north_flow_net"].fillna(0).rolling(20).mean(),
        "north_flow_change": lambda d: d["north_flow_net"].fillna(0).pct_change().replace([np.inf, -np.inf], 0),
        "north_flow_cumsum_20": lambda d: d["north_flow_net"].fillna(0).rolling(20).sum(),
    }
    return registry


DERIVED_DEPENDENCIES: Dict[str, List[str]] = {
    "pe_ttm_ma20": ["pe_ttm"], "pe_ttm_deviation": ["pe_ttm"], "pe_ttm_change": ["pe_ttm"],
    "pe_ttm_percentile": ["pe_ttm"], "pb_ma20": ["pb"], "pb_deviation": ["pb"], "pb_change": ["pb"],
    "pb_percentile": ["pb"], "pe_pb_ratio": ["pe_ttm", "pb"], "pe_vs_median": ["pe_ttm", "pe_median"],
    "pb_vs_median": ["pb", "pb_median"], "earnings_yield": ["pe_ttm"], "dividend_yield_ma20": ["dividend_yield"],
    "peg_proxy": ["pe_ttm", "idx_profit_growth_w"],
    "idx_roe_w_change": ["idx_roe_w"], "idx_roe_w_ma4q": ["idx_roe_w"],
    "idx_net_margin_w_change": ["idx_net_margin_w"],
    "idx_revenue_growth_w_ma2q": ["idx_revenue_growth_w"], "idx_profit_growth_w_ma2q": ["idx_profit_growth_w"],
    "idx_debt_ratio_w_change": ["idx_debt_ratio_w"], "idx_current_ratio_w_deviation": ["idx_current_ratio_w"],
    "idx_ocf_to_profit_w_deviation": ["idx_ocf_to_profit_w"], "idx_ocf_per_share_w_change": ["idx_ocf_per_share_w"],
    "m2_yoy_change": ["m2_yoy"], "pmi_mfg_ma3": ["pmi_mfg"], "ppi_yoy_change": ["ppi_yoy"],
    "lpr_spread": ["lpr_5y", "lpr_1y"], "social_financing_ma3": ["social_financing"],
    "shibor_ma10": ["shibor_on"], "shibor_change": ["shibor_on"], "shibor_high": ["shibor_on"],
    "margin_balance_change": ["margin_balance"], "margin_balance_ma20": ["margin_balance"],
    "north_flow_ma_5": ["north_flow_net"], "north_flow_ma_20": ["north_flow_net"],
    "north_flow_change": ["north_flow_net"], "north_flow_cumsum_20": ["north_flow_net"],
}


def add_fundamental_features(df: pd.DataFrame) -> pd.DataFrame:
    """按 config 计算财务衍生因子"""
    if not is_financial_enabled():
        return df

    df = df.copy()
    fin_cfg = get_financial_config()
    window = fin_cfg.get("percentile_window", get_config().get("features", {}).get("window", 60))
    derived_factors = get_derived_factors()
    registry = _build_derived_registry(window)

    built, skipped = [], []
    for name in derived_factors:
        if name not in registry:
            logger.warning(f"⚠️ 未知财务衍生因子: {name}")
            skipped.append(name)
            continue
        deps = DERIVED_DEPENDENCIES.get(name, [])
        if not all(dep in df.columns for dep in deps):
            skipped.append(name)
            continue
        df[name] = registry[name](df)
        built.append(name)

    logger.info(f"📐 财务衍生因子: 已构建 {len(built)} 个，跳过 {len(skipped)} 个")
    if built:
        logger.info(f"   构建列表: {', '.join(built)}")
    return df

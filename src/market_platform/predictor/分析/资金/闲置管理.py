"""
闲置资金管理模块 — 将闲置资金分配到黄金ETF、国债ETF、纳指ETF、国债逆回购、货币ETF

原理：
1. 策略仓位 = position (0~1)
2. 闲置资金 = 1 - position
3. 闲置资金按比例分配到五类资产
4. 日频再平衡，与实际调仓同步

数据源：
- 黄金ETF 518880 → akshare fund_etf_hist_sina
- 国债ETF 511010 → akshare fund_etf_hist_sina
- 纳指ETF 513100 → akshare fund_etf_hist_sina
- 货币ETF 511880 → akshare fund_etf_hist_sina
- 国债逆回购 → shibor O/N（macro_china_shibor_all）作为利率代理
"""

from __future__ import annotations

from market_platform.predictor.核心.路径.项目 import data_dir

from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
from loguru import logger

from market_platform.predictor.配置 import get_config
from market_platform.predictor.存储.数据访问 import load_series_df, save_series_df

RAW_DIR = data_dir() / "raw"


def get_cash_mgmt_config() -> dict:
    return get_config().get("cash_management", {})


def is_cash_mgmt_enabled() -> bool:
    return bool(get_cash_mgmt_config().get("enabled", False))


def _cache_path(name: str) -> Path:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    return RAW_DIR / name


def _load_cache(name: str) -> Optional[pd.DataFrame]:
    return load_series_df(name)


def _save_cache(df: pd.DataFrame, name: str):
    save_series_df(name, df)
    logger.info(f"💾 闲置资金数据已缓存: {_cache_path(name)} ({len(df)} 条)")


def _sanitize_etf_returns(df: pd.DataFrame, close_col: str, ret_col: str) -> pd.DataFrame:
    """
    修正 ETF 份额折算/拆分导致的价格跳变。
    折算日价格会骤降或骤升，但持有人资产不变，当日收益率应记为 0。
    """
    out = df.copy()
    close = out[close_col]
    ratio = close / close.shift(1)
    ret = close.pct_change()
    split_like = ((ratio < 0.4) | (ratio > 2.5) | (ret.abs() > 0.30)) & ratio.notna()
    n = int(split_like.sum())
    if n:
        logger.warning(f"ETF {close_col}: 修正 {n} 个疑似份额折算日（收益率置 0）")
    out[ret_col] = ret.where(~split_like, 0.0)
    return out


def _sanitize_money_market_returns(df: pd.DataFrame, close_col: str, ret_col: str) -> pd.DataFrame:
    """
    货币 ETF 年末分红除息：净值回落但持有人收到等额现金，总收益应≈0。
    未复权行情会把除息日记成大跌，需修正。
    """
    out = df.copy()
    dates = pd.to_datetime(out["date"])
    ret = out[ret_col].astype(float)
    div_ex = (dates.dt.month == 12) & (ret < -0.008)
    n = int(div_ex.sum())
    if n:
        logger.warning(f"货币ETF {close_col}: 修正 {n} 个年末除息日（收益率置 0）")
    out[ret_col] = ret.where(~div_ex, 0.0)
    return out


def fetch_etf_daily(symbol: str, name: str) -> pd.DataFrame:
    """拉取单只 ETF 日线"""
    import akshare as ak

    key = f"sh{symbol}" if not symbol.startswith(("sh", "sz")) else symbol
    raw = ak.fund_etf_hist_sina(symbol=key)
    raw["date"] = pd.to_datetime(raw["date"])
    raw = raw.sort_values("date").reset_index(drop=True)
    close_col = f"{name}_close"
    ret_col = f"{name}_ret"
    out = raw[["date", "close"]].rename(columns={"close": close_col})
    out[close_col] = out[close_col].astype(float)
    out[ret_col] = out[close_col].pct_change()
    out = _sanitize_etf_returns(out, close_col, ret_col)
    if name == "mm":
        out = _sanitize_money_market_returns(out, close_col, ret_col)
    return out


def _build_shibor_df() -> pd.DataFrame:
    """用 macro_china_shibor_all 构建隔夜利率序列"""
    import akshare as ak

    raw = ak.macro_china_shibor_all()
    raw["date"] = pd.to_datetime(raw["日期"])
    # O/N-定价 是百分数（如 1.4430 表示 1.443%）
    raw["shibor_on"] = pd.to_numeric(raw["O/N-定价"], errors="coerce") / 100.0
    out = raw[["date", "shibor_on"]].dropna(subset=["shibor_on"]).sort_values("date")
    return out.reset_index(drop=True)


def _build_repo_df(shibor_df: pd.DataFrame) -> pd.DataFrame:
    """
    用 shibor O/N 近似国债逆回购日收益率。
    逆回购利率 ≈ shibor * 0.85（逆回购通常低于 shibor）
    日收益率 = 利率 / 365
    """
    cfg = get_cash_mgmt_config()
    spread = float(cfg.get("repo_spread", 0.85))
    out = shibor_df.copy()
    out["repo_daily_ret"] = out["shibor_on"] * spread / 365.0
    return out


def fetch_cash_assets(
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> pd.DataFrame:
    """
    拉取并缓存所有现金管理类资产的历史日收益率。

    返回 DataFrame，包含：
      date, gold_ret, bond_ret, nasdaq_ret, mm_ret, repo_daily_ret
    """
    cfg = get_cash_mgmt_config()
    sources = cfg.get("sources", {})

    # 逐个获取
    frames: Dict[str, pd.DataFrame] = {}

    if sources.get("gold_etf", True):
        cache_file = cfg.get("gold_cache", "cash_gold_518880.parquet")
        symbol = cfg.get("gold_symbol", "518880")
        cached = _load_cache(cache_file)
        end_day = pd.Timestamp(end).normalize()
        if cached is not None and pd.Timestamp(cached["date"].max()).normalize() >= end_day:
            frames["gold"] = cached
        else:
            try:
                df = fetch_etf_daily(symbol, "gold")
                _save_cache(df, cache_file)
                frames["gold"] = df
                logger.info(f"🪙 黄金ETF: {len(df)} 条")
            except Exception as e:
                logger.warning(f"⚠️ 黄金ETF 获取失败: {e}")
                if cached is not None:
                    frames["gold"] = cached

    if sources.get("bond_etf", True):
        cache_file = cfg.get("bond_cache", "cash_bond_511010.parquet")
        symbol = cfg.get("bond_symbol", "511010")
        cached = _load_cache(cache_file)
        end_day = pd.Timestamp(end).normalize()
        if cached is not None and pd.Timestamp(cached["date"].max()).normalize() >= end_day:
            frames["bond"] = cached
        else:
            try:
                df = fetch_etf_daily(symbol, "bond")
                _save_cache(df, cache_file)
                frames["bond"] = df
                logger.info(f"📜 国债ETF: {len(df)} 条")
            except Exception as e:
                logger.warning(f"⚠️ 国债ETF 获取失败: {e}")
                if cached is not None:
                    frames["bond"] = cached

    if sources.get("nasdaq_etf", True):
        cache_file = cfg.get("nasdaq_cache", "cash_nasdaq_513100.parquet")
        symbol = cfg.get("nasdaq_symbol", "513100")
        cached = _load_cache(cache_file)
        end_day = pd.Timestamp(end).normalize()
        if cached is not None and pd.Timestamp(cached["date"].max()).normalize() >= end_day:
            frames["nasdaq"] = cached
        else:
            try:
                df = fetch_etf_daily(symbol, "nasdaq")
                _save_cache(df, cache_file)
                frames["nasdaq"] = df
                logger.info(f"🇺🇸 纳指ETF: {len(df)} 条")
            except Exception as e:
                logger.warning(f"⚠️ 纳指ETF 获取失败: {e}")
                if cached is not None:
                    frames["nasdaq"] = cached

    if sources.get("money_market_etf", True):
        cache_file = cfg.get("mm_cache", "cash_mm_511880.parquet")
        symbol = cfg.get("mm_symbol", "511880")
        cached = _load_cache(cache_file)
        end_day = pd.Timestamp(end).normalize()
        if cached is not None and pd.Timestamp(cached["date"].max()).normalize() >= end_day:
            frames["mm"] = cached
        else:
            try:
                df = fetch_etf_daily(symbol, "mm")
                _save_cache(df, cache_file)
                frames["mm"] = df
                logger.info(f"💰 货币ETF: {len(df)} 条")
            except Exception as e:
                logger.warning(f"⚠️ 货币ETF 获取失败: {e}")
                if cached is not None:
                    frames["mm"] = cached

    if sources.get("repo", True):
        cache_file = cfg.get("repo_cache", "cash_repo.parquet")
        cached = _load_cache(cache_file)
        end_day = pd.Timestamp(end).normalize()
        # 云端只读环境可能无 akshare：缓存足够新时直接用，失败再回退缓存
        if cached is not None and pd.Timestamp(cached["date"].max()).normalize() >= end_day:
            frames["repo"] = cached
        else:
            try:
                shibor_df = _build_shibor_df()
                repo_df = _build_repo_df(shibor_df)
                _save_cache(repo_df, cache_file)
                frames["repo"] = repo_df
                logger.info(f"🏦 逆回购代理: {len(repo_df)} 条 (shibor O/N 代理)")
            except Exception as e:
                logger.warning(f"⚠️ 逆回购代理获取失败: {e}")
                if cached is not None:
                    frames["repo"] = cached
                    logger.info(
                        "🏦 逆回购代理: 使用缓存 {} 条 (截至 {})",
                        len(cached),
                        str(cached["date"].max())[:10],
                    )

    # 合并所有资产到统一日期轴
    merged = None
    for key, df in frames.items():
        if merged is None:
            if key == "repo":
                merged = df[["date", "repo_daily_ret"]].copy()
            else:
                ret_col = f"{key}_ret"
                merged = df[["date", ret_col]].copy()
        else:
            if key == "repo":
                merged = merged.merge(df[["date", "repo_daily_ret"]], on="date", how="outer")
            else:
                ret_col = f"{key}_ret"
                merged = merged.merge(df[["date", ret_col]], on="date", how="outer")

    if merged is None:
        return pd.DataFrame(columns=["date"])

    merged = merged.sort_values("date").reset_index(drop=True)
    mask = (merged["date"] >= start) & (merged["date"] <= end)
    return merged.loc[mask].reset_index(drop=True)


SLEEVE_RET_COLS = {
    "csi300": "csi300_ret",
    "nasdaq": "nasdaq_ret",
    "gold": "gold_ret",
    "bond": "bond_ret",
    "mm": "mm_ret",
    "repo": "repo_daily_ret",
}


def fetch_all_sleeve_returns(
    start: pd.Timestamp,
    end: pd.Timestamp,
    *,
    index_df: Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    """
    六袖对齐日收益矩阵（unified_sharpe 用）。

    列: date, csi300_ret, nasdaq_ret, gold_ret, bond_ret, mm_ret, repo_daily_ret
    csi300_ret 与其它袖一致用同日 close.pct_change()（勿用未来 label_return）。
    """
    cash = fetch_cash_assets(start, end)
    if cash is None or cash.empty:
        return pd.DataFrame(columns=["date", *SLEEVE_RET_COLS.values()])

    out = cash.copy()
    out["date"] = pd.to_datetime(out["date"]).dt.normalize()

    if index_df is None:
        from market_platform.predictor.存储.数据访问 import load_index_features

        cfg = get_config()
        index_df = load_index_features(cfg["data"]["index_code"])
    idx = index_df.copy()
    idx["date"] = pd.to_datetime(idx["date"]).dt.normalize()
    if "close" in idx.columns:
        idx["csi300_ret"] = pd.to_numeric(idx["close"], errors="coerce").pct_change()
    else:
        idx["csi300_ret"] = np.nan

    out = out.merge(idx[["date", "csi300_ret"]], on="date", how="left")
    # 以指数交易日为主轴，保证与回测 df 对齐
    idx_days = idx[(idx["date"] >= pd.Timestamp(start).normalize()) & (idx["date"] <= pd.Timestamp(end).normalize())]
    out = idx_days[["date", "csi300_ret"]].merge(
        out.drop(columns=["csi300_ret"], errors="ignore"),
        on="date",
        how="left",
    )
    for col in SLEEVE_RET_COLS.values():
        if col not in out.columns:
            out[col] = np.nan
    return out.sort_values("date").reset_index(drop=True)


def get_portfolio_config() -> dict:
    return dict(get_config().get("portfolio", {}) or {})


def is_unified_sharpe_mode() -> bool:
    mode = str(get_portfolio_config().get("allocation_mode", "legacy")).strip().lower()
    return mode in {"unified_sharpe", "sharpe", "unified"}


def normalized_cash_weights() -> Dict[str, float]:
    cfg = get_cash_mgmt_config()
    weights = cfg.get("weights", {})
    w_gold = float(weights.get("gold", 0.165))
    w_bond = float(weights.get("bond", 0.165))
    w_nasdaq = float(weights.get("nasdaq", 0.33))
    w_repo = float(weights.get("repo", 0.17))
    w_mm = float(weights.get("money_market", 0.17))
    w_total = w_gold + w_bond + w_nasdaq + w_repo + w_mm
    if w_total <= 0:
        return {"gold": 0.0, "bond": 0.0, "nasdaq": 0.0, "repo": 0.0, "mm": 0.0}
    return {
        "gold": w_gold / w_total,
        "bond": w_bond / w_total,
        "nasdaq": w_nasdaq / w_total,
        "repo": w_repo / w_total,
        "mm": w_mm / w_total,
    }


CASH_ASSET_SPECS = (
    ("gold", "gold_ret", "黄金 ETF", "gold_symbol", "518880"),
    ("bond", "bond_ret", "国债 ETF", "bond_symbol", "511010"),
    ("nasdaq", "nasdaq_ret", "纳指 ETF", "nasdaq_symbol", "513100"),
    ("repo", "repo_daily_ret", "逆回购代理", None, "Shibor O/N"),
    ("mm", "mm_ret", "货币 ETF", "mm_symbol", "511880"),
)

CASH_KEYS = tuple(spec[0] for spec in CASH_ASSET_SPECS)

# 统一夏普六袖（组合绝对权重，不再拆「策略 / 闲置」）
SLEEVE_ASSET_SPECS = (
    ("csi300", "csi300_ret", "沪深300", None, "000300.SH"),
    ("nasdaq", "nasdaq_ret", "纳指 ETF", "nasdaq_symbol", "513100"),
    ("gold", "gold_ret", "黄金 ETF", "gold_symbol", "518880"),
    ("bond", "bond_ret", "国债 ETF", "bond_symbol", "511010"),
    ("mm", "mm_ret", "货币 ETF", "mm_symbol", "511880"),
    ("repo", "repo_daily_ret", "逆回购代理", None, "Shibor O/N"),
)
SLEEVE_KEYS = tuple(spec[0] for spec in SLEEVE_ASSET_SPECS)


def _first_valid_dates(cash_returns: pd.DataFrame) -> Dict[str, Optional[pd.Timestamp]]:
    """各收益列在原始数据中的首个有效日期"""
    out: Dict[str, Optional[pd.Timestamp]] = {}
    if cash_returns.empty:
        return out
    cr = cash_returns.sort_values("date")
    for col in [c for c in cr.columns if c != "date"]:
        valid = cr[cr[col].notna()]
        out[col] = pd.Timestamp(valid["date"].iloc[0]) if len(valid) else None
    return out


def align_cash_returns_to_dates(
    dates: pd.Series,
    cash_returns: pd.DataFrame,
) -> pd.DataFrame:
    """将现金资产日收益对齐到回测交易日；各品种在首个有效日之前为 NaN"""
    ddf = pd.DataFrame({"date": pd.to_datetime(dates)}).sort_values("date").reset_index(drop=True)
    # 统一到 ns，避免 merge_asof 因 datetime64[s]/[us] 报错
    ddf["date"] = pd.to_datetime(ddf["date"]).astype("datetime64[ns]")
    cr = cash_returns.sort_values("date").copy()
    cr["date"] = pd.to_datetime(cr["date"]).astype("datetime64[ns]")
    value_cols = [c for c in cr.columns if c != "date"]
    if cr.empty:
        for col in value_cols:
            ddf[col] = np.nan
        return ddf

    first_valid = _first_valid_dates(cr)
    aligned = pd.merge_asof(ddf, cr, on="date", direction="backward")
    for col in value_cols:
        start = first_valid.get(col)
        if start is None:
            aligned[col] = np.nan
        else:
            start_ns = pd.Timestamp(start).as_unit("ns")
            aligned.loc[aligned["date"] < start_ns, col] = np.nan
    return aligned


def _equity_from_returns(ret: pd.Series) -> pd.Series:
    """日收益复利净值：日期 T 为计入当日收益后的净值（与 (1+r).cumprod() 一致）。

    旧实现把首日标成 1.0（未计当日涨跌），收益从次日才开始乘，整条曲线错位一天。
    """
    out = pd.Series(np.nan, index=ret.index, dtype=float)
    valid = ret.notna()
    if not valid.any():
        return out
    first = int(valid.idxmax())
    cum = 1.0
    for i in range(first, len(ret)):
        r = ret.iloc[i]
        if pd.isna(r):
            out.iloc[i] = cum
            continue
        cum = cum * (1.0 + float(r))
        out.iloc[i] = cum
    return out


def _total_return_from_equity(equity: pd.Series) -> float:
    """相对起始资金 1.0 的累计收益（非曲线首末点之比）。"""
    valid = equity.dropna()
    if valid.empty:
        return 0.0
    return float(valid.iloc[-1] - 1.0)


def _equity_to_json(equity: pd.Series) -> list:
    return [None if pd.isna(v) else round(float(v), 6) for v in equity]


def build_cash_asset_curves(
    dates: pd.Series,
    cash_returns: pd.DataFrame,
    positions: Optional[pd.Series] = None,
    *,
    unified: bool = False,
) -> Dict[str, object]:
    """
    构建多资产净值曲线（与回测日期对齐）。

    - unified=False：闲置三袖参考曲线；含「实际闲置贡献」
    - unified=True：六袖各自满仓净值 + 等权六袖参考（不再拆策略/闲置）
    """
    aligned = align_cash_returns_to_dates(dates, cash_returns)
    first_valid = _first_valid_dates(cash_returns)
    cfg = get_cash_mgmt_config()

    if unified:
        specs = [
            spec for spec in SLEEVE_ASSET_SPECS
            if spec[1] in aligned.columns
        ]
        weights = {spec[0]: 1.0 / max(len(specs), 1) for spec in specs}
        combo_label = "等权六袖（参考）"
    else:
        weights = normalized_cash_weights()
        specs = [
            spec for spec in CASH_ASSET_SPECS
            if spec[1] in aligned.columns and weights.get(spec[0], 0.0) > 0
        ]
        combo_label = "配置组合（满仓闲置）"

    starts = [
        first_valid.get(spec[1])
        for spec in specs
        if first_valid.get(spec[1]) is not None
    ]
    if not starts:
        return {"enabled": False, "weights": weights, "series": [], "dates": [], "unified": unified}

    display_start = max(starts)
    trimmed = aligned[aligned["date"] >= display_start].reset_index(drop=True)
    if trimmed.empty:
        return {"enabled": False, "weights": weights, "series": [], "dates": [], "unified": unified}

    pos_trimmed = None
    if (not unified) and positions is not None and len(positions) == len(aligned):
        pos_trimmed = pd.Series(positions).reset_index(drop=True)
        pos_trimmed = pos_trimmed.iloc[aligned["date"] >= display_start].reset_index(drop=True)

    combo_ret = pd.Series(np.nan, index=trimmed.index, dtype=float)
    series: list[Dict[str, object]] = []

    for key, ret_col, label, symbol_key, default_symbol in specs:
        if ret_col not in trimmed.columns:
            continue
        if (not unified) and weights.get(key, 0.0) <= 0:
            continue
        ret = trimmed[ret_col]
        equity = _equity_from_returns(ret)
        symbol = cfg.get(symbol_key, default_symbol) if symbol_key else default_symbol
        series.append({
            "key": key,
            "label": label,
            "symbol": str(symbol),
            "weight": float(weights.get(key, 0.0)),
            "start_date": first_valid.get(ret_col).strftime("%Y-%m-%d")
            if first_valid.get(ret_col) is not None else None,
            "equity": _equity_to_json(equity),
            "total_return": _total_return_from_equity(equity),
        })

    for idx in trimmed.index:
        row_valid = all(
            pd.notna(trimmed.loc[idx, spec[1]])
            for spec in specs
            if spec[1] in trimmed.columns
        )
        if not row_valid:
            continue
        day_ret = 0.0
        for key, ret_col, *_ in specs:
            day_ret += float(weights.get(key, 0.0)) * float(trimmed.loc[idx, ret_col])
        combo_ret.loc[idx] = day_ret

    portfolio_equity = _equity_from_returns(combo_ret)
    series.append({
        "key": "portfolio",
        "label": combo_label,
        "symbol": "加权组合",
        "weight": 1.0,
        "start_date": display_start.strftime("%Y-%m-%d"),
        "equity": _equity_to_json(portfolio_equity),
        "total_return": _total_return_from_equity(portfolio_equity),
    })

    if pos_trimmed is not None and len(pos_trimmed) == len(trimmed):
        idle = (1.0 - pos_trimmed.astype(float).clip(0.0, 1.0))
        actual_ret = combo_ret * idle
        actual_equity = _equity_from_returns(actual_ret)
        series.append({
            "key": "actual",
            "label": "实际闲置贡献",
            "symbol": "按仓位缩放",
            "weight": None,
            "start_date": display_start.strftime("%Y-%m-%d"),
            "equity": _equity_to_json(actual_equity),
            "total_return": _total_return_from_equity(actual_equity),
        })

    out_dates = trimmed["date"].dt.strftime("%Y-%m-%d").tolist()
    return {
        "enabled": True,
        "unified": unified,
        "start_date": display_start.strftime("%Y-%m-%d"),
        "end_date": out_dates[-1] if out_dates else None,
        "dates": out_dates,
        "weights": weights,
        "series": series,
    }


ALLOCATION_LABELS = {
    "strategy": "沪深300 策略",
    "csi300": "沪深300",
    "gold": "黄金 ETF",
    "bond": "国债 ETF",
    "nasdaq": "纳指 ETF",
    "repo": "逆回购",
    "mm": "货币 ETF",
    "idle": "闲置现金",
    "idle_uninvested": "未投资闲置",
}


def get_cash_mgmt_active_start(cash_returns: Optional[pd.DataFrame] = None) -> Optional[pd.Timestamp]:
    """全部配置品种均有数据的首日（此前闲置资金不参与配置、不计收益）"""
    if cash_returns is None or cash_returns.empty:
        return None
    weights = normalized_cash_weights()
    first_valid = _first_valid_dates(cash_returns)
    starts: list[pd.Timestamp] = []
    for key, ret_col, *_ in CASH_ASSET_SPECS:
        if weights.get(key, 0.0) <= 0:
            continue
        d = first_valid.get(ret_col)
        if d is not None:
            starts.append(pd.Timestamp(d))
    return max(starts) if starts else None


def build_fund_allocation_history(
    dates: pd.Series,
    positions: pd.Series,
    cash_mgmt_enabled: bool = True,
    cash_returns: Optional[pd.DataFrame] = None,
    weight_history: Optional[List[Dict[str, float]]] = None,
    *,
    absolute_cash_weights: bool = False,
    strategy_label: Optional[str] = None,
) -> Dict[str, object]:
    """
    构建资金组成比例时间序列（各层占比之和 = 100%）。

    - legacy：策略仓位 = position；闲置按 cash 权重拆分
    - absolute_cash_weights=True：六袖统一账本（组合绝对权重，无「策略/闲置」二分）
    """
    pos = pd.Series(positions).astype(float).clip(0.0, 1.0).reset_index(drop=True)
    idle = (1.0 - pos).clip(0.0, 1.0)
    date_series = pd.to_datetime(dates).reset_index(drop=True)
    out_dates = date_series.dt.strftime("%Y-%m-%d").tolist()

    # ── 统一夏普：六袖堆叠 ──
    if absolute_cash_weights:
        use_dynamic = weight_history is not None and len(weight_history) == len(pos)
        layers: list[Dict[str, object]] = []
        for key in SLEEVE_KEYS:
            share_vals: list[float] = []
            for i in range(len(pos)):
                if use_dynamic:
                    w = weight_history[i] or {}
                    if key == "csi300" and "csi300" not in w:
                        share_vals.append(float(pos.iloc[i]))
                    else:
                        share_vals.append(float(w.get(key, 0.0) or 0.0))
                else:
                    share_vals.append(float(pos.iloc[i]) if key == "csi300" else 0.0)
            share = pd.Series(share_vals)
            layers.append({
                "key": key,
                "label": ALLOCATION_LABELS.get(key, key),
                "values": share.round(4).tolist(),
                "avg": float(share.mean()) if len(share) else 0.0,
                "avg_active": float(share.mean()) if len(share) else 0.0,
                "weight": float(share.mean()) if len(share) else 0.0,
                "weight_label": f"组合均 {float(share.mean()) * 100:.0f}%" if len(share) else "",
                "phase": "portfolio",
                "dynamic": use_dynamic,
            })
        return {
            "enabled": True,
            "cash_mgmt_enabled": True,
            "absolute_cash_weights": True,
            "allocation_mode": "unified_sharpe",
            "active_start": None,
            "dates": out_dates,
            "layers": layers,
            "idle_breakdown": {"enabled": False, "dates": [], "layers": []},
        }

    active_start = get_cash_mgmt_active_start(cash_returns) if cash_mgmt_enabled else None
    if active_start is not None:
        active_mask = date_series >= active_start
    else:
        active_mask = pd.Series(False, index=date_series.index)

    layers = [
        {
            "key": "strategy",
            "label": strategy_label or ALLOCATION_LABELS["strategy"],
            "values": pos.round(4).tolist(),
            "avg": float(pos.mean()) if len(pos) else 0.0,
        },
    ]

    if cash_mgmt_enabled:
        default_weights = normalized_cash_weights()
        use_dynamic = (
            weight_history is not None
            and len(weight_history) == len(idle)
        )

        uninvested_vals = [
            float(idle.iloc[i]) if not active_mask.iloc[i] else 0.0
            for i in range(len(idle))
        ]
        pre_mask = ~active_mask if active_start is not None else pd.Series(True, index=date_series.index)
        if active_start is None or pre_mask.any():
            uninvested = pd.Series(uninvested_vals)
            layers.append({
                "key": "idle_uninvested",
                "label": ALLOCATION_LABELS["idle_uninvested"],
                "values": uninvested.round(4).tolist(),
                "avg": float(uninvested.mean()) if len(uninvested) else 0.0,
                "avg_active": float(uninvested[pre_mask].mean()) if pre_mask.any() else 0.0,
                "phase": "pre_active",
            })

        for key in CASH_KEYS:
            share_vals = []
            weight_vals = []
            for i in range(len(idle)):
                if not active_mask.iloc[i]:
                    share_vals.append(0.0)
                    weight_vals.append(0.0)
                    continue
                w = float(
                    (weight_history[i].get(key, default_weights.get(key, 0.0)))
                    if use_dynamic
                    else default_weights.get(key, 0.0)
                )
                weight_vals.append(w)
                share_vals.append(float(idle.iloc[i] * w))
            share = pd.Series(share_vals)
            active_share = share[active_mask] if active_mask.any() else share
            avg_w = float(np.mean(weight_vals)) if weight_vals else 0.0
            layers.append({
                "key": key,
                "label": ALLOCATION_LABELS[key],
                "values": share.round(4).tolist(),
                "avg": float(share.mean()) if len(share) else 0.0,
                "avg_active": float(active_share.mean()) if len(active_share) else 0.0,
                "weight": avg_w,
                "weight_label": f"占闲置均 {avg_w * 100:.0f}%",
                "phase": "post_active",
                "dynamic": use_dynamic,
            })
    else:
        layers.append({
            "key": "idle",
            "label": ALLOCATION_LABELS["idle"],
            "values": idle.round(4).tolist(),
            "avg": float(idle.mean()) if len(idle) else 0.0,
        })

    return {
        "enabled": True,
        "cash_mgmt_enabled": cash_mgmt_enabled,
        "absolute_cash_weights": False,
        "allocation_mode": "legacy",
        "active_start": active_start.strftime("%Y-%m-%d") if active_start is not None else None,
        "dates": out_dates,
        "layers": layers,
        "idle_breakdown": _build_idle_breakdown_slice(
            out_dates, layers, active_start.strftime("%Y-%m-%d") if active_start is not None else None
        ),
    }


def _build_idle_breakdown_slice(
    dates: list[str],
    layers: list[Dict[str, object]],
    active_start: Optional[str],
) -> Dict[str, object]:
    """仅保留 active_start 之后的闲置品种拆分（右图专用）"""
    if not active_start:
        return {"enabled": False, "dates": [], "layers": []}
    cash_keys = set(CASH_KEYS)
    idx = next((i for i, d in enumerate(dates) if d >= active_start), 0)
    slice_dates = dates[idx:]
    slice_layers = []
    for layer in layers:
        if layer.get("key") not in cash_keys:
            continue
        vals = layer.get("values") or []
        slice_layers.append({
            **layer,
            "values": vals[idx:],
        })
    return {
        "enabled": len(slice_layers) > 0,
        "active_start": active_start,
        "dates": slice_dates,
        "layers": slice_layers,
    }


def _resolve_cash_day_row(
    cash_returns: pd.DataFrame,
    target_date: pd.Timestamp,
) -> Optional[pd.Series]:
    """按交易日取闲置资产收益行；非交易日回退最近一笔。"""
    target_date = pd.Timestamp(target_date).normalize()
    active_start = get_cash_mgmt_active_start(cash_returns)
    if active_start is not None and target_date < active_start:
        return None

    hits = cash_returns[cash_returns["date"] == target_date]
    if hits.empty:
        # 缺当日行返回 None（收益按 0），禁止回退前一日造成混日 PnL
        return None
    return hits.iloc[0]


def compute_cash_return_breakdown(
    position: float,
    row: pd.Series,
    cash_returns: pd.DataFrame,
    date_idx: Optional[pd.Timestamp] = None,
    weights: Optional[Dict[str, float]] = None,
) -> Dict[str, object]:
    """
    计算当日闲置资金收益及分品种贡献（对总资产的 period return）。

    Returns:
        total, weights, asset_returns, contributions (各品种对总资产收益的贡献)
    """
    empty_assets = {k: 0.0 for k in CASH_KEYS}
    empty = {
        "total": 0.0,
        "weights": weights or normalized_cash_weights(),
        "asset_returns": dict(empty_assets),
        "contributions": dict(empty_assets),
    }
    if not is_cash_mgmt_enabled():
        return empty

    idle = max(0.0, 1.0 - position)
    if idle <= 1e-6:
        return empty

    w = dict(weights or normalized_cash_weights())
    if sum(float(w.get(k, 0.0)) for k in CASH_KEYS) <= 0:
        return empty

    target_date = date_idx if date_idx is not None else pd.Timestamp(row.get("date"))
    hit = _resolve_cash_day_row(cash_returns, target_date)
    if hit is None:
        return {**empty, "weights": w}

    ret_col_map = {spec[0]: spec[1] for spec in CASH_ASSET_SPECS}
    asset_returns = {}
    for key in CASH_KEYS:
        col = ret_col_map[key]
        val = hit.get(col, 0.0)
        asset_returns[key] = float(val) if not pd.isna(val) else 0.0
    contributions = {
        key: idle * float(w.get(key, 0.0)) * asset_returns[key]
        for key in CASH_KEYS
    }
    total = sum(contributions.values())
    return {
        "total": float(total),
        "weights": w,
        "asset_returns": asset_returns,
        "contributions": contributions,
    }


def compute_cash_return(
    position: float,
    row: pd.Series,
    cash_returns: pd.DataFrame,
    date_idx: Optional[pd.Timestamp] = None,
    weights: Optional[Dict[str, float]] = None,
) -> float:
    """计算当日闲置资金加权收益率（对总资产的 period return）。"""
    detail = compute_cash_return_breakdown(
        position, row, cash_returns, date_idx=date_idx, weights=weights,
    )
    return float(detail.get("total", 0.0))


def extend_feature_df_with_cash(df: pd.DataFrame) -> pd.DataFrame:
    """
    在特征 DataFrame 上追加现金管理资产的净值序列（供回测引擎 preload）。
    """
    if not is_cash_mgmt_enabled():
        return df

    cfg = get_cash_mgmt_config()
    pf_col = cfg.get("portfolio_return_col", "cash_portfolio_return")

    dates = pd.to_datetime(df["date"])
    start, end = dates.min(), dates.max()
    cash_df = fetch_cash_assets(start, end)

    # 计算每日组合收益率（满仓闲置资金时的收益率）
    weights = normalized_cash_weights()

    if pf_col not in cash_df.columns:
        ret = pd.Series(0.0, index=cash_df.index)
        ret_col_map = {spec[0]: spec[1] for spec in CASH_ASSET_SPECS}
        for key in CASH_KEYS:
            col = ret_col_map[key]
            if col in cash_df.columns:
                ret += cash_df[col].fillna(0.0) * float(weights.get(key, 0.0))
        cash_df[pf_col] = ret

    out = df.copy()
    out["date"] = pd.to_datetime(out["date"])
    out = out.merge(cash_df[["date", pf_col]], on="date", how="left")
    out[pf_col] = out[pf_col].fillna(0.0)
    logger.info(
        f"🔗 现金管理组合收益率: {out[pf_col].notna().sum()}/{len(out)} 日有数据, "
        f"日均 {out[pf_col].mean()*10000:.2f}bp"
    )
    return out

"""日信号复合（生产）：week_end 聚合日频量价/因子/资金估值/新闻 → 周决策 Prompt。

不依赖日频 LLM 轨迹；每周只应被调用一次。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from loguru import logger

from market_platform.predictor.配置 import get_config

DAILY_FACTOR_COLS = [
    "momentum_1d",
    "momentum_5d",
    "momentum_10d",
    "momentum_20d",
    "volatility_20d",
    "rsi_14",
    "macd_hist",
    "volume_ratio",
    "boll_position",
    "atr_pct",
    "deviation_20",
    "close_position",
    "hl_spread",
]

VALUATION_COLS = [
    "pe_ttm",
    "pb",
    "pe_ttm_percentile",
    "pb_percentile",
    "dividend_yield",
    "earnings_yield",
    "pe_ttm_deviation",
    "pb_deviation",
]

FLOW_COLS = [
    "north_flow_net",
    "north_flow_ma_5",
    "north_flow_ma_20",
    "margin_balance",
    "margin_balance_change",
    "shibor_on",
    "shibor_change",
    "money_flow_change",
]

FLOW_COLS_EXTRA = [
    "etf_share_change_1d",
    "etf_share_change_5d",
    "if_basis_pct",
]

FACTOR_CN = {
    "momentum_1d": "1日动量",
    "momentum_5d": "5日动量",
    "momentum_10d": "10日动量",
    "momentum_20d": "20日动量",
    "volatility_20d": "20日波动",
    "rsi_14": "RSI14",
    "macd_hist": "MACD柱",
    "volume_ratio": "量比",
    "boll_position": "布林位置",
    "atr_pct": "ATR%",
    "deviation_20": "20日乖离",
    "close_position": "日内高低位置",
    "hl_spread": "振幅",
    "pe_ttm": "PE(TTM)",
    "pb": "PB",
    "pe_ttm_percentile": "PE历史分位",
    "pb_percentile": "PB历史分位",
    "dividend_yield": "股息率",
    "earnings_yield": "盈利收益率",
    "pe_ttm_deviation": "PE偏离",
    "pb_deviation": "PB偏离",
    "north_flow_net": "北向净流入",
    "north_flow_ma_5": "北向5日均",
    "north_flow_ma_20": "北向20日均",
    "margin_balance": "融资余额",
    "margin_balance_change": "融资余额变化",
    "shibor_on": "Shibor隔夜",
    "shibor_change": "Shibor变化",
    "money_flow_change": "资金流日变化率",
    "etf_share_change_1d": "ETF份额1日变化",
    "etf_share_change_5d": "ETF份额5日变化",
    "if_basis_pct": "股指期货基差%",
}

_COUNTRY_DUP = re.compile(
    r"(美国|中国|日本|韩国|欧元区|德国|法国|英国|瑞士|澳洲|加拿大|香港|台湾)\1"
)
_DATE_PAT = re.compile(
    r"(20\d{2}[-/.年]\d{1,2}([-/.月]\d{1,2}日?)?|\d{1,2}月\d{1,2}日|截至\d{1,2}月\d{1,2}日)"
)
_JUNK_TAIL = re.compile(r"[-–—]\s*(总金额|高配置|变动|初值|终值).*$")
_MULTISPACE = re.compile(r"\s+")


def is_weekly_decision_mode() -> bool:
    """硬切后默认 True：周频决策主路径。"""
    strat = get_config().get("backtest", {}).get("strategy", {}) or {}
    wd = strat.get("weekly_decision", {})
    if isinstance(wd, dict) and wd.get("enabled") is not None:
        return bool(wd.get("enabled"))
    dual = bool(strat.get("dual_freq_gate", False))
    wr = strat.get("weekly_rebalance", {}) or {}
    return (not dual) and bool(wr.get("on_week_end", wr.get("enabled", False)))


def week_label_cfg() -> dict:
    label = get_config().get("features", {}).get("label", {}) or {}
    thr = float(label.get("week_direction_threshold", 0.0075))
    return {
        "horizon": int(label.get("week_horizon", 5)),
        "threshold": max(thr, 1e-12),
    }


def is_week_end(date: pd.Timestamp, trading_dates: pd.Series | pd.DatetimeIndex) -> bool:
    """自然周最后一个交易日（通常周五）。"""
    d = pd.Timestamp(date).normalize()
    dates = pd.DatetimeIndex(pd.to_datetime(trading_dates)).normalize().unique().sort_values()
    if d not in dates:
        return False
    week_start = d - pd.Timedelta(days=int(d.dayofweek))
    week_end_cal = week_start + pd.Timedelta(days=6)
    in_week = dates[(dates >= week_start) & (dates <= week_end_cal)]
    if len(in_week) == 0:
        return False
    return d == in_week[-1]


def week_slice(df: pd.DataFrame, week_end: pd.Timestamp) -> pd.DataFrame:
    """取 week_end 所在自然周的全部交易日行（含 week_end）。"""
    data = df.copy()
    data["date"] = pd.to_datetime(data["date"]).dt.normalize()
    we = pd.Timestamp(week_end).normalize()
    week_start = we - pd.Timedelta(days=int(we.dayofweek))
    mask = (data["date"] >= week_start) & (data["date"] <= we)
    return data.loc[mask].sort_values("date")


def _path_features(closes: np.ndarray, volumes: Optional[np.ndarray] = None) -> Dict[str, float]:
    if len(closes) < 2:
        return {
            "path_week_ret": 0.0,
            "path_realized_vol": 0.0,
            "path_max_dd": 0.0,
            "path_skew": 0.0,
            "path_vol_change": 0.0,
        }
    rets = np.diff(closes) / np.clip(closes[:-1], 1e-12, None)
    equity = np.cumprod(1.0 + rets)
    peak = np.maximum.accumulate(equity)
    dd = equity / peak - 1.0
    vol_chg = 0.0
    if volumes is not None and len(volumes) >= 2:
        mid = len(volumes) // 2
        a, b = float(np.mean(volumes[:mid])), float(np.mean(volumes[mid:]))
        vol_chg = (b / a - 1.0) if a > 0 else 0.0
    skew = float(pd.Series(rets).skew()) if len(rets) >= 3 else 0.0
    if not np.isfinite(skew):
        skew = 0.0
    return {
        "path_week_ret": float(closes[-1] / closes[0] - 1.0),
        "path_realized_vol": float(np.std(rets)),
        "path_max_dd": float(dd.min()) if len(dd) else 0.0,
        "path_skew": skew,
        "path_vol_change": vol_chg,
    }


def _fmt_factor_line(c: str, mean_v: float, last_v: float) -> str:
    cn = FACTOR_CN.get(c, c)
    if "momentum" in c or c == "hl_spread" or c in (
        "money_flow_change",
        "etf_share_change_1d",
        "etf_share_change_5d",
        "if_basis_pct",
    ):
        return f"- {cn}: 周均 {100*mean_v:.2f}% / 末日 {100*last_v:.2f}%"
    if c in ("atr_pct", "deviation_20", "pe_ttm_deviation", "pb_deviation", "margin_balance_change"):
        return f"- {cn}: 周均 {mean_v:.2f} / 末日 {last_v:.2f}"
    if c == "rsi_14":
        return f"- {cn}: 周均 {mean_v:.1f} / 末日 {last_v:.1f}"
    if "percentile" in c or c in ("dividend_yield", "earnings_yield"):
        return f"- {cn}: 周均 {mean_v:.1f} / 末日 {last_v:.1f}"
    if c in ("pe_ttm", "pb", "shibor_on", "shibor_change"):
        return f"- {cn}: 周均 {mean_v:.2f} / 末日 {last_v:.2f}"
    if "north_flow" in c or c == "margin_balance":
        return f"- {cn}: 周均 {mean_v:.0f} / 末日 {last_v:.0f}"
    return f"- {cn}: 周均 {mean_v:.4f} / 末日 {last_v:.4f}"


def _agg_col_lines(grp: pd.DataFrame, cols: List[str]) -> Tuple[Dict[str, float], List[str]]:
    fac: Dict[str, float] = {}
    lines: List[str] = []
    for c in cols:
        if c not in grp.columns:
            continue
        vals = pd.to_numeric(grp[c], errors="coerce")
        if not vals.notna().any():
            continue
        mean_v = float(vals.mean())
        last_v = float(vals.iloc[-1]) if np.isfinite(vals.iloc[-1]) else mean_v
        fac[f"fac_mean_{c}"] = mean_v if np.isfinite(mean_v) else 0.0
        fac[f"fac_last_{c}"] = last_v if np.isfinite(last_v) else 0.0
        if np.isfinite(mean_v) and np.isfinite(last_v):
            lines.append(_fmt_factor_line(c, mean_v, last_v))
        # 北向额外给周累计
        if c == "north_flow_net" and vals.notna().any():
            s = float(vals.sum())
            fac["fac_sum_north_flow_net"] = s
            lines.append(f"- 北向本周累计: {s:.0f}")
    return fac, lines


def clean_news_item(text: str, *, blind: bool = False) -> Optional[str]:
    """清洗单条新闻：去重国名、截断脏尾巴、盲测去日期。"""
    s = str(text or "").strip()
    if not s or len(s) < 4:
        return None
    s = _COUNTRY_DUP.sub(r"\1", s)
    s = _MULTISPACE.sub("", s)
    s = _JUNK_TAIL.sub("", s).strip(" ；;,-")
    if blind:
        s = _DATE_PAT.sub("", s)
        s = re.sub(r"截至\s*", "", s)
        s = _MULTISPACE.sub("", s).strip(" ；;,-")
    # 过滤过短或纯占位
    if len(s) < 6:
        return None
    if s.count("(") != s.count(")"):
        s = re.sub(r"[（(][^）)]*$", "", s).strip(" ；;,-")
    if len(s) < 6:
        return None
    return s


def clean_weekly_news(raw: Optional[str], *, blind: bool = False, max_items: int = 5) -> Optional[str]:
    if not raw:
        return None
    items: List[str] = []
    seen = set()
    for part in str(raw).replace("\n", "；").split("；"):
        cleaned = clean_news_item(part, blind=blind)
        if not cleaned:
            continue
        key = cleaned[:40]
        if key in seen:
            continue
        seen.add(key)
        items.append(cleaned)
        if len(items) >= max_items:
            break
    return "；".join(items) if items else None


def _flow_cols_for_regime(regime: Optional[str] = None) -> List[str]:
    """按状态与配置挑选资金相关列；永不包含累计 money_flow。

    regime_router 关闭时：保持旧 weekly_v3 资金列（不含 money_flow_change/ETF/基差）。
    """
    from market_platform.predictor.分析.特征.状态路由 import (
        REGIME_RANGE,
        regime_router_cfg,
    )

    cfg = regime_router_cfg()
    classic = [
        "north_flow_net",
        "north_flow_ma_5",
        "north_flow_ma_20",
        "margin_balance",
        "margin_balance_change",
        "shibor_on",
        "shibor_change",
    ]
    if not cfg.get("enabled"):
        return classic

    cols = list(classic)
    if cfg.get("include_money_flow_change", True):
        cols.append("money_flow_change")

    extra: List[str] = []
    if cfg.get("include_etf", True):
        extra.extend(["etf_share_change_1d", "etf_share_change_5d"])
    if cfg.get("include_basis", True):
        extra.append("if_basis_pct")

    # 震荡市弱化额外资金/衍生品字段
    if regime == REGIME_RANGE:
        extra = []

    seen = set()
    out: List[str] = []
    for c in cols + extra:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


def aggregate_week_signals(
    df: pd.DataFrame,
    week_end: pd.Timestamp,
    *,
    blind: bool = False,
    regime: Optional[str] = None,
) -> Dict[str, Any]:
    """聚合截至 week_end 的当周日频信号。"""
    grp = week_slice(df, week_end)
    if grp.empty:
        raise ValueError(f"week_end={week_end} 无交易日数据")

    closes = grp["close"].astype(float).to_numpy()
    vols = grp["volume"].astype(float).to_numpy() if "volume" in grp.columns else None
    path = _path_features(closes, vols)

    fac_tech, tech_lines = _agg_col_lines(grp, DAILY_FACTOR_COLS)
    fac_val, val_lines = _agg_col_lines(grp, VALUATION_COLS)
    flow_cols = _flow_cols_for_regime(regime)
    fac_flow, flow_lines = _agg_col_lines(grp, flow_cols)

    news = None
    try:
        from market_platform.predictor.采集.外盘 import weekly_news_summary

        raw = weekly_news_summary(df, pd.Timestamp(week_end), max_items=12)
        news = clean_weekly_news(raw, blind=blind, max_items=5)
    except Exception as e:
        logger.debug("周新闻聚合失败: {}", e)

    end = grp.iloc[-1]
    snapshot_lines: List[str] = []
    # 盲测不写绝对收盘价水平，改写相对位置已在因子里；仍可写无日期含义的收盘
    if (not blind) and "close" in end.index and pd.notna(end["close"]):
        snapshot_lines.append(f"- 收盘: {float(end['close']):.2f}")

    return {
        "week_end": pd.Timestamp(week_end).normalize(),
        "n_days": int(len(grp)),
        "week_start": pd.Timestamp(grp["date"].iloc[0]).normalize(),
        **path,
        **fac_tech,
        **fac_val,
        **fac_flow,
        "tech_lines": tech_lines,
        "val_lines": val_lines,
        "flow_lines": flow_lines,
        "fac_lines": tech_lines,  # 兼容旧字段
        "snapshot_lines": snapshot_lines,
        "news": news,
        "end_row": end,
    }


def _regime_focus_lines(regime: str, end_row: pd.Series) -> List[str]:
    """状态相关的任务侧重与背离一句。"""
    from market_platform.predictor.分析.特征.状态路由 import (
        REGIME_BEAR,
        REGIME_BULL,
        price_flow_divergence,
        regime_zh,
    )

    lines = [f"【市场状态】{regime_zh(regime)}（规则路由，已平滑）"]
    if regime == REGIME_BULL:
        lines.append(
            "【状态侧重】关注动量延续、资金流变化率/北向/ETF，以及价资背离；趋势延续优先于均值回归。"
        )
        div = price_flow_divergence(end_row)
        if div:
            lines.append(f"【背离检测】价格涨但资金流出? {div}")
    elif regime == REGIME_BEAR:
        lines.append(
            "【状态侧重】关注超卖位置与反弹条件、资金流入信号、期货基差；谨防下跌中继假反弹。"
        )
        div = price_flow_divergence(end_row)
        if div:
            lines.append(f"【背离检测】价格跌但资金流入? {div}")
    else:
        lines.append(
            "【状态侧重】方向不明：弱化资金叙事，侧重基本面与外盘是否共振；"
            "不确定时请降低 confidence；若预计落在平盘带内可输出「平」。"
            "（量价波动/RSI 等由 Transformer 通道处理，勿假设本 Prompt 含技术序列。）"
        )
    return lines


def build_weekly_decision_prompt(
    df: pd.DataFrame,
    week_end: pd.Timestamp,
    *,
    index_name: str = "沪深300",
    blind: Optional[bool] = None,
    force_regime: Optional[str] = None,
) -> Tuple[str, Dict[str, Any]]:
    """正式周决策 Prompt：日信号复合 → 下周方向 + 幅度。

    force_regime: 对照实验可强制状态；None 时若 regime_router.enabled 则自动路由。
    """
    if blind is None:
        try:
            from market_platform.predictor.分析.提示词.构建器 import is_blind_prompt_enabled

            blind = bool(is_blind_prompt_enabled(False))
        except Exception:
            blind = True

    from market_platform.predictor.分析.特征.状态路由 import (
        REGIME_RANGE,
        regime_at,
        regime_router_cfg,
        regime_zh,
    )

    rr = regime_router_cfg()
    regime: Optional[str] = force_regime
    if regime is None and rr.get("enabled"):
        regime = regime_at(df, pd.Timestamp(week_end), smooth=True)
    if regime is None:
        regime = REGIME_RANGE if rr.get("enabled") else None

    agg = aggregate_week_signals(df, week_end, blind=blind, regime=regime)
    cfg = week_label_cfg()
    days = cfg["horizon"]
    n = int(agg["n_days"])

    if blind:
        header = [
            f"标的：{index_name}",
            f"决策日：T₀（本周最后交易日）；当周窗口：T₋{max(n-1, 0)} … T₀（共 {n} 个交易日）",
            "说明：未提供公历日期；请勿依据训练记忆识别具体年份或著名行情。",
        ]
    else:
        we = pd.Timestamp(agg["week_end"]).strftime("%Y-%m-%d")
        ws = pd.Timestamp(agg["week_start"]).strftime("%Y-%m-%d")
        header = [
            f"标的：{index_name}",
            f"决策日（本周最后交易日）：{we}；当周区间：{ws} → {we}（{n} 个交易日）",
        ]

    th = cfg["threshold"] * 100
    task_lines = [
        f"【任务】根据本周已实现的日频信号复合，判断随后 {days} 个交易日方向与幅度。",
        f"【周频三分类口径】涨 > +{th:.2f}%；跌 < -{th:.2f}%；平在 ±{th:.2f}% 内。",
    ]
    out_lines = [
        "【输出要求】",
        "只输出 JSON，不要其他文字：",
        (
            '{"week_direction":"涨|跌|平","week_return_pct":0.0,'
            '"week_confidence":0.0-1.0,"reason":"80字内依据"}'
        ),
    ]

    lines = [
        *header,
        "",
        *task_lines,
        "",
    ]

    # 状态路由块（enabled 或 force 时写入）
    if regime is not None and (rr.get("enabled") or force_regime is not None):
        lines.extend(_regime_focus_lines(str(regime), agg["end_row"]))
        lines.append("")

    # 量价/技术周聚合不进 LLM（由 Transformer 通道负责）
    lines.append("【通道说明】当周量价路径与技术因子周聚合不在本 Prompt；由本地 Transformer 处理。")

    lines.append("")
    lines.append("【估值周聚合】（周均 / 末日）")
    lines.extend(agg["val_lines"] or ["- （估值数据不足）"])

    flow_title = "【资金面周聚合】（周均 / 末日；北向另附本周累计）"
    if regime == "bull":
        flow_title = "【资金面周聚合】（趋势上升：提高资金流变化/北向权重；周均 / 末日）"
    elif regime == "bear":
        flow_title = "【资金面周聚合】（趋势下降：关注流入与基差；周均 / 末日）"
    elif regime == "range":
        flow_title = "【资金面周聚合】（震荡市：资金块仅供参考，权重宜低）"
    lines.append("")
    lines.append(flow_title)
    lines.extend(agg["flow_lines"] or ["- （资金面数据不足）"])

    if agg["snapshot_lines"]:
        lines.append("")
        lines.append("【周末快照】")
        lines.extend(agg["snapshot_lines"])

    lines.append("")
    lines.append("【本周宏观/市场新闻摘要】")
    if agg.get("news"):
        lines.append(f"- {agg['news']}")
    else:
        lines.append("- （无可用新闻摘要）")

    lines.append("")
    lines.extend(out_lines)
    prompt = "\n".join(lines)
    meta = {
        "week_end": str(pd.Timestamp(agg["week_end"]).date()),
        "week_start": str(pd.Timestamp(agg["week_start"]).date()),
        "n_days": n,
        "path_week_ret": agg["path_week_ret"],
        "prompt_kind": "weekly_decision",
        "blind": bool(blind),
        "has_magnitude": True,
        "regime": regime,
        "regime_zh": regime_zh(regime) if regime else None,
        "regime_router_enabled": bool(rr.get("enabled")),
    }
    # end_row 不进 meta（不可 JSON）
    return prompt, meta

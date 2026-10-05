"""Prompt 构建器 — 将七维基本面/资金 + 外盘/新闻 + 状态路由序列化为 LLM 文本。

量价技术指标不进 Prompt（由 Transformer 通道负责）。
"""

from __future__ import annotations


import json
import re
from typing import Dict, List, Optional

import pandas as pd

from market_platform.predictor.配置 import get_config

# 维度 key → 中文标题
DIMENSION_TITLES: Dict[str, str] = {
    "profitability": "盈利能力",
    "growth": "成长能力",
    "valuation": "估值",
    "leverage": "偿债能力",
    "cashflow": "现金流",
    "liquidity_macro": "宏观流动性",
    "market_flow": "资金面",
}

# 因子名 → (中文标签, 格式, 单位后缀)
FACTOR_SPECS: Dict[str, tuple] = {
    "idx_roe_w": ("加权ROE", "{:.2f}", "%"),
    "idx_roa_w": ("加权ROA", "{:.2f}", "%"),
    "idx_gross_margin_w": ("加权毛利率", "{:.2f}", "%"),
    "idx_net_margin_w": ("加权净利率", "{:.2f}", "%"),
    "idx_revenue_growth_w": ("营收同比增速", "{:.2f}", "%"),
    "idx_profit_growth_w": ("净利润同比增速", "{:.2f}", "%"),
    "pe_ttm": ("PE(TTM)", "{:.2f}", "倍"),
    "pe_static": ("PE(静态)", "{:.2f}", "倍"),
    "pe_median": ("PE中位数", "{:.2f}", "倍"),
    "pb": ("PB", "{:.2f}", "倍"),
    "pb_median": ("PB中位数", "{:.2f}", "倍"),
    "dividend_yield": ("股息率", "{:.2f}", "%"),
    "earnings_yield": ("盈利收益率", "{:.2f}", "%"),
    "pe_ttm_percentile": ("PE历史分位", "{:.0f}", "%"),
    "pb_percentile": ("PB历史分位", "{:.0f}", "%"),
    "peg_proxy": ("PEG近似", "{:.2f}", ""),
    "idx_debt_ratio_w": ("资产负债率", "{:.2f}", "%"),
    "idx_current_ratio_w": ("流动比率", "{:.2f}", ""),
    "idx_quick_ratio_w": ("速动比率", "{:.2f}", ""),
    "idx_ocf_per_share_w": ("每股经营现金流", "{:.2f}", "元"),
    "idx_ocf_to_profit_w": ("经营现金流/净利润", "{:.2f}", ""),
    "idx_asset_turnover_w": ("总资产周转率", "{:.2f}", "次"),
    "m2_yoy": ("M2同比", "{:.2f}", "%"),
    "m1_yoy": ("M1同比", "{:.2f}", "%"),
    "ppi_yoy": ("PPI同比", "{:.2f}", "%"),
    "pmi_mfg": ("制造业PMI", "{:.1f}", ""),
    "pmi_non_mfg": ("非制造业PMI", "{:.1f}", ""),
    "lpr_1y": ("1年期LPR", "{:.2f}", "%"),
    "lpr_5y": ("5年期LPR", "{:.2f}", "%"),
    "social_financing": ("社融增量", "{:.0f}", "亿"),
    "shibor_on": ("Shibor隔夜", "{:.2f}", "%"),
    "margin_balance": ("融资余额", "{:.0f}", "元"),
    "north_flow_net": ("北向净流入", "{:.0f}", "元"),
    "rsi_14": ("RSI(14)", "{:.1f}", ""),
    "momentum_5d": ("5期动量", "{:.2f}", "%"),
    "momentum_20d": ("20期动量", "{:.2f}", "%"),
    "macd_hist": ("MACD柱", "{:.4f}", ""),
    "volatility_20d": ("20期波动率", "{:.2f}", "%"),
    "margin_balance_change": ("融资余额变化", "{:.2f}", ""),
    "north_flow_ma_20": ("北向20期均值", "{:.0f}", "元"),
    "idx_roe_w_change": ("ROE变化", "{:.2f}", "%"),
    "idx_roe_w_ma4q": ("ROE(4季均值)", "{:.2f}", "%"),
    "idx_net_margin_w_change": ("净利率变化", "{:.2f}", "%"),
    "idx_revenue_growth_w_ma2q": ("营收增速(平滑)", "{:.2f}", "%"),
    "idx_profit_growth_w_ma2q": ("利润增速(平滑)", "{:.2f}", "%"),
    "pe_ttm_ma20": ("PE 20期均值", "{:.2f}", "倍"),
    "pe_ttm_deviation": ("PE偏离度", "{:.2f}", ""),
    "pb_ma20": ("PB 20期均值", "{:.2f}", "倍"),
    "pb_deviation": ("PB偏离度", "{:.2f}", ""),
    "pe_pb_ratio": ("PE/PB", "{:.2f}", ""),
    "pe_vs_median": ("PE减中位数", "{:.2f}", "倍"),
    "idx_debt_ratio_w_change": ("负债率变化", "{:.2f}", "%"),
    "idx_current_ratio_w_deviation": ("流动比率偏离", "{:.2f}", ""),
    "idx_ocf_to_profit_w_deviation": ("现金流质量偏离", "{:.2f}", ""),
    "idx_ocf_per_share_w_change": ("每股现金流变化", "{:.2f}", "%"),
    "m2_yoy_change": ("M2增速变化", "{:.2f}", "pct"),
    "pmi_mfg_ma3": ("PMI(3月均值)", "{:.1f}", ""),
    "ppi_yoy_change": ("PPI变化", "{:.2f}", "pct"),
    "lpr_spread": ("LPR期限利差", "{:.2f}", "%"),
    "social_financing_ma3": ("社融(3月均值)", "{:.0f}", "亿"),
    "shibor_ma10": ("Shibor 10日均", "{:.2f}", "%"),
    "shibor_change": ("Shibor变化", "{:.2f}", "bp"),
    "shibor_high": ("Shibor高位标记", "{:.0f}", ""),
    "north_flow_ma_5": ("北向5期均值", "{:.0f}", "元"),
    "north_flow_cumsum_20": ("北向20期累计", "{:.0f}", "元"),
    "north_flow_change": ("北向变化率", "{:.2f}", "%"),
    "margin_balance_ma20": ("融资余额20日均", "{:.0f}", "元"),
    "spx_close": ("标普500收盘", "{:.2f}", ""),
    "spx_ret_1d": ("标普500日涨跌", "{:.2f}", "%"),
    "hsi_close": ("恒生指数收盘", "{:.2f}", ""),
    "hsi_ret_1d": ("恒生指数日涨跌", "{:.2f}", "%"),
    "ndx_close": ("纳斯达克收盘", "{:.2f}", ""),
    "ndx_ret_1d": ("纳斯达克日涨跌", "{:.2f}", "%"),
}

LABEL_TO_TEXT = {-1: "跌", 0: "平", 1: "涨"}
TEXT_TO_LABEL = {v: k for k, v in LABEL_TO_TEXT.items()}


def get_llm_config() -> dict:
    return get_config().get("model", {}).get("llm", {})


def get_prompt_config() -> dict:
    return get_llm_config().get("prompt", {})


def is_blind_prompt_enabled(for_live: bool = False) -> bool:
    """回测默认盲测；今日研判由 blind_live 控制"""
    prompt_cfg = get_prompt_config()
    if not prompt_cfg.get("blind_mode", False):
        return False
    if for_live:
        return bool(prompt_cfg.get("blind_live", False))
    return True


def get_evaluation_since() -> str:
    return str(
        get_config().get("evaluation", {}).get("primary_since", "2020-01-01")
    )[:10]


def get_label_threshold() -> float:
    """与 features.label.direction_threshold 对齐的平盘带"""
    return float(get_config().get("features", {}).get("label", {}).get("direction_threshold", 0.004))


def _format_label_guidance(bar_unit: str) -> List[str]:
    """三分类口径说明（仅类别边界，不含历史频率或默认策略）"""
    th = get_label_threshold() * 100
    return [
        "【三分类口径 — 与回测标签一致】",
        f"预测对象：下一{bar_unit}收盘相对今日收盘的涨跌幅。",
        f"  - 涨：预计涨幅 > +{th:.2f}%",
        f"  - 跌：预计跌幅 < -{th:.2f}%",
        f"  - 平：预计涨跌幅落在 [{-th:.2f}%, +{th:.2f}%]",
        "",
        "【判断原则】",
        "  - 仅使用上文已提供的数据与特征，不得引用未给出的统计或先验",
        "  - 「平」须有窄幅波动的独立依据，不得作为「看不清」时的默认选项",
        "  - 有方向倾向时应输出涨或跌，并在 reasoning 中说明依据",
        "  - reasoning 须基于当期可观测信息，不得假设未提供的未来走势",
    ]


def _format_flat_context_hints(row: pd.Series, bar_unit: str) -> List[str]:
    """当期技术面摘要（只陈述已观测数据，不作预测引导）"""
    th = get_label_threshold() * 100
    lines = ["【当期技术面摘要】", f"  分类边界参考: ±{th:.2f}%（见三分类口径）"]

    hints = []
    mom5 = row.get("momentum_5d")
    if pd.notna(mom5):
        mom_pct = float(mom5) * 100
        hints.append(f"近5{bar_unit}动量 {mom_pct:+.2f}%")

    vol = row.get("volatility_20d")
    if pd.notna(vol):
        vol_pct = float(vol) * 100
        hints.append(f"20{bar_unit}波动率 {vol_pct:.2f}%")

    rsi = row.get("rsi_14")
    if pd.notna(rsi):
        hints.append(f"RSI {float(rsi):.1f}")

    macd = row.get("macd_hist")
    if pd.notna(macd):
        hints.append(f"MACD柱 {float(macd):+.4f}")

    if hints:
        lines.append("  " + "；".join(hints))
    else:
        lines.append("  （技术面数据不足）")
    return lines


def get_week_label_config() -> dict:
    return get_config().get("features", {}).get("label", {})


def is_dual_frequency_enabled() -> bool:
    prompt_cfg = get_prompt_config()
    dual = prompt_cfg.get("dual_frequency", {})
    if dual.get("enabled") is not None:
        return bool(dual.get("enabled"))
    return bool(
        get_config()
        .get("backtest", {})
        .get("strategy", {})
        .get("dual_freq_gate", False)
    )


def parse_direction_json(text: str) -> Dict:
    """解析模型 JSON 输出；失败时不默认「平」。

    兼容：markdown 围栏、`<think>` 思考块、截断 JSON（Gemini/Grok 常见）。
    """
    text = (text or "").strip()
    result: Dict = {
        "parse_ok": False,
        "label": None,
        "direction": None,
        "confidence": None,
        "week_label": None,
        "week_direction": None,
        "week_confidence": None,
        "week_return_pct": None,
        "reasoning": "",
        "raw": text,
    }
    if not text:
        return result

    stripped = text
    # Grok 等会先吐 <think>…</think> / 未闭合 think，再跟 JSON
    stripped = re.sub(r"<think>[\s\S]*?</think>", "", stripped, flags=re.IGNORECASE)
    stripped = re.sub(r"<thinking>[\s\S]*?</thinking>", "", stripped, flags=re.IGNORECASE)
    stripped = re.sub(r"<think>[\s\S]*?(?=\{)", "", stripped, flags=re.IGNORECASE)
    # 去掉 ```json ... ``` 围栏
    stripped = re.sub(r"```(?:json)?\s*", "", stripped, flags=re.IGNORECASE)
    stripped = re.sub(r"```", "", stripped).strip()

    data = None
    json_match = re.search(r"\{[\s\S]*\}", stripped)
    if json_match:
        try:
            data = json.loads(json_match.group())
        except (json.JSONDecodeError, TypeError, ValueError):
            data = None

    # 截断兜底：周频优先 week_direction；兼容旧日频 direction
    if data is None:
        week_m = re.search(r'"week_direction"\s*:\s*"(涨|跌|平)"', stripped)
        dir_m = re.search(r'"direction"\s*:\s*"(涨|跌|平)"', stripped)
        if not week_m and not dir_m:
            return result
        conf_m = re.search(r'"confidence"\s*:\s*([0-9]*\.?[0-9]+)', stripped)
        wconf_m = re.search(r'"week_confidence"\s*:\s*([0-9]*\.?[0-9]+)', stripped)
        mag_m = re.search(
            r'"week_return_pct"\s*:\s*(-?[0-9]*\.?[0-9]+)',
            stripped,
        )
        reason_m = re.search(
            r'"(?:reason|reasoning)"\s*:\s*"([^"]*)',
            stripped,
        )
        data = {
            "direction": dir_m.group(1) if dir_m else "",
            "confidence": float(conf_m.group(1)) if conf_m else None,
            "week_direction": week_m.group(1) if week_m else "",
            "week_confidence": float(wconf_m.group(1)) if wconf_m else None,
            "week_return_pct": float(mag_m.group(1)) if mag_m else None,
            "reasoning": (reason_m.group(1) if reason_m else "").strip(),
            "reason": (reason_m.group(1) if reason_m else "").strip(),
        }

    week_dir = str(data.get("week_direction", "") or "").strip()
    direction = str(data.get("direction", "") or "").strip()
    # 周频硬切：允许只有 week_direction
    if week_dir in TEXT_TO_LABEL and direction not in TEXT_TO_LABEL:
        direction = week_dir
    if direction not in TEXT_TO_LABEL and week_dir not in TEXT_TO_LABEL:
        return result
    if direction not in TEXT_TO_LABEL:
        direction = week_dir

    result["parse_ok"] = True
    result["label"] = TEXT_TO_LABEL[direction]
    result["direction"] = direction
    conf_raw = data.get("confidence", None)
    if conf_raw is None:
        conf_raw = data.get("week_confidence", 0.6)
    result["confidence"] = float(min(max(float(conf_raw or 0.6), 0.0), 1.0))
    result["reasoning"] = str(
        data.get("reasoning") or data.get("reason") or ""
    ).strip()

    if week_dir in TEXT_TO_LABEL:
        result["week_label"] = TEXT_TO_LABEL[week_dir]
        result["week_direction"] = week_dir
        wconf = data.get("week_confidence", result["confidence"])
        if wconf is None:
            wconf = result["confidence"]
        result["week_confidence"] = float(min(max(float(wconf), 0.0), 1.0))
        # 周决策主路径：主标签与置信度对齐周向
        result["label"] = result["week_label"]
        result["direction"] = week_dir
        result["confidence"] = result["week_confidence"]

    # 幅度：百分比点估计（可正可负）；与方向符号对齐
    mag_raw = data.get("week_return_pct", data.get("expected_return_pct"))
    if mag_raw is not None and mag_raw != "":
        try:
            mag = float(mag_raw)
            mag = max(-50.0, min(50.0, mag))
            lab = result.get("week_label")
            if lab is None:
                lab = result.get("label")
            if lab is not None and int(lab) != 0 and mag != 0:
                if int(lab) > 0 and mag < 0:
                    mag = abs(mag)
                elif int(lab) < 0 and mag > 0:
                    mag = -abs(mag)
            elif lab is not None and int(lab) > 0 and mag == 0:
                mag = 0.1
            elif lab is not None and int(lab) < 0 and mag == 0:
                mag = -0.1
            result["week_return_pct"] = float(mag)
        except (TypeError, ValueError):
            pass
    return result


def get_week_horizon() -> int:
    return int(get_week_label_config().get("week_horizon", 5))


def get_week_threshold() -> float:
    cfg = get_week_label_config()
    daily = float(cfg.get("direction_threshold", 0.005))
    return float(cfg.get("week_direction_threshold", daily * 2.5))


def infer_week_direction(row: pd.Series) -> int:
    """
    从中期技术面推断未来一周方向（API 缓存无 week_direction 时的回退）
    """
    score = 0.0
    mom20 = row.get("momentum_20d")
    if pd.notna(mom20):
        m = float(mom20)
        if m > 0.008:
            score += 1.0
        elif m < -0.008:
            score -= 1.0

    mom5 = row.get("momentum_5d")
    if pd.notna(mom5):
        m5 = float(mom5)
        if m5 > 0.012:
            score += 0.5
        elif m5 < -0.012:
            score -= 0.5

    close = row.get("close")
    sma60 = row.get("sma_60")
    if pd.notna(close) and pd.notna(sma60) and float(sma60) > 0:
        rel = float(close) / float(sma60) - 1
        if rel > 0.02:
            score += 1.0
        elif rel < -0.02:
            score -= 1.0
        elif rel > 0.005:
            score += 0.5
        elif rel < -0.005:
            score -= 0.5

    if score >= 1.5:
        return 1
    if score <= -1.5:
        return -1
    return 0


def _format_week_label_guidance() -> List[str]:
    """周频三分类口径（仅边界定义）"""
    th = get_week_threshold() * 100
    days = get_week_horizon()
    return [
        f"【周频三分类口径 — 随后 {days} 个交易日累计涨跌】",
        f"  - 周涨：预计累计涨幅 > +{th:.2f}%",
        f"  - 周跌：预计累计跌幅 < -{th:.2f}%",
        f"  - 周平：预计累计涨跌幅落在 [{-th:.2f}%, +{th:.2f}%]",
    ]


def _format_dual_freq_task() -> List[str]:
    days = get_week_horizon()
    return [
        "【双通道研判任务】",
        "1. 日频：判断下一交易日涨/跌/平（口径见上文）",
        f"2. 周频：判断随后 {days} 个交易日累计涨/跌/平（口径见上文）",
        "  日频与周频可不一致；若冲突须在 reasoning 中说明依据。",
    ]


def _format_output_requirements() -> List[str]:
    th = get_label_threshold() * 100
    if is_dual_frequency_enabled():
        wth = get_week_threshold() * 100
        days = get_week_horizon()
        return [
            "【输出要求】",
            "请严格按 JSON 输出，不要其他文字：",
            (
                '{"direction":"涨|跌|平","confidence":0.0-1.0,'
                '"week_direction":"涨|跌|平","week_confidence":0.0-1.0,'
                '"reasoning":"80字内，含日周是否一致"}'
            ),
            "  - direction / week_direction 只能是「涨」「跌」「平」",
            f"  - direction 对应下一交易日三分类口径（±{th:.2f}%）",
            f"  - week_direction 对应随后 {days} 日累计三分类口径（±{wth:.2f}%）",
            "  - reasoning 须说明日频与周频是否一致及依据",
        ]
    return [
        "【输出要求】",
        "请严格按 JSON 输出，不要其他文字：",
        '{"direction":"涨|跌|平","confidence":0.0-1.0,"reasoning":"50字内研判理由"}',
        "  - direction 只能是「涨」「跌」「平」之一",
        f"  - 分类边界见上文三分类口径（±{th:.2f}%）",
        "  - reasoning 须基于已提供信息，说明判断依据",
    ]


def get_prompt_lookback(prompt_cfg: Optional[dict] = None) -> int:
    """按 bar_freq 返回 Prompt 走势回看 bar 数"""
    prompt_cfg = prompt_cfg or get_prompt_config()
    bar_freq = get_config().get("data", {}).get("bar_freq", "daily")
    if bar_freq == "weekly":
        return int(prompt_cfg.get("lookback_weeks", 12))
    return int(prompt_cfg.get("lookback_days", 30))



def _factor_item(name: str, value) -> Optional[Dict]:
    """因子结构化条目，供 Web 信息窗口展示"""
    if pd.isna(value):
        return None
    spec = FACTOR_SPECS.get(name)
    raw = float(value)
    if spec is None:
        return {"key": name, "label": name, "display": f"{raw:.4g}", "unit": "", "raw": raw}
    label, fmt, unit = spec
    if name in {
        "momentum_5d", "momentum_20d", "idx_roe_w_change", "margin_balance_change",
        "idx_net_margin_w_change", "idx_ocf_per_share_w_change", "north_flow_change",
        "spx_ret_1d", "hsi_ret_1d", "ndx_ret_1d", "earnings_yield",
    }:
        display = fmt.format(raw * 100)
        shown = raw * 100
    else:
        display = fmt.format(raw)
        shown = raw
    return {"key": name, "label": label, "display": display, "unit": unit, "raw": shown}


def _build_trend_payload(window: pd.DataFrame, bar_unit: str) -> Dict:
    bars: List[Dict] = []
    summary: List[str] = []
    if window.empty or "close" not in window.columns:
        return {"bars": bars, "summary": summary, "lookback": 0}

    last_idx = len(window) - 1
    for i, (_, row) in enumerate(window.iterrows()):
        close = row.get("close")
        if pd.isna(close):
            continue
        ret = None
        if i > 0:
            prev = window.iloc[i - 1].get("close")
            if pd.notna(prev) and prev != 0:
                ret = float(close) / float(prev) - 1
        elif i < last_idx:
            ret = row.get("label_return")
            if pd.notna(ret):
                ret = float(ret)
        bars.append({
            "date": pd.Timestamp(row["date"]).strftime("%Y-%m-%d"),
            "close": float(close),
            "return_pct": None if i == last_idx else (float(ret) if pd.notna(ret) else None),
            "is_current": i == last_idx,
        })

    last = window.iloc[-1]
    close = float(last["close"])
    include_tech = bool(get_config().get("model", {}).get("llm", {}).get("prompt", {}).get("include_technical", True))
    if include_tech:
        if "sma_20" in window.columns and pd.notna(last.get("sma_20")):
            sma20 = float(last["sma_20"])
            summary.append(f"相对20{bar_unit}均线 {(close / sma20 - 1) * 100:+.2f}%")
        if "sma_60" in window.columns and pd.notna(last.get("sma_60")):
            sma60 = float(last["sma_60"])
            summary.append(f"相对60{bar_unit}均线 {(close / sma60 - 1) * 100:+.2f}%")
        if "rsi_14" in window.columns and pd.notna(last.get("rsi_14")):
            summary.append(f"RSI(14) {float(last['rsi_14']):.1f}")
        if "momentum_20d" in window.columns and pd.notna(last.get("momentum_20d")):
            summary.append(f"20期动量 {float(last['momentum_20d']) * 100:+.2f}%")
    if len(window) >= 4:
        recent_rets = window["close"].pct_change().dropna().tail(4)
        if not recent_rets.empty:
            up = int((recent_rets > 0).sum())
            down = int((recent_rets < 0).sum())
            summary.append(f"近4{bar_unit} {up}涨{down}跌")

    return {"bars": bars, "summary": summary, "lookback": len(bars)}


def _build_overseas_payload(row: pd.Series, window: Optional[pd.DataFrame] = None) -> Dict:
    """外盘完整快照 + 回看窗口序列"""
    indices: List[Dict] = []
    specs = [
        ("spx", "标普500"),
        ("ndx", "纳斯达克"),
        ("hsi", "恒生指数"),
    ]
    for prefix, name in specs:
        close_col = f"{prefix}_close"
        ret_col = f"{prefix}_ret_1d"
        if close_col not in row.index and ret_col not in row.index:
            continue

        bars: List[Dict] = []
        if window is not None and close_col in window.columns:
            for _, wrow in window.iterrows():
                close = wrow.get(close_col)
                if pd.isna(close):
                    continue
                ret = wrow.get(ret_col) if ret_col in window.columns else None
                bars.append({
                    "date": pd.Timestamp(wrow["date"]).strftime("%Y-%m-%d"),
                    "close": float(close),
                    "return_pct": float(ret) if pd.notna(ret) else None,
                })

        close_val = row.get(close_col)
        ret_val = row.get(ret_col)
        window_ret = None
        if bars and len(bars) >= 2:
            first, last = bars[0]["close"], bars[-1]["close"]
            if first:
                window_ret = last / first - 1

        indices.append({
            "key": prefix,
            "name": name,
            "close": float(close_val) if pd.notna(close_val) else None,
            "return_1d": float(ret_val) if pd.notna(ret_val) else None,
            "window_return": window_ret,
            "bars": bars,
        })

    return {"indices": indices}


def _build_news_payload(row: pd.Series) -> Optional[Dict]:
    from market_platform.predictor.采集.外盘 import load_macro_news_for_date

    summary = row.get("macro_news_summary")
    count = row.get("macro_news_count")
    day = pd.Timestamp(row["date"]).normalize()
    items = load_macro_news_for_date(day)

    if not items and (summary is None or (isinstance(summary, float) and pd.isna(summary))):
        return None

    text = ""
    if summary is not None and not (isinstance(summary, float) and pd.isna(summary)):
        text = str(summary).strip()

    return {
        "date": day.strftime("%Y-%m-%d"),
        "summary": text or None,
        "count": int(count) if pd.notna(count) else len(items),
        "items": items,
    }


def _build_dimensions_payload(row: pd.Series) -> List[Dict]:
    sections: List[Dict] = []
    for dim_key, factors in _collect_dimension_factors().items():
        items = []
        for name in factors:
            if name not in row.index:
                continue
            item = _factor_item(name, row[name])
            if item:
                items.append(item)
        if items:
            sections.append({
                "key": dim_key,
                "title": DIMENSION_TITLES.get(dim_key, dim_key),
                "factors": items,
            })
    return sections


def _build_technical_payload(row: pd.Series) -> List[Dict]:
    prompt_cfg = get_prompt_config()
    tech_factors = prompt_cfg.get("technical_factors", [
        "rsi_14", "momentum_5d", "momentum_20d", "macd_hist", "volatility_20d",
    ])
    items = []
    for name in tech_factors:
        if name not in row.index:
            continue
        item = _factor_item(name, row[name])
        if item:
            items.append(item)
    return items


def build_preview_payload(
    df: pd.DataFrame,
    date: Optional[str] = None,
) -> Dict:
    """构建研判信息窗口所需的结构化数据"""
    data = df.sort_values("date").reset_index(drop=True)
    idx, resolved = resolve_bar_index(data, date=date)
    index_name, bar_unit = _resolve_index_meta()
    row = data.iloc[idx]
    prompt_cfg = get_prompt_config()
    cfg = get_config()
    lookback = get_prompt_lookback(prompt_cfg)
    start = max(0, idx - lookback + 1)
    window = data.iloc[start: idx + 1]

    weekly_decision = False
    try:
        from market_platform.predictor.分析.特征.日信号复合 import (
            build_weekly_decision_prompt,
            is_weekly_decision_mode,
        )

        weekly_decision = is_weekly_decision_mode()
        if weekly_decision:
            prompt, _ = build_weekly_decision_prompt(
                data, pd.Timestamp(row["date"]), index_name=index_name or "沪深300",
            )
        else:
            prompt = build_prompt_from_row(
                row, index_name=index_name, bar_unit=bar_unit, df=data, idx=idx,
            )
    except Exception:
        prompt = build_prompt_from_row(
            row, index_name=index_name, bar_unit=bar_unit, df=data, idx=idx,
        )

    close = float(row["close"]) if pd.notna(row.get("close")) else None

    # 策略/模型元信息（供前端研判信息窗口展示）
    llm_cfg = get_llm_config()
    providers = llm_cfg.get("providers", {}) or {}
    active_api = llm_cfg.get("active_api") or "deepseek"
    active_model = (providers.get(active_api) or {}).get("model") or active_api
    regime_name = None
    regime_label = None
    try:
        from market_platform.predictor.分析.特征.状态路由 import regime_at, regime_router_cfg, regime_zh

        rr = regime_router_cfg()
        regime_enabled = bool(rr.get("enabled"))
        if regime_enabled:
            regime_name = regime_at(data, pd.Timestamp(row["date"]), smooth=True)
            regime_label = regime_zh(regime_name)
    except Exception:
        regime_enabled = bool((prompt_cfg.get("regime_router") or {}).get("enabled", False))

    decision_freq = "weekly" if weekly_decision else ("weekly" if bar_unit == "周" else "daily")
    include_technical = bool(prompt_cfg.get("include_technical", False))
    payload: Dict = {
        "meta": {
            "index_code": cfg.get("data", {}).get("index_code", "000300.SH"),
            "index_name": index_name,
            "date": pd.Timestamp(row["date"]).strftime("%Y-%m-%d"),
            "requested_date": date,
            "date_aligned": resolved,
            "close": close,
            "bar_unit": bar_unit,
            "bar_freq": cfg.get("data", {}).get("bar_freq", "daily"),
            "lookback": lookback,
            "prompt_version": prompt_cfg.get("version", "v1"),
            "dual_frequency": False if weekly_decision else is_dual_frequency_enabled(),
            "weekly_decision": weekly_decision,
            "decision_freq": decision_freq,
            "predict_horizon": "下一周" if weekly_decision or bar_unit == "周" else "下一交易日",
            "daily_threshold_pct": round(get_label_threshold() * 100, 2),
            "week_threshold_pct": round(get_week_threshold() * 100, 2),
            "week_horizon": get_week_horizon(),
            "active_api": active_api,
            "active_model": active_model,
            "regime_router": regime_enabled,
            "regime": regime_name,
            "regime_zh": regime_label,
            "classification_mode": "weekly_ternary" if weekly_decision else "daily_ternary",
            "classification_labels": ["涨", "跌", "平"],
            "include_technical": include_technical,
        },
        "trend": _build_trend_payload(window, bar_unit),
        "overseas": _build_overseas_payload(row, window=window),
        "news": _build_news_payload(row),
        "dimensions": _build_dimensions_payload(row),
        # 技术面留给本地 Transformer，默认不进 LLM 研判窗口
        "technical": _build_technical_payload(row) if include_technical else [],
        "task": prompt_cfg.get("task_instruction", ""),
        "prompt": prompt,
        "idx": idx,
    }

    try:
        from market_platform.predictor.接口.策略摘要 import build_strategy_summary

        summary = build_strategy_summary()
        payload["strategy"] = {
            "headline": summary.get("headline"),
            "philosophy": summary.get("philosophy"),
            "tags": summary.get("tags", []),
            "notes": (summary.get("notes") or [])[:4],
            "position_mode": summary.get("position_mode"),
            "position_cycle": summary.get("position_cycle"),
            "classification": summary.get("classification"),
            "prompt_label": summary.get("prompt_label"),
            "active_model": summary.get("active_model"),
            "include_technical": summary.get("include_technical"),
            "transformer_enabled": summary.get("transformer_enabled"),
            "base_position": summary.get("base_position"),
            "bull_regime_base": summary.get("bull_regime_base"),
            "bear_regime_base": summary.get("bear_regime_base"),
            "return_calibration": summary.get("return_calibration"),
        }
    except Exception:
        payload["strategy"] = {}

    if weekly_decision:
        payload["meta"]["bar_unit"] = "周"
        # 实际标签优先周向
        if pd.notna(row.get("label_week_direction")):
            label = int(row["label_week_direction"])
            payload["meta"]["actual_label"] = label
            payload["meta"]["actual_text"] = LABEL_TO_TEXT.get(label)
        elif pd.notna(row.get("label_direction")):
            label = int(row["label_direction"])
            payload["meta"]["actual_label"] = label
            payload["meta"]["actual_text"] = LABEL_TO_TEXT.get(label)
    elif pd.notna(row.get("label_direction")):
        label = int(row["label_direction"])
        payload["meta"]["actual_label"] = label
        payload["meta"]["actual_text"] = LABEL_TO_TEXT.get(label)
    return payload


def _format_factor(name: str, value) -> Optional[str]:
    if pd.isna(value):
        return None
    spec = FACTOR_SPECS.get(name)
    if spec is None:
        return f"{name}={float(value):.4g}"
    label, fmt, unit = spec
    if name in {"momentum_5d", "momentum_20d", "idx_roe_w_change", "margin_balance_change",
                "idx_net_margin_w_change", "idx_ocf_per_share_w_change", "north_flow_change",
                "spx_ret_1d", "hsi_ret_1d", "ndx_ret_1d"}:
        text = fmt.format(float(value) * 100)
    elif name == "earnings_yield":
        text = fmt.format(float(value) * 100)
    else:
        text = fmt.format(float(value))
    return f"{label} {text}{unit}"


def _format_section(title: str, factor_names: List[str], row: pd.Series) -> str:
    lines = []
    for name in factor_names:
        if name not in row.index:
            continue
        formatted = _format_factor(name, row[name])
        if formatted:
            lines.append(f"  - {formatted}")
    if not lines:
        return ""
    return f"【{title}】\n" + "\n".join(lines)


def _collect_dimension_factors() -> Dict[str, List[str]]:
    """从 config dimensions 收集每维的 raw + derived 因子"""
    fin = get_config().get("features", {}).get("financial", {})
    dimensions = fin.get("dimensions", {})
    result: Dict[str, List[str]] = {}
    for dim_key, dim_cfg in dimensions.items():
        if not isinstance(dim_cfg, dict) or not dim_cfg.get("enabled", True):
            continue
        factors = list(dim_cfg.get("raw_factors", [])) + list(dim_cfg.get("derived_factors", []))
        if factors:
            result[dim_key] = factors
    return result


def _resolve_index_meta() -> tuple[str, str]:
    cfg = get_config()
    index_code = cfg.get("data", {}).get("index_code", "000300.SH")
    index_names = {"000300.SH": "沪深300", "000001.SH": "上证指数", "399001.SZ": "深证成指"}
    bar_unit = "周" if cfg.get("data", {}).get("bar_freq") == "weekly" else "日"
    return index_names.get(index_code, "沪深300"), bar_unit


def _format_overseas_section(row: pd.Series, window: Optional[pd.DataFrame] = None) -> str:
    lines = []
    for col, label in [
        ("spx_ret_1d", "标普500"),
        ("ndx_ret_1d", "纳斯达克"),
        ("hsi_ret_1d", "恒生指数"),
    ]:
        if col in row.index and pd.notna(row.get(col)):
            lines.append(f"  - {label}最新日涨跌 {float(row[col]) * 100:+.2f}%")

    if window is not None:
        for col, label in [("spx_close", "标普500"), ("hsi_close", "恒生指数")]:
            if col not in window.columns:
                continue
            series = window[col].dropna()
            if len(series) >= 2:
                wret = float(series.iloc[-1]) / float(series.iloc[0]) - 1
                lines.append(f"  - {label}近{len(series)}期累计 {wret * 100:+.2f}%")

    if not lines:
        return ""
    return "【外盘参考】\n" + "\n".join(lines)


def _blind_time_label(offset: int) -> str:
    """盲测用相对时点标签：offset=0 为当前观测日，负数为历史"""
    if offset == 0:
        return "T₀"
    return f"T{offset}"


def _format_news_section(row: pd.Series, blind: bool = False) -> str:
    if blind:
        return "【宏观新闻与事件】\n  - 盲测已隐藏，避免事件/日期锚定训练记忆"
    summary = row.get("macro_news_summary")
    if summary is None or (isinstance(summary, float) and pd.isna(summary)):
        return ""
    text = str(summary).strip()
    if not text:
        return ""
    count = row.get("macro_news_count")
    count_text = f"（{int(count)}条）" if pd.notna(count) else ""
    return f"【宏观新闻与事件{count_text}】\n  - {text}"


def build_prompt_from_window(
    df: pd.DataFrame,
    idx: int,
    index_name: Optional[str] = None,
    bar_unit: Optional[str] = None,
    blind: bool = False,
) -> str:
    """综合研判 Prompt：七维截面因子 + 外盘/新闻 + 状态路由（不含量价技术）。"""
    prompt_cfg = get_prompt_config()
    if index_name is None or bar_unit is None:
        default_name, default_unit = _resolve_index_meta()
        index_name = index_name or default_name
        bar_unit = bar_unit or default_unit

    lookback = get_prompt_lookback(prompt_cfg)
    start = max(0, idx - lookback + 1)
    window = df.iloc[start: idx + 1]
    row = df.iloc[idx]

    date_str = "T₀（盲测：不提供公历日期）" if blind else pd.Timestamp(row["date"]).strftime("%Y-%m-%d")

    sections = [
        f"# {index_name} 综合研判请求",
        f"观测时点: {date_str}",
        f"分析频率: {bar_unit}频",
        "说明: 量价技术指标由本地 Transformer 通道处理，本 Prompt 不含收盘序列/技术因子。",
        "",
    ]

    if blind:
        sections.extend([
            "【盲测约束】",
            "  - 未提供任何公历日期、年份或著名行情名称",
            "  - 不得根据训练记忆推断历史时期；仅依据当期七维因子、外盘、新闻与状态提示判断",
            "",
        ])

    overseas = _format_overseas_section(row, window=window)
    if overseas:
        sections.append(overseas)
        sections.append("")

    news = _format_news_section(row, blind=blind)
    if news:
        sections.append(news)
        sections.append("")

    dim_factors = _collect_dimension_factors()
    for dim_key, factors in dim_factors.items():
        title = DIMENSION_TITLES.get(dim_key, dim_key)
        section = _format_section(title, factors, row)
        if section:
            sections.append(section)
            sections.append("")

    # 技术面仅当显式打开时写入（默认关；量价主路径在 Transformer）
    if prompt_cfg.get("include_technical", False):
        tech_factors = prompt_cfg.get("technical_factors", [
            "rsi_14", "momentum_5d", "momentum_20d", "macd_hist", "volatility_20d",
        ])
        section = _format_section("技术面（当期）", tech_factors, row)
        if section:
            sections.append(section)
            sections.append("")

    # V4 状态路由：日频主路径注入市场状态块
    try:
        from market_platform.predictor.分析.特征.日信号复合 import _regime_focus_lines
        from market_platform.predictor.分析.特征.状态路由 import regime_at, regime_router_cfg

        rr = regime_router_cfg()
        if rr.get("enabled"):
            regime = regime_at(df, pd.Timestamp(row["date"]), smooth=True)
            sections.extend(_regime_focus_lines(str(regime), row))
            sections.append("")
    except Exception:
        pass

    task = prompt_cfg.get(
        "task_instruction",
        f"请综合外盘、宏观新闻、七维基本面/资金与市场状态提示，判断下一{bar_unit}涨跌方向。",
    )
    sections.extend(_format_label_guidance(bar_unit))
    sections.append("")
    if is_dual_frequency_enabled():
        sections.extend(_format_week_label_guidance())
        sections.append("")
        sections.extend(_format_dual_freq_task())
    else:
        sections.append("【研判任务】")
        sections.append(task)
    sections.extend(_format_output_requirements())
    return "\n".join(s for s in sections if s).strip()


def build_prompt_from_row(
    row: pd.Series,
    index_name: str = "沪深300",
    bar_unit: str = "周",
    df: Optional[pd.DataFrame] = None,
    idx: Optional[int] = None,
    blind: bool = False,
) -> str:
    """将特征转为 LLM Prompt；综合模式下优先使用时序窗口"""
    prompt_cfg = get_prompt_config()
    mode = prompt_cfg.get("mode", "snapshot")
    if mode == "comprehensive" and df is not None and idx is not None:
        return build_prompt_from_window(
            df, idx, index_name=index_name, bar_unit=bar_unit, blind=blind,
        )

    date_str = "T₀（盲测：不提供公历日期）" if blind else pd.Timestamp(row["date"]).strftime("%Y-%m-%d")

    sections = [
        f"标的: {index_name}",
        f"日期: {date_str}",
        f"频率: {bar_unit}频分析",
        "说明: 量价技术指标由本地 Transformer 通道处理，本 Prompt 不含收盘序列/技术因子。",
        "",
    ]

    dim_factors = _collect_dimension_factors()
    for dim_key, factors in dim_factors.items():
        title = DIMENSION_TITLES.get(dim_key, dim_key)
        section = _format_section(title, factors, row)
        if section:
            sections.append(section)
            sections.append("")

    if prompt_cfg.get("include_technical", False):
        tech_factors = prompt_cfg.get("technical_factors", [
            "rsi_14", "momentum_5d", "momentum_20d", "macd_hist", "volatility_20d",
        ])
        section = _format_section("技术面", tech_factors, row)
        if section:
            sections.append(section)
            sections.append("")

    task = prompt_cfg.get(
        "task_instruction",
        f"请根据以上 {len(DIMENSION_TITLES)} 维基本面与市场信息，判断下一{bar_unit}涨跌方向。",
    )
    sections.append(task)
    if mode == "comprehensive":
        sections.append(
            '请严格按 JSON 输出：{"direction":"涨|跌|平","confidence":0.0-1.0,"reasoning":"理由"}'
        )
    else:
        answer_prefix = prompt_cfg.get("answer_prefix", "预测方向：")
        sections.append(answer_prefix)
    return "\n".join(sections).strip()


def resolve_bar_index(df: pd.DataFrame, date: Optional[str] = None) -> tuple[int, Optional[str]]:
    """解析 bar 索引；无精确匹配时对齐到时间上最近的一根 K 线"""
    data = df.sort_values("date").reset_index(drop=True)
    if not date:
        return len(data) - 1, None

    target = pd.Timestamp(str(date)[:10]).normalize()
    dates = pd.to_datetime(data["date"]).dt.normalize()
    exact = data.index[dates == target]
    if len(exact) > 0:
        return int(exact[-1]), None

    deltas = (dates - target).abs()
    idx = int(deltas.idxmin())
    bar_date = dates.iloc[idx].strftime("%Y-%m-%d")
    if bar_date == str(date)[:10]:
        return idx, None
    return idx, bar_date


def build_analysis_prompt(
    df: pd.DataFrame,
    date: Optional[str] = None,
    blind: bool = False,
) -> tuple[str, pd.Series, int, Optional[str]]:
    """按日期（默认最近一期）构建综合研判 Prompt。

    周频硬切下走日信号复合周决策 Prompt。
    """
    data = df.sort_values("date").reset_index(drop=True)
    idx, resolved = resolve_bar_index(data, date=date)
    row = data.iloc[idx]
    try:
        from market_platform.predictor.分析.特征.日信号复合 import (
            build_weekly_decision_prompt,
            is_weekly_decision_mode,
        )

        if is_weekly_decision_mode():
            index_name, _ = _resolve_index_meta()
            prompt, _meta = build_weekly_decision_prompt(
                data, pd.Timestamp(row["date"]), index_name=index_name or "沪深300",
            )
            return prompt, row, idx, resolved
    except Exception:
        pass
    index_name, bar_unit = _resolve_index_meta()
    prompt = build_prompt_from_row(
        row, index_name=index_name, bar_unit=bar_unit, df=data, idx=idx, blind=blind,
    )
    return prompt, row, idx, resolved

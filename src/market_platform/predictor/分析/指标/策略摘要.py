"""当前策略配置摘要（供 Web 展示）

策略主线（与 默认.yaml backtest.strategy 注释一致）：
  长期价投 — 趋势定底仓，研判调叠加，结构性风险才减仓，过热兑现留弹药。
LLM 只提供日频涨/跌/平信号；仓位与闲置配置才是交易侧。
unified_sharpe 模式下改为六袖统一账本（进攻/防守/现金流）。
"""

from __future__ import annotations

from loguru import logger

from typing import Any, Dict, List

from market_platform.predictor.配置 import get_config
from market_platform.predictor.分析.资金.配置引擎 import is_three_layer_allocation
from market_platform.predictor.分析.资金.闲置管理 import is_unified_sharpe_mode, get_portfolio_config


def _pct(v: float) -> str:
    return f"{v * 100:.0f}%"


def _prompt_display_name(version: str | None, *, regime_router: bool) -> str:
    """把内部 prompt_version 转成用户可读名称（避免 weekly_* 误导）。"""
    ver = str(version or "").strip()
    if ver in {"weekly_v4_regime", "v4_regime"}:
        return "日频研判 · 状态路由 v4" if regime_router else "日频研判 v4"
    if ver.startswith("weekly_v3"):
        return "旧周频模板 v3（已弃用）"
    if not ver:
        return "--"
    return ver


def build_strategy_summary() -> Dict[str, Any]:
    cfg = get_config()
    strat = cfg.get("backtest", {}).get("strategy", {})
    cash = cfg.get("cash_management", {})
    portfolio = get_portfolio_config()
    unified = is_unified_sharpe_mode()
    us_cfg = dict(portfolio.get("unified_sharpe") or {})
    label_cfg = cfg.get("features", {}).get("label", {}) or {}
    llm_cfg = cfg.get("model", {}).get("llm", {}) or {}
    prompt_cfg = llm_cfg.get("prompt", {}) or {}
    providers = llm_cfg.get("providers", {}) or {}

    weekly_decision = bool((strat.get("weekly_decision") or {}).get("enabled", False))
    dual_freq = bool(strat.get("dual_freq_gate", False))
    cash_enabled = bool(cash.get("enabled", False)) or unified
    three_layer = is_three_layer_allocation() and not unified
    position_cycle = bool(strat.get("position_cycle", False))
    return_cal = bool(strat.get("return_calibration", {}).get("enabled", False))
    vol_target = bool(strat.get("vol_target_enabled", False))
    take_profit = bool((strat.get("take_profit") or {}).get("enabled", False))
    systemic = bool((strat.get("systemic_risk_exit") or {}).get("enabled", False))
    include_technical = bool(prompt_cfg.get("include_technical", False))
    transformer_cfg = cfg.get("model", {}).get("transformer", {}) or {}
    transformer_on = bool(transformer_cfg.get("enabled", False))
    transformer_ckpt = False
    try:
        from market_platform.predictor.核心.路径.项目 import checkpoints_dir

        transformer_ckpt = (checkpoints_dir() / "transformer_model.pt").is_file()
    except Exception as exc:  # noqa: BLE001
        # 评审 C4:此前静默置 False,会把"检测失败"显示成"未启用 checkpoint"
        logger.warning("checkpoint 检测失败,按未启用展示: %s", exc)
        transformer_ckpt = False
    if transformer_on and not transformer_ckpt:
        transformer_status = "已开开关但缺 checkpoint（量价通道无法推理）"
    elif transformer_on and transformer_ckpt:
        transformer_status = "启用（量价通道 + checkpoint）"
    elif (not transformer_on) and transformer_ckpt:
        transformer_status = "关闭（已有 checkpoint，未接入主路径）"
    else:
        transformer_status = "未启用（无 checkpoint）"

    daily_th = float(label_cfg.get("direction_threshold", 0.003))
    week_th = float(label_cfg.get("week_direction_threshold", 0.0075))
    week_horizon = int(label_cfg.get("week_horizon", 5))
    prompt_ver = prompt_cfg.get("version")
    regime_router = bool((prompt_cfg.get("regime_router") or {}).get("enabled", False))
    active_api = llm_cfg.get("active_api") or "deepseek"
    active_model = (providers.get(active_api) or {}).get("model") or active_api
    prompt_label = _prompt_display_name(prompt_ver, regime_router=regime_router)

    base = float(strat.get("base_position", 0.25))
    bull = float(strat.get("bull_regime_base", 0.50))
    bull_strong = float(strat.get("bull_strong_base", bull))
    bear = float(strat.get("bear_regime_base", 0.0))
    bear_cap = float(strat.get("bear_regime_cap", strat.get("bear_cap", 0.20)))
    min_hold = int(strat.get("min_hold_periods", 10))
    conf_th = float(strat.get("confidence_threshold", 0.50))

    weights = cash.get("weights", {})
    w_gold = float(weights.get("gold", 0.35))
    w_bond = float(weights.get("bond", 0.10))
    w_nasdaq = float(weights.get("nasdaq", 0.35))
    w_repo = float(weights.get("repo", 0.10))
    w_mm = float(weights.get("money_market", 0.10))
    w_total = w_gold + w_bond + w_nasdaq + w_repo + w_mm or 1.0

    cash_alloc: List[Dict[str, Any]] = []
    sleeves: List[Dict[str, Any]] = []
    if unified:
        caps = us_cfg.get("group_caps") or {}
        off_a = float((caps.get("offense") or {}).get("anchor", 0.40))
        def_a = float((caps.get("defense") or {}).get("anchor", 0.40))
        cf_a = float((caps.get("cashflow") or {}).get("anchor", 0.20))
        cash_alloc = [
            {"key": "csi300", "label": "沪深300", "symbol": "000300.SH",
             "sleeve": "offense", "weight": off_a * 0.5},
            {"key": "nasdaq", "label": "纳指 ETF", "symbol": cash.get("nasdaq_symbol", "513100"),
             "sleeve": "offense", "weight": off_a * 0.5},
            {"key": "gold", "label": "黄金 ETF", "symbol": cash.get("gold_symbol", "518880"),
             "sleeve": "defense", "weight": def_a * 0.5},
            {"key": "bond", "label": "国债 ETF", "symbol": cash.get("bond_symbol", "511010"),
             "sleeve": "defense", "weight": def_a * 0.5},
            {"key": "money_market", "label": "货币 ETF", "symbol": cash.get("mm_symbol", "511880"),
             "sleeve": "cashflow", "weight": cf_a * 0.5},
            {"key": "repo", "label": "逆回购代理", "symbol": "Shibor O/N",
             "sleeve": "cashflow", "weight": cf_a * 0.5},
        ]
        sleeves = [
            {"key": "offense", "label": "进攻", "weight": off_a,
             "desc": "沪深300 + 纳指", "assets": ["csi300", "nasdaq"]},
            {"key": "defense", "label": "防守", "weight": def_a,
             "desc": "黄金 + 国债", "assets": ["gold", "bond"]},
            {"key": "cashflow", "label": "现金流", "weight": cf_a,
             "desc": "货币 + 逆回购", "assets": ["money_market", "repo"]},
        ]
    elif cash_enabled:
        cash_alloc = [
            {"key": "gold", "label": "黄金 ETF", "symbol": cash.get("gold_symbol", "518880"),
             "sleeve": "gold", "weight": w_gold / w_total},
            {"key": "bond", "label": "国债 ETF", "symbol": cash.get("bond_symbol", "511010"),
             "sleeve": "cash", "weight": w_bond / w_total},
            {"key": "nasdaq", "label": "纳指 ETF", "symbol": cash.get("nasdaq_symbol", "513100"),
             "sleeve": "growth", "weight": w_nasdaq / w_total},
            {"key": "repo", "label": "逆回购代理", "symbol": "Shibor O/N",
             "sleeve": "cash", "weight": w_repo / w_total},
            {"key": "money_market", "label": "货币 ETF", "symbol": cash.get("mm_symbol", "511880"),
             "sleeve": "cash", "weight": w_mm / w_total},
        ]
        sleeves = [
            {"key": "gold", "label": "黄金", "weight": w_gold / w_total,
             "desc": "黄金 ETF", "assets": ["gold"]},
            {"key": "growth", "label": "成长", "weight": w_nasdaq / w_total,
             "desc": "纳指 ETF", "assets": ["nasdaq"]},
            {"key": "cash", "label": "现金篮", "weight": (w_bond + w_repo + w_mm) / w_total,
             "desc": "国债 + 货币 + 逆回购", "assets": ["bond", "repo", "money_market"]},
        ]

    if unified:
        tags = ["统一夏普六袖", "进攻/防守/现金流", "LLM tilt + risk-off", "日频涨跌平信号"]
        if regime_router:
            tags.append("Prompt 状态路由")
        headline = "六袖统一账本 · 滚动夏普配权 · LLM 有限偏移"
        notes: List[str] = [
            (
                f"组锚定：进攻 {_pct(float((us_cfg.get('group_caps') or {}).get('offense', {}).get('anchor', 0.40)))} · "
                f"防守 {_pct(float((us_cfg.get('group_caps') or {}).get('defense', {}).get('anchor', 0.40)))} · "
                f"现金流 {_pct(float((us_cfg.get('group_caps') or {}).get('cashflow', {}).get('anchor', 0.20)))}；"
                f"组间 trailing Sharpe（lookback {int(us_cfg.get('lookback', 126))}）+ 组内逆波动"
            ),
            (
                "LLM：进攻组内沪深300/纳指 tilt；"
                f"高置信判跌（≥{float((us_cfg.get('risk_off') or {}).get('conf_min', 0.70)):.0%}）"
                f"压进攻组 ×{float((us_cfg.get('risk_off') or {}).get('offense_scale', 0.55)):.2f}"
            ),
            (
                f"信号：LLM × Transformer 综合评价；日频三分类平盘带 ±{daily_th * 100:.2f}%；"
                f"模板 {prompt_label}；Transformer：{transformer_status}"
            ),
        ]
    else:
        tags = [
            "长期价投",
            "趋势定底仓",
            "研判调叠加",
            f"底仓 {_pct(base)}",
        ]
        if position_cycle:
            tags.append("阶梯建仓")
        if cash_enabled and three_layer:
            tags.append("闲置三层配置")
        tags.append("日频涨跌平信号")
        if regime_router:
            tags.append("Prompt 状态路由")

        if cash_enabled and three_layer:
            headline = "趋势定底仓 · 研判调叠加 · 闲置三层配置"
        else:
            headline = "趋势定底仓 · 研判调叠加 · 结构性风险才减仓"

        notes = [
            (
                f"仓位：中性 {_pct(base)}；弱牛核心 {_pct(bull)} / 强牛 {_pct(bull_strong)}（上限满仓，含 catch-up）；"
                f"熊底仓下限 {_pct(bear)}、顶仓 {_pct(bear_cap)}；"
                f"判涨且置信度≥{conf_th:.0%} 才叠加卫星仓"
            ),
            (
                "加减仓："
                + ("阶梯建仓逐步到位；" if position_cycle else "")
                + f"最短持仓约 {min_hold} 日；"
                + "减仓需结构性确认（趋势/波动/过热），非单日判跌即砍"
            ),
            (
                f"信号：LLM（七维/外盘/新闻/状态）× Transformer（量价技术）综合评价；"
                f"日频三分类平盘带 ±{daily_th * 100:.2f}%；量价不进 LLM Prompt；"
                f"Transformer：{transformer_status}；模板 {prompt_label}"
            ),
        ]
        if return_cal or vol_target or take_profit or systemic:
            bits = []
            if return_cal:
                bits.append("收益校准加仓")
            if vol_target:
                bits.append(f"波动目标 {float(strat.get('vol_target_annual', 0.16)) * 100:.0f}%")
            if take_profit:
                bits.append("浮盈兑现叠加")
            if systemic:
                bits.append("系统性风险削减")
            notes.append("风控门控：" + " · ".join(bits))

        if cash_enabled and three_layer:
            max_shift = cash.get("allocation", {}).get("three_layer", {}).get("regime", {}).get("max_shift", 0.15)
            llm_floor = cash.get("allocation", {}).get("three_layer", {}).get("llm_override", {}).get("equity_floor", 0.22)
            notes.append(
                f"闲置（1−权益仓位）：锚定 金 {_pct(w_gold / w_total)} · 纳 {_pct(w_nasdaq / w_total)} · "
                f"现金篮 {_pct((w_bond + w_repo + w_mm) / w_total)}；"
                f"再经逆波动 → 状态偏移 ±{_pct(float(max_shift))} → "
                f"高置信判跌压权益至 {_pct(float(llm_floor))}"
            )
        elif cash_enabled:
            notes.append("闲置资金 = 1 − 权益仓位；数据就绪前闲置不投资")
        else:
            notes.append("闲置资金未启用")

    if weekly_decision:
        notes.insert(0, "注意：当前配置开启了周频硬切（与主叙述日频价投可能不一致）")
    if dual_freq:
        notes.append("双频门控已开启（实验项）")

    return {
        "allocation_mode": "unified_sharpe" if unified else "legacy",
        "position_mode": strat.get("position_mode", "core_overlay"),
        "base_position": base,
        "bull_regime_base": bull,
        "bull_strong_base": bull_strong,
        "bear_regime_base": bear,
        "bear_regime_cap": bear_cap,
        "dual_freq_gate": dual_freq,
        "weekly_decision": weekly_decision,
        "rebalance_on_week_end": False if not weekly_decision else True,
        "position_cycle": position_cycle,
        "three_layer_allocation": three_layer,
        "confidence_isotonic": bool(strat.get("confidence_isotonic", {}).get("enabled", False)),
        "return_calibration": return_cal,
        "vol_target_enabled": vol_target,
        "take_profit_enabled": take_profit,
        "systemic_risk_exit": systemic,
        "confidence_threshold": conf_th,
        "rebalance_interval_days": 0 if position_cycle and not weekly_decision else int(
            (strat.get("weekly_rebalance") or {}).get("interval_days", 0) or 0
        ),
        "min_hold_periods": min_hold,
        "prompt_version": prompt_ver,
        "prompt_label": prompt_label,
        "regime_router": regime_router,
        "include_technical": include_technical,
        "transformer_enabled": transformer_on,
        "transformer_checkpoint": transformer_ckpt,
        "transformer_status": transformer_status,
        "active_api": active_api,
        "active_model": active_model,
        "classification": {
            "mode": "weekly_ternary" if weekly_decision else "daily_ternary",
            "labels": ["涨", "跌", "平"],
            "daily_threshold": daily_th,
            "daily_threshold_pct": round(daily_th * 100, 2),
            "week_threshold": week_th,
            "week_threshold_pct": round(week_th * 100, 2),
            "week_horizon": week_horizon,
            "role": "signal",
        },
        "cash_management": {
            "enabled": cash_enabled,
            "mode": "unified_sharpe" if unified else cash.get("mode", "fixed"),
            "allocations": cash_alloc,
            "sleeves": sleeves,
        },
        "unified_sharpe": us_cfg if unified else None,
        "tags": tags,
        "headline": headline,
        "notes": notes,
        "philosophy": (
            "统一夏普：组间滚动夏普 + 组内逆波动，LLM 仅做进攻 tilt 与 risk-off"
            if unified
            else "长期价投：趋势定底仓，研判调叠加，结构性风险才减仓，过热兑现留弹药"
        ),
    }

"""
三层资产配置 — 风险预算 + 状态调整 + LLM 高置信覆盖

第一层：各资产滚动波动率逆权重（与锚定权重混合）→ 闲置资金基准配置
第二层：趋势 × 波动（±max_shift）对基准权重与权益目标做有限偏移
第三层：LLM 高置信判跌时，权益目标压至 floor，释放额度偏向防御性闲置资产
"""

from __future__ import annotations


from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from market_platform.predictor.配置 import get_config
from market_platform.predictor.分析.资金.闲置管理 import normalized_cash_weights

CASH_KEYS = ("gold", "bond", "nasdaq", "repo", "mm")
RET_COL_MAP = {
    "gold": "gold_ret",
    "bond": "bond_ret",
    "nasdaq": "nasdaq_ret",
    "repo": "repo_daily_ret",
    "mm": "mm_ret",
}


def get_allocation_config() -> dict:
    return get_config().get("cash_management", {}).get("allocation", {})


def is_three_layer_allocation() -> bool:
    cfg = get_allocation_config()
    return bool(cfg.get("three_layer", {}).get("enabled", False))


@dataclass
class AllocationDecision:
    cash_weights: Dict[str, float]
    regime: str
    regime_label: str
    equity_mult: float = 1.0
    equity_cap: Optional[float] = None
    llm_override: bool = False
    layers: Dict[str, object] = field(default_factory=dict)


class ThreeLayerAllocator:
    """三层资产配置决策器（回测/实盘共用）"""

    def __init__(self, params: Optional[dict] = None):
        root = get_allocation_config()
        self.cfg = dict(params or root.get("three_layer", {}))
        self.risk_cfg = dict(self.cfg.get("risk_budget", {}))
        self.regime_cfg = dict(self.cfg.get("regime", {}))
        self.llm_cfg = dict(self.cfg.get("llm_override", {}))
        self._anchor = normalized_cash_weights()
        self._vol_history: List[float] = []
        self._weight_ema: Dict[str, float] = {}

    def reset(self):
        self._vol_history = []
        self._weight_ema = {}

    def _annualized_vol(self, returns: pd.Series, lookback: int) -> float:
        r = returns.dropna().tail(lookback)
        if len(r) < max(5, lookback // 4):
            return float(self.risk_cfg.get("default_vol", 0.15))
        daily = float(r.std())
        floor = float(self.risk_cfg.get("min_vol", 0.05))
        return max(daily * np.sqrt(252), floor)

    def _layer1_risk_budget(
        self,
        row: pd.Series,
        cash_returns: Optional[pd.DataFrame],
        target_date: pd.Timestamp,
    ) -> Dict[str, float]:
        lookback = int(self.risk_cfg.get("vol_lookback", 60))
        blend = float(self.risk_cfg.get("anchor_blend", 0.35))
        inv: Dict[str, float] = {}

        if cash_returns is not None and not cash_returns.empty:
            cr = cash_returns[cash_returns["date"] <= target_date].tail(lookback + 5)
            for key in CASH_KEYS:
                col = RET_COL_MAP[key]
                if col not in cr.columns:
                    inv[key] = 1.0
                    continue
                vol = self._annualized_vol(cr[col], lookback)
                inv[key] = 1.0 / max(vol, 1e-6)
        else:
            inv = {k: 1.0 for k in CASH_KEYS}

        inv_total = sum(inv.values()) or 1.0
        risk_w = {k: inv[k] / inv_total for k in CASH_KEYS}

        out = {}
        for k in CASH_KEYS:
            anchor = float(self._anchor.get(k, 0.0))
            out[k] = blend * anchor + (1.0 - blend) * risk_w[k]
        s = sum(out.values()) or 1.0
        return {k: out[k] / s for k in CASH_KEYS}

    def _classify_regime(self, row: pd.Series) -> Tuple[str, str]:
        trend_cfg = self.regime_cfg.get("trend", {})
        vol_cfg = self.regime_cfg.get("volatility", {})
        sma_col = trend_cfg.get("sma_col", "sma_60")
        close = row.get("close")
        sma = row.get(sma_col)

        if pd.notna(close) and pd.notna(sma) and float(sma) > 0:
            dev = float(close) / float(sma) - 1.0
            bear_th = float(trend_cfg.get("bear_threshold", -0.02))
            if dev >= 0:
                trend = "bull"
                trend_zh = "牛市"
            elif dev <= bear_th:
                trend = "bear"
                trend_zh = "熊市"
            else:
                trend = "neutral"
                trend_zh = "震荡"
        else:
            trend, trend_zh = "neutral", "震荡"

        vol = row.get(vol_cfg.get("col", "volatility_20d"))
        if pd.isna(vol):
            vol_band, vol_zh = "mid", "中波"
        else:
            vol_f = float(vol)
            self._vol_history.append(vol_f)
            hist = self._vol_history[-int(vol_cfg.get("lookback", 252)) :]
            if len(hist) < 20:
                vol_band, vol_zh = "mid", "中波"
            else:
                low_pct = float(vol_cfg.get("low_pct", 0.33))
                high_pct = float(vol_cfg.get("high_pct", 0.67))
                arr = np.array(hist)
                lo = float(np.quantile(arr, low_pct))
                hi = float(np.quantile(arr, high_pct))
                if vol_f <= lo:
                    vol_band, vol_zh = "low", "低波"
                elif vol_f >= hi:
                    vol_band, vol_zh = "high", "高波"
                else:
                    vol_band, vol_zh = "mid", "中波"

        key = f"{trend}_{vol_band}"
        label = f"{trend_zh}+{vol_zh}"
        return key, label

    def _layer2_regime_shift(
        self,
        base_weights: Dict[str, float],
        regime_key: str,
    ) -> Tuple[Dict[str, float], float]:
        max_shift = float(self.regime_cfg.get("max_shift", 0.15))
        shifts = self.regime_cfg.get("shifts", {})
        row = dict(shifts.get(regime_key, shifts.get("neutral_mid", {})))

        equity_mult = float(row.pop("equity_mult", 1.0))
        equity_mult = float(np.clip(equity_mult, 1.0 - max_shift, 1.0 + max_shift))

        shifted = dict(base_weights)
        for k in CASH_KEYS:
            delta = float(row.get(k, 0.0))
            delta = float(np.clip(delta, -max_shift, max_shift))
            shifted[k] = shifted.get(k, 0.0) + delta

        for k in list(shifted.keys()):
            shifted[k] = max(0.0, float(shifted[k]))
        s = sum(shifted.values()) or 1.0
        shifted = {k: shifted[k] / s for k in CASH_KEYS}
        return shifted, equity_mult

    def _apply_weight_bounds(self, weights: Dict[str, float]) -> Dict[str, float]:
        """单品种硬夹 + 可选 EMA 平滑（E 实验）。"""
        bounds = self.cfg.get("weight_bounds") or {}
        w = dict(weights)
        for k, lim in bounds.items():
            if k not in w or not isinstance(lim, (list, tuple)) or len(lim) != 2:
                continue
            lo, hi = float(lim[0]), float(lim[1])
            w[k] = float(np.clip(w[k], lo, hi))
        for k in list(w.keys()):
            w[k] = max(0.0, float(w[k]))
        s = sum(w.values()) or 1.0
        w = {k: w[k] / s for k in CASH_KEYS}

        span = int(self.cfg.get("weight_ema_span", 0) or 0)
        if span > 1:
            alpha = 2.0 / (span + 1.0)
            if not self._weight_ema:
                self._weight_ema = dict(w)
            else:
                self._weight_ema = {
                    k: alpha * w[k] + (1.0 - alpha) * self._weight_ema.get(k, w[k])
                    for k in CASH_KEYS
                }
            w = dict(self._weight_ema)
            s = sum(w.values()) or 1.0
            w = {k: w[k] / s for k in CASH_KEYS}
        return w

    def _layer3_llm_override(
        self,
        equity_target: float,
        pred: int,
        max_conf: float,
        weights: Dict[str, float],
    ) -> Tuple[float, Optional[float], Dict[str, float], bool]:
        if not self.llm_cfg.get("enabled", True):
            return equity_target, None, weights, False

        min_conf = float(self.llm_cfg.get("min_confidence", 0.70))
        bear_pred = int(self.llm_cfg.get("bearish_pred", -1))
        if int(pred) != bear_pred or max_conf < min_conf:
            return equity_target, None, weights, False

        # v2: 置信度 < 0.65 的判跌不触发 override（错误判跌集中在低确信区间）
        low_conf_gate = float(self.llm_cfg.get("low_conf_gate", 0.65))
        if max_conf < low_conf_gate:
            return equity_target, None, weights, False

        floor = float(self.llm_cfg.get("equity_floor", 0.20))
        capped = min(equity_target, floor)

        defensive_boost = float(self.llm_cfg.get("defensive_boost", 0.08))
        w = dict(weights)
        # 高置信判跌：削减纳指，增防御袖（金+债）与现金袖（逆回购+货币）
        w["gold"] = w.get("gold", 0.0) + defensive_boost * 0.25
        w["bond"] = w.get("bond", 0.0) + defensive_boost * 0.25
        w["repo"] = w.get("repo", 0.0) + defensive_boost * 0.15
        w["mm"] = w.get("mm", 0.0) + defensive_boost * 0.15
        w["nasdaq"] = max(0.0, w.get("nasdaq", 0.0) - defensive_boost * 0.80)
        s = sum(w.values()) or 1.0
        w = {k: w[k] / s for k in CASH_KEYS}
        return capped, floor, w, True

    def resolve(
        self,
        row: pd.Series,
        pred: int,
        max_conf: float,
        equity_target: float,
        cash_returns: Optional[pd.DataFrame] = None,
    ) -> AllocationDecision:
        target_date = pd.Timestamp(row["date"]).normalize()
        base = self._layer1_risk_budget(row, cash_returns, target_date)
        regime_key, regime_label = self._classify_regime(row)
        shifted, equity_mult = self._layer2_regime_shift(base, regime_key)

        apply_mult = bool(self.cfg.get("apply_equity_mult", False))
        strength = float(self.cfg.get("equity_mult_strength", 1.0))
        bear_only = bool(self.cfg.get("equity_mult_bear_only", False))
        mult = float(equity_mult)
        if bear_only and not str(regime_key).startswith("bear"):
            mult = 1.0
        if apply_mult and strength > 0:
            mult = 1.0 + (mult - 1.0) * float(np.clip(strength, 0.0, 1.0))
            adjusted_equity = float(np.clip(equity_target * mult, 0.0, 1.0))
        else:
            mult = 1.0
            adjusted_equity = float(np.clip(equity_target, 0.0, 1.0))

        final_equity, equity_cap, final_weights, overridden = self._layer3_llm_override(
            adjusted_equity, pred, max_conf, shifted,
        )
        final_weights = self._apply_weight_bounds(final_weights)

        return AllocationDecision(
            cash_weights=final_weights,
            regime=regime_key,
            regime_label=regime_label,
            equity_mult=mult if apply_mult else equity_mult,
            equity_cap=equity_cap,
            llm_override=overridden,
            layers={
                "risk_budget": base,
                "regime_shift": shifted,
                "equity_before": equity_target,
                "equity_after": final_equity,
                "equity_mult_applied": bool(apply_mult and strength > 0),
                "equity_mult_raw": equity_mult,
                "equity_mult_effective": mult if apply_mult else 1.0,
            },
        )

    def apply_equity_adjustment(
        self,
        target: float,
        row: pd.Series,
        pred: int,
        max_conf: float,
        cash_returns: Optional[pd.DataFrame] = None,
    ) -> Tuple[float, AllocationDecision]:
        """仅调整权益目标；闲置权重随决策一并返回供当日收益计算"""
        decision = self.resolve(row, pred, max_conf, target, cash_returns)
        new_target = decision.layers.get("equity_after", target)
        return float(new_target), decision

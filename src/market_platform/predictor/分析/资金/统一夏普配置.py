"""
统一夏普六袖配置 — 进攻 / 防守 / 现金流

组间：滚动夏普（负值截断）与锚定混合，硬投影到 group_caps
组内：
  - 进攻(沪深300↔纳指)、防守(黄金↔国债)：互补配对（逆波动 + 相对夏普，高相关拉回等权）
  - 现金流：逆波动
LLM：在进攻组互补基线上做有限 tilt；高置信判跌压进攻组
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

import numpy as np
import pandas as pd

from market_platform.predictor.分析.资金.闲置管理 import SLEEVE_RET_COLS, get_portfolio_config

GROUPS: Dict[str, Tuple[str, ...]] = {
    "offense": ("csi300", "nasdaq"),
    "defense": ("gold", "bond"),
    "cashflow": ("mm", "repo"),
}
SLEEVES = ("csi300", "nasdaq", "gold", "bond", "mm", "repo")
# 需要组内互补轮动的配对组
PAIR_GROUPS = ("offense", "defense")


@dataclass
class PortfolioWeights:
    weights: Dict[str, float]
    group_weights: Dict[str, float]
    regime: str = "neutral"
    regime_label: str = "中性"
    llm_tilt: bool = False
    risk_off: bool = False
    layers: Dict[str, object] = field(default_factory=dict)

    @property
    def csi300(self) -> float:
        return float(self.weights.get("csi300", 0.0))

    def cash_legacy_weights(self) -> Dict[str, float]:
        """兼容旧资金图：把非 csi300 权重归一到闲置语义（仅展示）。"""
        rest = {k: float(self.weights.get(k, 0.0)) for k in ("gold", "bond", "nasdaq", "repo", "mm")}
        s = sum(rest.values())
        if s <= 1e-12:
            return {k: 0.0 for k in rest}
        return {k: v / s for k, v in rest.items()}


def get_unified_sharpe_config() -> dict:
    return dict(get_portfolio_config().get("unified_sharpe") or {})


class UnifiedSharpeAllocator:
    """六袖统一夏普配置器（回测/模拟盘共用）"""

    def __init__(self, params: Optional[dict] = None):
        self.cfg = dict(params or get_unified_sharpe_config())
        self.lookback = int(self.cfg.get("lookback", 126))
        self.rebalance = str(self.cfg.get("rebalance", "weekly")).lower()
        self.anchor_blend = float(self.cfg.get("anchor_blend", 0.60))
        self.sharpe_floor = float(self.cfg.get("sharpe_floor", 0.0))
        self.vol_floor_annual = float(self.cfg.get("vol_floor_annual", 0.10))
        self.within_group = str(self.cfg.get("within_group", "complementary")).lower()
        self.pair_cfg = dict(self.cfg.get("pair_complement") or {})
        self.weight_ema_span = int(self.cfg.get("weight_ema_span", 0) or 0)
        # 降换手：渐进靠拢目标 + L1 死区（risk_off 硬砍仍一跳到位）
        self.rebalance_step = float(self.cfg.get("rebalance_step", 1.0) or 1.0)
        self.trade_deadband = float(self.cfg.get("trade_deadband", 0.0) or 0.0)
        self.group_caps = dict(self.cfg.get("group_caps") or {})
        self.llm_tilt_cfg = dict(self.cfg.get("llm_tilt") or {})
        self.risk_off_cfg = dict(self.cfg.get("risk_off") or {})
        self.risk_on_cfg = dict(self.cfg.get("risk_on") or {})
        self._ema: Dict[str, float] = {}
        self._last_weights: Optional[PortfolioWeights] = None
        self._last_rebalance_date: Optional[str] = None

    def reset(self) -> None:
        self._ema = {}
        self._last_weights = None
        self._last_rebalance_date = None

    def export_state(self) -> dict:
        """持久化降换手状态，供模拟盘跨进程恢复。"""
        lw = self._last_weights
        return {
            "ema": {k: float(v) for k, v in (self._ema or {}).items()},
            "last_rebalance_date": self._last_rebalance_date,
            "last_weights": None
            if lw is None
            else {
                "weights": {k: float(v) for k, v in (lw.weights or {}).items()},
                "group_weights": {k: float(v) for k, v in (lw.group_weights or {}).items()},
                "regime": lw.regime,
                "regime_label": lw.regime_label,
                "llm_tilt": bool(lw.llm_tilt),
                "risk_off": bool(lw.risk_off),
                "layers": dict(lw.layers or {}),
            },
        }

    def import_state(self, payload: Optional[dict]) -> None:
        if not payload or not isinstance(payload, dict):
            return
        ema = payload.get("ema") or {}
        if isinstance(ema, dict):
            self._ema = {str(k): float(v) for k, v in ema.items()}
        self._last_rebalance_date = payload.get("last_rebalance_date")
        raw = payload.get("last_weights")
        if not isinstance(raw, dict) or not raw.get("weights"):
            self._last_weights = None
            return
        self._last_weights = PortfolioWeights(
            weights={str(k): float(v) for k, v in (raw.get("weights") or {}).items()},
            group_weights={str(k): float(v) for k, v in (raw.get("group_weights") or {}).items()},
            regime=str(raw.get("regime") or "neutral"),
            regime_label=str(raw.get("regime_label") or ""),
            llm_tilt=bool(raw.get("llm_tilt")),
            risk_off=bool(raw.get("risk_off")),
            layers=dict(raw.get("layers") or {}),
        )

    def seed_from_sleeve_weights(self, sleeve_weights: Optional[Dict[str, float]]) -> None:
        """无持久化状态时用隔夜袖口权重种子，避免进程重启触发假「强制调仓」。"""
        if self._last_weights is not None or not sleeve_weights:
            return
        w = {k: float(sleeve_weights.get(k, 0.0) or 0.0) for k in SLEEVES}
        total = sum(w.values())
        if total <= 1e-12:
            return
        w = {k: v / total for k, v in w.items()}
        group_w = {g: float(sum(w[m] for m in members)) for g, members in GROUPS.items()}
        self._last_weights = PortfolioWeights(
            weights=w,
            group_weights=group_w,
            regime="neutral",
            regime_label="隔夜种子",
        )

    def _blend_toward_prev(
        self,
        target: Dict[str, float],
        *,
        risk_off: bool,
    ) -> Dict[str, float]:
        """
        执行层降换手：非 risk_off 时按 rebalance_step 渐进；
        L1 变动低于 trade_deadband 则维持上期权重。
        """
        if self._last_weights is None:
            return dict(target)
        prev = {k: float(self._last_weights.weights.get(k, 0.0)) for k in SLEEVES}
        if risk_off:
            return dict(target)

        step = float(np.clip(self.rebalance_step, 0.0, 1.0))
        if step < 1.0 - 1e-12:
            blended = {
                k: float(prev[k] + step * (float(target.get(k, 0.0)) - prev[k]))
                for k in SLEEVES
            }
            s = sum(max(0.0, v) for v in blended.values()) or 1.0
            target = {k: float(max(0.0, blended[k])) / s for k in SLEEVES}

        if self.trade_deadband > 0:
            l1 = sum(abs(float(target.get(k, 0.0)) - prev[k]) for k in SLEEVES)
            if l1 < self.trade_deadband:
                return prev
        return dict(target)

    def _group_anchor(self, group: str) -> float:
        cap = self.group_caps.get(group) or {}
        return float(cap.get("anchor", 1.0 / 3.0))

    def _group_bounds(self, group: str) -> Tuple[float, float]:
        cap = self.group_caps.get(group) or {}
        lo = float(cap.get("min", 0.0))
        hi = float(cap.get("max", 1.0))
        return lo, hi

    def _clip_group(self, group: str, w: float) -> float:
        lo, hi = self._group_bounds(group)
        return float(np.clip(w, lo, hi))

    def _project_group_caps(self, weights: Dict[str, float]) -> Dict[str, float]:
        """
        将组权重投影到 [min,max] 且 Σ=1（避免 clip 后再归一冲破上限）。
        """
        groups = list(GROUPS.keys())
        raw = {g: max(0.0, float(weights.get(g, 0.0))) for g in groups}
        fixed: Dict[str, float] = {}
        for _ in range(len(groups) + 3):
            rem = 1.0 - sum(fixed.values())
            free = [g for g in groups if g not in fixed]
            if not free:
                break
            if rem <= 1e-12:
                # 剩余过小：把未固定组压到各自 min（可能略超约束，再归一）
                for g in free:
                    lo, _ = self._group_bounds(g)
                    fixed[g] = lo
                break
            base = sum(raw[g] for g in free)
            if base <= 1e-12:
                # 等权分配剩余
                trial = {g: rem / len(free) for g in free}
            else:
                trial = {g: rem * raw[g] / base for g in free}
            hit = False
            for g in free:
                lo, hi = self._group_bounds(g)
                if trial[g] < lo - 1e-12:
                    fixed[g] = lo
                    hit = True
                elif trial[g] > hi + 1e-12:
                    fixed[g] = hi
                    hit = True
            if not hit:
                out = dict(fixed)
                out.update(trial)
                s = sum(out.values()) or 1.0
                return {g: float(out[g]) / s for g in groups}
        # 兜底：锚定再投影一次
        anchors = {g: self._group_anchor(g) for g in groups}
        s = sum(anchors.values()) or 1.0
        anchors = {g: anchors[g] / s for g in groups}
        return {g: self._clip_group(g, anchors[g]) for g in groups}

    def _trailing_stats(
        self,
        sleeve_returns: pd.DataFrame,
        asof: pd.Timestamp,
        col: str,
    ) -> Tuple[float, float]:
        """返回 (ann_sharpe, ann_vol)。"""
        hist = sleeve_returns[sleeve_returns["date"] < asof].tail(self.lookback)
        if hist.empty or col not in hist.columns:
            return 0.0, 0.15
        r = pd.to_numeric(hist[col], errors="coerce").dropna()
        if len(r) < max(20, self.lookback // 5):
            return 0.0, 0.15
        mu = float(r.mean())
        sig = float(r.std())
        if sig <= 1e-12:
            return 0.0, max(self.vol_floor_annual, 0.05)
        vol = max(sig * np.sqrt(252.0), self.vol_floor_annual)
        sharpe = (mu * 252.0) / vol
        return float(sharpe), float(vol)

    def _group_sharpe_weights(
        self,
        sleeve_returns: pd.DataFrame,
        asof: pd.Timestamp,
    ) -> Dict[str, float]:
        raw: Dict[str, float] = {}
        for g, members in GROUPS.items():
            cols = [SLEEVE_RET_COLS[m] for m in members]
            hist = sleeve_returns[sleeve_returns["date"] < asof].tail(self.lookback)
            if hist.empty:
                raw[g] = max(self.sharpe_floor, 0.0)
                continue
            mat = hist[cols].apply(pd.to_numeric, errors="coerce")
            port = mat.mean(axis=1).dropna()
            if len(port) < max(20, self.lookback // 5):
                raw[g] = max(self.sharpe_floor, 0.0)
                continue
            mu, sig = float(port.mean()), float(port.std())
            # 波动地板：压制近零波动组的虚高夏普
            vol_ann = max(sig * np.sqrt(252.0), self.vol_floor_annual) if sig > 0 else self.vol_floor_annual
            sh = (mu * 252.0) / vol_ann
            raw[g] = max(float(sh), self.sharpe_floor)

        if sum(raw.values()) <= 1e-12:
            return self._project_group_caps({g: self._group_anchor(g) for g in GROUPS})

        sharpe_w = {g: v / sum(raw.values()) for g, v in raw.items()}
        blended = {
            g: self.anchor_blend * self._group_anchor(g)
            + (1.0 - self.anchor_blend) * sharpe_w[g]
            for g in GROUPS
        }
        return self._project_group_caps(blended)

    def _within_group_inv_vol(
        self,
        sleeve_returns: pd.DataFrame,
        asof: pd.Timestamp,
        members: Tuple[str, ...],
    ) -> Dict[str, float]:
        inv: Dict[str, float] = {}
        for m in members:
            _, vol = self._trailing_stats(sleeve_returns, asof, SLEEVE_RET_COLS[m])
            inv[m] = 1.0 / max(vol, self.vol_floor_annual)
        s = sum(inv.values())
        if s <= 0:
            return {m: 1.0 / len(members) for m in members}
        return {m: inv[m] / s for m in members}

    def _pair_member_bounds(self) -> Tuple[float, float]:
        lo = float(self.pair_cfg.get("member_min", 0.28))
        hi = float(self.pair_cfg.get("member_max", 0.72))
        lo = float(np.clip(lo, 0.05, 0.49))
        hi = float(np.clip(hi, 0.51, 0.95))
        if lo + hi > 1.0 and lo > 1.0 - hi:
            # 保证可行：两袖都能同时落在区间内
            lo = min(lo, 1.0 - hi)
        return lo, hi

    def _project_pair_shares(self, shares: Dict[str, float]) -> Dict[str, float]:
        """两元权重投影到 [member_min, member_max] 且和为 1。"""
        keys = list(shares.keys())
        if len(keys) != 2:
            s = sum(max(0.0, float(v)) for v in shares.values()) or 1.0
            return {k: float(max(0.0, shares[k])) / s for k in shares}
        a, b = keys[0], keys[1]
        lo, hi = self._pair_member_bounds()
        wa = float(np.clip(shares[a], lo, hi))
        wb = 1.0 - wa
        if wb < lo:
            wb = lo
            wa = 1.0 - wb
        elif wb > hi:
            wb = hi
            wa = 1.0 - wb
        wa = float(np.clip(wa, lo, hi))
        wb = 1.0 - wa
        return {a: wa, b: float(wb)}

    def _rolling_pair_corr(
        self,
        sleeve_returns: pd.DataFrame,
        asof: pd.Timestamp,
        a: str,
        b: str,
    ) -> float:
        n = int(self.pair_cfg.get("corr_lookback", 63))
        hist = sleeve_returns[sleeve_returns["date"] < asof].tail(max(n, 40))
        if hist.empty:
            return 0.0
        ca, cb = SLEEVE_RET_COLS[a], SLEEVE_RET_COLS[b]
        if ca not in hist.columns or cb not in hist.columns:
            return 0.0
        x = pd.to_numeric(hist[ca], errors="coerce")
        y = pd.to_numeric(hist[cb], errors="coerce")
        paired = pd.concat([x, y], axis=1).dropna()
        if len(paired) < 30:
            return 0.0
        c = float(paired.iloc[:, 0].corr(paired.iloc[:, 1]))
        return c if np.isfinite(c) else 0.0

    def _within_group_complementary(
        self,
        sleeve_returns: pd.DataFrame,
        asof: pd.Timestamp,
        members: Tuple[str, ...],
    ) -> Dict[str, float]:
        """
        配对互补：逆波动（风险均衡）+ 相对夏普（谁强偏谁）+ 高相关时拉回等权。
        保证双侧常在 [member_min, member_max]。
        """
        if len(members) != 2:
            return self._within_group_inv_vol(sleeve_returns, asof, members)

        a, b = members[0], members[1]
        rel_lb = int(self.pair_cfg.get("relative_lookback", 63))
        # 临时用短窗算相对夏普
        old_lb = self.lookback
        self.lookback = max(rel_lb, 40)
        try:
            sh_a, vol_a = self._trailing_stats(sleeve_returns, asof, SLEEVE_RET_COLS[a])
            sh_b, vol_b = self._trailing_stats(sleeve_returns, asof, SLEEVE_RET_COLS[b])
        finally:
            self.lookback = old_lb

        inv = {
            a: 1.0 / max(vol_a, self.vol_floor_annual),
            b: 1.0 / max(vol_b, self.vol_floor_annual),
        }
        inv_s = sum(inv.values()) or 1.0
        inv_w = {k: inv[k] / inv_s for k in (a, b)}

        # 相对夏普：负值截断，避免长期偏弱侧归零前先被 clip
        rel = {a: max(sh_a, 0.0) + 0.05, b: max(sh_b, 0.0) + 0.05}
        rel_s = sum(rel.values()) or 1.0
        rel_w = {k: rel[k] / rel_s for k in (a, b)}

        alpha = float(np.clip(self.pair_cfg.get("inv_vol_blend", 0.40), 0.0, 1.0))
        blend = {
            a: alpha * inv_w[a] + (1.0 - alpha) * rel_w[a],
            b: alpha * inv_w[b] + (1.0 - alpha) * rel_w[b],
        }

        if bool(self.pair_cfg.get("corr_diversify", True)):
            corr = self._rolling_pair_corr(sleeve_returns, asof, a, b)
            high = float(self.pair_cfg.get("high_corr", 0.55))
            # corr 从 high-0.3 → high：diversify 0→1
            diversify = float(np.clip((corr - (high - 0.30)) / 0.30, 0.0, 1.0))
            blend = {
                a: (1.0 - diversify) * blend[a] + diversify * 0.50,
                b: (1.0 - diversify) * blend[b] + diversify * 0.50,
            }

        return self._project_pair_shares(blend)

    def _within_group_weights(
        self,
        sleeve_returns: pd.DataFrame,
        asof: pd.Timestamp,
        group: str,
        members: Tuple[str, ...],
    ) -> Dict[str, float]:
        use_comp = (
            self.within_group in {"complementary", "pair", "complement"}
            or bool(self.pair_cfg.get("enabled", True))
        )
        if use_comp and group in PAIR_GROUPS:
            return self._within_group_complementary(sleeve_returns, asof, members)
        return self._within_group_inv_vol(sleeve_returns, asof, members)

    def _csi300_offense_share(self, pred: int, conf: float, *, base_share: float) -> float:
        """在互补基线 base_share 上叠加有限 LLM tilt。"""
        cfg = self.llm_tilt_cfg
        lo, hi = self._pair_member_bounds()
        lo = max(lo, float(cfg.get("min_csi300_share_in_offense", lo)))
        hi = min(hi, float(cfg.get("max_csi300_share_in_offense", hi)))
        if not bool(cfg.get("enabled", True)):
            return float(np.clip(base_share, lo, hi))

        mid = float(cfg.get("neutral_share", 0.50))
        c = float(np.clip(conf, 0.0, 1.0))
        if int(pred) == 1:
            llm_target = float(np.clip(mid + (hi - mid) * c, lo, hi))
        elif int(pred) == -1:
            llm_target = float(np.clip(mid - (mid - lo) * c, lo, hi))
        else:
            llm_target = mid

        blend = float(np.clip(cfg.get("blend_with_base", 0.30), 0.0, 1.0))
        out = (1.0 - blend) * float(base_share) + blend * llm_target
        return float(np.clip(out, lo, hi))

    def _classify_simple_regime(self, row: Optional[pd.Series]) -> Tuple[str, str]:
        if row is None:
            return "neutral", "中性"
        close, sma = row.get("close"), row.get("sma_60")
        if pd.notna(close) and pd.notna(sma) and float(sma) > 0:
            dev = float(close) / float(sma) - 1.0
            if dev >= 0.03:
                return "bull", "趋势偏多"
            if dev <= -0.03:
                return "bear", "趋势偏空"
        return "neutral", "震荡中性"

    def should_rebalance(
        self,
        asof: pd.Timestamp,
        *,
        trading_dates: Optional[pd.Series] = None,
        force: bool = False,
    ) -> bool:
        if force or self._last_weights is None:
            return True
        if self.rebalance == "daily":
            return True
        if trading_dates is not None:
            try:
                from market_platform.predictor.分析.特征.日信号复合 import is_week_end

                return bool(is_week_end(asof, trading_dates))
            except Exception:
                pass
        return int(pd.Timestamp(asof).dayofweek) == 4

    def _rebuild_from_groups(
        self,
        group_w: Dict[str, float],
        within: Dict[str, Dict[str, float]],
    ) -> Dict[str, float]:
        weights = {s: 0.0 for s in SLEEVES}
        for g, members in GROUPS.items():
            for m in members:
                weights[m] = float(group_w[g]) * float(within[g][m])
        return weights

    def _apply_group_shift(
        self,
        group_w: Dict[str, float],
        *,
        new_offense: float,
        take_from_defense: float = 0.75,
    ) -> Dict[str, float]:
        """把进攻调到 new_offense，差额按比例从防守/现金流抽补。"""
        out = {k: float(v) for k, v in group_w.items()}
        before = float(out["offense"])
        target = float(new_offense)
        delta = target - before
        if abs(delta) < 1e-12:
            return out
        out["offense"] = target
        if delta < 0:
            # 减进攻 → 释放到防守/现金流
            release = -delta
            to_def = float(np.clip(take_from_defense, 0.0, 1.0))
            out["defense"] = out["defense"] + release * to_def
            out["cashflow"] = out["cashflow"] + release * (1.0 - to_def)
        else:
            # 加进攻 → 从防守/现金流抽
            need = delta
            from_def = float(np.clip(take_from_defense, 0.0, 1.0))
            take_def = min(out["defense"] * 0.95, need * from_def)
            take_cf = min(out["cashflow"] * 0.95, need - take_def)
            # 若不够，再尽量从防守补
            short = need - take_def - take_cf
            if short > 1e-12:
                extra = min(out["defense"] - take_def, short)
                take_def += max(0.0, extra)
                short = need - take_def - take_cf
            if short > 1e-12:
                extra = min(out["cashflow"] - take_cf, short)
                take_cf += max(0.0, extra)
            out["defense"] = max(0.0, out["defense"] - take_def)
            out["cashflow"] = max(0.0, out["cashflow"] - take_cf)
            # 实际能抽到的可能不足，回写进攻
            out["offense"] = before + take_def + take_cf
        return out

    def _overlay_risk_regime(
        self,
        group_w: Dict[str, float],
        *,
        pred: int,
        confidence: float,
        regime: str,
    ) -> Tuple[Dict[str, float], bool, bool]:
        """
        最终风控层：敢砍 / 敢加。
        返回 (group_w, risk_off, risk_on)。二者互斥，risk_off 优先。
        """
        conf = float(confidence)
        risk_off = False
        risk_on = False
        out = {k: float(v) for k, v in group_w.items()}

        ro = self.risk_off_cfg
        if bool(ro.get("enabled", True)):
            hard = (
                int(pred) == int(ro.get("bearish_pred", -1))
                and conf >= float(ro.get("conf_min", 0.70))
            )
            soft = bool(ro.get("also_on_bear_regime", False)) and regime == "bear"
            if hard or soft:
                scale = float(ro.get("offense_scale", 0.42)) if hard else float(
                    ro.get("bear_regime_scale", 0.55)
                )
                scaled = float(out["offense"]) * scale
                cap = ro.get("offense_cap")
                if cap is not None:
                    scaled = min(scaled, float(cap))
                floor = float(ro.get("offense_floor", 0.15))
                hi = float((self.group_caps.get("offense") or {}).get("max", 0.80))
                scaled = float(np.clip(scaled, floor, hi))
                out = self._apply_group_shift(
                    out,
                    new_offense=scaled,
                    take_from_defense=float(ro.get("release_to_defense", 0.75)),
                )
                # 风控期允许突破常态 group min
                out = self._project_group_caps_relaxed(
                    out,
                    offense_min=floor,
                    offense_max=hi,
                )
                risk_off = True

        rn = self.risk_on_cfg
        if (not risk_off) and bool(rn.get("enabled", True)):
            hard = (
                int(pred) == int(rn.get("bullish_pred", 1))
                and conf >= float(rn.get("conf_min", 0.70))
            )
            soft = bool(rn.get("also_on_bull_regime", False)) and regime == "bull"
            if hard or soft:
                scale = float(rn.get("offense_scale", 1.22)) if hard else float(
                    rn.get("bull_regime_scale", 1.12)
                )
                scaled = float(out["offense"]) * scale
                floor = float(rn.get("offense_floor", 0.55))
                scaled = max(scaled, floor)
                hi = float((self.group_caps.get("offense") or {}).get("max", 0.80))
                lo = float((self.group_caps.get("offense") or {}).get("min", 0.22))
                scaled = float(np.clip(scaled, lo, hi))
                out = self._apply_group_shift(
                    out,
                    new_offense=scaled,
                    take_from_defense=float(rn.get("take_from_defense", 0.70)),
                )
                out = self._project_group_caps(out)
                risk_on = True

        return out, risk_off, risk_on

    def _project_group_caps_relaxed(
        self,
        weights: Dict[str, float],
        *,
        offense_min: float,
        offense_max: float,
    ) -> Dict[str, float]:
        """临时替换进攻上下限再投影。"""
        saved = dict(self.group_caps.get("offense") or {})
        tmp = dict(saved)
        tmp["min"] = float(offense_min)
        tmp["max"] = float(offense_max)
        self.group_caps["offense"] = tmp
        try:
            return self._project_group_caps(weights)
        finally:
            self.group_caps["offense"] = saved

    def resolve(
        self,
        sleeve_returns: pd.DataFrame,
        asof: pd.Timestamp,
        *,
        row: Optional[pd.Series] = None,
        pred: int = 0,
        confidence: float = 0.5,
        force_rebalance: bool = False,
        trading_dates: Optional[pd.Series] = None,
    ) -> PortfolioWeights:
        asof = pd.Timestamp(asof).normalize()
        if not self.should_rebalance(asof, trading_dates=trading_dates, force=force_rebalance):
            if self._last_weights is not None:
                return self._last_weights

        group_w = self._group_sharpe_weights(sleeve_returns, asof)
        regime, regime_label = self._classify_simple_regime(row)

        # 组内：进攻/防守走互补配对；现金流逆波动
        within: Dict[str, Dict[str, float]] = {}
        for g, members in GROUPS.items():
            within[g] = self._within_group_weights(sleeve_returns, asof, g, members)

        # 进攻组：在互补基线上有限 LLM tilt
        tilt_applied = False
        base_csi = float(within["offense"].get("csi300", 0.50))
        csi_share = base_csi
        if bool(self.llm_tilt_cfg.get("enabled", True)):
            csi_share = self._csi300_offense_share(
                int(pred), float(confidence), base_share=base_csi,
            )
            within["offense"] = self._project_pair_shares(
                {"csi300": csi_share, "nasdaq": 1.0 - csi_share}
            )
            csi_share = float(within["offense"]["csi300"])
            tilt_applied = abs(csi_share - base_csi) > 1e-6
        else:
            csi_share = base_csi

        weights = self._rebuild_from_groups(group_w, within)

        # EMA 平滑（先平滑基线，再叠最终风控，避免敢砍/敢加被平滑吃掉）
        if self.weight_ema_span and self.weight_ema_span > 1:
            alpha = 2.0 / (self.weight_ema_span + 1.0)
            for k, v in weights.items():
                prev = self._ema.get(k, v)
                self._ema[k] = alpha * v + (1.0 - alpha) * prev
            weights = dict(self._ema)
            s = sum(weights.values()) or 1.0
            weights = {k: float(v) / s for k, v in weights.items()}

        s = sum(max(0.0, float(v)) for v in weights.values()) or 1.0
        weights = {k: float(max(0.0, v)) / s for k, v in weights.items()}
        group_w = {
            g: float(sum(weights[m] for m in members))
            for g, members in GROUPS.items()
        }
        group_w = self._project_group_caps(group_w)

        # 保持组内相对比例
        rebuilt = {s: 0.0 for s in SLEEVES}
        for g, members in GROUPS.items():
            sub = sum(float(weights.get(m, 0.0)) for m in members)
            if sub <= 1e-12:
                share = {m: 1.0 / len(members) for m in members}
            else:
                share = {m: float(weights.get(m, 0.0)) / sub for m in members}
            within[g] = share
            for m in members:
                rebuilt[m] = group_w[g] * share[m]
        weights = rebuilt

        # 最终敢砍 / 敢加
        group_w, risk_off, risk_on = self._overlay_risk_regime(
            group_w,
            pred=int(pred),
            confidence=float(confidence),
            regime=regime,
        )
        weights = self._rebuild_from_groups(group_w, within)
        weights = self._blend_toward_prev(weights, risk_off=risk_off)
        group_w = {
            g: float(sum(weights[m] for m in members))
            for g, members in GROUPS.items()
        }

        pw = PortfolioWeights(
            weights=weights,
            group_weights=group_w,
            regime=regime,
            regime_label=regime_label,
            llm_tilt=tilt_applied,
            risk_off=risk_off,
            layers={
                "group_raw": dict(group_w),
                "csi300_offense_share": csi_share,
                "lookback": self.lookback,
                "risk_on": risk_on,
                "rebalance_step": self.rebalance_step,
                "trade_deadband": self.trade_deadband,
            },
        )
        self._last_weights = pw
        self._last_rebalance_date = str(asof.date()) if hasattr(asof, "date") else str(asof)[:10]
        return pw


# 可交易袖口（ETF 等）：权重变动按 |Δw|*(commission+slippage) 计费。
# repo 视为现金残余，不计手续费。
FEE_SLEEVES = ("csi300", "nasdaq", "gold", "bond", "mm")
OFFENSE_SLEEVES = ("csi300", "nasdaq")


def _sleeve_turnover_cost(
    weights: Dict[str, float],
    prev_weights: Optional[Dict[str, float]],
    commission: float,
    slippage: float,
) -> Tuple[float, float]:
    """
    返回 (total_cost, offense_cost)。
    买卖两侧都计费：cost = Σ_{fee sleeves} |Δw_i| * (commission + slippage)。
    """
    prev = prev_weights or {}
    rate = float(commission) + float(slippage)
    if rate <= 0:
        return 0.0, 0.0
    deltas = {
        k: abs(float(weights.get(k, 0.0) or 0.0) - float(prev.get(k, 0.0) or 0.0))
        for k in FEE_SLEEVES
    }
    traded = float(sum(deltas.values()))
    if traded <= 0:
        return 0.0, 0.0
    cost = traded * rate
    off_traded = float(sum(deltas[k] for k in OFFENSE_SLEEVES))
    offense_cost = cost * (off_traded / traded)
    return cost, offense_cost


def compute_unified_period_pnl(
    weights: Dict[str, float],
    day_row: pd.Series,
    *,
    prev_weights: Optional[Dict[str, float]] = None,
    prev_csi300: Optional[float] = None,
    commission: float,
    slippage: float,
    earn_weights: Optional[Dict[str, float]] = None,
) -> Tuple[float, float, float, float]:
    """
    返回 (total_pnl, offense_pnl, defensive_pnl, cost)。
    defensive = defense + cashflow。
    手续费按可交易袖口权重变动量计（不再只扣 CSI300）。
    prev_csi300 仅兼容旧调用：无 prev_weights 时用其近似 CSI 侧变动。

    earn_weights:
      - None（默认）：毛收益按 ``weights``（调仓后目标权重）计 —— 旧行为/兼容测试。
      - 传入隔夜权重：毛收益按隔夜仓计，成本仍按 prev→weights；对应「收盘调仓、次日生效」。
    """
    ret_map = {
        "csi300": float(day_row.get("csi300_ret", 0.0) or 0.0)
        if pd.notna(day_row.get("csi300_ret")) else 0.0,
        "nasdaq": float(day_row.get("nasdaq_ret", 0.0) or 0.0)
        if pd.notna(day_row.get("nasdaq_ret")) else 0.0,
        "gold": float(day_row.get("gold_ret", 0.0) or 0.0)
        if pd.notna(day_row.get("gold_ret")) else 0.0,
        "bond": float(day_row.get("bond_ret", 0.0) or 0.0)
        if pd.notna(day_row.get("bond_ret")) else 0.0,
        "mm": float(day_row.get("mm_ret", 0.0) or 0.0)
        if pd.notna(day_row.get("mm_ret")) else 0.0,
        "repo": float(day_row.get("repo_daily_ret", 0.0) or 0.0)
        if pd.notna(day_row.get("repo_daily_ret")) else 0.0,
    }
    if prev_weights is None and prev_csi300 is not None:
        prev_weights = {"csi300": float(prev_csi300)}
    holding = earn_weights if earn_weights is not None else weights
    gross = sum(float(holding.get(k, 0.0) or 0.0) * ret_map[k] for k in SLEEVES)
    cost, offense_cost = _sleeve_turnover_cost(weights, prev_weights, commission, slippage)
    total = gross - cost
    offense = (
        float(holding.get("csi300", 0.0) or 0.0) * ret_map["csi300"]
        + float(holding.get("nasdaq", 0.0) or 0.0) * ret_map["nasdaq"]
    ) - offense_cost
    defensive = total - offense
    return float(total), float(offense), float(defensive), float(cost)

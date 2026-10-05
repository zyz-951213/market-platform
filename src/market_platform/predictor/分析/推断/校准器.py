"""
「平」类校准 — API 优先、特征门控

设计原则：
1. API 高置信方向不轻易改动（避免 42%→34% 的负优化）
2. 「平」仅在特征支撑或 API 本人判平时保留/新增
3. 不用硬比例刷平；用 flat_score + 预期小幅波动 与标签 ±threshold 对齐
"""

from __future__ import annotations


from typing import Any, Dict, Optional, Tuple

import numpy as np
import pandas as pd

from market_platform.predictor.配置 import get_config
from market_platform.predictor.分析.提示词.构建器 import LABEL_TO_TEXT


def get_calibration_config() -> dict:
    return get_config().get("model", {}).get("llm", {}).get("calibration", {})


def get_label_threshold() -> float:
    return float(get_config().get("features", {}).get("label", {}).get("direction_threshold", 0.004))


class PredictionCalibrator:
    """API 输出 + 平盘门控（轻量修正）"""

    def __init__(self, cfg: Optional[dict] = None):
        self.cfg = dict(cfg or get_calibration_config())
        self.feature_cfg = dict(self.cfg.get("features", {}))
        self.flat_cfg = dict(self.cfg.get("flat_policy", {}))
        self.promote_to_flat = bool(self.cfg.get("promote_to_flat", False))
        raw_providers = self.cfg.get("promote_to_flat_providers")
        if isinstance(raw_providers, str):
            raw_providers = [raw_providers]
        self.promote_to_flat_providers = {
            str(x).strip() for x in (raw_providers or []) if str(x).strip()
        }

    def promote_enabled_for(self, provider: Optional[str] = None) -> bool:
        """全局 promote_to_flat 开启，且（未配置白名单 或 渠道在白名单内）。"""
        if not self.promote_to_flat:
            return False
        if not self.promote_to_flat_providers:
            return True
        if not provider:
            return False
        return provider in self.promote_to_flat_providers

    @property
    def enabled(self) -> bool:
        return bool(self.cfg.get("enabled", True))

    def flat_likelihood(self, row: pd.Series, window: Optional[pd.DataFrame] = None) -> float:
        """震荡似然 0~1：越高越像窄幅盘整"""
        score = 0.0
        weight = 0.0

        mom5 = row.get("momentum_5d")
        mom_max = float(self.feature_cfg.get("momentum_5d_max", 0.003))
        if pd.notna(mom5):
            score += max(0.0, 1.0 - abs(float(mom5)) / max(mom_max, 1e-8)) * 0.28
            weight += 0.28

        vol = row.get("volatility_20d")
        vol_max = float(self.feature_cfg.get("volatility_20d_max", 0.012))
        if pd.notna(vol):
            score += max(0.0, 1.0 - float(vol) / max(vol_max, 1e-8)) * 0.24
            weight += 0.24

        rsi = row.get("rsi_14")
        center = float(self.feature_cfg.get("rsi_neutral_center", 50))
        band = float(self.feature_cfg.get("rsi_neutral_band", 12))
        if pd.notna(rsi):
            score += max(0.0, 1.0 - abs(float(rsi) - center) / max(band, 1e-8)) * 0.18
            weight += 0.18

        macd = row.get("macd_hist")
        macd_max = float(self.feature_cfg.get("macd_hist_max", 0.015))
        if pd.notna(macd):
            score += max(0.0, 1.0 - abs(float(macd)) / max(macd_max, 1e-8)) * 0.15
            weight += 0.15

        if window is not None and len(window) >= 4 and "close" in window.columns:
            recent = window["close"].dropna().tail(5)
            if len(recent) >= 2:
                rng = float(recent.max() / recent.min() - 1)
                rng_max = float(self.feature_cfg.get("recent_range_max", 0.008))
                score += max(0.0, 1.0 - rng / max(rng_max, 1e-8)) * 0.15
                weight += 0.15

        return float(min(max(score / weight, 0.0), 1.0)) if weight > 0 else 0.0

    def expected_small_move(self, row: pd.Series) -> float:
        """与标签 threshold 对齐：特征是否暗示次日涨跌落在平盘带内"""
        th = get_label_threshold()
        mom5 = row.get("momentum_5d")
        vol = row.get("volatility_20d")
        if not pd.notna(mom5):
            return 0.0
        mom_abs = abs(float(mom5))
        vol_val = float(vol) if pd.notna(vol) else 0.012
        if mom_abs <= th * 0.75 and vol_val <= 0.011:
            return 0.75
        if mom_abs <= th * 1.25 and vol_val <= 0.013:
            return 0.50
        if mom_abs <= th * 2.0:
            return 0.25
        return 0.0

    def _transformer_signal(self, row: pd.Series) -> Tuple[Optional[int], float]:
        """量价 Transformer 通道：优先用 tf_proba_* / tf_pred_direction。"""
        if not bool(self.cfg.get("use_transformer_signal", True)):
            return None, 0.0
        pu = row.get("tf_proba_up")
        pf = row.get("tf_proba_flat")
        pdn = row.get("tf_proba_down")
        if pd.notna(pu) and pd.notna(pf) and pd.notna(pdn):
            probs = np.array([float(pdn), float(pf), float(pu)], dtype=float)
            if not np.isfinite(probs).all():
                return None, 0.0
            order = probs.argsort()
            top, second = float(probs[order[-1]]), float(probs[order[-2]])
            edge = top - second
            direction = int((-1, 0, 1)[int(order[-1])])
            min_edge = float(self.cfg.get("tf_min_edge", 0.015))
            # 平：需一定优势才采纳；涨/跌：小优势也可参与共振/分歧
            if direction == 0:
                if edge < max(min_edge, 0.02) or top < 0.34:
                    return None, 0.0
                return 0, float(min(1.0, 0.40 + edge * 3.0 + top * 0.35))
            if edge < min_edge and top < 0.36:
                return None, 0.0
            strength = float(min(1.0, 0.32 + edge * 4.5 + max(0.0, top - 1.0 / 3) * 2.2))
            return direction, strength
        pred = row.get("tf_pred_direction")
        if pd.notna(pred):
            direction = int(pred)
            if direction == 0:
                return None, 0.0
            return direction, 0.42
        return None, 0.0

    def _technical_signal(self, row: pd.Series) -> Tuple[Optional[int], float]:
        tf_dir, tf_strength = self._transformer_signal(row)
        if tf_dir is not None:
            return tf_dir, tf_strength

        mom_th = float(self.feature_cfg.get("momentum_dir_min", 0.0012))
        direction = 0
        strength = 0.0

        mom5 = row.get("momentum_5d")
        if pd.notna(mom5):
            mom = float(mom5)
            if abs(mom) >= mom_th:
                direction = 1 if mom > 0 else -1
                strength = min(1.0, abs(mom) / mom_th) * 0.5

        rsi = row.get("rsi_14")
        if pd.notna(rsi):
            rv = float(rsi)
            if rv >= float(self.feature_cfg.get("rsi_bull", 55)):
                direction = direction or 1
                strength = max(strength, 0.35)
            elif rv <= float(self.feature_cfg.get("rsi_bear", 45)):
                direction = direction or -1
                strength = max(strength, 0.35)

        if direction == 0:
            return None, 0.0
        return direction, float(min(strength, 1.0))

    def _credible_flat_score(self, flat_score: float, small_move: float) -> float:
        """综合震荡 + 预期小幅波动"""
        w_flat = float(self.flat_cfg.get("flat_weight", 0.55))
        w_small = float(self.flat_cfg.get("small_move_weight", 0.45))
        return w_flat * flat_score + w_small * small_move

    def _trend_strength(self, row: pd.Series) -> float:
        """趋势强度：越高越不宜判平"""
        strength = 0.0
        mom20 = row.get("momentum_20d")
        if pd.notna(mom20):
            strength = max(strength, min(1.0, abs(float(mom20)) / 0.025))
        mom5 = row.get("momentum_5d")
        if pd.notna(mom5):
            strength = max(strength, min(1.0, abs(float(mom5)) / 0.012))
        macd = row.get("macd_hist")
        if pd.notna(macd):
            strength = max(strength, min(1.0, abs(float(macd)) / 0.012))
        return float(strength)

    def _momentum_exhaustion_score(self, row: pd.Series) -> float:
        """动量极端 + 波动不高 → 次日易落入平盘带"""
        mom5 = row.get("momentum_5d")
        vol = row.get("volatility_20d")
        rsi = row.get("rsi_14")
        score = 0.0
        exhaust_mom = float(self.feature_cfg.get("momentum_exhaust_mom", 0.015))
        exhaust_vol = float(self.feature_cfg.get("momentum_exhaust_vol", 0.014))
        if pd.notna(mom5) and pd.notna(vol):
            m, v = abs(float(mom5)), float(vol)
            if m >= exhaust_mom and v <= exhaust_vol:
                score = max(score, min(1.0, 0.5 + (m - exhaust_mom) / 0.02))
        if pd.notna(rsi):
            rv = float(rsi)
            ob = float(self.feature_cfg.get("rsi_overbought", 68))
            os_ = float(self.feature_cfg.get("rsi_oversold", 32))
            if rv >= ob or rv <= os_:
                score = max(score, 0.55)
        return float(score)

    def _reversal_mismatch_score(self, api_label: int, row: pd.Series) -> float:
        """API 方向与短期动量背离 → 涨→跌/跌→涨 类误判风险"""
        if api_label == 0:
            return 0.0
        mom5 = row.get("momentum_5d")
        if not pd.notna(mom5):
            return 0.0
        m = float(mom5)
        rev_min = float(self.feature_cfg.get("reversal_mom_min", 0.012))
        if api_label == 1 and m <= -rev_min:
            return min(1.0, abs(m) / rev_min * 0.65)
        if api_label == -1 and m >= rev_min:
            return min(1.0, m / rev_min * 0.65)
        return 0.0

    def calibrate(
        self,
        parsed: Dict[str, Any],
        row: pd.Series,
        window: Optional[pd.DataFrame] = None,
        *,
        provider: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not self.enabled or not parsed.get("parse_ok", True):
            return parsed

        result = dict(parsed)
        if result.get("label") is None:
            return result

        api_label = int(result["label"])
        api_conf = float(result.get("confidence", 0.55))
        label = api_label
        confidence = api_conf
        allow_promote = self.promote_enabled_for(provider)

        flat_score = self.flat_likelihood(row, window=window)
        small_move = self.expected_small_move(row)
        credible = self._credible_flat_score(flat_score, small_move)
        tech_dir, tech_strength = self._technical_signal(row)
        trend_strength = self._trend_strength(row)
        exhaustion = self._momentum_exhaustion_score(row)
        reversal = self._reversal_mismatch_score(api_label, row)
        credible = max(credible, exhaustion * 0.35, reversal * 0.30)

        result["flat_score"] = round(flat_score, 4)
        result["small_move_score"] = round(small_move, 4)
        result["credible_flat"] = round(credible, 4)
        result["trend_strength"] = round(trend_strength, 4)
        result["exhaustion_score"] = round(exhaustion, 4)
        result["reversal_score"] = round(reversal, 4)
        result["promote_to_flat_applied"] = allow_promote

        preserve_conf = float(self.flat_cfg.get("preserve_api_conf", 0.70))
        promote_min = float(self.flat_cfg.get("promote_flat_min", 0.28))
        promote_conf_max = float(self.flat_cfg.get("promote_flat_conf_max", 0.68))
        retain_flat_min = float(self.flat_cfg.get("retain_api_flat_min", 0.10))
        demote_flat_max = float(self.flat_cfg.get("demote_api_flat_max", 0.08))
        strong_chop_min = float(self.flat_cfg.get("strong_chop_min", 0.55))
        reversal_flat_min = float(self.flat_cfg.get("reversal_flat_min", 0.55))
        exhaustion_flat_min = float(self.flat_cfg.get("exhaustion_flat_min", 0.50))

        reason_bits = []

        # A) API 高置信方向：仅共振加权，不改标签
        if api_label != 0 and api_conf >= preserve_conf:
            if tech_dir == api_label:
                confidence = min(0.90, api_conf + 0.06 + tech_strength * 0.05 + trend_strength * 0.04)
                reason_bits.append("高置信共振")
            elif trend_strength >= 0.45:
                confidence = min(0.88, api_conf + trend_strength * 0.05)
                reason_bits.append("趋势强化")

        # B) API 判平
        elif api_label == 0:
            if credible >= retain_flat_min:
                label = 0
                confidence = max(api_conf, 0.48 + credible * 0.25)
                reason_bits.append("保留API平")
            elif credible < demote_flat_max and tech_dir is not None and tech_strength >= 0.4:
                label = tech_dir
                confidence = max(0.55, 0.50 + tech_strength * 0.15)
                reason_bits.append(f"低波平反查→{LABEL_TO_TEXT[tech_dir]}")
            else:
                label = 0
                confidence = api_conf

        # C) API 方向但可信度一般（转平仅白名单渠道）
        else:
            trend_block = trend_strength >= 0.45 and tech_dir == api_label

            if allow_promote and reversal >= reversal_flat_min and api_conf < preserve_conf:
                label = 0
                confidence = max(0.50, 0.46 + reversal * 0.25)
                reason_bits.append(f"动量背离→平({reversal:.2f})")
            elif allow_promote and exhaustion >= exhaustion_flat_min and api_conf < preserve_conf and not trend_block:
                label = 0
                confidence = max(0.50, 0.45 + exhaustion * 0.28)
                reason_bits.append(f"动量衰竭→平({exhaustion:.2f})")
            elif allow_promote and credible >= strong_chop_min and not trend_block:
                label = 0
                confidence = max(0.50, 0.45 + credible * 0.3)
                reason_bits.append(f"强震荡→平({credible:.2f})")
            elif (
                allow_promote
                and credible >= promote_min
                and api_conf <= promote_conf_max
                and trend_strength < 0.35
                and (tech_dir is None or tech_strength < 0.38)
            ):
                label = 0
                confidence = max(0.48, 0.42 + credible * 0.35)
                reason_bits.append(f"可信震荡→平({credible:.2f})")
            elif tech_dir == api_label and self.cfg.get("signal_fusion", True):
                boost = float(self.cfg.get("agreement_conf_boost", 0.06))
                confidence = min(0.85, api_conf + boost + tech_strength * 0.05 + trend_strength * 0.04)
                reason_bits.append("量价共振" if row.get("tf_proba_up") is not None and pd.notna(row.get("tf_proba_up")) else "技术共振")
            elif (
                self.cfg.get("signal_fusion", True)
                and bool(self.cfg.get("tf_disagree_to_flat", True))
                and allow_promote
                and tech_dir is not None
                and tech_dir != api_label
                and api_label != 0
                and api_conf <= float(self.cfg.get("tf_disagree_conf_max", 0.70))
                and tech_strength >= float(self.cfg.get("tf_disagree_strength_min", 0.50))
                and not trend_block
            ):
                label = 0
                confidence = max(0.48, 0.44 + tech_strength * 0.20)
                reason_bits.append(f"LLM×TF分歧→平(tf={LABEL_TO_TEXT.get(tech_dir, tech_dir)})")
            elif trend_strength >= 0.50 and api_label != 0:
                confidence = min(0.82, api_conf + 0.04)
                reason_bits.append("趋势保留方向")
            else:
                label = api_label
                confidence = api_conf

        result["label"] = label
        result["direction"] = LABEL_TO_TEXT[label]
        result["confidence"] = float(min(max(confidence, 0.0), 1.0))
        result["tech_dir"] = tech_dir
        result["tech_strength"] = round(tech_strength, 4)
        result["tf_dir"] = tech_dir if (
            pd.notna(row.get("tf_proba_up")) or pd.notna(row.get("tf_pred_direction"))
        ) else None
        result["calibrated"] = label != api_label or bool(reason_bits)
        if reason_bits:
            base = str(result.get("reasoning", "")).strip()
            result["reasoning"] = (base + "；平校准: " + "，".join(reason_bits))[:120]
        return result

    def adjust_proba(self, proba: np.ndarray) -> np.ndarray:
        return proba


class ConfidenceReturnMapper:
    """
    置信度 → 历史同档位平均实际收益 → 仓位缩放

    例：判涨置信 0.75，历史上该档位平均次日收益 +0.3%，
    则 overlay 缩放 ≈ 历史期望收益 / label_threshold(0.3%)
    """

    def __init__(self, cfg: Optional[dict] = None):
        raw = cfg or get_config().get("backtest", {}).get("strategy", {}).get(
            "return_calibration", {}
        )
        self.cfg = dict(raw)
        self.enabled = bool(self.cfg.get("enabled", False))
        self.lookback = int(self.cfg.get("lookback", 252))
        self.min_samples = int(self.cfg.get("min_samples", 8))
        self.bin_width = float(self.cfg.get("conf_bin_width", 0.05))
        self.min_scale = float(self.cfg.get("min_scale", 0.10))
        self.max_scale = float(self.cfg.get("max_scale", 1.0))
        self.fallback_scale = float(self.cfg.get("fallback_scale", 0.40))
        self.widen_bins = int(self.cfg.get("widen_bins", 1))
        self.label_threshold = get_label_threshold()
        self._history: list = []

    def reset(self):
        self._history.clear()

    def _bucket(self, conf: float) -> int:
        w = max(self.bin_width, 1e-6)
        return int(np.clip(conf / w, 0, int(1.0 / w) - 1))

    def record(self, pred: int, confidence: float, label_return: float):
        if not self.enabled or pd.isna(label_return):
            return
        self._history.append({
            "pred": int(pred),
            "conf": float(confidence),
            "ret": float(label_return),
        })
        if len(self._history) > self.lookback:
            self._history = self._history[-self.lookback:]

    def _match_samples(self, pred: int, confidence: float) -> list:
        if not self._history:
            return []
        bucket = self._bucket(confidence)
        window = self._history[-self.lookback:]
        for widen in range(self.widen_bins + 1):
            matches = [
                h for h in window
                if h["pred"] == pred
                and abs(self._bucket(h["conf"]) - bucket) <= widen
            ]
            if len(matches) >= self.min_samples:
                return matches
        return []

    def expected_return(self, pred: int, confidence: float) -> Optional[float]:
        matches = self._match_samples(pred, confidence)
        if not matches:
            return None
        rets = [h["ret"] for h in matches]
        if pred == 1:
            return float(np.mean(rets))
        if pred == -1:
            return float(np.mean([-r for r in rets]))
        return float(np.mean([abs(r) for r in rets]))

    def overlay_scale(self, pred: int, confidence: float) -> float:
        """将历史期望收益映射为 overlay 缩放系数 [min_scale, max_scale]"""
        if not self.enabled or pred == 0:
            return 1.0
        exp_ret = self.expected_return(pred, confidence)
        if exp_ret is None:
            return self.fallback_scale
        if pred == 1:
            if exp_ret <= 0:
                return self.min_scale
            raw = exp_ret / max(self.label_threshold, 1e-8)
        else:
            if exp_ret <= 0:
                return self.min_scale
            raw = exp_ret / max(self.label_threshold, 1e-8)
        return float(np.clip(raw, self.min_scale, self.max_scale))


class ConfidenceIsotonicCalibrator:
    """滚动 isotonic：将 API 置信度映射为历史同档实际命中率"""

    def __init__(self, cfg: Optional[dict] = None):
        raw = cfg or get_config().get("backtest", {}).get("strategy", {}).get(
            "confidence_isotonic", {}
        )
        self.cfg = dict(raw)
        self.enabled = bool(self.cfg.get("enabled", False))
        self.lookback = int(self.cfg.get("lookback", 400))
        self.min_samples = int(self.cfg.get("min_samples", 60))
        self.refit_every = int(self.cfg.get("refit_every", 25))
        self._history: list = []
        self._iso = None
        self._since_fit = 0

    def reset(self):
        self._history.clear()
        self._iso = None
        self._since_fit = 0

    def record(self, confidence: float, hit: int):
        if not self.enabled:
            return
        self._history.append((float(confidence), int(hit)))
        if len(self._history) > self.lookback:
            self._history = self._history[-self.lookback:]
        self._since_fit += 1

    def _fit(self):
        try:
            from sklearn.isotonic import IsotonicRegression
        except ImportError:
            self._iso = None
            return
        if len(self._history) < self.min_samples:
            self._iso = None
            return
        xs = np.array([h[0] for h in self._history], dtype=float)
        ys = np.array([h[1] for h in self._history], dtype=float)
        iso = IsotonicRegression(out_of_bounds="clip", y_min=0.05, y_max=0.95)
        iso.fit(xs, ys)
        self._iso = iso
        self._since_fit = 0

    def calibrate(self, confidence: float) -> float:
        if not self.enabled:
            return float(confidence)
        if self._iso is None or self._since_fit >= self.refit_every:
            self._fit()
        if self._iso is None:
            shrink = float(self.cfg.get("fallback_shrink", 0.88))
            return float(np.clip(confidence * shrink, 0.05, 0.95))
        return float(self._iso.predict([confidence])[0])

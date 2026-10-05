"""擅长因子综合：各 LLM 渠道当条件因子，因果滚动加权合成。

设计要点（避免「先看全样本再跟 qwen」）：
  - 不设固定冠军锚
  - 每日只用 i 之前的窗口估计：全局准确率、状态桶准确率、方向条件准确率
  - 权重 ∝ 条件准确率^power × 当日置信度
  - kimi/minimax 高度同质时合并为一票，降低双计
  - 分歧大时压置信（少触发 risk overlay），而非强行改方向

不调 LLM API：读已入库成员回测 signals。
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd
from loguru import logger

from market_platform.predictor.配置 import get_config
from market_platform.predictor.分析.推断.集合元模型 import (
    CLASS_ORDER,
    EnsemblePanel,
    load_ensemble_panel,
    _conf_to_proba,
)

SPECIALTY_PROVIDER = "specialty_factor"
DEFAULT_MEMBERS = ("deepseek", "qwen", "doubao", "kimi", "minimax")
DEFAULT_TWIN = ("kimi", "minimax")


def is_specialty_provider(provider: Optional[str]) -> bool:
    if not provider:
        return False
    if provider == SPECIALTY_PROVIDER:
        return True
    cfg = (get_config().get("model", {}).get("llm", {}).get("providers") or {}).get(provider) or {}
    return str(cfg.get("kind", "")).lower() in ("specialty", "specialty_factor")


def get_specialty_config() -> dict:
    llm = get_config().get("model", {}).get("llm", {})
    cfg = dict(llm.get("specialty_factor") or {})
    prov = (llm.get("providers") or {}).get(SPECIALTY_PROVIDER) or {}
    cfg.update({k: v for k, v in prov.items() if k not in ("model", "kind")})
    cfg.setdefault("members", list(DEFAULT_MEMBERS))
    cfg.setdefault("twin", list(DEFAULT_TWIN))
    cfg.setdefault("roll", 252)
    cfg.setdefault("min_bucket_n", 30)
    cfg.setdefault("min_global_n", 60)
    cfg.setdefault("acc_power", 2.0)
    cfg.setdefault("state_blend", 0.65)  # 条件准确率 vs 全局
    cfg.setdefault("dir_bonus", 0.15)    # 方向条件准确率加成权重
    cfg.setdefault("agree_conf_boost", 0.04)
    cfg.setdefault("disagree_conf_scale", 0.85)
    cfg.setdefault("dispersion_flat", 0.45)  # 多数票占比低于此 → 平 + 低置信
    return cfg


def _pick(available: Sequence[str], wanted: Sequence[str]) -> List[str]:
    return [m for m in wanted if m in available]


def _rolling_mean(x: np.ndarray, window: int, min_periods: int) -> np.ndarray:
    s = pd.Series(x.astype(float))
    out = s.rolling(window, min_periods=min_periods).mean().shift(1)
    return out.to_numpy(dtype=float)


def synthesize_specialty_day(
    *,
    preds: Dict[str, int],
    confs: Dict[str, float],
    weights: Dict[str, float],
    cfg: Optional[dict] = None,
) -> Tuple[int, float, dict]:
    """单日：按擅长权重软投票。"""
    cfg = {**get_specialty_config(), **(cfg or {})}
    twin = _pick(preds, cfg.get("twin") or DEFAULT_TWIN)

    # 有效选票：twin 合并
    votes: List[Tuple[str, int, float, float]] = []  # label, pred, conf, weight
    used = set()
    if len(twin) == 2:
        a, b = twin
        if int(preds[a]) == int(preds[b]):
            p = int(preds[a])
            c = 0.5 * (float(confs[a]) + float(confs[b]))
            w = 0.5 * (float(weights.get(a, 0.0)) + float(weights.get(b, 0.0)))
        else:
            p, c, w = 0, min(float(confs[a]), float(confs[b])), 0.5 * (
                float(weights.get(a, 0.0)) + float(weights.get(b, 0.0))
            )
        votes.append(("twin:" + "+".join(twin), p, c, w))
        used.update(twin)
    for m, p in preds.items():
        if m in used:
            continue
        votes.append((m, int(p), float(confs[m]), float(weights.get(m, 0.0))))

    score = {-1: 0.0, 0: 0.0, 1: 0.0}
    w_mass = 0.0
    for _lab, p, c, w in votes:
        ww = max(w, 1e-6) * max(c, 1e-6)
        score[int(p)] += ww
        w_mass += ww

    raw_preds = [p for _, p, _, _ in votes]
    agree = Counter(raw_preds).most_common(1)[0][1] / max(len(raw_preds), 1)
    meta: Dict[str, Any] = {
        "rule": "specialty_vote",
        "agreement": round(agree, 4),
        "weights": {k: round(float(v), 4) for k, v in weights.items()},
        "votes": {lab: {"pred": p, "conf": round(c, 3), "w": round(w, 4)} for lab, p, c, w in votes},
        "score": {str(k): round(v, 4) for k, v in score.items()},
    }

    disp_th = float(cfg.get("dispersion_flat", 0.45))
    if agree < disp_th:
        meta["rule"] = "specialty_dispersion_flat"
        return 0, float(np.clip(0.42 + 0.25 * agree, 0.42, 0.58)), meta

    pred = int(max(score, key=score.get))
    conf = float(score[pred] / max(w_mass, 1e-9))
    # 映射到可用置信区间
    conf = float(np.clip(0.45 + 0.5 * conf, 0.48, 0.92))
    if agree >= 0.75 and pred != 0:
        conf = float(np.clip(conf + float(cfg.get("agree_conf_boost", 0.04)), 0.48, 0.95))
        meta["rule"] = "specialty_agree"
    elif agree < 0.6:
        conf = float(np.clip(conf * float(cfg.get("disagree_conf_scale", 0.85)), 0.45, 0.85))
        meta["rule"] = "specialty_soft"

    return pred, conf, meta


class SpecialtyFactorPredictor:
    """鸭式接口：擅长因子综合预测器。"""

    CLASS_ORDER = list(CLASS_ORDER)

    def __init__(self, params: Optional[dict] = None):
        self.cfg = {**get_specialty_config(), **(params or {})}
        self.provider_name = SPECIALTY_PROVIDER
        self._panel: Optional[EnsemblePanel] = None
        self._date_to_i: Dict[pd.Timestamp, int] = {}
        self._out_pred: Optional[np.ndarray] = None
        self._out_proba: Optional[np.ndarray] = None
        self._out_meta: List[dict] = []
        self._last_meta: dict = {}
        self._df: Optional[pd.DataFrame] = None
        self._members: List[str] = []
        self._cache_hits = 0
        self._cache_misses = 0

    def reset_cache_stats(self) -> None:
        self._cache_hits = 0
        self._cache_misses = 0

    def cache_stats(self) -> dict:
        return {
            "hits": self._cache_hits,
            "misses": self._cache_misses,
            "mode": "specialty_factor",
            "members": list(self._members),
        }

    def set_api_cutoff_date(self, date: Optional[str]) -> None:
        """本渠道**不调用 LLM API**(只消费已入库的成员信号),因此 API 截断无意义。

        评审 C8:此处原为空实现(裸 return),外部会误以为"已设置截断"。现显式说明并留痕。
        """
        if date:
            logger.debug("{}: 该渠道只读缓存信号,忽略 api_cutoff_date={}", self.__class__.__name__, date)
        return

    def bind_dataframe(self, df: pd.DataFrame) -> None:
        self._df = df

    def _causal_weights_at(
        self,
        i: int,
        *,
        hits: Dict[str, np.ndarray],
        states: List[str],
        global_roll: Dict[str, np.ndarray],
    ) -> Dict[str, float]:
        """只用 <i 的信息估各成员当日权重。"""
        cfg = self.cfg
        roll = int(cfg.get("roll", 252))
        min_b = int(cfg.get("min_bucket_n", 30))
        power = float(cfg.get("acc_power", 2.0))
        state_blend = float(cfg.get("state_blend", 0.65))
        dir_bonus = float(cfg.get("dir_bonus", 0.15))
        st = states[i]
        lo = max(0, i - roll)
        weights: Dict[str, float] = {}
        assert self._panel is not None

        for m in self._members:
            g = global_roll[m][i]
            if not np.isfinite(g):
                g = 0.34

            # 状态桶准确率：[lo, i)
            h = n = 0
            for j in range(lo, i):
                if states[j] == st:
                    n += 1
                    h += int(hits[m][j])
            s_acc = (h / n) if n >= min_b else g

            # 方向条件：历史上「报今日同方向」的命中率
            p_today = int(self._panel.preds[m][i])
            dh = dn = 0
            for j in range(lo, i):
                if int(self._panel.preds[m][j]) == p_today:
                    dn += 1
                    dh += int(hits[m][j])
            d_acc = (dh / dn) if dn >= min_b else s_acc

            acc = (1.0 - state_blend) * float(g) + state_blend * float(s_acc)
            acc = (1.0 - dir_bonus) * acc + dir_bonus * float(d_acc)
            weights[m] = max(float(acc), 1e-3) ** power

        s = sum(weights.values()) or 1.0
        return {k: v / s for k, v in weights.items()}

    def train(self, train_df: pd.DataFrame) -> "SpecialtyFactorPredictor":
        wanted = list(dict.fromkeys(self.cfg.get("members") or DEFAULT_MEMBERS))
        panel = load_ensemble_panel(members=wanted)
        members = [m for m in wanted if m in panel.members]
        if len(members) < 2:
            raise RuntimeError(f"擅长因子成员不足: {panel.members}")

        self._panel = panel
        self._members = members
        self._date_to_i = {pd.Timestamp(d).normalize(): i for i, d in enumerate(panel.dates)}

        roll = int(self.cfg.get("roll", 252))
        min_g = int(self.cfg.get("min_global_n", 60))
        hits = {m: (panel.preds[m] == panel.actual).astype(float) for m in members}
        global_roll = {m: _rolling_mean(hits[m], roll, min_g) for m in members}

        n = len(panel.dates)
        out_pred = np.zeros(n, dtype=int)
        out_proba = np.zeros((n, 3), dtype=float)
        out_meta: List[dict] = []
        rule_counts: Counter = Counter()

        for i in range(n):
            preds = {m: int(panel.preds[m][i]) for m in members}
            confs = {m: float(panel.confs[m][i]) for m in members}
            if i < min_g:
                # 冷启动：等权
                w = {m: 1.0 / len(members) for m in members}
                meta_extra = {"warmup": True}
            else:
                w = self._causal_weights_at(
                    i,
                    hits=hits,
                    states=panel.states,
                    global_roll=global_roll,
                )
                meta_extra = {"warmup": False}

            pred, conf, meta = synthesize_specialty_day(
                preds=preds, confs=confs, weights=w, cfg=self.cfg,
            )
            meta.update(meta_extra)
            meta["date"] = str(pd.Timestamp(panel.dates[i]).date())
            out_pred[i] = int(pred)
            out_proba[i] = _conf_to_proba(int(pred), float(conf))
            out_meta.append(meta)
            rule_counts[meta.get("rule", "?")] += 1

        self._out_pred = out_pred
        self._out_proba = out_proba
        self._out_meta = out_meta
        acc = float((out_pred == panel.actual).mean())
        logger.info(
            "擅长因子已就绪: members={} days={} acc={:.3f} rules={}",
            members, n, acc, dict(rule_counts),
        )
        return self

    def _predict_at_date(self, date: pd.Timestamp) -> Tuple[int, np.ndarray, Optional[int], dict]:
        if self._out_pred is None or self._panel is None:
            raise RuntimeError("SpecialtyFactorPredictor 未 train")
        key = pd.Timestamp(date).normalize()
        i = self._date_to_i.get(key)
        if i is None:
            pos = self._panel.dates.searchsorted(key, side="right") - 1
            if pos < 0:
                return 0, np.array([1 / 3, 1 / 3, 1 / 3]), None, {"rule": "missing"}
            i = int(pos)
        pred = int(self._out_pred[i])
        proba = np.asarray(self._out_proba[i], dtype=float)
        meta = dict(self._out_meta[i])
        self._cache_hits += 1
        return pred, proba, pred, meta

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        row = X.iloc[-1]
        date = row.get("date", X.index[-1] if hasattr(X, "index") else None)
        pred, _, _, _ = self._predict_at_date(pd.Timestamp(date))
        return np.array([pred], dtype=int)

    def predict_proba(self, X: pd.DataFrame) -> np.ndarray:
        row = X.iloc[-1]
        date = row.get("date", X.index[-1] if hasattr(X, "index") else None)
        _, proba, _, _ = self._predict_at_date(pd.Timestamp(date))
        return np.array([proba], dtype=float)

    def predict_bundle(self, X: pd.DataFrame) -> Tuple[int, np.ndarray, Optional[int]]:
        row = X.iloc[-1]
        date = row.get("date", X.index[-1] if hasattr(X, "index") else None)
        pred, proba, week_pred, meta = self._predict_at_date(pd.Timestamp(date))
        self._last_meta = meta
        return pred, proba, week_pred

"""五模型角色化调仓：合成日频 pred/conf，再喂现有仓位引擎。

当前策略（因果）：
  - anchor=qwen、hedge=deepseek：按滚动准确率加权软投票
  - 同向确认抬置信；对冲显著更准时才允许改写锚方向
  - doubao/kimi/minimax 暂不直接改写方向（避免扁平风格拖累）

不调 LLM API：从已入库成员回测 signals 读同日预测。
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

ROLE_PROVIDER = "role_ensemble"

DEFAULT_DIRECTIONAL = ("deepseek", "qwen")
DEFAULT_CAUTIOUS = ("doubao", "kimi", "minimax")
DEFAULT_TWIN = ("kimi", "minimax")  # 高度同质，合并为一票


def is_role_provider(provider: Optional[str]) -> bool:
    if not provider:
        return False
    if provider == ROLE_PROVIDER:
        return True
    cfg = (get_config().get("model", {}).get("llm", {}).get("providers") or {}).get(provider) or {}
    return str(cfg.get("kind", "")).lower() in ("role", "role_ensemble")


def get_role_config() -> dict:
    llm = get_config().get("model", {}).get("llm", {})
    role = dict(llm.get("role_ensemble") or {})
    prov = (llm.get("providers") or {}).get(ROLE_PROVIDER) or {}
    role.update({k: v for k, v in prov.items() if k not in ("model", "kind")})
    role.setdefault("anchor", "qwen")
    role.setdefault("hedge", "deepseek")
    role.setdefault("directional", list(DEFAULT_DIRECTIONAL))
    role.setdefault("cautious", list(DEFAULT_CAUTIOUS))
    role.setdefault("twin", list(DEFAULT_TWIN))
    role.setdefault("bear_conf", 0.70)
    role.setdefault("bull_conf", 0.62)
    role.setdefault("hard_hedge_bear", 0.80)
    role.setdefault("soft_hedge_bull", 0.78)
    role.setdefault("conflict_flat_conf", 0.85)
    role.setdefault("risk_off_floor", 0.72)
    role.setdefault("risk_on_floor", 0.74)
    role.setdefault("confirm_bull_boost", 0.03)
    role.setdefault("confirm_bear_boost", 0.05)
    role.setdefault("bear_regime_hedge_conf", 0.80)
    role.setdefault("soft_bull_conf", 0.75)
    role.setdefault("dispersion_flat", 0.55)
    role.setdefault("dir_vote_weight", 1.5)
    role.setdefault("caut_vote_weight", 1.0)
    return role


def _pick_members(available: Sequence[str], wanted: Sequence[str]) -> List[str]:
    return [m for m in wanted if m in available]


def _majority(preds: Sequence[int]) -> Optional[int]:
    if not preds:
        return None
    lab, n = Counter(int(p) for p in preds).most_common(1)[0]
    if n < (len(preds) + 1) // 2:
        return None
    return int(lab)


def _majority_share(preds: Sequence[int]) -> float:
    if not preds:
        return 0.0
    return Counter(int(p) for p in preds).most_common(1)[0][1] / float(len(preds))


def synthesize_role_day(
    *,
    preds: Dict[str, int],
    confs: Dict[str, float],
    cfg: Optional[dict] = None,
    state: Optional[str] = None,
    anchor_acc: float = 0.35,
    hedge_acc: float = 0.35,
) -> Tuple[int, float, dict]:
    """单日角色合成 → (pred, confidence, meta)。

    因果加权：用截止日前的滚动准确率给 qwen/deepseek 加权软投票；
    平票或极低置信时回退到 qwen。
    """
    cfg = {**get_role_config(), **(cfg or {})}
    anchor_name = str(cfg.get("anchor", "qwen"))
    hedge_name = str(cfg.get("hedge", "deepseek"))

    if anchor_name not in preds:
        directional = _pick_members(preds, cfg.get("directional") or DEFAULT_DIRECTIONAL)
        if not directional:
            return 0, 0.5, {"rule": "missing"}
        anchor_name = directional[0]
    if hedge_name not in preds:
        hedge_name = anchor_name

    q_pred = int(preds[anchor_name])
    q_conf = float(confs[anchor_name])
    d_pred = int(preds[hedge_name])
    d_conf = float(confs[hedge_name])

    meta: Dict[str, Any] = {
        "rule": "role",
        "anchor": anchor_name,
        "hedge": hedge_name,
        "anchor_acc": round(float(anchor_acc), 4),
        "hedge_acc": round(float(hedge_acc), 4),
        "directional": {
            anchor_name: {"pred": q_pred, "conf": q_conf},
            hedge_name: {"pred": d_pred, "conf": d_conf},
        },
    }

    # 软投票：准确率×置信度 → 三类质量
    w_q = max(float(anchor_acc), 1e-3) ** 1.5
    w_d = max(float(hedge_acc), 1e-3) ** 1.5
    # 锚保底权重，避免完全丢掉 qwen
    anchor_floor = float(cfg.get("anchor_weight_floor", 0.55))
    w_sum = w_q + w_d
    w_q_n = max(anchor_floor, w_q / w_sum)
    w_d_n = 1.0 - w_q_n

    score = {-1: 0.0, 0: 0.0, 1: 0.0}
    score[q_pred] += w_q_n * q_conf
    score[d_pred] += w_d_n * d_conf
    pred = int(max(score, key=score.get))
    conf = float(score[pred] / max(w_q_n + w_d_n, 1e-9))
    # 同向确认：抬置信
    if q_pred == d_pred == pred and pred != 0:
        conf = float(np.clip(max(q_conf, d_conf) + float(cfg.get("confirm_bull_boost", 0.04)), 0.55, 0.95))
        meta["rule"] = "vote_confirm"
    elif pred == q_pred:
        conf = float(np.clip(max(conf, q_conf * 0.9), 0.45, 0.92))
        meta["rule"] = "vote_anchor"
    else:
        # 与锚分歧：仅当对冲明显更准且置信够才改写，否则回退锚
        edge = float(hedge_acc) - float(anchor_acc)
        min_edge = float(cfg.get("override_acc_edge", 0.02))
        min_conf = float(cfg.get("override_min_conf", 0.75))
        if edge >= min_edge and d_conf >= min_conf and pred == d_pred:
            conf = float(np.clip(d_conf - 0.02, 0.55, 0.90))
            meta["rule"] = "vote_override"
        else:
            pred, conf = q_pred, q_conf
            meta["rule"] = "vote_fallback_anchor"

    meta["vote_score"] = {str(k): round(v, 4) for k, v in score.items()}
    meta["weights"] = {"anchor": round(w_q_n, 3), "hedge": round(w_d_n, 3)}
    return int(pred), float(np.clip(conf, 0.42, 0.95)), meta


class RoleEnsemblePredictor:
    """鸭式接口对齐 LLMApiPredictor / MetaEnsemblePredictor。"""

    CLASS_ORDER = list(CLASS_ORDER)

    def __init__(self, params: Optional[dict] = None):
        self.cfg = {**get_role_config(), **(params or {})}
        self.provider_name = ROLE_PROVIDER
        self._panel: Optional[EnsemblePanel] = None
        self._date_to_i: Dict[pd.Timestamp, int] = {}
        self._out_pred: Optional[np.ndarray] = None
        self._out_proba: Optional[np.ndarray] = None
        self._out_meta: List[dict] = []
        self._last_meta: dict = {}
        self._df: Optional[pd.DataFrame] = None
        self._cache_hits = 0
        self._cache_misses = 0
        self._members: List[str] = []

    def reset_cache_stats(self) -> None:
        self._cache_hits = 0
        self._cache_misses = 0

    def cache_stats(self) -> dict:
        return {
            "hits": self._cache_hits,
            "misses": self._cache_misses,
            "mode": "role_ensemble",
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

    def train(self, train_df: pd.DataFrame) -> "RoleEnsemblePredictor":
        wanted = list(dict.fromkeys([
            str(self.cfg.get("anchor", "qwen")),
            str(self.cfg.get("hedge", "deepseek")),
            *self.cfg["directional"],
            *self.cfg["cautious"],
        ]))
        panel = load_ensemble_panel(members=wanted)
        members = [m for m in wanted if m in panel.members]
        # 去重且保持顺序
        members = list(dict.fromkeys(members))
        if len(members) < 3:
            raise RuntimeError(f"角色调仓成员不足: available={panel.members}, wanted={wanted}")

        self._panel = panel
        self._members = members
        self._date_to_i = {pd.Timestamp(d).normalize(): i for i, d in enumerate(panel.dates)}

        n = len(panel.dates)
        out_pred = np.zeros(n, dtype=int)
        out_proba = np.zeros((n, 3), dtype=float)
        out_meta: List[dict] = []
        rule_counts: Counter = Counter()

        anchor = str(self.cfg.get("anchor", "qwen"))
        hedge = str(self.cfg.get("hedge", "deepseek"))
        if anchor not in members:
            anchor = members[0]
        if hedge not in members:
            hedge = members[min(1, len(members) - 1)]
        roll = int(self.cfg.get("acc_roll", 252))
        # 因果滚动命中：只用 i 之前的窗口
        a_hits = (panel.preds[anchor] == panel.actual).astype(float)
        h_hits = (panel.preds[hedge] == panel.actual).astype(float)
        a_roll = pd.Series(a_hits).rolling(roll, min_periods=60).mean().shift(1)
        h_roll = pd.Series(h_hits).rolling(roll, min_periods=60).mean().shift(1)

        for i in range(n):
            preds = {m: int(panel.preds[m][i]) for m in members}
            confs = {m: float(panel.confs[m][i]) for m in members}
            aa = float(a_roll.iloc[i]) if pd.notna(a_roll.iloc[i]) else 0.35
            ha = float(h_roll.iloc[i]) if pd.notna(h_roll.iloc[i]) else 0.35
            pred, conf, meta = synthesize_role_day(
                preds=preds,
                confs=confs,
                cfg=self.cfg,
                state=panel.states[i],
                anchor_acc=aa,
                hedge_acc=ha,
            )
            out_pred[i] = int(pred)
            out_proba[i] = _conf_to_proba(int(pred), float(conf))
            meta["date"] = str(pd.Timestamp(panel.dates[i]).date())
            out_meta.append(meta)
            rule_counts[meta.get("rule", "?")] += 1

        self._out_pred = out_pred
        self._out_proba = out_proba
        self._out_meta = out_meta

        # 合成准确率（全样本仅供日志；回测仍按日因果使用预计算序列）
        acc = float((out_pred == panel.actual).mean())
        logger.info(
            "角色调仓已就绪: members={} days={} acc={:.3f} rules={}",
            members, n, acc, dict(rule_counts),
        )
        return self

    def _predict_at_date(self, date: pd.Timestamp) -> Tuple[int, np.ndarray, Optional[int], dict]:
        if self._out_pred is None or self._panel is None:
            raise RuntimeError("RoleEnsemblePredictor 未 train")
        key = pd.Timestamp(date).normalize()
        i = self._date_to_i.get(key)
        if i is None:
            # 最近不超过当日的面板点
            idx = self._panel.dates
            pos = idx.searchsorted(key, side="right") - 1
            if pos < 0:
                meta = {"rule": "missing_flat"}
                return 0, np.array([1 / 3, 1 / 3, 1 / 3]), None, meta
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

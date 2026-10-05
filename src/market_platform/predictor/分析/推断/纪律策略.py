"""纪律策略：从多模型行为抽象的仓位纪律（只吃已入库信号）。

原则（刻意不写死 qwen）：
  1. 因果选「风格健康」主信号：涨跌比接近、不太爱观望、滚动准确率尚可
  2. 主信号为平时：观望
  3. 主信号有方向：辅信号同向高置信 → 确认抬置信；反向高置信 → 否决降为平/低置信
  4. 不做多模型软投票追准确率

过拟合风险：规则灵感来自观察 LLM 行为；用因果主信号 + holdout 检验是否只是换皮跟 qwen。
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

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

DISCIPLINE_PROVIDER = "discipline_rule"
DEFAULT_MEMBERS = ("deepseek", "qwen", "doubao", "kimi", "minimax")


def is_discipline_provider(provider: Optional[str]) -> bool:
    if not provider:
        return False
    if provider == DISCIPLINE_PROVIDER:
        return True
    cfg = (get_config().get("model", {}).get("llm", {}).get("providers") or {}).get(provider) or {}
    return str(cfg.get("kind", "")).lower() in ("discipline", "discipline_rule")


def get_discipline_config() -> dict:
    llm = get_config().get("model", {}).get("llm", {})
    cfg = dict(llm.get("discipline_rule") or {})
    prov = (llm.get("providers") or {}).get(DISCIPLINE_PROVIDER) or {}
    cfg.update({k: v for k, v in prov.items() if k not in ("model", "kind")})
    cfg.setdefault("members", list(DEFAULT_MEMBERS))
    cfg.setdefault("roll", 252)
    cfg.setdefault("min_n", 60)
    cfg.setdefault("max_flat", 0.52)          # 风格：滚动报平率上限
    cfg.setdefault("min_balance", 0.55)       # min(up,dn)/max(up,dn)
    cfg.setdefault("aux_conf", 0.68)          # 辅信号确认/否决门槛
    cfg.setdefault("confirm_boost", 0.05)
    cfg.setdefault("solo_haircut", 0.04)      # 无确认时略压置信
    cfg.setdefault("veto_to_flat", True)
    cfg.setdefault("veto_conf", 0.50)
    cfg.setdefault("primary_conf_floor", 0.55)  # 主信号置信过低 → 平
    return cfg


def _style_score(preds: np.ndarray) -> Tuple[float, dict]:
    """越高越「健康」：涨跌对称、不太偏平。"""
    _ = max(len(preds), 1)  # 保留原式以求语义不变,变量未被使用
    up = float((preds == 1).mean())
    dn = float((preds == -1).mean())
    flat = float((preds == 0).mean())
    balance = (min(up, dn) / max(up, dn)) if max(up, dn) > 1e-9 else 0.0
    # 惩罚过度观望与涨跌失衡
    score = balance - max(0.0, flat - 0.45) * 1.2 - abs(up - dn) * 0.5
    return score, {"up": up, "dn": dn, "flat": flat, "balance": balance, "style": score}


def synthesize_discipline_day(
    *,
    preds: Dict[str, int],
    confs: Dict[str, float],
    primary: str,
    cfg: Optional[dict] = None,
) -> Tuple[int, float, dict]:
    cfg = {**get_discipline_config(), **(cfg or {})}
    p0 = int(preds[primary])
    c0 = float(confs[primary])
    aux = [m for m in preds if m != primary]
    meta: Dict[str, Any] = {
        "rule": "discipline_primary",
        "primary": primary,
        "primary_pred": p0,
        "primary_conf": round(c0, 3),
    }

    floor = float(cfg.get("primary_conf_floor", 0.55))
    if p0 == 0 or c0 < floor:
        meta["rule"] = "discipline_flat"
        return 0, float(np.clip(c0 if p0 == 0 else 0.48, 0.42, 0.58)), meta

    th = float(cfg.get("aux_conf", 0.68))
    agree = [m for m in aux if int(preds[m]) == p0 and float(confs[m]) >= th]
    conflict = [m for m in aux if int(preds[m]) == -p0 and float(confs[m]) >= th]
    meta["agree"] = agree
    meta["conflict"] = conflict

    if conflict and not agree:
        meta["rule"] = "discipline_veto"
        if bool(cfg.get("veto_to_flat", True)):
            return 0, float(cfg.get("veto_conf", 0.50)), meta
        return p0, float(np.clip(c0 - 0.12, 0.45, 0.70)), meta

    if agree:
        conf = float(np.clip(c0 + float(cfg.get("confirm_boost", 0.05)), 0.50, 0.95))
        meta["rule"] = "discipline_confirm"
        return p0, conf, meta

    conf = float(np.clip(c0 - float(cfg.get("solo_haircut", 0.04)), 0.48, 0.90))
    meta["rule"] = "discipline_solo"
    return p0, conf, meta


class DisciplineRulePredictor:
    """鸭式接口：纪律策略预测器。"""

    CLASS_ORDER = list(CLASS_ORDER)

    def __init__(self, params: Optional[dict] = None):
        self.cfg = {**get_discipline_config(), **(params or {})}
        self.provider_name = DISCIPLINE_PROVIDER
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
            "mode": "discipline_rule",
            "members": list(self._members),
        }

    def set_api_cutoff_date(self, date: Optional[str]) -> None:
        return

    def bind_dataframe(self, df: pd.DataFrame) -> None:
        self._df = df

    def _pick_primary(self, i: int) -> Tuple[str, dict]:
        """只用 <i 的窗口选主信号。"""
        assert self._panel is not None
        roll = int(self.cfg.get("roll", 252))
        min_n = int(self.cfg.get("min_n", 60))
        max_flat = float(self.cfg.get("max_flat", 0.52))
        min_bal = float(self.cfg.get("min_balance", 0.55))
        lo = max(0, i - roll)
        if i - lo < min_n:
            # 冷启动：临时用全员里风格分最高的（仍只用历史）
            best_m, best_s, best_info = self._members[0], -1e9, {}
            for m in self._members:
                s, info = _style_score(self._panel.preds[m][lo:i]) if i > lo else (0.0, {})
                if s > best_s:
                    best_m, best_s, best_info = m, s, info
            return best_m, {"warmup": True, **best_info}

        candidates = []
        for m in self._members:
            hist = self._panel.preds[m][lo:i]
            style, info = _style_score(hist)
            acc = float((hist == self._panel.actual[lo:i]).mean())
            ok = info["flat"] <= max_flat and info["balance"] >= min_bal
            # 综合分：风格合格者优先，再用准确率
            rank = (1 if ok else 0, style + acc)
            candidates.append((rank, m, {**info, "acc": acc, "eligible": ok}))

        candidates.sort(key=lambda x: x[0], reverse=True)
        _, primary, info = candidates[0]
        return primary, info

    def train(self, train_df: pd.DataFrame) -> "DisciplineRulePredictor":
        wanted = list(dict.fromkeys(self.cfg.get("members") or DEFAULT_MEMBERS))
        panel = load_ensemble_panel(members=wanted)
        members = [m for m in wanted if m in panel.members]
        if len(members) < 2:
            raise RuntimeError(f"纪律策略成员不足: {panel.members}")

        self._panel = panel
        self._members = members
        self._date_to_i = {pd.Timestamp(d).normalize(): i for i, d in enumerate(panel.dates)}

        n = len(panel.dates)
        out_pred = np.zeros(n, dtype=int)
        out_proba = np.zeros((n, 3), dtype=float)
        out_meta: List[dict] = []
        rule_counts: Counter = Counter()
        primary_counts: Counter = Counter()

        for i in range(n):
            primary, pinfo = self._pick_primary(i)
            preds = {m: int(panel.preds[m][i]) for m in members}
            confs = {m: float(panel.confs[m][i]) for m in members}
            pred, conf, meta = synthesize_discipline_day(
                preds=preds, confs=confs, primary=primary, cfg=self.cfg,
            )
            meta["primary_stats"] = {k: (round(v, 4) if isinstance(v, float) else v) for k, v in pinfo.items()}
            meta["date"] = str(pd.Timestamp(panel.dates[i]).date())
            out_pred[i] = int(pred)
            out_proba[i] = _conf_to_proba(int(pred), float(conf))
            out_meta.append(meta)
            rule_counts[meta.get("rule", "?")] += 1
            primary_counts[primary] += 1

        self._out_pred = out_pred
        self._out_proba = out_proba
        self._out_meta = out_meta
        acc = float((out_pred == panel.actual).mean())
        same_q = float((out_pred == panel.preds["qwen"]).mean()) if "qwen" in panel.preds else None
        logger.info(
            "纪律策略已就绪: members={} days={} acc={:.3f} same_qwen={} rules={} primary={}",
            members, n, acc,
            f"{same_q:.1%}" if same_q is not None else "n/a",
            dict(rule_counts), dict(primary_counts),
        )
        return self

    def _predict_at_date(self, date: pd.Timestamp) -> Tuple[int, np.ndarray, Optional[int], dict]:
        if self._out_pred is None or self._panel is None:
            raise RuntimeError("DisciplineRulePredictor 未 train")
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

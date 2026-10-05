"""
LLM 集合元模型：综合多渠道方向/置信度，再喂现有仓位引擎。

不调 LLM API：从已入库的各渠道回测 signals 读取同日预测。
模式：
  - stack（默认）：walk-forward stacking，特征含 raw / 反转双通道 + 因果条件准确率
  - rule：准确率加权投票 / 条件反转（消融对照）
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from loguru import logger

from market_platform.predictor.配置 import get_config
from market_platform.predictor.存储.数据访问 import load_index_features
from market_platform.predictor.接口.回测存储 import load_backtest_for_provider, strategy_fingerprint
from market_platform.predictor.接口.准确率分区 import _enrich_features


ENSEMBLE_PROVIDER = "ensemble"
CLASS_ORDER = (-1, 0, 1)
_LABEL_TO_Y = {-1: 0, 0: 1, 1: 2}
_Y_TO_LABEL = {0: -1, 1: 0, 2: 1}


def is_ensemble_provider(provider: Optional[str]) -> bool:
    if not provider:
        return False
    if provider == ENSEMBLE_PROVIDER:
        return True
    cfg = (get_config().get("model", {}).get("llm", {}).get("providers") or {}).get(provider) or {}
    return str(cfg.get("kind", "")).lower() == "ensemble"


def list_member_providers() -> List[str]:
    from market_platform.predictor.分析.推断.语言模型预测 import list_llm_providers
    from market_platform.predictor.分析.推断.语言模型因子 import is_llm_factor_provider
    from market_platform.predictor.分析.推断.角色调仓 import is_role_provider
    from market_platform.predictor.分析.推断.擅长因子 import is_specialty_provider
    from market_platform.predictor.分析.推断.纪律策略 import is_discipline_provider

    return [
        p for p in list_llm_providers()
        if not is_ensemble_provider(p)
        and not is_llm_factor_provider(p)
        and not is_role_provider(p)
        and not is_specialty_provider(p)
        and not is_discipline_provider(p)
    ]


def get_ensemble_config() -> dict:
    llm = get_config().get("model", {}).get("llm", {})
    ens = dict(llm.get("ensemble") or {})
    prov = (llm.get("providers") or {}).get(ENSEMBLE_PROVIDER) or {}
    ens.update({k: v for k, v in prov.items() if k not in ("model", "kind")})
    ens.setdefault("mode", "stack")
    ens.setdefault("min_bucket_n", 40)
    ens.setdefault("trust_acc", 0.50)
    ens.setdefault("invert_acc", 0.35)
    ens.setdefault("conf_floor", 0.42)
    ens.setdefault("conf_ceil", 0.88)
    ens.setdefault("agreement_min", 0.75)
    ens.setdefault("conf_threshold", 0.42)  # stacking 门控：低于则观望(0)
    ens.setdefault("min_train_days", 504)
    ens.setdefault("refit_every", 63)
    ens.setdefault("include_invert_features", True)
    return ens


def _state_key(row: pd.Series) -> str:
    parts = []
    for col in ("vol_q", "trend", "rsi_bucket", "above_sma60"):
        val = row.get(col)
        parts.append(str(val) if pd.notna(val) else "?")
    return "|".join(parts)


def _conf_to_proba(pred: int, conf: float) -> np.ndarray:
    conf = float(np.clip(conf, 0.34, 0.99))
    proba = np.full(3, (1.0 - conf) / 2.0, dtype=float)
    idx = CLASS_ORDER.index(int(pred))
    proba[idx] = conf
    s = proba.sum()
    if s > 0:
        proba /= s
    return proba


def _onehot_dir(pred: int) -> List[float]:
    return [1.0 if pred == c else 0.0 for c in CLASS_ORDER]


@dataclass
class _BucketStats:
    hits: int = 0
    n: int = 0

    @property
    def acc(self) -> float:
        return self.hits / self.n if self.n else 0.5


@dataclass
class EnsemblePanel:
    """对齐后的多渠道日频面板。"""
    dates: pd.DatetimeIndex
    actual: np.ndarray
    preds: Dict[str, np.ndarray]
    confs: Dict[str, np.ndarray]
    states: List[str]
    fingerprints: Dict[str, str] = field(default_factory=dict)

    @property
    def members(self) -> List[str]:
        return list(self.preds.keys())


def load_ensemble_panel(
    *,
    prefer_fingerprint: Optional[str] = None,
    members: Optional[List[str]] = None,
) -> EnsemblePanel:
    """加载成员渠道 signals，按 date 内连接。

    members: 若给定则只对齐这些渠道（避免 glm 等短序列裁短面板）。
    """
    cfg = get_config()
    raw = load_index_features(cfg["data"]["index_code"])
    if raw is None or raw.empty:
        raise FileNotFoundError("特征数据不存在")
    feat = _enrich_features(raw)

    wanted = list(members) if members else list_member_providers()
    frames: Dict[str, pd.DataFrame] = {}
    fingerprints: Dict[str, str] = {}
    prefer = prefer_fingerprint or strategy_fingerprint()

    for name in wanted:
        bt = load_backtest_for_provider(name)
        if not bt or not bt.get("signals"):
            logger.warning("集合元模型：渠道 {} 无回测信号，跳过", name)
            continue
        fp = str(bt.get("strategy_fingerprint") or "")
        fingerprints[name] = fp
        if prefer and fp and fp != prefer:
            logger.warning(
                "集合元模型：{} 指纹 {} ≠ 当前 {}，仍用最新信号",
                name, fp, prefer,
            )
        sdf = pd.DataFrame(bt["signals"]).copy()
        sdf["date"] = pd.to_datetime(sdf["date"])
        sdf = sdf.sort_values("date").drop_duplicates("date", keep="last")
        sdf = sdf.set_index("date")
        frames[name] = sdf[["prediction", "confidence", "actual"]].astype({
            "prediction": int,
            "confidence": float,
            "actual": float,
        })

    if len(frames) < 2:
        raise RuntimeError(f"集合元模型成员不足：仅 {list(frames)}")

    common = None
    for name, sdf in frames.items():
        idx = sdf.index
        common = idx if common is None else common.intersection(idx)
    common = common.sort_values()
    if len(common) < 100:
        raise RuntimeError(f"集合对齐交易日过少: {len(common)}")

    feat_idx = feat.reindex(common)
    states = [_state_key(feat_idx.loc[d]) if d in feat_idx.index else "?|?|?|?" for d in common]

    first = next(iter(frames))
    actual = frames[first].loc[common, "actual"].astype(int).to_numpy()

    preds = {n: frames[n].loc[common, "prediction"].astype(int).to_numpy() for n in frames}
    confs = {n: frames[n].loc[common, "confidence"].astype(float).to_numpy() for n in frames}

    logger.info(
        "集合面板就绪: {} 日 · {} 渠道 · 指纹样本 {}",
        len(common), list(frames), {k: v[:8] for k, v in fingerprints.items()},
    )
    return EnsemblePanel(
        dates=common,
        actual=actual,
        preds=preds,
        confs=confs,
        states=states,
        fingerprints=fingerprints,
    )


class MetaEnsemblePredictor:
    """
    鸭式接口对齐 LLMApiPredictor：
    train / bind_dataframe / predict / predict_proba / predict_bundle
    """

    CLASS_ORDER = list(CLASS_ORDER)

    def __init__(self, params: Optional[dict] = None):
        self.cfg = {**get_ensemble_config(), **(params or {})}
        self.provider_name = ENSEMBLE_PROVIDER
        self._panel: Optional[EnsemblePanel] = None
        self._date_to_i: Dict[pd.Timestamp, int] = {}
        self._bucket_hist: Dict[str, List[Tuple[str, int]]] = {}
        self._global_hits: Dict[str, _BucketStats] = {}
        self._df: Optional[pd.DataFrame] = None
        self._cache_hits = 0
        self._cache_misses = 0
        self._feat: Optional[pd.DataFrame] = None
        # 预计算：stack / rule 逐日结果
        self._out_pred: Optional[np.ndarray] = None
        self._out_proba: Optional[np.ndarray] = None
        self._out_meta: List[dict] = []
        self._last_meta: dict = {}
        self._X_all: Optional[np.ndarray] = None
        self._last_clf = None

    def reset_cache_stats(self) -> None:
        self._cache_hits = 0
        self._cache_misses = 0

    def cache_stats(self) -> dict:
        return {
            "hits": self._cache_hits,
            "misses": self._cache_misses,
            "mode": f"ensemble:{self.cfg.get('mode', 'stack')}",
        }

    def set_api_cutoff_date(self, date: Optional[str]) -> None:
        return

    def bind_dataframe(self, df: pd.DataFrame) -> None:
        self._df = df

    def train(self, train_df: pd.DataFrame) -> "MetaEnsemblePredictor":
        panel = load_ensemble_panel()
        self._panel = panel
        self._date_to_i = {pd.Timestamp(d).normalize(): i for i, d in enumerate(panel.dates)}
        self._bucket_hist = {}
        self._global_hits = {m: _BucketStats() for m in panel.members}
        for m in panel.members:
            hist: List[Tuple[str, int]] = []
            for i in range(len(panel.dates)):
                hit = int(panel.preds[m][i] == panel.actual[i])
                hist.append((panel.states[i], hit))
                self._global_hits[m].n += 1
                self._global_hits[m].hits += hit
            self._bucket_hist[m] = hist

        mode = str(self.cfg.get("mode", "stack")).lower()
        if mode == "stack":
            self._precompute_stack()
        else:
            self._precompute_rule()

        logger.info(
            "集合元模型已训练: mode={} members={} days={} conf_th={} invert_feat={}",
            mode, panel.members, len(panel.dates),
            self.cfg.get("conf_threshold"), self.cfg.get("include_invert_features"),
        )
        return self

    def _acc_before(self, member: str, index: int, state: str) -> float:
        min_n = int(self.cfg.get("min_bucket_n", 40))
        hist = self._bucket_hist.get(member) or []
        hits = n = 0
        for j in range(index):
            st, h = hist[j]
            if st == state:
                hits += h
                n += 1
        if n >= min_n:
            return hits / n
        gh = gn = 0
        for j in range(index):
            gh += hist[j][1]
            gn += 1
        if gn >= 20:
            return gh / gn
        g = self._global_hits.get(member) or _BucketStats()
        return g.acc if g.n else 0.5

    def _state_feats(self, state: str) -> List[float]:
        parts = (state or "||||").split("|")
        while len(parts) < 4:
            parts.append("?")
        vol, trend, rsi, sma = parts[:4]
        vol_map = {"低波": 0.0, "中波": 0.5, "高波": 1.0, "?": 0.5}
        trend_map = {"下跌段": 0.0, "震荡": 0.5, "上涨段": 1.0, "?": 0.5}
        rsi_map = {"超卖": 0.0, "中性": 0.5, "超买": 1.0, "?": 0.5}
        return [
            vol_map.get(vol, 0.5),
            trend_map.get(trend, 0.5),
            rsi_map.get(rsi, 0.5),
            1.0 if sma == "1" else (0.0 if sma == "0" else 0.5),
        ]

    def _row_features(
        self,
        index: int,
        preds: Dict[str, int],
        confs: Dict[str, float],
        accs: Dict[str, float],
        state: str,
    ) -> np.ndarray:
        include_inv = bool(self.cfg.get("include_invert_features", True))
        row: List[float] = []
        votes = []
        for m in preds:
            p = int(preds[m])
            c = float(confs[m])
            a = float(accs[m])
            votes.append(p)
            row.extend(_onehot_dir(p))
            row.append(c)
            row.append(a)
            row.append(a - 1.0 / 3.0)  # 相对随机的偏差：负值暗示可反转
            if include_inv:
                inv = 0 if p == 0 else -p
                row.extend(_onehot_dir(inv))
                # 「反转可信度」：准确率越低、非平仓时越大
                row.append(max(0.0, (1.0 / 3.0 - a)) if p != 0 else 0.0)
        cnt = Counter(votes)
        maj, maj_n = cnt.most_common(1)[0]
        agree = maj_n / max(len(votes), 1)
        row.extend([agree, float(len(cnt)), float(maj)])
        row.extend(self._state_feats(state))
        return np.asarray(row, dtype=float)

    def _build_feature_matrix(self) -> np.ndarray:
        assert self._panel is not None
        panel = self._panel
        rows = []
        for i in range(len(panel.dates)):
            state = panel.states[i]
            preds = {m: int(panel.preds[m][i]) for m in panel.members}
            confs = {m: float(panel.confs[m][i]) for m in panel.members}
            accs = {m: self._acc_before(m, i, state) for m in panel.members}
            rows.append(self._row_features(i, preds, confs, accs, state))
        return np.vstack(rows)

    def _new_clf(self):
        from sklearn.ensemble import HistGradientBoostingClassifier

        return HistGradientBoostingClassifier(
            max_depth=3,
            max_iter=120,
            learning_rate=0.06,
            min_samples_leaf=20,
            random_state=0,
        )

    def _precompute_stack(self) -> None:
        assert self._panel is not None
        panel = self._panel
        n = len(panel.dates)
        X = self._build_feature_matrix()
        self._X_all = X
        y = np.asarray([_LABEL_TO_Y[int(a)] for a in panel.actual], dtype=int)

        min_train = int(self.cfg.get("min_train_days", 504))
        refit_every = max(1, int(self.cfg.get("refit_every", 63)))
        conf_th = float(self.cfg.get("conf_threshold", 0.42))
        c_lo = float(self.cfg.get("conf_floor", 0.42))
        c_hi = float(self.cfg.get("conf_ceil", 0.88))

        out_pred = np.zeros(n, dtype=int)
        out_proba = np.full((n, 3), 1.0 / 3.0)
        metas: List[dict] = []
        clf = None
        last_fit = -10**9
        classes = np.array([0, 1, 2])

        for i in range(n):
            meta: Dict[str, Any] = {"rule": "stack", "mode": "stack"}
            if i < min_train:
                out_pred[i] = 0
                metas.append({**meta, "rule": "warmup_flat"})
                continue
            if clf is None or (i - last_fit) >= refit_every:
                # 需至少两类
                y_tr = y[:i]
                if len(np.unique(y_tr)) < 2:
                    out_pred[i] = 0
                    metas.append({**meta, "rule": "stack_single_class"})
                    continue
                clf = self._new_clf()
                clf.fit(X[:i], y_tr)
                self._last_clf = clf
                last_fit = i

            raw = clf.predict_proba(X[i : i + 1])[0]
            # 对齐到三类
            proba = np.full(3, 1e-6)
            for j, cls in enumerate(clf.classes_):
                proba[int(cls)] = float(raw[j])
            proba = proba / proba.sum()
            yhat = int(np.argmax(proba))
            pred = int(_Y_TO_LABEL[yhat])
            pmax = float(proba.max())
            gated = False
            if pmax < conf_th:
                pred = 0
                gated = True
                # 门控后置信度压到 floor 附近
                conf = c_lo
                proba = _conf_to_proba(0, conf)
            else:
                conf = float(np.clip(pmax, c_lo, c_hi))
                proba = _conf_to_proba(pred, conf)

            out_pred[i] = pred
            out_proba[i] = proba
            meta.update({
                "pmax": round(pmax, 4),
                "gated": gated,
                "ungated_pred": int(_Y_TO_LABEL[yhat]),
            })
            metas.append(meta)

        self._out_pred = out_pred
        self._out_proba = out_proba
        self._out_meta = metas

        # 快速诊断
        active = np.array([m.get("rule") == "stack" and not m.get("gated") for m in metas])
        warm = np.array([m.get("rule") == "warmup_flat" for m in metas])
        eval_mask = ~warm
        if eval_mask.any():
            acc = float((out_pred[eval_mask] == panel.actual[eval_mask]).mean())
            if active.any():
                sel = float((out_pred[active] == panel.actual[active]).mean())
                cov = float(active.mean())
            else:
                sel, cov = 0.0, 0.0
            logger.info(
                "stack 预计算: full_acc={:.3f} selective_acc={:.3f} coverage={:.1%} conf_th={}",
                acc, sel, cov, conf_th,
            )

    def _precompute_rule(self) -> None:
        assert self._panel is not None
        panel = self._panel
        n = len(panel.dates)
        out_pred = np.zeros(n, dtype=int)
        out_proba = np.full((n, 3), 1.0 / 3.0)
        metas: List[dict] = []
        for i in range(n):
            state = panel.states[i]
            preds = {m: int(panel.preds[m][i]) for m in panel.members}
            confs = {m: float(panel.confs[m][i]) for m in panel.members}
            accs = {m: self._acc_before(m, i, state) for m in panel.members}
            pred, conf, meta = self._aggregate(preds, confs, accs)
            meta["mode"] = "rule"
            meta["state"] = state
            out_pred[i] = pred
            out_proba[i] = _conf_to_proba(pred, conf)
            metas.append(meta)
        self._out_pred = out_pred
        self._out_proba = out_proba
        self._out_meta = metas

    def _aggregate(
        self,
        preds: Dict[str, int],
        confs: Dict[str, float],
        accs: Dict[str, float],
    ) -> Tuple[int, float, dict]:
        trust = float(self.cfg.get("trust_acc", 0.50))
        invert = float(self.cfg.get("invert_acc", 0.35))
        agree_min = float(self.cfg.get("agreement_min", 0.75))
        c_lo = float(self.cfg.get("conf_floor", 0.42))
        c_hi = float(self.cfg.get("conf_ceil", 0.88))

        members = [m for m in preds if m in accs]
        if not members:
            return 0, 0.45, {"rule": "empty"}

        counts: Dict[int, float] = defaultdict(float)
        for m in members:
            counts[int(preds[m])] += float(accs[m])
        majority = max(counts, key=counts.get)
        total_w = sum(counts.values()) or 1.0
        agree_ratio = counts[majority] / total_w

        maj_members = [m for m in members if int(preds[m]) == majority]
        mean_acc = float(np.mean([accs[m] for m in maj_members])) if maj_members else 0.5
        mean_conf = float(np.mean([confs[m] for m in maj_members])) if maj_members else 0.5

        meta: Dict[str, Any] = {
            "majority": majority,
            "agree_ratio": round(agree_ratio, 4),
            "mean_acc": round(mean_acc, 4),
            "n_members": len(members),
        }

        if agree_ratio >= agree_min:
            if mean_acc >= trust:
                conf = float(np.clip(0.5 * mean_conf + 0.5 * mean_acc, c_lo, c_hi))
                meta["rule"] = "agree_trust"
                return majority, conf, meta
            if mean_acc <= invert and majority != 0:
                conf = float(np.clip(0.55 + (invert - mean_acc), c_lo, c_hi))
                meta["rule"] = "agree_invert"
                return -majority, conf, meta
            conf = float(np.clip(0.4 * mean_conf + 0.4 * mean_acc + 0.1, c_lo, c_hi))
            meta["rule"] = "agree_weak"
            return majority, conf, meta

        best = max(members, key=lambda m: (accs[m], confs[m]))
        pred = int(preds[best])
        conf = float(np.clip(0.5 * confs[best] + 0.5 * accs[best], c_lo, c_hi))
        if accs[best] <= invert and pred != 0:
            meta["rule"] = "best_invert"
            meta["best"] = best
            return -pred, float(np.clip(0.55, c_lo, c_hi)), meta
        meta["rule"] = "best_channel"
        meta["best"] = best
        return pred, conf, meta

    def _predict_at_date(self, date: pd.Timestamp) -> Tuple[int, np.ndarray, Optional[int], dict]:
        if self._panel is None or self._out_pred is None:
            self.train(pd.DataFrame())
        assert self._panel is not None and self._out_pred is not None and self._out_proba is not None

        d = pd.Timestamp(date).normalize()
        i = self._date_to_i.get(d)
        if i is None:
            earlier = [k for k in self._date_to_i if k <= d]
            if not earlier:
                self._cache_misses += 1
                return 0, np.array([1 / 3, 1 / 3, 1 / 3]), None, {"rule": "no_history"}
            d = max(earlier)
            i = self._date_to_i[d]
            self._cache_misses += 1
        else:
            self._cache_hits += 1

        pred = int(self._out_pred[i])
        proba = np.asarray(self._out_proba[i], dtype=float)
        meta = dict(self._out_meta[i] if i < len(self._out_meta) else {})
        meta["date"] = str(d.date())
        meta["state"] = self._panel.states[i]
        meta["member_preds"] = {m: int(self._panel.preds[m][i]) for m in self._panel.members}

        week_pred = None
        try:
            from market_platform.predictor.分析.提示词.构建器 import infer_week_direction, is_dual_frequency_enabled
            if is_dual_frequency_enabled() and self._df is not None and len(self._df):
                week_pred = infer_week_direction(self._df.iloc[-1])
        except Exception:
            week_pred = None
        return pred, proba, week_pred, meta

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


def evaluate_stack_curves(
    conf_thresholds: Optional[List[float]] = None,
    *,
    include_invert: bool = True,
) -> Dict[str, Any]:
    """离线 walk-forward：扫置信度门控，输出 coverage / selective_acc。"""
    conf_thresholds = conf_thresholds or [0.36, 0.38, 0.40, 0.42, 0.45, 0.48, 0.50, 0.55]
    curves = []
    for thr in conf_thresholds:
        pred = MetaEnsemblePredictor(params={
            "mode": "stack",
            "conf_threshold": thr,
            "include_invert_features": include_invert,
        })
        pred.train(pd.DataFrame())
        assert pred._panel is not None and pred._out_pred is not None and pred._out_meta
        panel = pred._panel
        warm = np.array([m.get("rule") == "warmup_flat" for m in pred._out_meta])
        gated = np.array([bool(m.get("gated")) for m in pred._out_meta])
        eval_mask = ~warm
        active = eval_mask & ~gated
        full_acc = float((pred._out_pred[eval_mask] == panel.actual[eval_mask]).mean()) if eval_mask.any() else 0.0
        sel_acc = float((pred._out_pred[active] == panel.actual[active]).mean()) if active.any() else 0.0
        cov = float(active.mean()) if len(active) else 0.0
        curves.append({
            "conf_threshold": thr,
            "full_accuracy": round(full_acc, 4),
            "selective_accuracy": round(sel_acc, 4),
            "coverage": round(cov, 4),
            "n_eval": int(eval_mask.sum()),
            "n_active": int(active.sum()),
        })
    return {
        "include_invert_features": include_invert,
        "curves": curves,
    }


def build_agreement_index(
    *,
    prefer_fingerprint: Optional[str] = None,
) -> Dict[pd.Timestamp, float]:
    """各交易日成员预测一致比例（多数票占比），供分歧降仓门控。"""
    panel = load_ensemble_panel(prefer_fingerprint=prefer_fingerprint)
    out: Dict[pd.Timestamp, float] = {}
    n_m = max(len(panel.members), 1)
    for i, d in enumerate(panel.dates):
        votes = [int(panel.preds[m][i]) for m in panel.members]
        maj_n = Counter(votes).most_common(1)[0][1]
        out[pd.Timestamp(d).normalize()] = float(maj_n) / float(n_m)
    return out


def build_conditional_accuracy_report() -> Dict[str, Any]:
    """一次性分析报告：各渠道 × 状态桶条件准确率（全样本，供审阅）。"""
    panel = load_ensemble_panel()
    min_n = int(get_ensemble_config().get("min_bucket_n", 40))
    report: Dict[str, Any] = {
        "n_days": len(panel.dates),
        "members": panel.members,
        "fingerprints": panel.fingerprints,
        "current_fingerprint": strategy_fingerprint(),
        "buckets": [],
        "global_accuracy": {},
    }
    for m in panel.members:
        hits = (panel.preds[m] == panel.actual).astype(int)
        report["global_accuracy"][m] = {
            "accuracy": round(float(hits.mean()), 4),
            "n": int(len(hits)),
        }

    rows = []
    for m in panel.members:
        by_state: Dict[str, _BucketStats] = defaultdict(_BucketStats)
        for i, st in enumerate(panel.states):
            hit = int(panel.preds[m][i] == panel.actual[i])
            by_state[st].n += 1
            by_state[st].hits += hit
        for st, stv in by_state.items():
            if stv.n < min_n:
                continue
            rows.append({
                "provider": m,
                "state": st,
                "accuracy": round(stv.acc, 4),
                "n": stv.n,
            })
    rows.sort(key=lambda r: (-r["accuracy"], -r["n"]))
    report["buckets"] = rows
    report["n_buckets"] = len(rows)
    invert = float(get_ensemble_config().get("invert_acc", 0.35))
    report["invert_candidates"] = [r for r in rows if r["accuracy"] <= invert]
    report["trust_candidates"] = [
        r for r in rows if r["accuracy"] >= float(get_ensemble_config().get("trust_acc", 0.50))
    ]
    return report

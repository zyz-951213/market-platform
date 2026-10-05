"""
LLM 作因子：把多渠道 LLM pred/conf 并入传统量价因子，walk-forward 分类。

不调 LLM API：LLM 信号来自已入库回测 signals；目标为 label_direction。
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd
from loguru import logger

from market_platform.predictor.配置 import get_config
from market_platform.predictor.存储.数据访问 import load_index_features
from market_platform.predictor.分析.推断.集合元模型 import load_ensemble_panel, list_member_providers
from market_platform.predictor.分析.特征.状态路由 import regime_series, REGIME_BULL, REGIME_BEAR, REGIME_RANGE


LLM_FACTOR_PROVIDER = "llm_factor_hgb"
CLASS_ORDER = (-1, 0, 1)
_LABEL_TO_Y = {-1: 0, 0: 1, 1: 2}
_Y_TO_LABEL = {0: -1, 1: 0, 2: 1}

_EXCLUDE_COLS = {
    "date", "open", "high", "low", "close", "volume", "amount", "turnover",
    "label_direction", "label_return", "label_week_direction", "label_week_return",
}


def is_llm_factor_provider(provider: Optional[str]) -> bool:
    if not provider:
        return False
    if provider == LLM_FACTOR_PROVIDER or provider.startswith("llm_factor"):
        return True
    cfg = (get_config().get("model", {}).get("llm", {}).get("providers") or {}).get(provider) or {}
    return str(cfg.get("kind", "")).lower() in ("llm_factor", "factor_model")


def get_llm_factor_config() -> dict:
    root = get_config().get("model", {}) or {}
    cfg = dict(root.get("llm_factor") or {})
    # providers.llm_factor_hgb 可覆盖
    prov = (root.get("llm", {}).get("providers") or {}).get(LLM_FACTOR_PROVIDER) or {}
    cfg.update({k: v for k, v in prov.items() if k not in ("model", "kind")})
    cfg.setdefault("model", "hgb")  # hgb | logreg
    cfg.setdefault("feature_mode", "both")  # factors | llm | both
    cfg.setdefault("min_train_days", 504)
    cfg.setdefault("refit_every", 63)
    cfg.setdefault("conf_threshold", 0.0)  # 0=不门控观望
    cfg.setdefault("conf_floor", 0.42)
    cfg.setdefault("conf_ceil", 0.88)
    return cfg


def _factor_columns(df: pd.DataFrame) -> List[str]:
    cols = []
    for c in df.columns:
        if c in _EXCLUDE_COLS or str(c).startswith("label_"):
            continue
        if pd.api.types.is_numeric_dtype(df[c]):
            cols.append(c)
    return cols


def build_llm_factor_frame() -> pd.DataFrame:
    """
    因子表 ⋈ 8 路 LLM pred/conf + agreement/dispersion。
    索引为交易日；含 label_direction。
    """
    cfg = get_config()
    raw = load_index_features(cfg["data"]["index_code"])
    if raw is None or raw.empty:
        raise FileNotFoundError("特征数据不存在")
    feat = raw.copy()
    feat["date"] = pd.to_datetime(feat["date"]).dt.normalize()
    feat = feat.sort_values("date").drop_duplicates("date", keep="last").set_index("date")

    panel = load_ensemble_panel()
    members = panel.members
    llm_rows = []
    for i, d in enumerate(panel.dates):
        row: Dict[str, Any] = {"date": pd.Timestamp(d).normalize()}
        preds = []
        for m in members:
            p = int(panel.preds[m][i])
            c = float(panel.confs[m][i])
            row[f"llm_pred_{m}"] = float(p)
            row[f"llm_conf_{m}"] = c
            # one-hot
            for lab in CLASS_ORDER:
                row[f"llm_oh_{m}_{lab}"] = 1.0 if p == lab else 0.0
            preds.append(p)
        cnt = Counter(preds)
        maj_n = cnt.most_common(1)[0][1]
        agree = maj_n / max(len(preds), 1)
        # 分歧：1-agree；另加预测方差
        disp = 1.0 - agree
        var = float(np.var(np.asarray(preds, dtype=float))) if preds else 0.0
        row["ensemble_agreement"] = agree
        row["ensemble_dispersion"] = disp
        row["ensemble_pred_var"] = var
        llm_rows.append(row)

    llm_df = pd.DataFrame(llm_rows).set_index("date").sort_index()
    factor_cols = _factor_columns(feat)
    keep = factor_cols + (["label_direction"] if "label_direction" in feat.columns else [])
    base = feat[keep].copy()

    merged = base.join(llm_df, how="inner")
    if "label_direction" not in merged.columns:
        raise RuntimeError("合并后缺少 label_direction")
    merged = merged.dropna(subset=["label_direction"])
    logger.info(
        "LLM因子帧就绪: {} 日 · 因子列={} · LLM成员={} · 总列={}",
        len(merged), len(factor_cols), members, merged.shape[1],
    )

    # 加入市场状态（三态 one-hot），让 HGB 学 LLM 信号在不同状态下不同权重
    feat_for_regime = feat.copy()
    merged_for_regime = merged.copy().reset_index()
    merged_for_regime = merged_for_regime.sort_values("date").reset_index(drop=True)
    raw_regime = regime_series(feat_for_regime.reset_index(drop=False), smooth=True)
    # regime_series 输出按 feat 的 index 对齐，需要按时间匹配到 merged
    feat_dates = pd.to_datetime(feat.index)
    regime_map = dict(zip(feat_dates, raw_regime.values))
    merged_dates = pd.to_datetime(merged_for_regime["date"])
    merged_for_regime["regime"] = merged_dates.map(regime_map).fillna(REGIME_RANGE)
    merged_for_regime[f"regime_{REGIME_BULL}"] = (merged_for_regime["regime"] == REGIME_BULL).astype(float)
    merged_for_regime[f"regime_{REGIME_BEAR}"] = (merged_for_regime["regime"] == REGIME_BEAR).astype(float)
    merged_for_regime[f"regime_{REGIME_RANGE}"] = (merged_for_regime["regime"] == REGIME_RANGE).astype(float)
    merged = merged_for_regime.set_index("date").sort_index()
    merged = merged.drop(columns=["regime"], errors="ignore")

    # 把状态特征加入 factor_cols，确保 _select_feature_cols 能选到
    regime_cols = [f"regime_{REGIME_BULL}", f"regime_{REGIME_BEAR}", f"regime_{REGIME_RANGE}"]
    for rc in regime_cols:
        if rc in merged.columns and rc not in factor_cols:
            factor_cols.append(rc)

    # 附加元数据列名列表（不写入 df）
    merged.attrs["factor_cols"] = factor_cols
    merged.attrs["llm_cols"] = [c for c in llm_df.columns]
    merged.attrs["members"] = list(members)
    return merged


def _select_feature_cols(frame: pd.DataFrame, mode: str) -> List[str]:
    factor_cols = list(frame.attrs.get("factor_cols") or [])
    llm_cols = list(frame.attrs.get("llm_cols") or [])
    if not factor_cols:
        factor_cols = [
            c for c in frame.columns
            if c not in _EXCLUDE_COLS and not str(c).startswith("label_")
            and not str(c).startswith("llm_") and not str(c).startswith("ensemble_")
            and pd.api.types.is_numeric_dtype(frame[c])
        ]
    if not llm_cols:
        llm_cols = [
            c for c in frame.columns
            if str(c).startswith("llm_") or str(c).startswith("ensemble_")
        ]
    mode = (mode or "both").lower()
    if mode == "factors":
        return factor_cols
    if mode == "llm":
        return llm_cols
    # both
    return factor_cols + llm_cols


def _fill_median(X: np.ndarray) -> np.ndarray:
    X = np.asarray(X, dtype=float)
    if X.ndim == 1:
        X = X.reshape(1, -1)
    out = X.copy()
    for j in range(out.shape[1]):
        col = out[:, j]
        finite = col[np.isfinite(col)]
        med = float(np.median(finite)) if len(finite) else 0.0
        mask = ~np.isfinite(col)
        if mask.any():
            out[mask, j] = med
    return out


def _new_clf(model_name: str):
    name = (model_name or "hgb").lower()
    if name == "logreg":
        from sklearn.linear_model import LogisticRegression
        from sklearn.pipeline import Pipeline
        from sklearn.preprocessing import StandardScaler

        return Pipeline([
            ("scaler", StandardScaler()),
            ("clf", LogisticRegression(max_iter=800, C=0.5)),
        ])
    from sklearn.ensemble import HistGradientBoostingClassifier

    return HistGradientBoostingClassifier(
        max_depth=3,
        max_iter=120,
        learning_rate=0.06,
        min_samples_leaf=20,
        random_state=0,
    )


def _proba_to_label_and_conf(
    proba: np.ndarray,
    classes: np.ndarray,
    *,
    conf_threshold: float,
    conf_floor: float,
    conf_ceil: float,
) -> Tuple[int, np.ndarray, float, bool]:
    full = np.full(3, 1e-6)
    for j, cls in enumerate(classes):
        full[int(cls)] = float(proba[j])
    full = full / full.sum()
    yhat = int(np.argmax(full))
    pred = int(_Y_TO_LABEL[yhat])
    pmax = float(full.max())
    gated = False
    if conf_threshold > 0 and pmax < conf_threshold:
        pred = 0
        gated = True
        conf = conf_floor
        out_p = np.full(3, (1.0 - conf) / 2.0)
        out_p[1] = conf
        out_p = out_p / out_p.sum()
        return pred, out_p, conf, gated
    conf = float(np.clip(pmax, conf_floor, conf_ceil))
    out_p = np.full(3, (1.0 - conf) / 2.0)
    out_p[CLASS_ORDER.index(pred)] = conf
    out_p = out_p / out_p.sum()
    return pred, out_p, conf, gated


def evaluate_walk_forward(
    *,
    mode: str = "both",
    model_name: Optional[str] = None,
    conf_thresholds: Optional[Sequence[float]] = None,
) -> Dict[str, Any]:
    """Walk-forward 评估；返回 accuracy / selective 曲线。"""
    cfg = get_llm_factor_config()
    model_name = model_name or cfg.get("model", "hgb")
    min_train = int(cfg.get("min_train_days", 504))
    refit_every = max(1, int(cfg.get("refit_every", 63)))
    conf_thresholds = list(conf_thresholds or [0.0, 0.40, 0.45, 0.48, 0.50, 0.55])

    frame = build_llm_factor_frame()
    feat_cols = _select_feature_cols(frame, mode)
    dates = frame.index
    y = frame["label_direction"].astype(int).to_numpy()
    X_raw = frame[feat_cols].to_numpy(dtype=float)
    n = len(dates)

    # 预计算无门控预测与 pmax
    preds = np.zeros(n, dtype=int)
    pmaxs = np.full(n, np.nan)
    warm = np.ones(n, dtype=bool)
    clf = None
    last_fit = -10**9

    for i in range(n):
        if i < min_train:
            preds[i] = 0
            continue
        warm[i] = False
        if clf is None or (i - last_fit) >= refit_every:
            y_tr = np.array([_LABEL_TO_Y[int(v)] for v in y[:i]], dtype=int)
            if len(np.unique(y_tr)) < 2:
                preds[i] = 0
                continue
            X_tr = _fill_median(X_raw[:i])
            clf = _new_clf(model_name)
            clf.fit(X_tr, y_tr)
            last_fit = i
        X_i = _fill_median(X_raw[i : i + 1])
        raw_p = clf.predict_proba(X_i)[0]
        classes = getattr(clf, "classes_", None)
        if classes is None and hasattr(clf, "named_steps"):
            classes = clf.named_steps["clf"].classes_
        full = np.full(3, 1e-6)
        for j, cls in enumerate(classes):
            full[int(cls)] = float(raw_p[j])
        full /= full.sum()
        yhat = int(np.argmax(full))
        preds[i] = int(_Y_TO_LABEL[yhat])
        pmaxs[i] = float(full.max())

    eval_mask = ~warm
    full_acc = float((preds[eval_mask] == y[eval_mask]).mean()) if eval_mask.any() else 0.0
    curves = []
    for thr in conf_thresholds:
        if thr <= 0:
            active = eval_mask
            sel_preds = preds
        else:
            active = eval_mask & np.isfinite(pmaxs) & (pmaxs >= thr)
            sel_preds = preds.copy()
            # 门控日不算 active
        sel_acc = float((sel_preds[active] == y[active]).mean()) if active.any() else 0.0
        curves.append({
            "conf_threshold": float(thr),
            "full_accuracy": round(full_acc, 4),
            "selective_accuracy": round(sel_acc, 4),
            "coverage": round(float(active.mean()), 4) if len(active) else 0.0,
            "n_eval": int(eval_mask.sum()),
            "n_active": int(active.sum()),
        })

    return {
        "mode": mode,
        "model": model_name,
        "n_days": n,
        "n_features": len(feat_cols),
        "feature_cols_sample": feat_cols[:20],
        "min_train_days": min_train,
        "refit_every": refit_every,
        "full_accuracy": round(full_acc, 4),
        "curves": curves,
        "members": list(frame.attrs.get("members") or []),
    }


def evaluate_all_modes(
    *,
    model_names: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    model_names = list(model_names or ["hgb", "logreg"])
    modes = ["factors", "llm", "both"]
    results = []
    for model_name in model_names:
        for mode in modes:
            logger.info("评估 walk-forward: model={} mode={}", model_name, mode)
            results.append(evaluate_walk_forward(mode=mode, model_name=model_name))
    # 增量判定：both vs factors / llm（取 hgb）
    by_key = {(r["model"], r["mode"]): r for r in results}
    hgb_f = by_key.get(("hgb", "factors"), {})
    hgb_l = by_key.get(("hgb", "llm"), {})
    hgb_b = by_key.get(("hgb", "both"), {})
    af = float(hgb_f.get("full_accuracy") or 0)
    al = float(hgb_l.get("full_accuracy") or 0)
    ab = float(hgb_b.get("full_accuracy") or 0)
    lift_vs_factors = ab - af
    lift_vs_llm = ab - al
    verdict = "inconclusive"
    if lift_vs_factors >= 0.01 and lift_vs_llm >= 0.005:
        verdict = "both_helps"
    elif abs(ab - al) < 0.005 and ab > af + 0.005:
        verdict = "llm_only_signal"  # 因子没帮上
    elif abs(ab - af) < 0.005 and ab > al + 0.005:
        verdict = "factors_dominate"  # LLM 无增量
    elif ab <= max(af, al) + 0.002:
        verdict = "no_gain"

    return {
        "results": results,
        "hgb_summary": {
            "factors_acc": af,
            "llm_acc": al,
            "both_acc": ab,
            "lift_vs_factors": round(lift_vs_factors, 4),
            "lift_vs_llm": round(lift_vs_llm, 4),
            "verdict": verdict,
        },
    }


class LlmFactorPredictor:
    """鸭式接口：train / predict / predict_proba / predict_bundle。"""

    CLASS_ORDER = list(CLASS_ORDER)

    def __init__(self, params: Optional[dict] = None):
        self.cfg = {**get_llm_factor_config(), **(params or {})}
        self.provider_name = LLM_FACTOR_PROVIDER
        self._frame: Optional[pd.DataFrame] = None
        self._feat_cols: List[str] = []
        self._date_to_i: Dict[pd.Timestamp, int] = {}
        self._out_pred: Optional[np.ndarray] = None
        self._out_proba: Optional[np.ndarray] = None
        self._df: Optional[pd.DataFrame] = None
        self._cache_hits = 0
        self._cache_misses = 0
        self._last_meta: dict = {}

    def reset_cache_stats(self) -> None:
        self._cache_hits = 0
        self._cache_misses = 0

    def cache_stats(self) -> dict:
        return {
            "hits": self._cache_hits,
            "misses": self._cache_misses,
            "mode": f"llm_factor:{self.cfg.get('model')}:{self.cfg.get('feature_mode')}",
        }

    def set_api_cutoff_date(self, date: Optional[str]) -> None:
        return

    def bind_dataframe(self, df: pd.DataFrame) -> None:
        self._df = df

    def train(self, train_df: pd.DataFrame) -> "LlmFactorPredictor":
        mode = str(self.cfg.get("feature_mode", "both"))
        model_name = str(self.cfg.get("model", "hgb"))
        min_train = int(self.cfg.get("min_train_days", 504))
        refit_every = max(1, int(self.cfg.get("refit_every", 63)))
        conf_th = float(self.cfg.get("conf_threshold", 0.0))
        c_lo = float(self.cfg.get("conf_floor", 0.42))
        c_hi = float(self.cfg.get("conf_ceil", 0.88))

        frame = build_llm_factor_frame()
        feat_cols = _select_feature_cols(frame, mode)
        self._frame = frame
        self._feat_cols = feat_cols
        self._date_to_i = {pd.Timestamp(d).normalize(): i for i, d in enumerate(frame.index)}

        y = frame["label_direction"].astype(int).to_numpy()
        X_raw = frame[feat_cols].to_numpy(dtype=float)
        n = len(frame)
        out_pred = np.zeros(n, dtype=int)
        out_proba = np.full((n, 3), 1.0 / 3.0)
        clf = None
        last_fit = -10**9

        for i in range(n):
            if i < min_train:
                continue
            if clf is None or (i - last_fit) >= refit_every:
                y_tr = np.array([_LABEL_TO_Y[int(v)] for v in y[:i]], dtype=int)
                if len(np.unique(y_tr)) < 2:
                    continue
                clf = _new_clf(model_name)
                clf.fit(_fill_median(X_raw[:i]), y_tr)
                last_fit = i
            raw_p = clf.predict_proba(_fill_median(X_raw[i : i + 1]))[0]
            classes = getattr(clf, "classes_", None)
            if classes is None and hasattr(clf, "named_steps"):
                classes = clf.named_steps["clf"].classes_
            pred, proba, _, _ = _proba_to_label_and_conf(
                raw_p, classes,
                conf_threshold=conf_th, conf_floor=c_lo, conf_ceil=c_hi,
            )
            out_pred[i] = pred
            out_proba[i] = proba

        self._out_pred = out_pred
        self._out_proba = out_proba
        eval_m = np.arange(n) >= min_train
        if eval_m.any():
            acc = float((out_pred[eval_m] == y[eval_m]).mean())
            logger.info(
                "LLM因子模型已训练: mode={} model={} feats={} days={} acc={:.3f}",
                mode, model_name, len(feat_cols), n, acc,
            )
        return self

    def _predict_at_date(self, date: pd.Timestamp) -> Tuple[int, np.ndarray, Optional[int], dict]:
        if self._frame is None or self._out_pred is None:
            self.train(pd.DataFrame())
        assert self._out_pred is not None and self._out_proba is not None
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
        meta = {"date": str(d.date()), "mode": self.cfg.get("feature_mode"), "rule": "llm_factor"}
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

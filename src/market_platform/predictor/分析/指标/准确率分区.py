"""按市场状态切片追踪各 LLM 渠道准确率，并生成动态路由权重。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from loguru import logger

from market_platform.predictor.分析.推断.语言模型预测 import list_llm_providers, list_member_providers, provider_model_id
from market_platform.predictor.配置 import get_config
from market_platform.predictor.存储.数据访问 import load_index_features
from market_platform.predictor.存储.回测存储 import load_backtest_for_provider

_CACHE: Dict[str, Any] = {"key": None, "payload": None}

# 后四个为本地特征事后切片（非 LLM 输入）；用于环境条件准确率与路由
DIM_SPECS = (
    ("pred", "预测方向"),
    ("conf_bucket", "置信度"),
    ("vol_q", "波动环境"),
    ("trend", "趋势环境"),
    ("above_sma60", "均线位置"),
    ("rsi_bucket", "RSI区间"),
)

MIN_N = 50
MIN_N_SWEET = 80
SWEET_GAP = 0.02


def _enrich_features(feat: pd.DataFrame) -> pd.DataFrame:
    out = feat.copy()
    out["date"] = pd.to_datetime(out["date"])
    out = out.sort_values("date").set_index("date")
    close = out["close"].astype(float) if "close" in out.columns else None
    if close is None:
        return out
    out["ret_20"] = close.pct_change(20)
    sma = out["sma_60"] if "sma_60" in out.columns else close.rolling(60).mean()
    out["above_sma60"] = (close > sma).astype(int)
    vol = (
        out["volatility_20d"].astype(float)
        if "volatility_20d" in out.columns
        else close.pct_change().rolling(20).std()
    )
    # 用全样本分位标签，保证与回测切片一致
    ranked = vol.rank(method="first")
    out["vol_q"] = pd.qcut(ranked, 3, labels=["低波", "中波", "高波"])
    if "rsi_14" in out.columns:
        out["rsi_bucket"] = pd.cut(
            out["rsi_14"].astype(float),
            bins=[0, 30, 50, 70, 100],
            labels=["超卖", "偏弱", "偏强", "超买"],
        )
    out["trend"] = pd.cut(
        out["ret_20"],
        bins=[-np.inf, -0.05, 0.05, np.inf],
        labels=["下跌段", "震荡", "上涨段"],
    )
    return out


def _conf_bucket(conf: float) -> str:
    c = float(conf)
    if c < 0.55:
        return "低置信"
    if c < 0.65:
        return "中置信"
    if c < 0.75:
        return "高置信"
    return "极高置信"


def _attach_signal_slices(signals: pd.DataFrame, feat: pd.DataFrame) -> pd.DataFrame:
    s = signals.copy()
    s["date"] = pd.to_datetime(s["date"])
    s = s.set_index("date").sort_index()
    s["hit"] = (s["prediction"].astype(int) == s["actual"].astype(int)).astype(int)
    join_cols = [c for c in ("ret_20", "above_sma60", "vol_q", "rsi_14", "rsi_bucket", "trend") if c in feat.columns]
    s = s.join(feat[join_cols], how="left")
    s["conf_bucket"] = s["confidence"].astype(float).map(_conf_bucket)
    s["pred"] = s["prediction"].astype(int).map({-1: "↓", 0: "→", 1: "↑"})
    if "above_sma60" in s.columns:
        s["above_sma60"] = s["above_sma60"].map({0: "低于SMA60", 1: "高于SMA60"})
    return s


def _cache_key(providers: List[str]) -> str:
    parts = []
    for name in providers:
        bt = load_backtest_for_provider(name)
        parts.append(f"{name}:{bt.get('saved_at') if bt else 'na'}:{bt.get('data_as_of') if bt else 'na'}")
    return "|".join(parts)


def _build_frames() -> Tuple[Dict[str, pd.DataFrame], pd.DataFrame]:
    cfg = get_config()
    raw = load_index_features(cfg["data"]["index_code"])
    if raw is None or raw.empty:
        raise FileNotFoundError("特征数据不存在")
    feat = _enrich_features(raw)
    frames: Dict[str, pd.DataFrame] = {}
    for name in list_member_providers():
        bt = load_backtest_for_provider(name)
        if not bt or not bt.get("signals"):
            continue
        frames[name] = _attach_signal_slices(pd.DataFrame(bt["signals"]), feat)
    return frames, feat


def _slice_table(frames: Dict[str, pd.DataFrame], col: str, min_n: int = MIN_N) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for name, s in frames.items():
        if col not in s.columns:
            continue
        for key, g in s.groupby(col, observed=True):
            n = int(len(g))
            if n < min_n:
                continue
            rows.append({
                "provider": name,
                "model": provider_model_id(name),
                "slice": str(key),
                "n": n,
                "accuracy": round(float(g["hit"].mean()), 4),
            })
    return rows


def _heatmap(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    slices = sorted({r["slice"] for r in rows})
    providers = []
    seen = set()
    for r in rows:
        if r["provider"] not in seen:
            seen.add(r["provider"])
            providers.append({"id": r["provider"], "model": r["model"]})
    matrix = []
    for sl in slices:
        cell = {"slice": sl, "by_provider": {}}
        best_acc = -1.0
        best_p = None
        for r in rows:
            if r["slice"] != sl:
                continue
            cell["by_provider"][r["provider"]] = {
                "accuracy": r["accuracy"],
                "n": r["n"],
                "model": r["model"],
            }
            if r["accuracy"] > best_acc:
                best_acc = r["accuracy"]
                best_p = r["provider"]
        cell["best_provider"] = best_p
        cell["best_accuracy"] = best_acc if best_p else None
        matrix.append(cell)
    return {"slices": slices, "providers": providers, "matrix": matrix}


def _sweet_spots(frames: Dict[str, pd.DataFrame]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for col, label in DIM_SPECS:
        rows = _slice_table(frames, col, min_n=MIN_N_SWEET)
        by_slice: Dict[str, List[Dict[str, Any]]] = {}
        for r in rows:
            by_slice.setdefault(r["slice"], []).append(r)
        for sl, items in by_slice.items():
            items = sorted(items, key=lambda x: x["accuracy"], reverse=True)
            if len(items) < 2:
                continue
            gap = items[0]["accuracy"] - items[1]["accuracy"]
            if gap < SWEET_GAP:
                continue
            out.append({
                "dimension": label,
                "dimension_key": col,
                "slice": sl,
                "provider": items[0]["provider"],
                "model": items[0]["model"],
                "accuracy": items[0]["accuracy"],
                "n": items[0]["n"],
                "lead_pp": round(gap * 100, 2),
            })
    out.sort(key=lambda x: (-x["lead_pp"], -x["n"]))
    return out


def _self_lifts(frames: Dict[str, pd.DataFrame], top_k: int = 5) -> Dict[str, List[Dict[str, Any]]]:
    result: Dict[str, List[Dict[str, Any]]] = {}
    for name, s in frames.items():
        base = float(s["hit"].mean())
        lifts: List[Dict[str, Any]] = []
        for col, label in DIM_SPECS:
            if col not in s.columns:
                continue
            for key, g in s.groupby(col, observed=True):
                if len(g) < MIN_N_SWEET:
                    continue
                acc = float(g["hit"].mean())
                lifts.append({
                    "dimension": label,
                    "slice": str(key),
                    "accuracy": round(acc, 4),
                    "lift_pp": round((acc - base) * 100, 2),
                    "n": int(len(g)),
                })
        lifts.sort(key=lambda x: -x["lift_pp"])
        result[name] = {
            "model": provider_model_id(name),
            "global_accuracy": round(base, 4),
            "top_lifts": lifts[:top_k],
        }
    return result


def compute_accuracy_partitions(*, force: bool = False) -> Dict[str, Any]:
    """全量切片准确率矩阵 + 优势区间（带缓存）。"""
    providers = list_llm_providers()
    key = _cache_key(providers)
    if not force and _CACHE.get("key") == key and _CACHE.get("payload"):
        return _CACHE["payload"]

    frames, _feat = _build_frames()
    dimensions = []
    for col, label in DIM_SPECS:
        rows = _slice_table(frames, col, min_n=MIN_N)
        dimensions.append({
            "key": col,
            "label": label,
            "rows": rows,
            "heatmap": _heatmap(rows),
        })

    globals_acc = {
        name: {
            "model": provider_model_id(name),
            "accuracy": round(float(s["hit"].mean()), 4),
            "n": int(len(s)),
        }
        for name, s in frames.items()
    }

    payload = {
        "available": bool(frames),
        "n_providers": len(frames),
        "min_n": MIN_N,
        "note": (
            "波动/趋势/均线/RSI 仅作事后市场状态切片，未输入 LLM；"
            "用于比较各渠道在不同环境下的命中率与动态路由。"
        ),
        "dimensions": dimensions,
        "sweet_spots": _sweet_spots(frames),
        "self_lifts": _self_lifts(frames),
        "global_accuracy": globals_acc,
        "providers": [
            {"id": name, "model": provider_model_id(name)}
            for name in frames
        ],
    }
    _CACHE["key"] = key
    _CACHE["payload"] = payload
    logger.info("准确率分区已计算: {} 渠道 · {} 维", len(frames), len(dimensions))
    return payload


def detect_current_regime(date: Optional[str] = None) -> Dict[str, Any]:
    """根据特征行识别当前市场状态切片。"""
    cfg = get_config()
    raw = load_index_features(cfg["data"]["index_code"])
    feat = _enrich_features(raw)
    if date:
        ts = pd.Timestamp(date)
        # 取该日或之前最近一根
        sub = feat.loc[feat.index <= ts]
        row = sub.iloc[-1] if not sub.empty else feat.iloc[-1]
    else:
        row = feat.iloc[-1]

    regime = {
        "date": pd.Timestamp(row.name).strftime("%Y-%m-%d"),
        "vol_q": str(row["vol_q"]) if pd.notna(row.get("vol_q")) else None,
        "trend": str(row["trend"]) if pd.notna(row.get("trend")) else None,
        "above_sma60": (
            "高于SMA60" if int(row["above_sma60"]) == 1 else "低于SMA60"
        ) if pd.notna(row.get("above_sma60")) else None,
        "rsi_bucket": str(row["rsi_bucket"]) if pd.notna(row.get("rsi_bucket")) else None,
    }
    return regime


def _lookup_acc(rows: List[Dict[str, Any]], provider: str, slice_val: str) -> Optional[Tuple[float, int]]:
    for r in rows:
        if r["provider"] == provider and r["slice"] == slice_val:
            return float(r["accuracy"]), int(r["n"])
    return None


def compute_regime_weights(
    *,
    date: Optional[str] = None,
    channel_confidences: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """按当前市场状态 + 各渠道置信度切片，生成路由权重。

    权重 ∝ 匹配切片准确率的均值（缺省回退全局准确率），再 softmax 归一化。
    """
    partitions = compute_accuracy_partitions()
    regime = detect_current_regime(date=date)
    dim_map = {d["key"]: d for d in partitions.get("dimensions") or []}
    globals_acc = partitions.get("global_accuracy") or {}

    env_keys = [
        ("vol_q", regime.get("vol_q")),
        ("trend", regime.get("trend")),
        ("above_sma60", regime.get("above_sma60")),
        ("rsi_bucket", regime.get("rsi_bucket")),
    ]

    weights: Dict[str, float] = {}
    detail: Dict[str, Any] = {}
    for name in list_llm_providers():
        if name not in globals_acc:
            continue
        matched: List[Dict[str, Any]] = []
        accs: List[float] = []
        for key, sl in env_keys:
            if not sl or key not in dim_map:
                continue
            hit = _lookup_acc(dim_map[key]["rows"], name, sl)
            if hit:
                acc, n = hit
                accs.append(acc)
                matched.append({"dimension": dim_map[key]["label"], "slice": sl, "accuracy": acc, "n": n})

        # 渠道自身置信度桶
        conf = (channel_confidences or {}).get(name)
        if conf is not None and "conf_bucket" in dim_map:
            cb = _conf_bucket(conf)
            hit = _lookup_acc(dim_map["conf_bucket"]["rows"], name, cb)
            if hit:
                acc, n = hit
                accs.append(acc)
                matched.append({"dimension": "置信度", "slice": cb, "accuracy": acc, "n": n})

        base = float(globals_acc[name]["accuracy"])
        regime_acc = float(np.mean(accs)) if accs else base
        # 略放大相对优势，避免权重几乎一样
        score = max(regime_acc, 1e-3) ** 2.5
        weights[name] = score
        detail[name] = {
            "model": globals_acc[name]["model"],
            "global_accuracy": base,
            "regime_accuracy": round(regime_acc, 4),
            "matched_slices": matched,
            "raw_score": round(score, 6),
        }

    total = sum(weights.values()) or 1.0
    norm = {k: round(v / total, 4) for k, v in weights.items()}
    for name, info in detail.items():
        info["weight"] = norm.get(name, 0.0)

    ranked = sorted(detail.items(), key=lambda kv: kv[1]["weight"], reverse=True)
    return {
        "method": "regime_accuracy_routing",
        "regime": regime,
        "weights": norm,
        "per_provider": {k: v for k, v in ranked},
        "top_provider": ranked[0][0] if ranked else None,
    }

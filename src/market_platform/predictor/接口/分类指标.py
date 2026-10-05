"""分类指标计算 — 回测信号扩展统计"""

from __future__ import annotations

from typing import Any, Dict, List


def compute_classification_metrics(signals: List[dict]) -> Dict[str, Any]:
    if not signals:
        return {}

    preds = [int(s["prediction"]) for s in signals]
    actuals = [int(s["actual"]) for s in signals]
    n = len(preds)
    accuracy = sum(p == a for p, a in zip(preds, actuals)) / n

    label_map = {-1: "跌", 0: "平", 1: "涨"}
    pred_dist = {label_map[k]: sum(1 for p in preds if p == k) for k in (-1, 0, 1)}
    actual_dist = {label_map[k]: sum(1 for a in actuals if a == k) for k in (-1, 0, 1)}

    flat_actual = [i for i, a in enumerate(actuals) if a == 0]
    flat_recall = (
        sum(1 for i in flat_actual if preds[i] == 0) / len(flat_actual)
        if flat_actual else 0.0
    )

    directional_idx = [i for i, a in enumerate(actuals) if a != 0]
    directional_accuracy = (
        sum(1 for i in directional_idx if preds[i] == actuals[i]) / len(directional_idx)
        if directional_idx else 0.0
    )

    confidences = [s.get("confidence") for s in signals if s.get("confidence") is not None]
    high_conf_accuracy = None
    high_conf_coverage = None
    if confidences:
        high_idx = [i for i, s in enumerate(signals) if (s.get("confidence") or 0) >= 0.55]
        if high_idx:
            high_conf_accuracy = sum(
                1 for i in high_idx if preds[i] == actuals[i]
            ) / len(high_idx)
            high_conf_coverage = len(high_idx) / n

    # 选择性准确率：非平仓预测日（集合门控后的“主动出手”）
    active_idx = [i for i, p in enumerate(preds) if p != 0]
    selective_accuracy = (
        sum(1 for i in active_idx if preds[i] == actuals[i]) / len(active_idx)
        if active_idx else None
    )
    selective_coverage = len(active_idx) / n if n else 0.0

    return {
        "accuracy": accuracy,
        "directional_accuracy": directional_accuracy,
        "flat_recall": flat_recall,
        "high_conf_accuracy": high_conf_accuracy,
        "high_conf_coverage": high_conf_coverage,
        "selective_accuracy": selective_accuracy,
        "selective_coverage": selective_coverage,
        "pred_flat_rate": pred_dist.get("平", 0) / n,
        "actual_flat_rate": actual_dist.get("平", 0) / n,
        "prediction_dist": pred_dist,
        "actual_dist": actual_dist,
    }

"""仓位与组合权重硬约束（风控闸门）。"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class RiskConfig:
    enabled: bool = True
    max_single_position: float = 1.0
    panic_max_position: float = 0.10
    min_position: float = 0.0
    max_portfolio_position: float = 1.0
    max_single_weight: float = 0.12
    max_sector_weight: float = 0.35
    drawdown_reduce_threshold: float = 0.08
    drawdown_clear_threshold: float = 0.15
    drawdown_reduce_scale: float = 0.8
    drawdown_clear_scale: float = 0.30
    # 回撤恢复：DD 浅于该阈值后才解除缩放，避免牛市中段一直欠仓
    drawdown_recover_threshold: float = 0.06


def clip_position(raw: float, state: str, risk: RiskConfig) -> tuple[float, str | None]:
    """单标的建议仓位硬约束。"""
    if not risk.enabled:
        return float(raw), None

    pos = float(np.clip(raw, risk.min_position, risk.max_single_position))
    note: str | None = None
    state_name = str(state or "").strip()
    if state_name == "恐慌" and pos > risk.panic_max_position:
        pos = min(pos, risk.panic_max_position)
        note = "恐慌封顶"
    if pos < float(raw) - 1e-9:
        note = note or "仓位上限"
    return pos, note


def apply_risk_to_states(states: pd.DataFrame, risk: RiskConfig) -> pd.DataFrame:
    """为状态序列写入 raw_suggested_position，并覆写风控后 suggested_position。"""
    out = states.copy()
    if "suggested_position" not in out.columns:
        raise ValueError("states must contain suggested_position")

    raw_col = "raw_suggested_position"
    if raw_col not in out.columns:
        out[raw_col] = out["suggested_position"].astype(float)
    else:
        out[raw_col] = out[raw_col].astype(float)

    if not risk.enabled:
        out["risk_note"] = ""
        return out

    adjusted: list[float] = []
    notes: list[str] = []
    for row in out.itertuples(index=False):
        state = str(getattr(row, "market_state", "") or "")
        raw = float(getattr(row, raw_col, getattr(row, "suggested_position", 0.0)) or 0.0)
        pos, note = clip_position(raw, state, risk)
        adjusted.append(pos)
        notes.append(note or "")

    out["suggested_position"] = adjusted
    out["risk_note"] = notes
    return out


def drawdown_position_scale(
    current_equity: float,
    peak_equity: float,
    risk: RiskConfig,
    *,
    prev_scale: float = 1.0,
) -> float:
    """按策略净值回撤动态缩放；带恢复滞后，回撤收窄后才解除。"""
    if not risk.enabled or peak_equity <= 0 or current_equity <= 0:
        return 1.0
    dd = current_equity / peak_equity - 1.0
    if dd <= -risk.drawdown_clear_threshold:
        return float(risk.drawdown_clear_scale)
    if dd <= -risk.drawdown_reduce_threshold:
        return float(risk.drawdown_reduce_scale)

    recover = float(getattr(risk, "drawdown_recover_threshold", 0.04) or 0.04)
    if prev_scale < 1.0 - 1e-9:
        # 仍处于恢复期：只有回撤浅于 recover 才回到 1.0
        if dd > -recover:
            return 1.0
        return float(prev_scale)
    return 1.0


def constrain_portfolio_weights(holdings: list[dict], risk: RiskConfig) -> list[dict]:
    """组合单票/行业权重硬顶，并重新归一化。"""
    if not holdings:
        return holdings
    if not risk.enabled:
        return holdings

    items = [dict(h) for h in holdings]
    weights = np.array([max(float(h.get("weight", 0) or 0), 0.0) for h in items], dtype=float)
    if weights.sum() <= 0:
        return items

    for h in items:
        if "raw_weight" not in h:
            h["raw_weight"] = float(h.get("weight", 0) or 0)

    weights = weights / weights.sum()
    weights = np.minimum(weights, float(risk.max_single_weight))

    for _ in range(6):
        sector_idx: dict[str, list[int]] = {}
        for i, h in enumerate(items):
            sec = str(h.get("sector_l3") or h.get("sector") or "其他")
            sector_idx.setdefault(sec, []).append(i)
        changed = False
        for idxs in sector_idx.values():
            sec_w = float(weights[idxs].sum())
            if sec_w > risk.max_sector_weight:
                weights[idxs] *= risk.max_sector_weight / sec_w
                changed = True
        if not changed:
            break

    total = float(weights.sum())
    if total > 0:
        weights = weights / total

    for i, h in enumerate(items):
        h["weight"] = float(weights[i])
        raw_pos = float(h.get("suggested_position", 0) or 0)
        adj_pos, note = clip_position(raw_pos, str(h.get("latest_state", "")), risk)
        if "raw_suggested_position" not in h:
            h["raw_suggested_position"] = raw_pos
        h["suggested_position"] = adj_pos
        if note:
            h["risk_note"] = note
    return items


def latest_risk_snapshot(states: pd.DataFrame) -> dict:
    """提取最新一行风控相关字段。"""
    if states.empty:
        return {}
    latest = states.iloc[-1]
    raw = latest.get("raw_suggested_position", latest.get("suggested_position"))
    return {
        "latest_state": str(latest.get("market_state", "")),
        "suggested_position": float(latest.get("suggested_position", 0) or 0),
        "raw_suggested_position": float(raw or 0),
        "risk_note": str(latest.get("risk_note", "") or ""),
        "risk_adjusted": bool(
            abs(float(latest.get("suggested_position", 0) or 0) - float(raw or 0)) > 1e-9
        ),
    }

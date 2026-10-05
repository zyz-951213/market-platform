"""日线 OHLC 清洗：剔除非正价格、无效振幅与前复权跳价。"""

from __future__ import annotations

import numpy as np
import pandas as pd

# A 股常态涨跌停约 10%/20%；>50% 基本可判定为复权/脏数据
_MAX_ABS_RETURN = 0.50
_STRICT_ABS_RETURN = 0.30
# 长期停牌复牌首日允许更大跳空
_RESUME_GAP_DAYS = 30
_RESUME_ABS_RETURN = 1.0
_MAX_PASSES = 20


def _ensure_numeric_ohlc(out: pd.DataFrame) -> pd.DataFrame:
    for col in ("open", "high", "low", "close"):
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")
    return out


def _basic_ohlc_mask(out: pd.DataFrame) -> pd.Series:
    need = [c for c in ("open", "high", "low", "close") if c in out.columns]
    mask = pd.Series(True, index=out.index)
    for col in need:
        mask &= out[col].notna() & (out[col] > 0)
    if {"high", "low"}.issubset(out.columns):
        mask &= out["high"] >= out["low"]
    return mask


def _gap_days_np(out: pd.DataFrame) -> np.ndarray:
    n = len(out)
    if "date" not in out.columns or n == 0:
        return np.zeros(n, dtype=float)
    dates = pd.to_datetime(out["date"], errors="coerce")
    gap = dates.diff().dt.days.to_numpy(dtype=float, copy=True)
    if len(gap):
        gap[0] = 0.0
    return np.nan_to_num(gap, nan=0.0)


def _ret_and_bad(close: np.ndarray, gap: np.ndarray, *, max_abs: float) -> np.ndarray:
    """返回 bad[i]=True 表示 i 相对 i-1 为不可接受跳价（i>=1）。"""
    n = len(close)
    bad = np.zeros(n, dtype=bool)
    if n < 2:
        return bad
    prev = close[:-1]
    cur = close[1:]
    with np.errstate(divide="ignore", invalid="ignore"):
        ret = np.where(prev > 0, cur / prev - 1.0, np.nan)
    abs_ret = np.abs(ret)
    resume_cap = max(_RESUME_ABS_RETURN, max_abs)
    resume = (gap[1:] > _RESUME_GAP_DAYS) & np.isfinite(abs_ret) & (abs_ret <= resume_cap)
    jump = np.isfinite(abs_ret) & (abs_ret > max_abs) & ~resume
    bad[1:] = jump
    return bad


def _drop_isolated_spikes(out: pd.DataFrame, *, max_abs: float) -> pd.DataFrame:
    """孤立毛刺：当日相对前一跳，但前后两日桥接正常 → 删当日。"""
    n = len(out)
    if n < 3 or "close" not in out.columns:
        return out
    out = out.reset_index(drop=True)
    close = out["close"].to_numpy(dtype=float)
    gap = _gap_days_np(out)
    bad = _ret_and_bad(close, gap, max_abs=max_abs)
    if not bad.any():
        return out
    with np.errstate(divide="ignore", invalid="ignore"):
        bridge = np.empty(n, dtype=float)
        bridge[:] = np.nan
        bridge[1:-1] = close[2:] / close[:-2] - 1.0
    spike = bad & np.isfinite(bridge) & (np.abs(bridge) <= max_abs)
    spike[0] = False
    spike[-1] = False
    if not spike.any():
        return out
    return out.loc[~spike].copy().reset_index(drop=True)


def _trim_leading_unstable(out: pd.DataFrame, *, max_abs: float) -> pd.DataFrame:
    """从左截断到最后一个异常跳价日（含），保留其后近端可信历史。"""
    if len(out) < 2 or "close" not in out.columns:
        return out
    out = out.reset_index(drop=True)
    close = out["close"].to_numpy(dtype=float)
    gap = _gap_days_np(out)
    bad = _ret_and_bad(close, gap, max_abs=max_abs)
    if not bad.any():
        return out
    start = int(np.flatnonzero(bad)[-1])
    if start <= 0:
        return out
    return out.iloc[start:].copy().reset_index(drop=True)


def sanitize_daily_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    """清洗日线：正价格 + 合法振幅 + 孤立毛刺 + 左侧不稳定段截断。"""
    if df is None or df.empty:
        return pd.DataFrame() if df is None else df.copy()
    out = _ensure_numeric_ohlc(df.copy())
    need = [c for c in ("open", "high", "low", "close") if c in out.columns]
    if not need:
        return out
    out = out.loc[_basic_ohlc_mask(out)].copy()
    if "date" in out.columns:
        out = out.drop_duplicates(subset=["date"], keep="last")
        out = out.sort_values("date")
    out = out.reset_index(drop=True)

    for max_abs in (_MAX_ABS_RETURN, _STRICT_ABS_RETURN):
        for _ in range(_MAX_PASSES):
            before = len(out)
            out = _drop_isolated_spikes(out, max_abs=max_abs)
            out = _trim_leading_unstable(out, max_abs=max_abs)
            if len(out) == before:
                break
            if len(out) < 30:
                break

    return out.reset_index(drop=True)


def daily_extreme_stats(df: pd.DataFrame) -> dict[str, float | int]:
    """统计清洗后仍存的极端涨跌（用于挑源 / 验收）。"""
    if df is None or df.empty or "close" not in df.columns:
        return {"n": 0, "n50": 0, "n30": 0, "max_abs_ret": 0.0}
    out = df.sort_values("date") if "date" in df.columns else df
    out = out.reset_index(drop=True)
    close = out["close"].to_numpy(dtype=float)
    if len(close) < 2:
        return {"n": int(len(out)), "n50": 0, "n30": 0, "max_abs_ret": 0.0}
    with np.errstate(divide="ignore", invalid="ignore"):
        ret = close[1:] / close[:-1] - 1.0
    ret = ret[np.isfinite(ret)]
    if ret.size == 0:
        return {"n": int(len(out)), "n50": 0, "n30": 0, "max_abs_ret": 0.0}
    abs_ret = np.abs(ret)
    return {
        "n": int(len(out)),
        "n50": int((abs_ret > _MAX_ABS_RETURN).sum()),
        "n30": int((abs_ret > _STRICT_ABS_RETURN).sum()),
        "max_abs_ret": float(abs_ret.max()),
    }


def winsorize_series(values: pd.Series, *, lower: float = 0.02, upper: float = 0.98) -> pd.Series:
    """截面分位截尾；样本过少时原样返回。"""
    v = pd.to_numeric(values, errors="coerce")
    valid = v.dropna()
    if len(valid) < 8:
        return v
    lo = float(valid.quantile(lower))
    hi = float(valid.quantile(upper))
    if not (lo < hi):
        return v
    return v.clip(lower=lo, upper=hi)

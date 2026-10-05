"""legacy（牛50） vs unified_sharpe 同区间 A/B（DeepSeek 缓存）。

用法:
  python -m market_platform.predictor.脚本.统一夏普AB对比
"""

from __future__ import annotations

import json

import pandas as pd

from market_platform.predictor.配置 import get_config, reload_config
from market_platform.predictor.脚本.运行管道 import run_llm_backtest
from market_platform.predictor.核心.路径.项目 import data_dir


def main() -> None:
    reload_config()
    cfg = get_config()
    cfg.setdefault("portfolio", {})
    out = data_dir() / "processed" / "ab_unified_sharpe_vs_legacy.json"
    results = {}
    for mode in ("legacy", "unified_sharpe"):
        cfg["portfolio"]["allocation_mode"] = mode
        r = run_llm_backtest(
            "llm",
            test_start_date=None,
            save=False,
            provider="deepseek",
            is_latest=False,
        )
        if r is None:
            results[mode] = {"error": "null"}
            continue
        sig = r.daily_signals
        yearly = []
        if not sig.empty:
            s = sig.copy()
            s["date"] = pd.to_datetime(s["date"])
            s["year"] = s["date"].dt.year
            for yr, g in s.groupby("year"):
                if len(g) < 20:
                    continue
                row = {
                    "year": int(yr),
                    "total_ret": round(float((1 + g["period_return"].fillna(0)).prod() - 1), 4),
                }
                if "group_w_offense" in g.columns and g["group_w_offense"].notna().any():
                    row["avg_offense"] = round(float(g["group_w_offense"].mean()), 3)
                    row["avg_defense"] = round(float(g["group_w_defense"].mean()), 3)
                    row["avg_cashflow"] = round(float(g["group_w_cashflow"].mean()), 3)
                yearly.append(row)
        payload = {
            "total_return": round(float(r.total_return), 4),
            "sharpe_ratio": round(float(r.sharpe_ratio), 4),
            "max_drawdown": round(float(r.max_drawdown), 4),
            "avg_position": round(float(r.avg_position), 4),
            "strategy_only_return": None
            if r.strategy_only_return is None
            else round(float(r.strategy_only_return), 4),
            "cash_return": None if r.cash_return is None else round(float(r.cash_return), 4),
            "static_4020_return": getattr(r, "static_4020_return", None),
            "equal_six_return": getattr(r, "equal_six_return", None),
            "yearly": yearly,
        }
        if mode == "unified_sharpe" and not sig.empty and "cash_w_nasdaq" in sig.columns:
            h = sig["holding_position"].astype(float)
            nq = sig["cash_w_nasdaq"].astype(float)
            payload["pct_days_csi_and_nasdaq_both_high"] = round(float(((h > 0.35) & (nq > 0.25)).mean()), 4)
            payload["avg_csi300"] = round(float(h.mean()), 4)
            payload["avg_nasdaq_abs"] = round(float(nq.mean()), 4)
        results[mode] = payload

    cfg["portfolio"]["allocation_mode"] = "legacy"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(results, ensure_ascii=False, indent=2))
    print("saved", out)


if __name__ == "__main__":
    main()

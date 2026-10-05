"""纪律策略实验：discipline_rule vs qwen / deepseek（默认 holdout）。

用法:
  python -m market_platform.predictor.脚本.纪律策略实验
  python -m market_platform.predictor.脚本.纪律策略实验 --holdout 2021-01-01
"""

from __future__ import annotations

import argparse
import json
from collections import Counter

import numpy as np
import pandas as pd
from loguru import logger

from market_platform.predictor.配置 import reload_config, get_config
from market_platform.predictor.脚本.运行管道 import run_llm_backtest
from market_platform.predictor.核心.路径.项目 import data_dir
from market_platform.predictor.分析.推断.纪律策略 import DisciplineRulePredictor


def _summarize_result(label: str, r) -> dict:
    if r is None:
        return {"label": label, "error": "null"}
    sig = r.daily_signals
    pred = pd.to_numeric(sig["prediction"], errors="coerce") if not sig.empty else pd.Series(dtype=float)
    act = pd.to_numeric(sig["actual"], errors="coerce") if not sig.empty else pd.Series(dtype=float)
    conf = pd.to_numeric(sig["confidence"], errors="coerce") if not sig.empty else pd.Series(dtype=float)
    hit = (pred == act) if len(pred) else pd.Series(dtype=bool)
    both_dir = (pred != 0) & (act != 0) if len(pred) else pd.Series(dtype=bool)
    return {
        "label": label,
        "n": int(len(sig)) if sig is not None else 0,
        "acc": round(float(hit.mean()), 4) if len(hit) else None,
        "dir_acc": round(float((pred[both_dir] == act[both_dir]).mean()), 4) if both_dir.any() else None,
        "flat_pct": round(float((pred == 0).mean()) * 100, 1) if len(pred) else None,
        "up_pct": round(float((pred == 1).mean()) * 100, 1) if len(pred) else None,
        "dn_pct": round(float((pred == -1).mean()) * 100, 1) if len(pred) else None,
        "conf_mean": round(float(conf.mean()), 3) if len(conf) else None,
        "total_return": round(float(r.total_return), 4),
        "ann": round(float(r.annualized_return), 4),
        "sharpe": round(float(r.sharpe_ratio), 4),
        "mdd": round(float(r.max_drawdown), 4),
        "trades": int(r.total_trades),
    }


def _breakdown() -> dict:
    pred = DisciplineRulePredictor()
    pred.train(pd.DataFrame())
    assert pred._panel is not None and pred._out_pred is not None
    panel = pred._panel
    rules = Counter(m.get("rule") for m in pred._out_meta)
    primaries = Counter(m.get("primary") for m in pred._out_meta)
    out = pred._out_pred
    by_rule = {}
    for rule, n in rules.items():
        mask = np.array([m.get("rule") == rule for m in pred._out_meta])
        acc = float((out[mask] == panel.actual[mask]).mean()) if n else None
        by_rule[rule] = {"n": int(n), "share_pct": round(100.0 * n / len(out), 1), "acc": round(acc, 4) if acc else None}
    same_q = float((out == panel.preds["qwen"]).mean()) if "qwen" in panel.preds else None
    # holdout primary share
    hmask = panel.dates >= pd.Timestamp("2021-01-01")
    prim_h = Counter(m.get("primary") for m, ok in zip(pred._out_meta, hmask) if ok)
    return {
        "rules": by_rule,
        "primary_counts": dict(primaries),
        "primary_holdout": dict(prim_h),
        "same_as_qwen_pct": round(100.0 * same_q, 1) if same_q is not None else None,
        "cls_acc": round(float((out == panel.actual).mean()), 4),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="纪律策略实验")
    parser.add_argument("--holdout", type=str, default="2021-01-01")
    parser.add_argument("--save", action="store_true")
    parser.add_argument("--baselines", type=str, default="qwen,deepseek", help="逗号分隔对照渠道")
    args = parser.parse_args()

    reload_config()
    cfg = get_config()
    cfg.setdefault("portfolio", {})["allocation_mode"] = "unified_sharpe"
    holdout = args.holdout.strip() or None

    info = _breakdown()
    logger.info("规则分布: {}", info["rules"])
    logger.info("主信号占比: {} | holdout: {}", info["primary_counts"], info["primary_holdout"])
    logger.info("与 qwen 同向: {}%", info["same_as_qwen_pct"])

    rows = []
    logger.info("=== discipline_rule holdout={} ===", holdout)
    r = run_llm_backtest(
        "llm", save=bool(args.save), provider="discipline_rule",
        is_latest=False, test_start_date=holdout,
    )
    rows.append(_summarize_result("discipline_rule", r))

    for name in [x.strip() for x in args.baselines.split(",") if x.strip()]:
        logger.info("=== {} ===", name)
        br = run_llm_backtest(
            "llm", save=False, provider=name, is_latest=False, test_start_date=holdout,
        )
        rows.append(_summarize_result(name, br))

    # 附上此前 specialty holdout 结果便于对照
    prev_path = data_dir() / "processed" / "specialty_factor_experiment.json"
    if prev_path.exists():
        prev = json.loads(prev_path.read_text(encoding="utf-8"))
        for row in prev.get("results") or []:
            if row.get("label") in ("role_ensemble", "specialty_factor") and row.get("label") not in {r["label"] for r in rows}:
                row = dict(row)
                row["source"] = "prev_holdout_json"
                rows.append(row)

    out_df = pd.DataFrame(rows).sort_values("sharpe", ascending=False)
    print("\n=== holdout 对照（按夏普）===")
    print(out_df.to_string(index=False))

    out_path = data_dir() / "processed" / "discipline_rule_experiment.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"holdout": holdout, "breakdown": info, "results": rows, "best": out_df.iloc[0].to_dict() if len(out_df) else None}
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("结果已写 {}", out_path)


if __name__ == "__main__":
    main()

"""五模型角色化调仓实验：role_ensemble vs 单渠道 / stacking。

用法:
  python -m market_platform.predictor.脚本.角色调仓实验
  python -m market_platform.predictor.脚本.角色调仓实验 --save
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
from market_platform.predictor.分析.推断.角色调仓 import RoleEnsemblePredictor
from market_platform.predictor.存储.数据访问 import load_backtest_by_model_name
from market_platform.predictor.接口.回测存储 import expected_model_label


BASELINES = ("deepseek", "qwen", "doubao", "kimi", "minimax")


def _summarize_result(label: str, r) -> dict:
    if r is None:
        return {"label": label, "error": "null"}
    sig = r.daily_signals
    pred = pd.to_numeric(sig["prediction"], errors="coerce") if not sig.empty else pd.Series(dtype=float)
    act = pd.to_numeric(sig["actual"], errors="coerce") if not sig.empty else pd.Series(dtype=float)
    conf = pd.to_numeric(sig["confidence"], errors="coerce") if not sig.empty else pd.Series(dtype=float)
    hit = (pred == act) if len(pred) else pd.Series(dtype=bool)
    both_dir = (pred != 0) & (act != 0) if len(pred) else pd.Series(dtype=bool)
    off = pd.to_numeric(sig.get("group_w_offense"), errors="coerce") if not sig.empty and "group_w_offense" in sig.columns else None
    return {
        "label": label,
        "n": int(len(sig)) if sig is not None else 0,
        "acc": round(float(hit.mean()), 4) if len(hit) else None,
        "dir_acc": round(float((pred[both_dir] == act[both_dir]).mean()), 4) if both_dir.any() else None,
        "flat_pct": round(float((pred == 0).mean()) * 100, 1) if len(pred) else None,
        "up_pct": round(float((pred == 1).mean()) * 100, 1) if len(pred) else None,
        "dn_pct": round(float((pred == -1).mean()) * 100, 1) if len(pred) else None,
        "conf_mean": round(float(conf.mean()), 3) if len(conf) else None,
        "avg_offense": round(float(off.mean()), 3) if off is not None and off.notna().any() else None,
        "total_return": round(float(r.total_return), 4),
        "ann": round(float(r.annualized_return), 4),
        "sharpe": round(float(r.sharpe_ratio), 4),
        "mdd": round(float(r.max_drawdown), 4),
        "trades": int(r.total_trades),
    }


def _rule_breakdown() -> dict:
    pred = RoleEnsemblePredictor()
    pred.train(pd.DataFrame())
    assert pred._panel is not None and pred._out_pred is not None
    panel = pred._panel
    rules = Counter(m.get("rule") for m in pred._out_meta)
    out_pred = pred._out_pred
    actual = panel.actual
    by_rule = {}
    for rule in rules:
        mask = np.array([m.get("rule") == rule for m in pred._out_meta])
        n = int(mask.sum())
        acc = float((out_pred[mask] == actual[mask]).mean()) if n else None
        by_rule[rule] = {
            "n": n,
            "share_pct": round(100.0 * n / len(mask), 1),
            "acc": round(acc, 4) if acc is not None else None,
        }
    return {"rules": by_rule, "members": pred._members, "n_days": len(panel.dates)}


def _load_saved_baseline(name: str) -> dict | None:
    bt = load_backtest_by_model_name(expected_model_label(name))
    if not bt:
        return None
    return {
        "label": name,
        "n": len(bt.get("signals") or []),
        "acc": round(float(bt.get("accuracy") or 0), 4),
        "flat_pct": round(float(bt.get("pred_flat_rate") or 0) * 100, 1) if bt.get("pred_flat_rate") is not None else None,
        "total_return": round(float(bt.get("total_return") or 0), 4),
        "ann": round(float(bt.get("annualized_return") or 0), 4),
        "sharpe": round(float(bt.get("sharpe_ratio") or 0), 4),
        "mdd": round(float(bt.get("max_drawdown") or 0), 4),
        "trades": bt.get("total_trades"),
        "source": "saved",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="五模型角色化调仓实验")
    parser.add_argument("--save", action="store_true", help="将 role_ensemble 回测入库（is_latest=False）")
    parser.add_argument("--run-ensemble", action="store_true", help="同时跑 stacking ensemble 对照（较慢）")
    parser.add_argument("--rerun-baselines", action="store_true", help="重跑五渠道而非读已入库结果")
    args = parser.parse_args()

    reload_config()
    cfg = get_config()
    cfg.setdefault("portfolio", {})["allocation_mode"] = "unified_sharpe"

    rows = []
    logger.info("=== 角色规则分布（合成信号）===")
    breakdown = _rule_breakdown()
    for rule, info in sorted(breakdown["rules"].items(), key=lambda kv: -kv[1]["n"]):
        logger.info("  {:>16}: n={:<5} share={:>5}% acc={}", rule, info["n"], info["share_pct"], info["acc"])

    logger.info("=== 跑 role_ensemble 回测 ===")
    role_r = run_llm_backtest(
        "llm",
        save=bool(args.save),
        provider="role_ensemble",
        is_latest=False,
    )
    rows.append(_summarize_result("role_ensemble", role_r))

    if args.run_ensemble:
        logger.info("=== 跑 stacking ensemble 对照 ===")
        ens_r = run_llm_backtest("llm", save=False, provider="ensemble", is_latest=False)
        rows.append(_summarize_result("ensemble_stack", ens_r))

    for name in BASELINES:
        if args.rerun_baselines:
            logger.info("=== 重跑 {} ===", name)
            r = run_llm_backtest("llm", save=False, provider=name, is_latest=False)
            rows.append(_summarize_result(name, r))
        else:
            saved = _load_saved_baseline(name)
            if saved:
                rows.append(saved)
            else:
                logger.warning("{} 无入库回测，跳过", name)

    out_df = pd.DataFrame(rows).sort_values("sharpe", ascending=False)
    print("\n=== 对照汇总（按夏普）===")
    print(out_df.to_string(index=False))

    out_path = data_dir() / "processed" / "role_ensemble_experiment.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "rule_breakdown": breakdown,
        "results": rows,
        "best": out_df.iloc[0].to_dict() if len(out_df) else None,
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("结果已写 {}", out_path)


if __name__ == "__main__":
    main()

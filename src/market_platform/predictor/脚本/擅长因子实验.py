"""擅长因子综合实验：specialty_factor vs 单渠道 / role。

用法:
  python -m market_platform.predictor.脚本.擅长因子实验
  python -m market_platform.predictor.脚本.擅长因子实验 --save
  python -m market_platform.predictor.脚本.擅长因子实验 --holdout 2021-01-01
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
from market_platform.predictor.分析.推断.擅长因子 import SpecialtyFactorPredictor
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
    off = (
        pd.to_numeric(sig.get("group_w_offense"), errors="coerce")
        if not sig.empty and "group_w_offense" in sig.columns
        else None
    )
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
    pred = SpecialtyFactorPredictor()
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
    # 平均权重（非 warmup）
    w_acc: dict[str, list[float]] = {m: [] for m in pred._members}
    for meta in pred._out_meta:
        if meta.get("warmup"):
            continue
        for m, w in (meta.get("weights") or {}).items():
            if m in w_acc:
                w_acc[m].append(float(w))
    avg_w = {m: round(float(np.mean(vs)), 4) if vs else None for m, vs in w_acc.items()}
    return {
        "rules": by_rule,
        "members": pred._members,
        "n_days": len(panel.dates),
        "avg_weights": avg_w,
        "cls_acc": round(float((out_pred == actual).mean()), 4),
    }


def _overlap_with_qwen() -> dict:
    """合成信号与 qwen 同向比例（看是否实质退化成跟 qwen）。"""
    sp = SpecialtyFactorPredictor()
    sp.train(pd.DataFrame())
    assert sp._panel is not None and sp._out_pred is not None
    if "qwen" not in sp._panel.preds:
        return {}
    q = sp._panel.preds["qwen"]
    same = float((sp._out_pred == q).mean())
    return {"same_as_qwen_pct": round(100.0 * same, 1), "n": len(q)}


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
    parser = argparse.ArgumentParser(description="擅长因子综合实验")
    parser.add_argument("--save", action="store_true", help="将 specialty_factor 回测入库（is_latest=False）")
    parser.add_argument("--holdout", type=str, default="", help="样本外起点，如 2021-01-01")
    parser.add_argument("--with-role", action="store_true", help="同时跑 role_ensemble 对照")
    parser.add_argument("--rerun-baselines", action="store_true", help="重跑五渠道而非读已入库结果")
    args = parser.parse_args()

    reload_config()
    cfg = get_config()
    cfg.setdefault("portfolio", {})["allocation_mode"] = "unified_sharpe"
    holdout = args.holdout.strip() or None

    rows = []
    logger.info("=== 擅长因子规则分布 ===")
    breakdown = _rule_breakdown()
    for rule, info in sorted(breakdown["rules"].items(), key=lambda kv: -kv[1]["n"]):
        logger.info("  {:>24}: n={:<5} share={:>5}% acc={}", rule, info["n"], info["share_pct"], info["acc"])
    logger.info("平均权重: {}", breakdown.get("avg_weights"))
    overlap = _overlap_with_qwen()
    logger.info("与 qwen 同向: {}", overlap)

    logger.info("=== 跑 specialty_factor 回测 holdout={} ===", holdout or "full")
    sp_r = run_llm_backtest(
        "llm",
        save=bool(args.save),
        provider="specialty_factor",
        is_latest=False,
        test_start_date=holdout,
    )
    rows.append(_summarize_result("specialty_factor", sp_r))

    if args.with_role:
        logger.info("=== 跑 role_ensemble 对照 ===")
        role_r = run_llm_backtest(
            "llm",
            save=False,
            provider="role_ensemble",
            is_latest=False,
            test_start_date=holdout,
        )
        rows.append(_summarize_result("role_ensemble", role_r))

    for name in BASELINES:
        if args.rerun_baselines or holdout:
            logger.info("=== 跑 {} holdout={} ===", name, holdout or "full")
            r = run_llm_backtest("llm", save=False, provider=name, is_latest=False, test_start_date=holdout)
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

    out_path = data_dir() / "processed" / "specialty_factor_experiment.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "holdout": holdout,
        "rule_breakdown": breakdown,
        "overlap_qwen": overlap,
        "results": rows,
        "best": out_df.iloc[0].to_dict() if len(out_df) else None,
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("结果已写 {}", out_path)


if __name__ == "__main__":
    main()

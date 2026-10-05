"""配置加载:默认读取 `config/指数预测/配置档/默认.yaml`,可用 CONFIG_PATH 覆盖。

环境变量与 `${ENV}` 展开统一走 `core.配置`(唯一实现),不再各自维护一份。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict

import pandas as pd
import yaml

from market_platform.core.配置 import 展开环境变量 as _core_expand_env
from market_platform.core.配置 import 加载环境变量 as _core_load_env
from market_platform.predictor.核心.路径.项目 import backend_root, profile_path, repo_root

DEFAULT_CONFIG_PATH = profile_path("默认.yaml")

# 兼容旧代码中的 PROJECT_ROOT（指向仓库根）
PROJECT_ROOT = repo_root()

_config: Dict[str, Any] | None = None


def _load_env_files() -> None:
    """加载环境变量文件(优先 config/.env,回退仓库根/.env)。"""
    _core_load_env()


def _expand_env(value: Any) -> Any:
    """展开 YAML 中的 ${ENV_VAR} 占位符(委托 core,支持 ${VAR:-默认值})。"""
    return _core_expand_env(value)


def _deep_merge(base: dict, overlay: dict) -> dict:
    out = dict(base)
    for key, val in overlay.items():
        if isinstance(val, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], val)
        else:
            out[key] = val
    return out


def _resolve_config_path(config_path: str | Path | None = None) -> Path:
    if config_path is not None:
        path = Path(config_path)
    else:
        env_path = os.environ.get("CONFIG_PATH", "").strip()
        if env_path:
            path = Path(env_path)
        else:
            return DEFAULT_CONFIG_PATH
    if not path.is_absolute():
        # 相对路径优先从仓库根解析，其次从后端根
        candidate = repo_root() / path
        if candidate.exists():
            return candidate
        path = backend_root() / path
    return path


def load_config(config_path: str | Path | None = None) -> Dict[str, Any]:
    """加载 YAML 配置文件。"""
    _load_env_files()
    path = _resolve_config_path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"配置文件不存在: {path}")
    with open(path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}
    if not isinstance(config, dict):
        raise ValueError(f"配置文件格式无效（应为 YAML 映射）: {path}")

    local_path = path.parent / "本地.yaml"
    if local_path.exists():
        with open(local_path, "r", encoding="utf-8") as f:
            local_cfg = yaml.safe_load(f) or {}
        if isinstance(local_cfg, dict):
            config = _deep_merge(config, local_cfg)

    return _expand_env(config)


def reload_config(config_path: str | Path | None = None) -> Dict[str, Any]:
    """重新加载配置并刷新单例缓存。"""
    global _config
    _config = load_config(config_path)
    return _config


def get_config() -> Dict[str, Any]:
    global _config
    if _config is None:
        _config = load_config()
    return _config


def resolve_end_date(value: str | None = None) -> str:
    """解析数据截止日期；auto/today 表示今天。"""
    if value is None:
        value = get_config().get("data", {}).get("end_date", "auto")
    text = str(value).strip().lower()
    if text in {"", "auto", "today", "null", "none"}:
        return pd.Timestamp.now().normalize().strftime("%Y-%m-%d")
    return str(value)


def get_bar_meta() -> Dict[str, Any]:
    """当前 K 线频率 / 决策频率相关的 UI · Prompt 元数据。"""
    cfg = get_config()
    bar_freq = cfg.get("data", {}).get("bar_freq", "daily")
    prompt_cfg = cfg.get("model", {}).get("llm", {}).get("prompt", {})
    strat = cfg.get("backtest", {}).get("strategy", {}) or {}
    weekly_decision = bool((strat.get("weekly_decision") or {}).get("enabled", False))
    if not weekly_decision:
        # 兼容：关双频 + 周调仓 on_week_end
        dual = bool(strat.get("dual_freq_gate", False))
        wr = strat.get("weekly_rebalance", {}) or {}
        weekly_decision = (not dual) and bool(wr.get("on_week_end", wr.get("enabled", False)))

    if weekly_decision:
        # 底层仍可能是日 K，但产品叙事统一为周频决策
        lookback = int(prompt_cfg.get("lookback_days", 30))
        week_h = int(cfg.get("features", {}).get("label", {}).get("week_horizon", 5))
        return {
            "bar_freq": bar_freq,
            "decision_freq": "weekly",
            "weekly_decision": True,
            "dual_frequency": False,
            "bar_unit": "周",
            "sample_unit": "周",
            "lookback_bars": lookback,
            "lookback_label": f"近 {lookback} 根信号窗口",
            "price_series_default": 60,
            "subtitle": "沪深300 周频决策 · LLM 综合研判",
            "predict_horizon": "下一周",
            "analyze_date_hint": "默认最近周尾交易日",
            "analysis_intro": (
                f"点击「开始分析」调用 LLM，基于本周已实现信号复合（量价/因子/估值资金/新闻）"
                f"研判随后约 {week_h} 个交易日方向与幅度。"
            ),
            "week_horizon": week_h,
        }

    if bar_freq == "weekly":
        lookback = int(prompt_cfg.get("lookback_weeks", 12))
        return {
            "bar_freq": "weekly",
            "decision_freq": "weekly",
            "weekly_decision": False,
            "dual_frequency": False,
            "bar_unit": "周",
            "sample_unit": "周",
            "lookback_bars": lookback,
            "lookback_label": f"近 {lookback} 周",
            "price_series_default": 52,
            "subtitle": "沪深300 周频 · LLM 综合研判",
            "predict_horizon": "下一周",
            "analyze_date_hint": "默认最近一周",
            "analysis_intro": (
                f"点击「开始分析」调用 LLM，基于近 {lookback} 周走势 + 七维因子研判下一周方向。"
            ),
        }
    lookback = int(prompt_cfg.get("lookback_days", 30))
    daily_th = float(cfg.get("features", {}).get("label", {}).get("direction_threshold", 0.003))
    regime_on = bool((prompt_cfg.get("regime_router") or {}).get("enabled", False))
    prompt_ver = prompt_cfg.get("version", "v1")
    th_pct = f"{daily_th * 100:.2f}"
    return {
        "bar_freq": "daily",
        "decision_freq": "daily",
        "weekly_decision": False,
        "dual_frequency": bool(strat.get("dual_freq_gate", False)),
        "bar_unit": "日",
        "sample_unit": "个交易日",
        "lookback_bars": lookback,
        "lookback_label": f"近 {lookback} 日",
        "price_series_default": 60,
        "subtitle": "沪深300 · 趋势定底仓 · 研判调叠加",
        "predict_horizon": "下一交易日",
        "analyze_date_hint": "默认最近交易日",
        "daily_threshold_pct": round(daily_th * 100, 2),
        "prompt_version": prompt_ver,
        "regime_router": regime_on,
        "analysis_intro": (
            f"交易主线：趋势（SMA60）定底仓，研判信号调叠加；"
            f"信号 = LLM（基本面/外盘/新闻）× Transformer（量价）；平盘带 ±{th_pct}%。"
            + ("Prompt 含状态路由；" if regime_on else "")
            + f"近 {lookback} 日七维因子 + 外盘/新闻供 LLM 参考（量价不进 Prompt，只读展示）。"
        ),
    }

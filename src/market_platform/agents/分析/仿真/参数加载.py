from __future__ import annotations

import json
from pathlib import Path

from market_platform.agents.settings import AppConfig
from market_platform.agents.分析.仿真.智能体 import (
    HotMoneyAgentParams,
    InstitutionalAgentParams,
    MacroAgentParams,
    QuantAgentParams,
    RetailAgentParams,
    SimulationParams,
)


def calibrated_params_path(config: AppConfig) -> Path:
    return config.output.root / config.data.symbol / "simulation" / "calibrated_params.json"


def load_calibrated_params(config: AppConfig) -> SimulationParams | None:
    """从 calibrate 输出加载参数，不存在则返回 None。"""
    path = calibrated_params_path(config)
    if not path.exists():
        return None

    payload = json.loads(path.read_text(encoding="utf-8"))
    raw = payload.get("params", payload)

    return SimulationParams(
        retail=RetailAgentParams(
            momentum_sensitivity=float(raw["retail_momentum_sensitivity"]),
            base_order_size=float(raw["retail_base_order_size"]),
            chase_threshold=float(raw["retail_chase_threshold"]),
            panic_threshold=-float(raw["retail_chase_threshold"]),
        ),
        institutional=InstitutionalAgentParams(
            reversion_sensitivity=float(raw["institutional_reversion_sensitivity"]),
            base_order_size=float(raw["institutional_base_order_size"]),
            vwap_entry_threshold=float(raw["institutional_vwap_threshold"]),
        ),
        quant=QuantAgentParams(
            signal_sensitivity=float(raw.get("quant_signal_sensitivity", 1.0)),
            base_order_size=float(raw.get("quant_base_order_size", 150_000)),
            rsi_oversold=float(raw.get("quant_rsi_oversold", 30)),
            rsi_overbought=float(raw.get("quant_rsi_overbought", 70)),
            vol_spike_threshold=float(raw.get("quant_vol_spike_threshold", 2.0)),
        ),
        macro=MacroAgentParams(
            signal_sensitivity=float(raw.get("macro_signal_sensitivity", 1.0)),
            base_order_size=float(raw.get("macro_base_order_size", 500_000)),
        ),
        hot_money=HotMoneyAgentParams(
            signal_sensitivity=float(raw.get("hot_money_signal_sensitivity", 1.0)),
            base_order_size=float(raw.get("hot_money_base_order_size", 300_000)),
        ),
        impact_lambda=float(raw["impact_lambda"]),
        lookback=int(raw.get("lookback", config.simulation.lookback)),
    )


def resolve_simulation_params(
    config: AppConfig,
    prefer_calibrated: bool = True,
) -> tuple[SimulationParams, str]:
    """优先使用校准参数，否则回退到 YAML 配置。"""
    base = config.simulation.to_params()

    if prefer_calibrated:
        loaded = load_calibrated_params(config)
        if loaded is not None:
            loaded.quant = base.quant
            loaded.macro = base.macro
            loaded.hot_money = base.hot_money
            loaded.lookback = config.simulation.lookback
            return loaded, "calibrated"

    return base, "config"

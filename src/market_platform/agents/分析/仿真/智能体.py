from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass
class DailyMacroContext:
    """日频宏观信号，分钟内共享。"""

    northbound_signal: float = 0.0
    margin_signal: float = 0.0


@dataclass
class MarketContext:
    """Agent 决策时可观测的市场状态。"""

    price: float
    return_1min: float
    return_5min: float
    vwap_deviation: float
    volume: float
    avg_volume: float
    rsi: float = 50.0
    realized_vol: float = 0.0
    volume_spike: float = 1.0
    minute_progress: float = 0.5
    northbound_signal: float = 0.0
    margin_signal: float = 0.0
    retail_activity: float = 1.0
    institutional_activity: float = 1.0
    quant_activity: float = 1.0
    macro_activity: float = 1.0
    hot_money_activity: float = 1.0


@dataclass
class RetailAgentParams:
    momentum_sensitivity: float = 1.0
    chase_threshold: float = 0.002
    panic_threshold: float = -0.002
    base_order_size: float = 50_000.0


@dataclass
class InstitutionalAgentParams:
    reversion_sensitivity: float = 1.0
    vwap_entry_threshold: float = 0.003
    base_order_size: float = 200_000.0


@dataclass
class QuantAgentParams:
    signal_sensitivity: float = 1.0
    base_order_size: float = 150_000.0
    rsi_oversold: float = 30.0
    rsi_overbought: float = 70.0
    vol_spike_threshold: float = 2.0


@dataclass
class MacroAgentParams:
    signal_sensitivity: float = 1.0
    base_order_size: float = 500_000.0
    active_minutes_start: float = 0.0
    active_minutes_end: float = 0.2
    active_minutes_tail: float = 0.8


@dataclass
class HotMoneyAgentParams:
    signal_sensitivity: float = 1.0
    base_order_size: float = 300_000.0
    momentum_threshold: float = 0.003
    vol_spike_threshold: float = 2.5
    tail_progress_threshold: float = 0.85


@dataclass
class SimulationParams:
    retail: RetailAgentParams = field(default_factory=RetailAgentParams)
    institutional: InstitutionalAgentParams = field(default_factory=InstitutionalAgentParams)
    quant: QuantAgentParams = field(default_factory=QuantAgentParams)
    macro: MacroAgentParams = field(default_factory=MacroAgentParams)
    hot_money: HotMoneyAgentParams = field(default_factory=HotMoneyAgentParams)
    impact_lambda: float = 0.15
    lookback: int = 5


class RetailAgent:
    """散户追涨杀跌型：短周期动量驱动。"""

    def __init__(self, params: RetailAgentParams | None = None):
        self.params = params or RetailAgentParams()

    def decide(self, ctx: MarketContext) -> float:
        p = self.params
        scale = max(ctx.retail_activity, 0.0)
        if ctx.return_5min >= p.chase_threshold:
            return p.base_order_size * p.momentum_sensitivity * scale
        if ctx.return_5min <= p.panic_threshold:
            return -p.base_order_size * p.momentum_sensitivity * scale
        return 0.0


class InstitutionalAgent:
    """机构价值型：对 VWAP 偏离做均值回归。"""

    def __init__(self, params: InstitutionalAgentParams | None = None):
        self.params = params or InstitutionalAgentParams()

    def decide(self, ctx: MarketContext) -> float:
        p = self.params
        scale = max(ctx.institutional_activity, 0.0)
        if ctx.vwap_deviation <= -p.vwap_entry_threshold:
            strength = min(abs(ctx.vwap_deviation) / p.vwap_entry_threshold, 3.0)
            return p.base_order_size * p.reversion_sensitivity * strength * scale
        if ctx.vwap_deviation >= p.vwap_entry_threshold:
            strength = min(abs(ctx.vwap_deviation) / p.vwap_entry_threshold, 3.0)
            return -p.base_order_size * p.reversion_sensitivity * strength * scale
        return 0.0


class QuantitativeAgent:
    """量化/统计套利型：RSI 极值反转 + 放量动量。"""

    def __init__(self, params: QuantAgentParams | None = None):
        self.params = params or QuantAgentParams()

    def decide(self, ctx: MarketContext) -> float:
        p = self.params
        scale = max(ctx.quant_activity, 0.0)
        signal = 0.0

        if ctx.rsi <= p.rsi_oversold:
            signal += 1.0
        elif ctx.rsi >= p.rsi_overbought:
            signal -= 1.0

        if ctx.volume_spike >= p.vol_spike_threshold:
            signal += float(np.sign(ctx.return_5min)) * 0.5

        if abs(signal) < 1e-9:
            return 0.0
        return p.base_order_size * p.signal_sensitivity * signal * scale


class MacroAgent:
    """宏观对冲/国家队型：北向 + 两融日频信号，开盘/尾盘慢速执行。"""

    def __init__(self, params: MacroAgentParams | None = None):
        self.params = params or MacroAgentParams()

    def decide(self, ctx: MarketContext) -> float:
        p = self.params
        scale = max(ctx.macro_activity, 0.0)
        in_open = ctx.minute_progress <= p.active_minutes_end
        in_tail = ctx.minute_progress >= p.active_minutes_tail
        if not (in_open or in_tail):
            return 0.0

        signal = ctx.northbound_signal * 0.6 + ctx.margin_signal * 0.4
        if abs(signal) < 0.05:
            return 0.0
        return p.base_order_size * p.signal_sensitivity * signal * scale


class HotMoneyAgent:
    """游资/事件驱动型：放量突破 + 尾盘拉升跟随。"""

    def __init__(self, params: HotMoneyAgentParams | None = None):
        self.params = params or HotMoneyAgentParams()

    def decide(self, ctx: MarketContext) -> float:
        p = self.params
        scale = max(ctx.hot_money_activity, 0.0)
        signal = 0.0

        if (
            ctx.volume_spike >= p.vol_spike_threshold
            and ctx.return_5min >= p.momentum_threshold
        ):
            signal += 1.0
        if (
            ctx.minute_progress >= p.tail_progress_threshold
            and ctx.return_5min > 0
            and ctx.volume_spike >= 1.5
        ):
            signal += 0.6

        if abs(signal) < 1e-9:
            return 0.0
        return p.base_order_size * p.signal_sensitivity * signal * scale


def params_to_vector(params: SimulationParams) -> np.ndarray:
    return np.array(
        [
            params.retail.momentum_sensitivity,
            params.retail.base_order_size,
            params.retail.chase_threshold,
            params.institutional.reversion_sensitivity,
            params.institutional.base_order_size,
            params.institutional.vwap_entry_threshold,
            params.impact_lambda,
        ],
        dtype=float,
    )


def vector_to_params(
    vec: np.ndarray,
    lookback: int = 5,
    template: SimulationParams | None = None,
) -> SimulationParams:
    base = template or SimulationParams()
    return SimulationParams(
        retail=RetailAgentParams(
            momentum_sensitivity=float(vec[0]),
            base_order_size=float(vec[1]),
            chase_threshold=float(vec[2]),
            panic_threshold=-float(vec[2]),
        ),
        institutional=InstitutionalAgentParams(
            reversion_sensitivity=float(vec[3]),
            base_order_size=float(vec[4]),
            vwap_entry_threshold=float(vec[5]),
        ),
        quant=base.quant,
        macro=base.macro,
        hot_money=base.hot_money,
        impact_lambda=float(vec[6]),
        lookback=lookback,
    )

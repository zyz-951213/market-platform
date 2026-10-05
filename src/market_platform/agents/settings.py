from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    from market_platform.agents.分析.仿真.智能体 import SimulationParams


def _dedupe_source_chain(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in items:
        name = str(raw).strip()
        if name and name not in seen:
            seen.add(name)
            out.append(name)
    return out


def build_source_chain(
    raw: dict[str, Any],
    *,
    key: str,
    primary_key: str,
    fallback_key: str,
    default_primary: str,
    default_fallback: list[str],
    legacy_default: list[str],
) -> list[str]:
    """解析数据源链：支持 primary/fallback 或 legacy 列表。"""
    block = raw.get(key)
    if isinstance(block, dict):
        primary = str(block.get("primary", default_primary)).strip() or default_primary
        fallback = block.get("fallback", default_fallback)
        if not isinstance(fallback, list):
            fallback = default_fallback
        return _dedupe_source_chain([primary, *[str(x).strip() for x in fallback if str(x).strip()]])
    if isinstance(block, list) and block:
        return _dedupe_source_chain([str(x).strip() for x in block if str(x).strip()])
    primary = str(raw.get(primary_key, default_primary)).strip() or default_primary
    fallback_raw = raw.get(fallback_key, default_fallback)
    if not isinstance(fallback_raw, list):
        fallback_raw = default_fallback
    if primary or fallback_raw:
        return _dedupe_source_chain([primary, *[str(x).strip() for x in fallback_raw if str(x).strip()]])
    return list(legacy_default)


def _default_data_root() -> Path:
    from market_platform.agents.核心.路径.项目 import resolve_data_root

    return resolve_data_root("data")


def _default_output_root() -> Path:
    from market_platform.agents.核心.路径.项目 import resolve_output_root

    return resolve_output_root("outputs")


@dataclass
class EmailNotifyConfig:
    enabled: bool = False
    smtp_host: str = ""
    smtp_port: int = 587
    username: str = ""
    password: str = ""
    from_addr: str = ""
    to_addrs: list[str] = field(default_factory=list)
    use_tls: bool = True


@dataclass
class NotifyConfig:
    enabled: bool = False
    webhook_url: str = ""
    msgtype: str = "text"
    email: EmailNotifyConfig = field(default_factory=EmailNotifyConfig)


@dataclass
class DataConfig:
    root: Path = field(default_factory=_default_data_root)
    symbol: str = "000001"
    market: str = "sz"
    sector: str = ""
    name: str = ""
    start_date: str = "20240101"
    end_date: str | None = None
    fetch_sleep_seconds: float = 1.0
    fallback_daily_proxy: bool = True
    daily_sources: list[str] = field(default_factory=lambda: ["eastmoney", "tencent", "sina"])
    minute_sources: list[str] = field(default_factory=lambda: ["baostock", "sina", "eastmoney", "daily_proxy"])
    fund_flow_sources: list[str] = field(
        default_factory=lambda: ["eastmoney_direct", "sina", "eastmoney", "volume_proxy"]
    )
    use_rolling_window: bool = True
    rolling_window_days: int = 504
    group_by_sector: bool = True
    group_by_l2: bool = True
    name_as_folder: bool = True

    @property
    def ts_code(self) -> str:
        suffix = "SH" if self.market.lower() == "sh" else "SZ"
        return f"{self.symbol}.{suffix}"

    def resolved_end_date(self) -> str:
        if self.end_date:
            return self.end_date
        return date.today().strftime("%Y%m%d")

    def resolved_start_date(self) -> str:
        """滚动窗口优先；否则使用配置中的固定 start_date。"""
        if self.use_rolling_window:
            from datetime import timedelta

            start = date.today() - timedelta(days=self.rolling_window_days)
            return start.strftime("%Y%m%d")
        return self.start_date


@dataclass
class NMFConfig:
    n_components: int = 5
    max_iter: int = 500
    random_state: int = 42
    # point-in-time 追加式分解(评审 R4):历史活跃度保持原值,只算新增行;
    # 新增行数达到 refit_every_rows 时,仅用"历史窗口"重拟合基,不触碰历史行。
    append_only: bool = True
    refit_every_rows: int = 250


@dataclass
class FeatureConfig:
    minute: dict[str, int] = field(
        default_factory=lambda: {"volume_spike_window": 20, "realized_vol_window": 5}
    )


@dataclass
class OutputConfig:
    root: Path = field(default_factory=_default_output_root)
    group_by_sector: bool = False
    group_by_l2: bool = True
    name_as_folder: bool = True


@dataclass
class SimulationConfig:
    lookback: int = 5
    impact_lambda: float = 0.15
    retail_momentum_sensitivity: float = 1.0
    retail_base_order_size: float = 50_000.0
    retail_chase_threshold: float = 0.002
    institutional_reversion_sensitivity: float = 1.0
    institutional_base_order_size: float = 200_000.0
    institutional_vwap_threshold: float = 0.003
    quant_signal_sensitivity: float = 1.0
    quant_base_order_size: float = 150_000.0
    quant_rsi_oversold: float = 30.0
    quant_rsi_overbought: float = 70.0
    quant_vol_spike_threshold: float = 2.0
    macro_signal_sensitivity: float = 1.0
    macro_base_order_size: float = 500_000.0
    hot_money_signal_sensitivity: float = 1.0
    hot_money_base_order_size: float = 300_000.0
    hot_money_momentum_threshold: float = 0.003
    prefer_calibrated: bool = True

    def to_params(self) -> "SimulationParams":
        from market_platform.agents.分析.仿真.智能体 import (
            HotMoneyAgentParams,
            InstitutionalAgentParams,
            MacroAgentParams,
            QuantAgentParams,
            RetailAgentParams,
            SimulationParams,
        )

        return SimulationParams(
            retail=RetailAgentParams(
                momentum_sensitivity=self.retail_momentum_sensitivity,
                base_order_size=self.retail_base_order_size,
                chase_threshold=self.retail_chase_threshold,
                panic_threshold=-self.retail_chase_threshold,
            ),
            institutional=InstitutionalAgentParams(
                reversion_sensitivity=self.institutional_reversion_sensitivity,
                base_order_size=self.institutional_base_order_size,
                vwap_entry_threshold=self.institutional_vwap_threshold,
            ),
            quant=QuantAgentParams(
                signal_sensitivity=self.quant_signal_sensitivity,
                base_order_size=self.quant_base_order_size,
                rsi_oversold=self.quant_rsi_oversold,
                rsi_overbought=self.quant_rsi_overbought,
                vol_spike_threshold=self.quant_vol_spike_threshold,
            ),
            macro=MacroAgentParams(
                signal_sensitivity=self.macro_signal_sensitivity,
                base_order_size=self.macro_base_order_size,
            ),
            hot_money=HotMoneyAgentParams(
                signal_sensitivity=self.hot_money_signal_sensitivity,
                base_order_size=self.hot_money_base_order_size,
                momentum_threshold=self.hot_money_momentum_threshold,
            ),
            impact_lambda=self.impact_lambda,
            lookback=self.lookback,
        )


@dataclass
class StateConfig:
    method: str = "rules"
    n_states: int = 5
    residual_z_threshold: float = 2.0
    cluster_map_method: str = "forward_return"
    forward_horizon_days: int = 1
    state_confirm_days: int = 2
    # 不对称确认：进防守快、出防守慢、进进攻快（巩固防守 + 增加进攻）
    confirm_asymmetric: bool = True
    confirm_enter_defensive_days: int = 1
    confirm_exit_defensive_days: int = 2
    confirm_enter_offensive_days: int = 1
    walk_forward_cluster_map: bool = True

    def confirm_policy(self):
        from market_platform.agents.分析.推断.状态 import StateConfirmPolicy

        if not self.confirm_asymmetric:
            return None
        return StateConfirmPolicy(
            default_days=int(self.state_confirm_days),
            enter_defensive_days=int(self.confirm_enter_defensive_days),
            exit_defensive_days=int(self.confirm_exit_defensive_days),
            enter_offensive_days=int(self.confirm_enter_offensive_days),
        )


@dataclass
class LLMConfig:
    provider: str = "rules"
    model: str = "qwen2.5:7b"
    base_url: str = "http://127.0.0.1:11434"


@dataclass
class BacktestConfig:
    initial_capital: float = 1_000_000.0
    commission: float = 0.0003
    stamp_tax: float = 0.0005
    slippage: float = 0.0005
    limit_pct: float = 0.098
    apply_limit: bool = True
    apply_stamp_tax: bool = True
    apply_slippage: bool = True
    optimize_step: float = 0.1
    drawdown_penalty: float = 0.5
    optimize_objective: str = "trend"
    min_position_floor: float = 0.4
    mid_position_floor: float = 0.6
    strong_position_floor: float = 0.9
    walk_forward_ratio: float | None = 0.7


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
    drawdown_recover_threshold: float = 0.06


@dataclass
class BatchSymbolConfig:
    symbol: str
    market: str
    sector: str = ""
    name: str = ""


@dataclass
class BatchConfig:
    symbols: list[BatchSymbolConfig] = field(default_factory=list)


@dataclass
class PaperTradingConfig:
    enabled: bool = True
    top_n: int = 20
    min_suggested_position: float = 0.25
    min_recommend_score: float = 0.0
    exclude_states: list[str] = field(default_factory=lambda: ["恐慌"])
    apply_risk_constraints: bool = True
    respect_limit_up_down: bool = True
    notify_after_cycle: bool = True
    initial_capital: float = 1_000_000.0
    rebalance_buffer: int = 5


@dataclass
class AppConfig:
    data: DataConfig = field(default_factory=DataConfig)
    nmf: NMFConfig = field(default_factory=NMFConfig)
    features: FeatureConfig = field(default_factory=FeatureConfig)
    simulation: SimulationConfig = field(default_factory=SimulationConfig)
    state: StateConfig = field(default_factory=StateConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    backtest: BacktestConfig = field(default_factory=BacktestConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    batch: BatchConfig = field(default_factory=BatchConfig)
    notify: NotifyConfig = field(default_factory=NotifyConfig)
    output: OutputConfig = field(default_factory=OutputConfig)
    paper_trading: PaperTradingConfig = field(default_factory=PaperTradingConfig)

    @classmethod
    def from_yaml(cls, path: Path) -> AppConfig:
        with path.open(encoding="utf-8") as f:
            raw: dict[str, Any] = yaml.safe_load(f) or {}

        data_raw = raw.get("data", {})
        batch_raw = raw.get("batch", {})
        symbols_raw = batch_raw.get("symbols", [])
        batch_symbols = [
            BatchSymbolConfig(
                symbol=str(s["symbol"]),
                market=str(s.get("market", "sz")),
                sector=str(s.get("sector", "")),
                name=str(s.get("name", "")),
            )
            for s in symbols_raw
            if isinstance(s, dict) and "symbol" in s
        ]

        notify_raw = raw.get("notify", {}) or {}
        email_raw = notify_raw.get("email", {}) or {}
        paper_raw = raw.get("paper_trading", {}) or {}
        from market_platform.agents.核心.路径.项目 import resolve_data_root, resolve_output_root

        return cls(
            data=DataConfig(
                root=resolve_data_root(data_raw.get("root", "data")),
                symbol=str(data_raw.get("symbol", "000001")),
                market=str(data_raw.get("market", "sz")),
                start_date=str(data_raw.get("start_date", "20240101")),
                end_date=data_raw.get("end_date"),
                fetch_sleep_seconds=float(data_raw.get("fetch_sleep_seconds", 1.0)),
                fallback_daily_proxy=bool(data_raw.get("fallback_daily_proxy", True)),
                daily_sources=build_source_chain(
                    data_raw,
                    key="daily_sources",
                    primary_key="daily_source_primary",
                    fallback_key="daily_source_fallback",
                    default_primary="eastmoney",
                    default_fallback=["tencent", "sina"],
                    legacy_default=["eastmoney", "tencent", "sina"],
                ),
                minute_sources=build_source_chain(
                    data_raw,
                    key="minute_sources",
                    primary_key="minute_source_primary",
                    fallback_key="minute_source_fallback",
                    default_primary="baostock",
                    default_fallback=["sina", "eastmoney", "daily_proxy"],
                    legacy_default=["baostock", "sina", "eastmoney", "daily_proxy"],
                ),
                fund_flow_sources=build_source_chain(
                    data_raw,
                    key="fund_flow_sources",
                    primary_key="fund_flow_source_primary",
                    fallback_key="fund_flow_source_fallback",
                    default_primary="eastmoney_direct",
                    default_fallback=["sina", "eastmoney", "volume_proxy"],
                    legacy_default=["eastmoney_direct", "sina", "eastmoney", "volume_proxy"],
                ),
                use_rolling_window=bool(data_raw.get("use_rolling_window", True)),
                rolling_window_days=int(data_raw.get("rolling_window_days", 504)),
                group_by_sector=bool(data_raw.get("group_by_sector", True)),
                group_by_l2=bool(data_raw.get("group_by_l2", True)),
                name_as_folder=bool(data_raw.get("name_as_folder", True)),
            ),
            nmf=NMFConfig(**raw.get("nmf", {})),
            features=FeatureConfig(minute=raw.get("features", {}).get("minute", {})),
            simulation=SimulationConfig(**raw.get("simulation", {})),
            state=StateConfig(**raw.get("state", {})),
            llm=LLMConfig(**raw.get("llm", {})),
            backtest=BacktestConfig(**raw.get("backtest", {})),
            risk=RiskConfig(**raw.get("risk", {})),
            batch=BatchConfig(symbols=batch_symbols),
            notify=NotifyConfig(
                enabled=bool(notify_raw.get("enabled", False)),
                webhook_url=str(notify_raw.get("webhook_url", "")),
                msgtype=str(notify_raw.get("msgtype", "text")),
                email=EmailNotifyConfig(
                    enabled=bool(email_raw.get("enabled", False)),
                    smtp_host=str(email_raw.get("smtp_host", "")),
                    smtp_port=int(email_raw.get("smtp_port", 587)),
                    username=str(email_raw.get("username", "")),
                    password=str(email_raw.get("password", "")),
                    from_addr=str(email_raw.get("from_addr", "")),
                    to_addrs=[str(x) for x in email_raw.get("to_addrs", [])],
                    use_tls=bool(email_raw.get("use_tls", True)),
                ),
            ),
            output=OutputConfig(
                root=resolve_output_root(raw.get("output", {}).get("root", "outputs")),
                group_by_sector=bool(raw.get("output", {}).get("group_by_sector", False)),
                group_by_l2=bool(raw.get("output", {}).get("group_by_l2", True)),
                name_as_folder=bool(raw.get("output", {}).get("name_as_folder", True)),
            ),
            paper_trading=PaperTradingConfig(
                enabled=bool(paper_raw.get("enabled", True)),
                top_n=int(paper_raw.get("top_n", 20)),
                min_suggested_position=float(paper_raw.get("min_suggested_position", 0.25)),
                min_recommend_score=float(paper_raw.get("min_recommend_score", 0.0)),
                exclude_states=[str(x) for x in paper_raw.get("exclude_states", ["恐慌"])],
                apply_risk_constraints=bool(paper_raw.get("apply_risk_constraints", True)),
                respect_limit_up_down=bool(paper_raw.get("respect_limit_up_down", True)),
                notify_after_cycle=bool(paper_raw.get("notify_after_cycle", True)),
                initial_capital=float(paper_raw.get("initial_capital", 1_000_000.0)),
                rebalance_buffer=int(paper_raw.get("rebalance_buffer", 5)),
            ),
        )

    def resolved_end_date(self) -> str:
        return self.data.resolved_end_date()

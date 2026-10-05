"""
回测引擎 — 滚动窗口回测 + 仓位持有（贴近实盘）
"""

import numpy as np
import pandas as pd
from loguru import logger
from typing import Dict, List, Optional, Callable, Tuple
from dataclasses import dataclass, field

from market_platform.predictor.配置 import get_config
from market_platform.predictor.分析.推断.校准器 import ConfidenceReturnMapper, ConfidenceIsotonicCalibrator
from market_platform.predictor.分析.回测.策略配置 import resolve_strategy_for_provider


@dataclass
class BacktestResult:
    """回测结果"""
    total_trades: int = 0
    win_trades: int = 0
    loss_trades: int = 0
    win_rate: float = 0.0
    total_return: float = 0.0
    annualized_return: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown: float = 0.0
    benchmark_total_return: float = 0.0
    benchmark_annualized_return: float = 0.0
    benchmark_max_drawdown: float = 0.0
    benchmark_start_date: Optional[pd.Timestamp] = None
    benchmark_end_date: Optional[pd.Timestamp] = None
    excess_return: float = 0.0
    accuracy: float = 0.0
    precision: float = 0.0
    recall: float = 0.0
    f1_score: float = 0.0
    periods_in_market: float = 0.0
    llm_call_count: int = 0
    confusion_matrix: np.ndarray = field(default_factory=lambda: np.zeros((3, 3)))
    daily_signals: pd.DataFrame = field(default_factory=pd.DataFrame)
    # 闲置资金相关
    avg_position: float = 0.0
    cash_mgmt_enabled: bool = False
    cash_return: Optional[float] = None
    strategy_only_return: Optional[float] = None


class BacktestEngine:
    """
    滚动窗口回测引擎（仓位制）

    - 每个 bar 收盘产生信号，下一 bar 按新仓位持有
    - 周频：每周最多调仓 1 次，持有至下周
    - 手续费仅在仓位变化时扣除（按换手比例）
  """

    def __init__(self, config_override: Optional[dict] = None):
        cfg = get_config()["backtest"]
        provider = None
        if config_override:
            provider = config_override.get("provider")
        if config_override and isinstance(config_override.get("strategy"), dict):
            strategy = config_override["strategy"]
        else:
            strategy = resolve_strategy_for_provider(provider)

        self.provider = provider
        self.train_ratio = cfg.get("train_ratio", 0.7)
        self.test_start_date = cfg.get("test_start_date")
        self.min_train_bars = int(cfg.get("min_train_bars", 0))
        # 命名修正(评审 R15):该值**不是调仓频率**,而是"重训/测试块长度"
        # (每 N 根 bar 重训一次模型并预测整块)。真实调仓节奏由 weekly_rebalance_only /
        # rebalance_on_week_end / interval_days 决定。旧键 rebalance_freq 继续兼容。
        self.retrain_block = int(
            cfg.get("retrain_block", cfg.get("rebalance_freq", 20))
        )
        self.rebalance_freq = self.retrain_block  # 兼容旧引用
        self.commission = cfg.get("commission", 0.0003)
        self.slippage = cfg.get("slippage", 0.0005)
        self.confidence_threshold = strategy.get("confidence_threshold", 0.6)
        self.only_trade_high_conf = strategy.get("only_trade_high_conf", True)
        self.max_position = strategy.get("max_position", 1.0)
        self.allow_short = strategy.get("allow_short", False)
        self.flat_on_low_conf = strategy.get("flat_on_low_conf", True)
        self.min_hold_periods = strategy.get("min_hold_periods", 0)
        self.cooldown_periods = strategy.get("cooldown_periods", 0)
        self.stop_loss = strategy.get("stop_loss", None)
        # 评审 R13:止损依据。False(默认)=用入场以来已实现回撤(因果);
        # True=旧行为,用未来一期的 label_ret(前视,仅用于复现旧结果)
        self.止损用未来标签 = bool(strategy.get("stop_loss_uses_future_label", False))
        if self.止损用未来标签:
            logger.warning(
                "⚠️ stop_loss_uses_future_label=true:止损将使用未来一期收益(前视),"
                "仅用于复现旧结果"
            )

        # 连续仓位 + 部分调仓
        self.position_mode = strategy.get("position_mode", "scaled")
        self.confidence_scaling = strategy.get("confidence_scaling", True)
        self.neutral_cap = strategy.get("neutral_cap", 0.25)
        self.bear_cap = strategy.get("bear_cap", 0.0)
        self.edge_threshold = strategy.get("edge_threshold", 0.0)
        self.use_edge_percentile = strategy.get("use_edge_percentile", False)
        self.edge_lookback = strategy.get("edge_lookback", 20)
        self.edge_lookback_min = strategy.get("edge_lookback_min", 8)
        self.max_target_position = strategy.get("max_target_position", 1.0)
        self.min_conf_scale = strategy.get("min_conf_scale", 0.0)

        # 底仓 + 研判叠加模式
        self.base_position = strategy.get("base_position", 0.0)
        self.neutral_overlay_cap = strategy.get("neutral_overlay_cap", 0.4)
        self.neutral_overlay_scale = strategy.get("neutral_overlay_scale", 0.35)
        self.overlay_prob_weight = strategy.get("overlay_prob_weight", 0.50)
        self.overlay_edge_weight = strategy.get("overlay_edge_weight", 0.30)
        self.overlay_conf_weight = strategy.get("overlay_conf_weight", 0.20)
        self.overlay_conviction_boost = strategy.get("overlay_conviction_boost", 0.35)

        # 趋势自适应 + 判跌减仓
        self.trend_filter = strategy.get("trend_filter", False)
        self.trend_sma_col = strategy.get("trend_sma_col", "sma_60")
        self.trend_neutral_band = float(strategy.get("trend_neutral_band", 0.03))
        self.bull_regime_base = float(strategy.get("bull_regime_base", self.base_position))
        self.bull_strong_base = float(strategy.get("bull_strong_base", self.bull_regime_base))
        self.bull_strong_band = float(strategy.get("bull_strong_band", 0.06))
        self.bull_strong_sma_col = str(strategy.get("bull_strong_sma_col", "sma_20"))
        self.bear_regime_base = float(strategy.get("bear_regime_base", self.base_position))
        self.bear_regime_cap = float(strategy.get("bear_regime_cap", strategy.get("bear_cap", 0.20)))
        self.bear_derisk = strategy.get("bear_derisk", False)
        self.bear_derisk_affects_base = strategy.get("bear_derisk_affects_base", True)
        self.structural_release_only = strategy.get("structural_release_only", False)
        self.bear_derisk_min_conf = strategy.get("bear_derisk_min_conf", 0.62)
        self.bear_floor = strategy.get("bear_floor", 0.0)
        self.high_conviction_threshold = strategy.get("high_conviction_threshold", 0.65)
        self.high_conviction_boost = strategy.get("high_conviction_boost", 0.35)
        self.min_overlay_confidence = strategy.get("min_overlay_confidence", 0.0)

        # 阶梯建仓 / 条件释放（避免仓位来回跳动）
        self.position_cycle = strategy.get("position_cycle", False)
        self.build_step = strategy.get("build_step", 0.10)
        self.residual_build = strategy.get("residual_build", False)
        self.residual_build_ratio = float(strategy.get("residual_build_ratio", 0.30))
        self.release_step = strategy.get("release_step", 0.05)
        self.release_hysteresis = strategy.get("release_hysteresis", 0.10)
        self.position_tiers = strategy.get("position_tiers", [0.0, 0.25, 0.45, 0.65, 0.85, 1.0])
        self.bull_catch_up_gap = float(strategy.get("bull_catch_up_gap", 0.15))
        self.bull_catch_up_step = float(strategy.get("bull_catch_up_step", 0.20))
        self.euphoria_rsi = strategy.get("euphoria_rsi", 72.0)
        self.euphoria_momentum_5d = strategy.get("euphoria_momentum_5d", 0.022)
        self.risk_volatility_20d = strategy.get("risk_volatility_20d", 0.020)

        self.vol_target_enabled = strategy.get("vol_target_enabled", False)
        self.vol_target_annual = strategy.get("vol_target_annual", 0.15)
        self.vol_target_annual_bull = float(
            strategy.get("vol_target_annual_bull", self.vol_target_annual)
        )
        self.vol_feature_col = strategy.get("vol_feature_col", "volatility_20d")
        self.vol_cap_min = strategy.get("vol_cap_min", 0.10)
        self.vol_cap_max = strategy.get("vol_cap_max", 1.0)

        self.dual_freq_gate = strategy.get("dual_freq_gate", False)
        self.dual_freq_week_bear_scale = strategy.get("dual_freq_week_bear_scale", 0.85)
        self.dual_freq_daily_bull_week_bear_scale = float(
            strategy.get("dual_freq_daily_bull_week_bear_scale", 0.70)
        )

        # 多 LLM 分歧降仓：已试 A/B，伤收益 → 默认关
        dg = strategy.get("disagreement_gate", {}) or {}
        self.disagreement_gate_enabled = bool(dg.get("enabled", False))
        self.disagree_min_agree = float(dg.get("min_agree", 0.50))
        self.disagree_full_agree = float(dg.get("full_agree", 0.75))
        self.disagree_min_scale = float(dg.get("min_scale", 0.35))
        self._agreement_by_date: Dict[pd.Timestamp, float] = {}

        # 损益非对称 Selective size：仅高一致∩高置信允许 overlay；否则拉向底仓
        ss = strategy.get("selective_size", {}) or {}
        self.selective_size_enabled = bool(ss.get("enabled", False))
        self.selective_agree_min = float(ss.get("agree_min", 0.75))
        self.selective_conf_min = float(ss.get("conf_min", 0.62))
        self.selective_weak_scale = float(ss.get("weak_scale", 0.40))

        # 连错反做（消融）：高置信连续 miss_days 日后反转当日方向用于仓位
        inv = strategy.get("invert_after_misses", {}) or {}
        self.invert_after_misses_enabled = bool(inv.get("enabled", False))
        self.invert_miss_days = int(inv.get("miss_days", 2))
        self.invert_conf_min = float(inv.get("conf_min", 0.62))
        self._recent_pred_hits: List[Tuple[int, bool, float]] = []  # pred, hit, conf

        need_agree = self.disagreement_gate_enabled or self.selective_size_enabled
        if need_agree:
            try:
                from market_platform.predictor.分析.推断.集合元模型 import build_agreement_index

                self._agreement_by_date = build_agreement_index()
                logger.info(
                    "  一致度索引: days={} (disagreement={} selective_size={})",
                    len(self._agreement_by_date),
                    self.disagreement_gate_enabled,
                    self.selective_size_enabled,
                )
            except Exception as e:
                logger.warning("一致度索引初始化失败: {}", e)
                self._agreement_by_date = {}
                if self.disagreement_gate_enabled:
                    self.disagreement_gate_enabled = False
                if self.selective_size_enabled:
                    self.selective_size_enabled = False
        if self.disagreement_gate_enabled:
            logger.info(
                "  分歧降仓门控: min_agree={} full_agree={} min_scale={}",
                self.disagree_min_agree,
                self.disagree_full_agree,
                self.disagree_min_scale,
            )
        if self.selective_size_enabled:
            logger.info(
                "  Selective size: agree>={} conf>={} weak_scale={}",
                self.selective_agree_min,
                self.selective_conf_min,
                self.selective_weak_scale,
            )
        if self.invert_after_misses_enabled:
            logger.info(
                "  连错反做消融: miss_days={} conf_min={}",
                self.invert_miss_days,
                self.invert_conf_min,
            )

        # 分区准确率缩放叠加仓（B 实验）
        part_cfg = strategy.get("partition_overlay_scale", {}) or {}
        self.partition_overlay_enabled = bool(part_cfg.get("enabled", False))
        self.partition_scale_add_only = bool(part_cfg.get("add_only", True))
        self.partition_low_acc = float(part_cfg.get("low_accuracy", 0.45))
        self.partition_high_acc = float(part_cfg.get("high_accuracy", 0.55))
        self.partition_low_scale = float(part_cfg.get("low_scale", 0.50))
        self.partition_high_scale = float(part_cfg.get("high_scale", 1.15))
        self.partition_provider = provider
        # 评审 R13:命中率来源。causal=只用当根之前已结算结果(默认);global=旧行为(有前视)
        self.partition_overlay_source = str(part_cfg.get("source", "causal")).strip().lower()
        self._partition_hit_map: Dict[str, float] = {}
        # 评审 R10/R11:失败丢弃与口径降级的显式记账(供指标与报告披露)
        self.训练失败块数: int = 0
        self.预测失败bar数: int = 0
        self.丢弃bar数: int = 0
        self.统一夏普降级原因: str | None = None
        # 评审 R13:分桶命中率改为**因果账本**(只用当根之前已结算的预测结果)
        self._分桶命中: Dict[str, int] = {}
        self._分桶总数: Dict[str, int] = {}
        self.因果分桶最小样本: int = int(part_cfg.get("min_n", cfg.get("partition_overlay_min_n", 20)))

        ret_cal_cfg = strategy.get("return_calibration", {})
        self.return_mapper = ConfidenceReturnMapper(ret_cal_cfg)
        self.return_calibration_enabled = self.return_mapper.enabled
        self.confidence_isotonic = ConfidenceIsotonicCalibrator(
            strategy.get("confidence_isotonic", {})
        )

        wr = strategy.get("weekly_rebalance", {})
        self.weekly_rebalance_only = wr.get("enabled", False)
        self.rebalance_on_week_end = bool(wr.get("on_week_end", False))
        self.rebalance_interval_days = int(wr.get("interval_days", 5))
        self.weekly_full_move = wr.get("full_move_on_rebalance", True)
        self.gradual_add_multiplier = float(wr.get("max_add_multiplier", 2.0))
        self.gradual_reduce_scale = float(wr.get("max_reduce_scale", 0.5))
        wd = strategy.get("weekly_decision", {})
        if isinstance(wd, dict):
            self.weekly_decision = bool(wd.get("enabled", False))
        else:
            self.weekly_decision = False
        if self.weekly_decision:
            # 硬切：决策日=week_end，调仓跟决策日
            self.rebalance_on_week_end = True
            self.weekly_rebalance_only = True
            self.dual_freq_gate = False

        sr = strategy.get("systemic_risk_exit", {})
        self.systemic_risk_enabled = sr.get("enabled", False)
        self.sys_daily_crash = float(sr.get("daily_crash", 0.028))
        self.sys_rolling_5d_loss = float(sr.get("rolling_5d_loss", 0.065))
        self.sys_vol_threshold = float(sr.get("vol_20d_threshold", 0.022))
        self.sys_below_sma_pct = float(sr.get("below_sma_pct", 0.05))
        self.sys_high_conf_bear = sr.get("high_conf_bear_exit", True)
        self.sys_exit_scale = float(sr.get("exit_scale", 0.5))

        tp = strategy.get("take_profit", {})
        self.take_profit_enabled = tp.get("enabled", False)
        self.tp_gain_from_entry = float(tp.get("gain_from_entry", 0.12))
        self.tp_trim_scale = float(tp.get("trim_scale", 0.5))
        self.tp_gain_tier_2 = float(tp.get("gain_tier_2", 0.0))
        self.tp_trim_scale_2 = float(tp.get("trim_scale_2", 0.55))
        self.tp_euphoria_take = tp.get("euphoria_take", False)
        self.tp_euphoria_trim_scale = float(tp.get("euphoria_trim_scale", 0.45))
        self.tp_immediate = tp.get("immediate", True)
        self.tp_trailing_peak_dd = float(tp.get("trailing_peak_dd", 0.10))
        self.tp_trailing_trim_scale = float(tp.get("trailing_trim_scale", 0.5))

        self.emergency_reduce_scale = float(strategy.get("emergency_reduce_scale", 0.5))
        self.emergency_cooldown_days = int(strategy.get("emergency_reduce_cooldown_days", 5))

        # 部分调仓: 每周仅向目标仓位移动一定比例
        self.partial_rebalance = strategy.get("partial_rebalance", True)
        self.rebalance_step = strategy.get("rebalance_step", 0.35)
        self.trade_epsilon = strategy.get("trade_epsilon", 0.02)

        data_cfg = get_config().get("data", {})
        bar_freq = data_cfg.get("bar_freq", "daily")
        self.periods_per_year = 52 if bar_freq == "weekly" else 252
        self.bar_unit = "周" if bar_freq == "weekly" else "日"

        if config_override:
            if config_override.get("test_start_date") is not None:
                self.test_start_date = config_override["test_start_date"]
                logger.info(f"  回测起点覆盖: test_start_date={self.test_start_date}")
        if provider:
            logger.info(
                f"  渠道策略覆盖: provider={provider}, "
                f"min_hold={self.min_hold_periods}, interval={self.rebalance_interval_days}, "
                f"rebalance_step={self.rebalance_step}, min_overlay_conf={self.min_overlay_confidence}"
            )
    def _parse_proba(self, proba: np.ndarray) -> Tuple[float, float, float]:
        """解析三分类概率，顺序: 跌(-1), 平(0), 涨(+1)"""
        proba = np.asarray(proba, dtype=float)
        if proba.shape[0] != 3:
            raise ValueError(f"期望 3 类概率，收到 {proba.shape}")
        return float(proba[0]), float(proba[1]), float(proba[2])

    def _resolve_initial_train_end(self, df: pd.DataFrame, total_len: int) -> int:
        """确定首次进入样本外评估的训练截止下标（不含）"""
        min_end = max(self.min_train_bars, 1)

        if self.test_start_date:
            target = pd.Timestamp(self.test_start_date)
            dates = pd.to_datetime(df["date"])
            hits = df.index[dates >= target]
            if len(hits) == 0:
                logger.warning(
                    f"⚠️ test_start_date={self.test_start_date} 超出数据范围，"
                    f"回退 train_ratio={self.train_ratio}"
                )
                end_idx = int(total_len * self.train_ratio)
            else:
                end_idx = int(hits[0])

        else:
            end_idx = int(total_len * self.train_ratio)

        end_idx = int(np.clip(end_idx, min_end, total_len - 1))
        if end_idx >= total_len - 1:
            end_idx = max(min_end, total_len // 2)
            logger.warning(f"⚠️ 训练窗口过长，回测起点调整为 index={end_idx}")

        logger.info(
            f"  回测起始: {pd.Timestamp(df.iloc[end_idx]['date']).date()} "
            f"（样本外 {total_len - end_idx} {self.bar_unit}）"
        )
        return end_idx

    def _confidence_scale(self, max_conf: float) -> float:
        if not self.confidence_scaling:
            return 1.0
        scale = (max_conf - self.confidence_threshold) / (
            1.0 - self.confidence_threshold + 1e-10
        )
        scale = float(np.clip(scale, 0.0, 1.0))
        if self.min_conf_scale > 0:
            scale = self.min_conf_scale + (1.0 - self.min_conf_scale) * scale
        return scale

    def _directional_confidence(
        self, pred: int, p_down: float, p_flat: float, p_up: float,
    ) -> float:
        if pred == 1:
            return p_up
        if pred == -1:
            return p_down
        return p_flat

    def _prob_excess(self, prob: float, uniform: float = 1.0 / 3.0) -> float:
        """类别概率相对均匀分布的超额部分，归一化到 [0,1]"""
        return float(np.clip((prob - uniform) / (1.0 - uniform + 1e-10), 0.0, 1.0))

    def _trend_regime(self, row: Optional[pd.Series]) -> str:
        """相对 SMA 的趋势状态：bull / bear / neutral。"""
        if not self.trend_filter or row is None:
            return "neutral"
        close = row.get("close")
        sma = row.get(self.trend_sma_col)
        if not (pd.notna(close) and pd.notna(sma) and float(sma) > 0):
            return "neutral"
        dev = float(close) / float(sma) - 1.0
        band = max(0.0, float(self.trend_neutral_band))
        if band > 0:
            if dev >= band:
                return "bull"
            if dev <= -band:
                return "bear"
            return "neutral"
        return "bull" if float(close) >= float(sma) else "bear"

    def _is_strong_bull(self, row: Optional[pd.Series]) -> bool:
        """强牛：偏离均线较深，或短均线在长均线之上且价格站上短均线。"""
        if row is None or self._trend_regime(row) != "bull":
            return False
        close = row.get("close")
        sma_long = row.get(self.trend_sma_col)
        if not (pd.notna(close) and pd.notna(sma_long) and float(sma_long) > 0):
            return False
        dev = float(close) / float(sma_long) - 1.0
        if dev >= float(self.bull_strong_band):
            return True
        sma_short = row.get(self.bull_strong_sma_col)
        if pd.notna(sma_short) and float(sma_short) > 0:
            if float(close) >= float(sma_short) and float(sma_short) >= float(sma_long):
                mom = row.get("momentum_20d")
                if pd.isna(mom) or float(mom) >= 0:
                    return True
        return False

    def _resolve_base_position(
        self, pred: int, max_conf: float = 0.0, row: Optional[pd.Series] = None,
    ) -> float:
        """动态底仓：弱牛/强牛抬高、熊压低、均线附近用中性底仓。"""
        if (
            self.bear_derisk_affects_base
            and pred == -1
            and self.bear_derisk
            and not self.allow_short
            and max_conf >= self.bear_derisk_min_conf
        ):
            return float(self.bear_regime_base)

        base = float(self.base_position)
        regime = self._trend_regime(row)
        if regime == "bull":
            if self._is_strong_bull(row):
                return float(self.bull_strong_base)
            return float(self.bull_regime_base)
        if regime == "bear":
            return float(self.bear_regime_base)
        return base

    def _resolve_target_position(
        self, pred: int, proba: np.ndarray, edge_history: Optional[list] = None,
        row: Optional[pd.Series] = None,
    ) -> Optional[float]:
        """根据预测和置信度确定目标仓位（0 ~ max_position）"""
        p_down, p_flat, p_up = self._parse_proba(proba)
        max_conf = float(np.max(proba))

        if self.only_trade_high_conf and max_conf < self.confidence_threshold:
            if self.position_mode == "core_overlay":
                return self._resolve_base_position(pred, max_conf=max_conf, row=row)
            return 0.0 if self.flat_on_low_conf else None

        if self.position_mode == "binary":
            return self._binary_target(pred)
        if self.position_mode == "core_overlay":
            return self._core_overlay_target(
                pred, p_down, p_flat, p_up, max_conf, edge_history=edge_history, row=row,
            )

        return self._scaled_target(
            pred, p_down, p_flat, p_up, max_conf, edge_history=edge_history,
        )

    def _edge_strength(self, edge: float, edge_history: Optional[list]) -> float:
        """信号强度: 绝对 edge 或滚动分位数（应对模型长期偏多）"""
        if self.use_edge_percentile and edge_history and len(edge_history) >= self.edge_lookback_min:
            window = edge_history[-self.edge_lookback:]
            rank = sum(1 for e in window if e <= edge) / len(window)
            # 将分位数映射到 [0,1]，threshold 以下视为无信号
            if rank < self.edge_threshold:
                return 0.0
            return float((rank - self.edge_threshold) / (1.0 - self.edge_threshold + 1e-10))
        return max(0.0, edge)

    def _binary_target(self, pred: int) -> float:
        if self.allow_short:
            if pred == 0:
                return 0.0
            return float(pred) * self.max_position
        if pred == 1:
            return self.max_position
        return 0.0

    def _directional_strength(
        self, pred: int, p_down: float, p_flat: float, p_up: float, edge_strength: float,
    ) -> float:
        """方向感知强度：判涨/跌用类别概率，判平允许轻仓"""
        uniform = 1.0 / 3.0
        if pred == 1:
            return max(edge_strength, p_up - uniform)
        if pred == -1:
            return max(edge_strength, p_down - uniform) if self.allow_short else 0.0
        # 判平：edge 常为 0，用「非平」概率给轻仓空间
        return max(edge_strength, (1.0 - p_flat) * 0.4)

    def _overlay_strength(
        self,
        pred: int,
        p_down: float,
        p_flat: float,
        p_up: float,
        max_conf: float,
        edge_history: Optional[list] = None,
    ) -> float:
        """研判叠加强度 0~1：看涨概率 × 边际 × 置信度，看好则多加仓"""
        edge = p_up - p_down
        edge_s = self._edge_strength(edge, edge_history)
        dir_conf = self._directional_confidence(pred, p_down, p_flat, p_up)
        if self.return_calibration_enabled and pred == 1:
            conf_s = self.return_mapper.overlay_scale(pred, dir_conf)
        else:
            conf_s = self._confidence_scale(max_conf)

        if pred == 1:
            if max_conf < self.min_overlay_confidence:
                return 0.0
            p_excess = self._prob_excess(p_up)
            linear = (
                self.overlay_prob_weight * p_excess
                + self.overlay_edge_weight * edge_s
                + self.overlay_conf_weight * conf_s
            )
            # 概率与置信同向偏高时加码叠加
            conviction = p_excess * conf_s
            strength = linear * (1.0 + self.overlay_conviction_boost * conviction)
            if max_conf >= self.high_conviction_threshold and p_up >= 0.55:
                strength *= 1.0 + self.high_conviction_boost
            strength *= self._partition_overlay_multiplier(max_conf)
            return float(np.clip(strength, 0.0, 1.0))

        if pred == 0:
            # 判平：仅在低平信下小幅叠加
            non_flat = self._prob_excess(1.0 - p_flat, uniform=0.0)
            small = non_flat * conf_s * self.neutral_overlay_scale
            return float(np.clip(small, 0.0, self.neutral_overlay_cap))

        return 0.0

    def _记入分桶结果(self, max_conf: float, hit: bool) -> None:
        """把已结算的预测结果记入分桶账本(因果:只影响之后的 bar)。"""
        if not self.partition_overlay_enabled:
            return
        try:
            from market_platform.predictor.分析.指标.准确率分区 import _conf_bucket

            bucket = str(_conf_bucket(float(max_conf)))
        except Exception:  # noqa: BLE001
            return
        self._分桶总数[bucket] = self._分桶总数.get(bucket, 0) + 1
        if hit:
            self._分桶命中[bucket] = self._分桶命中.get(bucket, 0) + 1

    def _因果分桶准确率(self, max_conf: float) -> float | None:
        """该置信度桶**历史已结算**的命中率;样本不足返回 None。"""
        try:
            from market_platform.predictor.分析.指标.准确率分区 import _conf_bucket

            bucket = str(_conf_bucket(float(max_conf)))
        except Exception:  # noqa: BLE001
            return None
        total = self._分桶总数.get(bucket, 0)
        if total < self.因果分桶最小样本:
            return None
        return self._分桶命中.get(bucket, 0) / total

    def _ensure_partition_hit_map(self) -> None:
        if self._partition_hit_map or not self.partition_overlay_enabled:
            return
        try:
            from market_platform.predictor.分析.指标.准确率分区 import (
                compute_accuracy_partitions,
            )
            payload = compute_accuracy_partitions()
            dim = next(
                (d for d in (payload.get("dimensions") or []) if d.get("key") == "conf_bucket"),
                None,
            )
            if not dim:
                return
            provider = self.partition_provider or "deepseek"
            for row in dim.get("rows") or []:
                if row.get("provider") != provider:
                    continue
                self._partition_hit_map[str(row.get("slice"))] = float(row.get("accuracy") or 0.0)
            if self._partition_hit_map:
                logger.info(
                    "分区叠加缩放已加载: provider={} buckets={}",
                    provider, len(self._partition_hit_map),
                )
        except Exception as exc:
            logger.warning("分区叠加缩放加载失败: {}", exc)

    def _partition_overlay_multiplier(self, max_conf: float) -> float:
        """按该渠道置信度桶命中率缩放加仓强度(B 实验)。

        前视修正(评审 R13):原实现用 `compute_accuracy_partitions()` 的**全历史**命中率,
        等于用未来才知道的准确率去调过去的仓位。现默认走**因果账本**(只用当根之前
        已结算的预测);`partition_overlay_source: global` 保留旧行为但会大声告警。
        """
        if not self.partition_overlay_enabled:
            return 1.0
        if self.partition_overlay_source == "global":
            logger.warning(
                "⚠️ partition_overlay_source=global 会使用全历史命中率(含未来信息),"
                "仅用于复现旧结果;因果模式请设为 causal"
            )
            self._ensure_partition_hit_map()
            if not self._partition_hit_map:
                return 1.0
            try:
                from market_platform.predictor.分析.指标.准确率分区 import _conf_bucket
                bucket = _conf_bucket(float(max_conf))
            except Exception:
                return 1.0
            acc = self._partition_hit_map.get(bucket)
        else:
            acc = self._因果分桶准确率(max_conf)
        if acc is None:
            return 1.0
        if acc < self.partition_low_acc:
            return self.partition_low_scale
        if acc > self.partition_high_acc:
            return self.partition_high_scale
        return 1.0

    def _core_overlay_target(
        self,
        pred: int,
        p_down: float,
        p_flat: float,
        p_up: float,
        max_conf: float,
        edge_history: Optional[list] = None,
        row: Optional[pd.Series] = None,
    ) -> float:
        """底仓 + 研判叠加：趋势自适应底仓，判跌减仓，高确信加码"""
        base = self._resolve_base_position(pred, max_conf=max_conf, row=row)
        regime = self._trend_regime(row)
        cap = min(self.max_target_position, self.max_position)
        # 牛市：上限可到满仓；熊市：顶仓封顶
        if regime == "bull":
            cap = self.max_position
        elif regime == "bear":
            cap = min(cap, float(self.bear_regime_cap))
        overlay_room = max(0.0, cap - base)
        overlay = self._overlay_strength(
            pred, p_down, p_flat, p_up, max_conf, edge_history=edge_history,
        ) * overlay_room
        target = base + overlay

        floor = base
        if self.bear_derisk_affects_base and (
            pred == -1 and self.bear_derisk and max_conf >= self.bear_derisk_min_conf
        ):
            floor = self.bear_regime_base
        elif (
            self.structural_release_only
            and row is not None
            and self._has_structural_bear(row, pred)
        ):
            floor = self.bear_regime_base
        return float(np.clip(target, floor, cap if regime == "bear" else self.max_position))

    def _is_euphoria(self, row: pd.Series) -> bool:
        """市场过热：价投在此兑现叠加、保留底仓"""
        rsi = row.get("rsi_14")
        if pd.notna(rsi) and float(rsi) >= self.euphoria_rsi:
            return True
        mom5 = row.get("momentum_5d")
        if pd.notna(mom5) and float(mom5) >= self.euphoria_momentum_5d:
            return True
        return False

    def _has_structural_bear(self, row: pd.Series, pred: int, week_pred: Optional[int] = None) -> bool:
        """结构性熊市：趋势破位 / 波动飙升 / 周频同向看跌"""
        if pred != -1:
            return False
        vol = row.get("volatility_20d")
        if pd.notna(vol) and float(vol) >= self.risk_volatility_20d:
            return True
        if self.trend_filter:
            close, sma = row.get("close"), row.get(self.trend_sma_col)
            if (
                pd.notna(close) and pd.notna(sma) and float(sma) > 0
                and float(close) < float(sma) * 0.97
            ):
                return True
        if week_pred is not None and int(week_pred) == -1:
            return True
        return False

    def _trim_overlay(self, position: float, target: float, anchor: float, scale: float) -> float:
        """紧急减仓：仅削减锚定底仓以上的叠加部分（单次，不连乘整仓）"""
        if position <= anchor + self.trade_epsilon:
            return float(np.clip(max(target, anchor), 0.0, self.max_position))
        trimmed = anchor + (position - anchor) * scale
        return float(np.clip(min(target, trimmed) if target > 0 else trimmed, 0.0, self.max_position))

    def _in_bear_derisk(
        self,
        pred: int,
        max_conf: float,
        row: Optional[pd.Series] = None,
        week_pred: Optional[int] = None,
    ) -> bool:
        if not self.bear_derisk or self.allow_short:
            return False
        if pred != -1 or max_conf < self.bear_derisk_min_conf:
            return False
        if self.bear_derisk_affects_base and not self.structural_release_only:
            return True
        if self.structural_release_only and row is not None:
            return self._has_structural_bear(row, pred, week_pred)
        return False

    def _lift_gated_target(
        self,
        target: float,
        regime_base: float,
        pred: int,
        max_conf: float,
        row: Optional[pd.Series] = None,
        week_pred: Optional[int] = None,
    ) -> float:
        """门控后若目标被压穿动态底仓，回升至底仓（结构性看跌除外）"""
        if self._in_bear_derisk(pred, max_conf, row=row, week_pred=week_pred):
            return float(np.clip(target, 0.0, self.max_position))
        if target > 0 and target < regime_base - 1e-6:
            return float(np.clip(regime_base, 0.0, self.max_position))
        return float(np.clip(target, 0.0, self.max_position))

    def _scale_overlay_at_base(self, target: float, anchor: float, scale: float) -> float:
        if target <= anchor + self.trade_epsilon:
            return max(target, anchor)
        return anchor + (target - anchor) * scale

    def _scaled_target(
        self,
        pred: int,
        p_down: float,
        p_flat: float,
        p_up: float,
        max_conf: float,
        edge_history: Optional[list] = None,
    ) -> float:
        """连续仓位: 方向概率 × 边际强度 × 置信度缩放，平/跌信号封顶"""
        edge = p_up - p_down
        edge_strength = self._edge_strength(edge, edge_history)

        if not self.use_edge_percentile and edge_strength < self.edge_threshold:
            return 0.0

        strength = self._directional_strength(pred, p_down, p_flat, p_up, edge_strength)

        if self.confidence_scaling:
            strength *= self._confidence_scale(max_conf)

        target = strength * self.max_position
        cap = self.max_target_position * self.max_position
        target = min(target, cap)

        if pred == 0:
            target = min(target, self.neutral_cap * self.max_position)
        elif pred == -1:
            target = min(target, self.bear_cap * self.max_position)

        return float(np.clip(target, 0.0, self.max_position))

    def _quantize_target(self, target: float) -> float:
        tiers = sorted(float(t) for t in self.position_tiers)
        return float(min(tiers, key=lambda t: abs(t - target)))

    def _should_release_position(
        self,
        row: pd.Series,
        pred: int,
        max_conf: float,
        forced_exit: bool,
    ) -> bool:
        """仅在市场狂热或结构性风险时允许释放叠加（价投不因单日信号减仓）"""
        if forced_exit:
            return True
        if self._is_euphoria(row):
            return True
        if pred == -1 and max_conf >= self.bear_derisk_min_conf:
            if self.structural_release_only:
                return self._has_structural_bear(row, pred)
            return True

        vol = row.get("volatility_20d")
        if pd.notna(vol) and float(vol) >= self.risk_volatility_20d:
            return True

        if self.trend_filter:
            close, sma = row.get("close"), row.get(self.trend_sma_col)
            if (
                pd.notna(close) and pd.notna(sma) and float(sma) > 0
                and float(close) < float(sma) * 0.97
                and pred == -1
            ):
                return True
        return False

    def _apply_position_cycle(
        self,
        current: float,
        target: float,
        row: pd.Series,
        pred: int,
        max_conf: float,
        forced_exit: bool,
        weeks_held: int,
    ) -> float:
        """阶梯建仓、条件释放：减仓需狂热/风险触发；牛市落后目标时加速追赶。"""
        target = self._quantize_target(target)

        if target > current + self.trade_epsilon:
            tiers = sorted(float(t) for t in self.position_tiers)
            tiers_up = [t for t in tiers if t > current + self.trade_epsilon]
            if not tiers_up:
                return current

            gap_to_target = target - current
            catch_up = (
                self._trend_regime(row) == "bull"
                and gap_to_target >= float(self.bull_catch_up_gap)
            )
            step = float(self.bull_catch_up_step if catch_up else self.build_step)
            ceiling = min(current + step, target)

            if catch_up:
                reachable = [t for t in tiers_up if t <= ceiling + self.trade_epsilon]
                if reachable:
                    return float(np.clip(reachable[-1], 0.0, self.max_position))
                return float(np.clip(ceiling, 0.0, self.max_position))

            next_tier = tiers_up[0]
            gap = next_tier - current
            if self.residual_build:
                move = max(self.build_step, gap * self.residual_build_ratio)
                move = min(move, gap)
            else:
                move = min(gap, self.build_step)
            return float(np.clip(current + move, 0.0, self.max_position))

        if target < current - self.release_hysteresis:
            if not self._should_release_position(row, pred, max_conf, forced_exit):
                return current
            if weeks_held < self.min_hold_periods and not forced_exit:
                return current
            delta = target - current
            return float(np.clip(current + self.release_step * delta, 0.0, self.max_position))

        return current

    def _apply_vol_target_cap(
        self,
        row: pd.Series,
        target: float,
        regime_base: float = 0.0,
        pred: int = 0,
        max_conf: float = 0.0,
    ) -> float:
        """波动率目标化：高波动期压低上限，但不压穿动态底仓；牛市目标波动略放宽。"""
        if not self.vol_target_enabled or target <= 0:
            return target
        vol = row.get(self.vol_feature_col)
        if not pd.notna(vol) or float(vol) <= 0:
            return target
        vol_annual = float(vol) * np.sqrt(self.periods_per_year)
        vol_target = float(self.vol_target_annual)
        if self._trend_regime(row) == "bull":
            vol_target = float(self.vol_target_annual_bull)
        cap = vol_target / vol_annual
        cap = float(np.clip(cap, self.vol_cap_min, self.vol_cap_max))
        capped = min(target, cap)
        if regime_base > 0 and not self._in_bear_derisk(pred, max_conf, row=row):
            if target >= regime_base - self.trade_epsilon:
                capped = max(capped, regime_base)
        return capped

    def _apply_dual_freq_gate(
        self,
        target: float,
        daily_pred: int,
        week_pred: Optional[int],
        row: pd.Series,
        max_conf: float,
    ) -> float:
        """日频 + 周频双通道确认：共振加仓，背离仅保留底仓或防御"""
        if not self.dual_freq_gate or week_pred is None:
            return target
        d, w = int(daily_pred), int(week_pred)
        base = self._resolve_base_position(d, max_conf=max_conf, row=row)

        if d == 1 and w == 1:
            return target
        if d == 1 and w == -1:
            floor = base * self.dual_freq_daily_bull_week_bear_scale
            return min(target, max(floor, self.bear_regime_base * 0.5))
        if d == 1 and w != 1:
            return min(target, base)
        if d == -1 and w == -1:
            floor = max(self.bear_regime_base, base * 0.5)
            return min(target, max(floor, self.bear_regime_base))
        if d == -1 and w != -1:
            return min(target, base)
        if d == 0 and w == -1:
            return min(target, base * self.dual_freq_week_bear_scale)
        if d == 0 and w == 1:
            return target
        return target

    def _disagreement_scale(self, date) -> Optional[float]:
        """一致比例 → [min_scale, 1]；一致时不放大（上限 1）。"""
        if not self.disagreement_gate_enabled or not self._agreement_by_date:
            return None
        d = pd.Timestamp(date).normalize()
        agree = self._agreement_by_date.get(d)
        if agree is None:
            # 找不晚于当日的最近面板日
            earlier = [k for k in self._agreement_by_date if k <= d]
            if not earlier:
                return None
            agree = self._agreement_by_date[max(earlier)]
        lo = self.disagree_min_agree
        hi = max(self.disagree_full_agree, lo + 1e-6)
        t = float(np.clip((float(agree) - lo) / (hi - lo), 0.0, 1.0))
        return float(self.disagree_min_scale + t * (1.0 - self.disagree_min_scale))

    def _apply_disagreement_gate(
        self,
        target: float,
        regime_base: float,
        row: pd.Series,
    ) -> float:
        """分歧时把目标仓位拉向底仓；一致时 scale=1，不加信任加成。"""
        scale = self._disagreement_scale(row.get("date"))
        if scale is None or abs(scale - 1.0) < 1e-9:
            return target
        base = float(regime_base)
        return float(base + (float(target) - base) * scale)

    def _lookup_agreement(self, date) -> Optional[float]:
        if not self._agreement_by_date:
            return None
        d = pd.Timestamp(date).normalize()
        agree = self._agreement_by_date.get(d)
        if agree is not None:
            return float(agree)
        earlier = [k for k in self._agreement_by_date if k <= d]
        if not earlier:
            return None
        return float(self._agreement_by_date[max(earlier)])

    def _apply_selective_size(
        self,
        target: float,
        regime_base: float,
        row: pd.Series,
        max_conf: float,
    ) -> float:
        """
        仅当 agreement≥τ 且 conf≥c 时保留 overlay；否则仓位拉向底仓。
        比「分裂完全空仓」更温和。
        """
        if not self.selective_size_enabled:
            return target
        agree = self._lookup_agreement(row.get("date"))
        if agree is None:
            return target
        strong = (
            float(agree) >= self.selective_agree_min
            and float(max_conf) >= self.selective_conf_min
        )
        if strong:
            return target
        base = float(regime_base)
        scale = float(np.clip(self.selective_weak_scale, 0.0, 1.0))
        return float(base + (float(target) - base) * scale)

    def _maybe_invert_prediction(self, pred: int, max_conf: float) -> int:
        """连错反做消融：过去 miss_days 个高置信日全错 → 反转当日非平仓方向。"""
        if not self.invert_after_misses_enabled:
            return pred
        need = max(1, self.invert_miss_days)
        if len(self._recent_pred_hits) < need:
            return pred
        window = self._recent_pred_hits[-need:]
        if not all(
            (not hit) and conf >= self.invert_conf_min
            for _, hit, conf in window
        ):
            return pred
        if int(pred) == 0:
            return pred
        return -int(pred)

    def _record_pred_outcome(self, pred: int, actual: int, max_conf: float) -> None:
        if not self.invert_after_misses_enabled:
            return
        hit = int(pred) == int(actual)
        self._recent_pred_hits.append((int(pred), bool(hit), float(max_conf)))
        # 只留最近若干日
        keep = max(10, self.invert_miss_days + 5)
        if len(self._recent_pred_hits) > keep:
            self._recent_pred_hits = self._recent_pred_hits[-keep:]

    def _is_weekly_rebalance_day(
        self,
        global_idx: int,
        first_test_idx: int,
        *,
        bar_date=None,
        trading_dates=None,
    ) -> bool:
        """是否调仓日：周频硬切用自然周最后交易日；否则按 interval_days。"""
        if not self.weekly_rebalance_only:
            return True
        if self.rebalance_on_week_end and bar_date is not None and trading_dates is not None:
            try:
                from market_platform.predictor.分析.特征.日信号复合 import is_week_end

                return bool(is_week_end(bar_date, trading_dates))
            except Exception:
                pass
        days = global_idx - first_test_idx
        return days >= 0 and days % self.rebalance_interval_days == 0

    def _detect_systemic_risk(
        self,
        row: pd.Series,
        lookback_df: pd.DataFrame,
        pred: int,
        max_conf: float,
        week_pred: Optional[int],
    ) -> bool:
        """系统性风险：触发即时减半减仓（可突破周频约束）"""
        if not self.systemic_risk_enabled:
            return False

        data = lookback_df.sort_values("date").reset_index(drop=True)
        if len(data) >= 2 and "close" in data.columns:
            ret1 = float(data.iloc[-1]["close"]) / float(data.iloc[-2]["close"]) - 1
            if ret1 <= -self.sys_daily_crash:
                return True

        if len(data) >= 6 and "close" in data.columns:
            ret5 = float(data.iloc[-1]["close"]) / float(data.iloc[-6]["close"]) - 1
            if ret5 <= -self.sys_rolling_5d_loss:
                return True

        vol = row.get("volatility_20d")
        if pd.notna(vol) and float(vol) >= self.sys_vol_threshold:
            return True

        if self.trend_filter:
            close, sma = row.get("close"), row.get(self.trend_sma_col)
            if (
                pd.notna(close) and pd.notna(sma) and float(sma) > 0
                and float(close) < float(sma) * (1.0 - self.sys_below_sma_pct)
            ):
                return True

        if (
            self.sys_high_conf_bear
            and pred == -1
            and max_conf >= self.bear_derisk_min_conf
            and week_pred == -1
        ):
            return True

        return False

    def _apply_take_profit(
        self,
        target: float,
        position: float,
        equity: float,
        peak_equity: float,
        entry_equity: Optional[float],
        regime_base: float = 0.0,
        row: Optional[pd.Series] = None,
    ) -> Tuple[float, bool]:
        """止盈 / 净值保护：仅削减底仓以上叠加，保留底仓以待补仓"""
        if not self.take_profit_enabled or position <= self.trade_epsilon:
            return target, False

        triggered = False
        new_target = target
        anchor = regime_base if regime_base > 0 else 0.0

        if entry_equity and entry_equity > 0:
            gain = equity / entry_equity - 1.0
            if gain >= self.tp_gain_from_entry:
                trim_scale = self.tp_trim_scale
                if self.tp_gain_tier_2 > 0 and gain >= self.tp_gain_tier_2:
                    trim_scale = self.tp_trim_scale_2
                trimmed = self._trim_overlay(position, target, anchor, trim_scale)
                new_target = min(target, trimmed)
                triggered = True

        if row is not None and self.tp_euphoria_take and self._is_euphoria(row):
            trimmed = self._trim_overlay(position, new_target, anchor, self.tp_euphoria_trim_scale)
            new_target = min(new_target, trimmed)
            triggered = True

        if peak_equity > 0:
            dd = equity / peak_equity - 1.0
            if dd <= -self.tp_trailing_peak_dd:
                trimmed = self._trim_overlay(position, new_target, anchor, self.tp_trailing_trim_scale)
                new_target = min(new_target, trimmed)
                triggered = True

        return float(np.clip(new_target, 0.0, self.max_position)), triggered

    def _apply_rebalance_cadence(
        self,
        current: float,
        target: float,
        is_rebalance_day: bool,
        allow_immediate: bool,
        row: pd.Series,
        pred: int,
        max_conf: float,
        forced_exit: bool,
        weeks_held: int,
        resonance_add: bool = False,
    ) -> float:
        """周频调仓：非调仓日持仓不变；调仓日阶梯对齐；紧急减仓即时到位"""
        if allow_immediate:
            return float(np.clip(target, 0.0, self.max_position))

        if self.weekly_rebalance_only and not is_rebalance_day:
            return current

        if self.position_cycle:
            quant_target = self._quantize_target(target)
            if self.weekly_rebalance_only and is_rebalance_day:
                if self.weekly_full_move:
                    return quant_target
                if resonance_add and target > current + self.trade_epsilon:
                    return float(np.clip(
                        min(target, current * self.gradual_add_multiplier),
                        0.0, self.max_position,
                    ))
            return self._apply_position_cycle(
                current, target, row, pred, max_conf, forced_exit, weeks_held,
            )

        if self.weekly_rebalance_only and is_rebalance_day:
            if resonance_add and target > current + self.trade_epsilon:
                return float(np.clip(
                    min(target, current * self.gradual_add_multiplier),
                    0.0, self.max_position,
                ))
            return float(np.clip(target, 0.0, self.max_position))

        return self._apply_partial_rebalance(current, target)

    def _apply_partial_rebalance(self, current: float, target: float) -> float:
        """部分调仓: 每周仅向目标移动 rebalance_step 比例"""
        if not self.partial_rebalance:
            return target
        delta = target - current
        if abs(delta) <= self.trade_epsilon:
            return current
        new_pos = current + self.rebalance_step * delta
        return float(np.clip(new_pos, 0.0, self.max_position))

    def _period_return(self, position: float, label_return: float) -> float:
        """单期收益 = 持仓 × 市场涨跌"""
        return position * label_return

    def _transaction_cost(self, old_pos: float, new_pos: float) -> float:
        """换手成本 = |Δ仓位| × (佣金 + 滑点)"""
        turnover = abs(new_pos - old_pos)
        if turnover == 0:
            return 0.0
        return turnover * (self.commission + self.slippage)

    def _apply_execution_rules(
        self,
        position: float,
        target: float,
        weeks_held: int,
        weeks_since_exit: int,
        force_reduce: bool = False,
    ) -> float:
        """实盘执行约束：最短持仓、冷却期（紧急减仓可绕过）"""
        if position == 0 and target > 0:
            # weeks_since_exit==0 表示从未持仓，不应触发清仓冷却
            if weeks_since_exit > 0 and weeks_since_exit < self.cooldown_periods and not force_reduce:
                return 0.0

        if position > 0 and target < position:
            if weeks_held < self.min_hold_periods and not force_reduce:
                return position

        return target

    def run(
        self,
        df: pd.DataFrame,
        train_fn: Callable,
        predict_fn: Callable,
        model_name: str = "model",
    ) -> BacktestResult:
        logger.info(f"🚀 开始回测: {model_name}（{self.bar_unit}频仓位制）")
        logger.info(
            f"  规则: allow_short={self.allow_short}, "
            f"position_mode={self.position_mode}, "
            f"base_position={self.base_position:.0%}, "
            f"position_cycle={self.position_cycle}, "
            f"partial_rebalance={self.partial_rebalance}, "
            f"rebalance_step={self.rebalance_step}, "
            f"commission={self.commission:.4f}, slippage={self.slippage:.4f}, "
            f"min_hold={self.min_hold_periods}{self.bar_unit}, "
            f"cooldown={self.cooldown_periods}{self.bar_unit}, "
            f"vol_target={self.vol_target_enabled}, "
            f"dual_freq={self.dual_freq_gate}, "
            f"weekly_decision={getattr(self, 'weekly_decision', False)}, "
            f"return_cal={self.return_calibration_enabled}, "
            f"weekly_rebal={self.weekly_rebalance_only}, "
            f"on_week_end={getattr(self, 'rebalance_on_week_end', False)}, "
            f"systemic_exit={self.systemic_risk_enabled}, "
            f"take_profit={self.take_profit_enabled}"
        )

        df = df.sort_values("date").reset_index(drop=True)
        total_len = len(df)
        train_end = self._resolve_initial_train_end(df, total_len)
        first_test_idx = train_end
        trading_dates = pd.to_datetime(df["date"])
        last_pred = 0
        last_proba = np.array([1 / 3, 1 / 3, 1 / 3], dtype=float)
        last_week_pred = None
        llm_call_count = 0
        score_flags: list = []

        # 闲置资金管理：预加载现金资产收益率数据
        cash_returns = None
        cash_mgmt_enabled = get_config().get("cash_management", {}).get("enabled", False)
        three_layer_allocator = None
        unified_mode = False
        unified_allocator = None
        sleeve_returns = None
        try:
            from market_platform.predictor.分析.资金.闲置管理 import is_unified_sharpe_mode

            unified_mode = is_unified_sharpe_mode()
        except Exception as exc:  # noqa: BLE001 — 必须留痕:此前静默回退 legacy(评审 R11)
            logger.error(
                "❌ 统一夏普模式探测失败,本次按 legacy 口径回测: %s: %s",
                type(exc).__name__, exc,
            )
            self.统一夏普降级原因 = f"模式探测失败: {type(exc).__name__}: {exc}"
            unified_mode = False

        if unified_mode:
            try:
                from market_platform.predictor.分析.资金.闲置管理 import fetch_all_sleeve_returns
                from market_platform.predictor.分析.资金.统一夏普配置 import UnifiedSharpeAllocator

                cash_start = pd.Timestamp(df.iloc[0]["date"])
                cash_end = pd.Timestamp(df.iloc[-1]["date"])
                sleeve_returns = fetch_all_sleeve_returns(cash_start, cash_end, index_df=df)
                cash_returns = sleeve_returns  # 兼容 active_start / 资金图
                unified_allocator = UnifiedSharpeAllocator()
                cash_mgmt_enabled = True
                three_layer_allocator = None
                logger.info(
                    f"📐 统一夏普六袖已启用，预加载 {len(sleeve_returns)} 条六袖收益数据"
                )
            except Exception as e:
                logger.error(
                    "❌ 统一夏普六袖初始化失败,本次回测按 legacy 口径执行(结果口径与"
                    "配置声明不一致,请勿与 unified 结果混比): %s", e
                )
                self.统一夏普降级原因 = f"初始化失败: {type(e).__name__}: {e}"
                unified_mode = False
                unified_allocator = None
                sleeve_returns = None

        if cash_mgmt_enabled and not unified_mode:
            try:
                from market_platform.predictor.分析.资金.闲置管理 import fetch_cash_assets
                from market_platform.predictor.分析.资金.配置引擎 import ThreeLayerAllocator, is_three_layer_allocation

                cash_start = pd.Timestamp(df.iloc[0]["date"])
                cash_end = pd.Timestamp(df.iloc[-1]["date"])
                cash_returns = fetch_cash_assets(cash_start, cash_end)
                if is_three_layer_allocation():
                    three_layer_allocator = ThreeLayerAllocator()
                    logger.info("📐 三层资产配置已启用（风险预算 → 状态调整 → LLM 覆盖）")
                logger.info(
                    f"💰 闲置资金管理已启用，预加载 {len(cash_returns)} 条现金资产数据"
                )
            except Exception as e:
                logger.warning(f"⚠️ 闲置资金数据预加载失败: {e}")
                cash_mgmt_enabled = False

        result = BacktestResult()
        predictions: list = []
        actuals: list = []
        timestamps: list = []
        signals: list = []
        confidences: list = []
        pred_week_returns: list = []
        target_positions: list = []
        positions: list = []
        holding_positions: list = []
        period_returns: list = []
        offense_returns: list = []
        defensive_returns: list = []
        allocation_regimes: list = []
        allocation_regime_labels: list = []
        cash_weight_csi300: list = []
        cash_weight_gold: list = []
        cash_weight_bond: list = []
        cash_weight_nasdaq: list = []
        cash_weight_repo: list = []
        cash_weight_mm: list = []
        llm_override_flags: list = []
        group_w_offense: list = []
        group_w_defense: list = []
        group_w_cashflow: list = []

        position = 0.0
        prev_sleeve_weights: Dict[str, float] = {}
        weeks_held = 0
        weeks_since_exit = self.cooldown_periods
        equity = 1.0
        peak_equity = 1.0
        entry_equity: Optional[float] = None
        emergency_cooldown_left = 0
        edge_history: list = []
        current_train_end = train_end
        self.return_mapper.reset()
        self._recent_pred_hits = []

        # 闲置资金累计（unified: offense / defense+cashflow）
        cum_strategy_ret = 1.0  # legacy 复利路径仍保留
        cum_cash_ret = 1.0
        strat_contrib = 0.0  # 统一账本：期初净值加权贡献（可加总）
        cash_contrib = 0.0
        equity_ratio_u = 1.0
        static_4020 = 1.0  # 40/40/20 静态基准（仅 unified 记）
        equal_six = 1.0

        while current_train_end < total_len:
            test_start = current_train_end
            test_end = min(current_train_end + self.retrain_block, total_len)
            train_df = df.iloc[:current_train_end]
            test_df = df.iloc[test_start:test_end]

            try:
                model = train_fn(train_df)
            except Exception as e:
                # 评审 R10:失败即丢样本会**静默**缩短样本外期数,直接扭曲年化/Sharpe/回撤。
                # 这里显式记账(块数 + 根数),并进入指标供报告披露。
                logger.error(f"❌ 训练失败(该块 {test_end - test_start} 根 bar 不计入收益序列): {e}")
                self.训练失败块数 += 1
                self.丢弃bar数 += max(0, test_end - current_train_end)
                current_train_end = test_end
                continue

            for idx in range(len(test_df)):
                row = test_df.iloc[idx]
                global_idx = current_train_end + idx
                is_rebalance_day = self._is_weekly_rebalance_day(
                    global_idx,
                    first_test_idx,
                    bar_date=row["date"],
                    trading_dates=trading_dates,
                )
                # 必须包含当前 bar，否则会用前一日特征预测当日标签
                lookback_df = df.iloc[: current_train_end + idx + 1]

                if position > self.trade_epsilon and entry_equity is None:
                    entry_equity = equity
                if position <= self.trade_epsilon:
                    entry_equity = None
                peak_equity = max(peak_equity, equity)

                # 周频硬切：仅 week_end 调 LLM；其余日复用上周信号
                need_predict = is_rebalance_day or not self.weekly_decision
                try:
                    if need_predict:
                        out = predict_fn(model, lookback_df)
                        week_pred = None
                        if isinstance(out, (tuple, list)):
                            pred, proba = out[0], out[1]
                            if len(out) > 2:
                                week_pred = out[2]
                        else:
                            pred, proba = out
                        last_pred, last_proba, last_week_pred = pred, proba, week_pred
                        llm_call_count += 1
                    else:
                        pred, proba, week_pred = last_pred, last_proba, last_week_pred
                except Exception as e:
                    # 评审 R10:单 bar 预测失败同样要记账(此前静默丢样本)
                    logger.warning(f"⚠️ 预测失败(该 bar 不计入序列): {e}")
                    self.预测失败bar数 += 1
                    self.丢弃bar数 += 1
                    continue

                p_down, p_flat, p_up = self._parse_proba(proba)
                edge_history.append(p_up - p_down)
                raw_conf = float(np.max(proba))
                max_conf = self.confidence_isotonic.calibrate(raw_conf)
                proba = proba.copy()
                pred_idx = int(np.argmax(proba))
                proba[pred_idx] = max_conf
                rest = max(0.0, 1.0 - max_conf) / 2.0
                for i in range(3):
                    if i != pred_idx:
                        proba[i] = rest
                p_down, p_flat, p_up = self._parse_proba(proba)

                raw_pred = int(pred)
                sizing_pred = self._maybe_invert_prediction(raw_pred, max_conf)

                predictions.append(sizing_pred)
                if self.weekly_decision and "label_week_direction" in row.index:
                    actual_lab = row.get("label_week_direction")
                    if pd.isna(actual_lab):
                        actual_lab = row["label_direction"]
                    try:
                        actuals.append(int(actual_lab) if pd.notna(actual_lab) else 0)
                    except (TypeError, ValueError):
                        actuals.append(0)
                    score_flags.append(bool(is_rebalance_day and need_predict))
                else:
                    ad = row["label_direction"]
                    actuals.append(int(ad) if pd.notna(ad) else 0)
                    score_flags.append(True)
                timestamps.append(row["date"])
                signals.append(sizing_pred)
                confidences.append(max_conf)
                # 幅度：仅 week_end 有新预测；其余日沿用上周
                mag = None
                if need_predict and hasattr(model, "_last_result"):
                    mag = (getattr(model, "_last_result", {}) or {}).get("week_return_pct")
                if mag is None and pred_week_returns:
                    mag = pred_week_returns[-1]
                pred_week_returns.append(float(mag) if mag is not None else float("nan"))

                label_ret = row["label_return"]
                period_pnl = 0.0

                if unified_mode and unified_allocator is not None and sleeve_returns is not None:
                    from market_platform.predictor.分析.资金.统一夏普配置 import compute_unified_period_pnl

                    asof = pd.Timestamp(row["date"]).normalize()
                    pw = unified_allocator.resolve(
                        sleeve_returns,
                        asof,
                        row=row,
                        pred=int(sizing_pred),
                        confidence=float(max_conf),
                        trading_dates=trading_dates,
                    )
                    target = float(pw.csi300)
                    new_position = target
                    sleeve_l1 = sum(
                        abs(float(pw.weights.get(k, 0.0)) - float(prev_sleeve_weights.get(k, 0.0)))
                        for k in ("csi300", "nasdaq", "gold", "bond", "mm", "repo")
                    )
                    if sleeve_l1 > self.trade_epsilon:
                        result.total_trades += 1

                    sleeve_hit = sleeve_returns[sleeve_returns["date"] == asof]
                    # 禁止回退前一日；CSI 用矩阵同日收益，勿覆盖为未来 label_return
                    if sleeve_hit.empty:
                        day_row = pd.Series({"csi300_ret": 0.0})
                    else:
                        day_row = sleeve_hit.iloc[0].copy()

                    holding_weights = {
                        k: float(prev_sleeve_weights.get(k, 0.0) or 0.0)
                        for k in ("csi300", "nasdaq", "gold", "bond", "mm", "repo")
                    }
                    total_pnl, offense_pnl, defensive_pnl, _cost = compute_unified_period_pnl(
                        pw.weights,
                        day_row,
                        prev_weights=holding_weights,
                        earn_weights=holding_weights,
                        commission=self.commission,
                        slippage=self.slippage,
                    )
                    period_returns.append(total_pnl)
                    offense_returns.append(offense_pnl)
                    defensive_returns.append(defensive_pnl)
                    target_positions.append(target)
                    positions.append(position)
                    holding_positions.append(new_position)

                    if new_position != 0:
                        if total_pnl > 0:
                            result.win_trades += 1
                        else:
                            result.loss_trades += 1
                    if new_position > 0:
                        weeks_held = weeks_held + 1 if position > 0 else 1
                        weeks_since_exit = 0
                    else:
                        weeks_held = 0
                        weeks_since_exit = 1 if position > 0 else weeks_since_exit + 1

                    position = new_position
                    prev_sleeve_weights = {k: float(pw.weights.get(k, 0.0)) for k in (
                        "csi300", "nasdaq", "gold", "bond", "mm", "repo"
                    )}
                    strat_contrib += float(offense_pnl) * equity_ratio_u
                    cash_contrib += float(defensive_pnl) * equity_ratio_u
                    equity_ratio_u *= 1.0 + float(total_pnl)
                    equity = equity * (1.0 + total_pnl)
                    cum_strategy_ret = 1.0 + strat_contrib
                    cum_cash_ret = 1.0 + cash_contrib

                    # 静态基准：组内等权 × 40/40/20；等权六袖
                    def _safe(v):
                        return float(v) if pd.notna(v) else 0.0

                    r_csi = _safe(day_row.get("csi300_ret"))
                    r_nq = _safe(day_row.get("nasdaq_ret"))
                    r_g = _safe(day_row.get("gold_ret"))
                    r_b = _safe(day_row.get("bond_ret"))
                    r_m = _safe(day_row.get("mm_ret"))
                    r_r = _safe(day_row.get("repo_daily_ret"))
                    static_4020 *= 1.0 + (
                        0.40 * 0.5 * (r_csi + r_nq)
                        + 0.40 * 0.5 * (r_g + r_b)
                        + 0.20 * 0.5 * (r_m + r_r)
                    )
                    equal_six *= 1.0 + (r_csi + r_nq + r_g + r_b + r_m + r_r) / 6.0

                    allocation_regimes.append(pw.regime)
                    allocation_regime_labels.append(pw.regime_label)
                    cash_weight_csi300.append(pw.weights.get("csi300"))
                    cash_weight_gold.append(pw.weights.get("gold"))
                    cash_weight_bond.append(pw.weights.get("bond"))
                    cash_weight_nasdaq.append(pw.weights.get("nasdaq"))
                    cash_weight_repo.append(pw.weights.get("repo"))
                    cash_weight_mm.append(pw.weights.get("mm"))
                    llm_override_flags.append(bool(pw.risk_off or pw.llm_tilt or bool((pw.layers or {}).get('risk_on'))))
                    group_w_offense.append(pw.group_weights.get("offense"))
                    group_w_defense.append(pw.group_weights.get("defense"))
                    group_w_cashflow.append(pw.group_weights.get("cashflow"))

                    if self.return_calibration_enabled and not pd.isna(label_ret):
                        dir_conf = self._directional_confidence(sizing_pred, p_down, p_flat, p_up)
                        self.return_mapper.record(sizing_pred, dir_conf, label_ret)
                    if self.confidence_isotonic.enabled and not pd.isna(label_ret):
                        actual_dir = row.get("label_direction")
                        if pd.notna(actual_dir):
                            hit = int(int(sizing_pred) == int(actual_dir))
                            self.confidence_isotonic.record(max_conf, hit)
                    actual_dir = row.get("label_direction")
                    if pd.notna(actual_dir):
                        self._record_pred_outcome(raw_pred, int(actual_dir), max_conf)
                        self._记入分桶结果(max_conf, int(int(sizing_pred) == int(actual_dir)))
                    continue

                # 周度止损：持仓期单周跌幅超过阈值则强制平仓
                # 前视修正(评审 R13):原实现用 label_ret(t→t+1 的**未来**收益)判止损。
                # 现默认改用"入场以来的**已实现**回撤";旧行为可用
                # backtest.stop_loss_uses_future_label=true 复现(会大声告警)。
                if self.止损用未来标签:
                    forced_exit = (
                        position > self.trade_epsilon
                        and not pd.isna(label_ret)
                        and self.stop_loss is not None
                        and label_ret <= -abs(self.stop_loss)
                    )
                else:
                    _已实现 = (
                        (float(equity) / float(entry_equity) - 1.0)
                        if (entry_equity and float(entry_equity) > 0)
                        else 0.0
                    )
                    forced_exit = (
                        position > self.trade_epsilon
                        and self.stop_loss is not None
                        and _已实现 <= -abs(self.stop_loss)
                    )

                target = self._resolve_target_position(sizing_pred, proba, edge_history, row=row)
                regime_base = self._resolve_base_position(sizing_pred, max_conf=max_conf, row=row)
                target = self._apply_vol_target_cap(
                    row, target, regime_base, sizing_pred, max_conf,
                )
                target = self._apply_dual_freq_gate(
                    target, sizing_pred, week_pred, row, max_conf,
                )
                target = self._apply_disagreement_gate(target, regime_base, row)
                target = self._apply_selective_size(target, regime_base, row, max_conf)
                target = self._lift_gated_target(
                    target, regime_base, sizing_pred, max_conf, row=row, week_pred=week_pred,
                )

                resonance_add = (
                    sizing_pred == 1
                    and week_pred == 1
                    and max_conf >= self.high_conviction_threshold
                )

                systemic_triggered = self._detect_systemic_risk(
                    row, lookback_df, sizing_pred, max_conf, week_pred,
                )
                allow_immediate = forced_exit
                if systemic_triggered and emergency_cooldown_left <= 0:
                    target = self._trim_overlay(
                        position, target, regime_base, self.sys_exit_scale,
                    )
                    allow_immediate = True
                    emergency_cooldown_left = self.emergency_cooldown_days

                target, tp_triggered = self._apply_take_profit(
                    target, position, equity, peak_equity, entry_equity, regime_base, row=row,
                )
                if tp_triggered and self.tp_immediate:
                    allow_immediate = True
                    if emergency_cooldown_left <= 0:
                        emergency_cooldown_left = self.emergency_cooldown_days

                if forced_exit and emergency_cooldown_left <= 0:
                    target = self._trim_overlay(
                        position, target, regime_base, self.emergency_reduce_scale,
                    )
                    allow_immediate = True
                    emergency_cooldown_left = self.emergency_cooldown_days
                elif target is None:
                    target = position

                day_cash_weights = None
                if three_layer_allocator is not None and target is not None:
                    target, alloc_dec = three_layer_allocator.apply_equity_adjustment(
                        float(target), row, int(sizing_pred), max_conf, cash_returns,
                    )
                    day_cash_weights = alloc_dec.cash_weights
                    allocation_regimes.append(alloc_dec.regime)
                    allocation_regime_labels.append(alloc_dec.regime_label)
                    cash_weight_gold.append(alloc_dec.cash_weights.get("gold"))
                    cash_weight_bond.append(alloc_dec.cash_weights.get("bond"))
                    cash_weight_nasdaq.append(alloc_dec.cash_weights.get("nasdaq"))
                    cash_weight_repo.append(alloc_dec.cash_weights.get("repo"))
                    cash_weight_mm.append(alloc_dec.cash_weights.get("mm"))
                    cash_weight_csi300.append(None)
                    llm_override_flags.append(alloc_dec.llm_override)
                else:
                    allocation_regimes.append(None)
                    allocation_regime_labels.append(None)
                    cash_weight_csi300.append(None)
                    cash_weight_gold.append(None)
                    cash_weight_bond.append(None)
                    cash_weight_nasdaq.append(None)
                    cash_weight_repo.append(None)
                    cash_weight_mm.append(None)
                    llm_override_flags.append(False)

                group_w_offense.append(None)
                group_w_defense.append(None)
                group_w_cashflow.append(None)

                if allow_immediate or is_rebalance_day:
                    target = self._apply_execution_rules(
                        position, target, weeks_held, weeks_since_exit,
                        force_reduce=allow_immediate,
                    )
                else:
                    target = position

                target_positions.append(target)

                new_position = self._apply_rebalance_cadence(
                    position, target, is_rebalance_day, allow_immediate,
                    row, sizing_pred, max_conf, forced_exit or allow_immediate, weeks_held,
                    resonance_add,
                )

                if emergency_cooldown_left > 0:
                    emergency_cooldown_left -= 1

                cost = self._transaction_cost(position, new_position)
                if abs(new_position - position) > self.trade_epsilon:
                    result.total_trades += 1

                if not pd.isna(label_ret):
                    period_pnl = self._period_return(new_position, label_ret)
                period_pnl -= cost
                period_returns.append(period_pnl)
                offense_returns.append(period_pnl)
                defensive_returns.append(0.0)
                positions.append(position)
                holding_positions.append(new_position)

                if new_position != 0:
                    if period_pnl > 0:
                        result.win_trades += 1
                    else:
                        result.loss_trades += 1

                if new_position > 0:
                    weeks_held = weeks_held + 1 if position > 0 else 1
                    weeks_since_exit = 0
                else:
                    weeks_held = 0
                    weeks_since_exit = 1 if position > 0 else weeks_since_exit + 1

                position = new_position

                # 闲置资金收益（与策略收益同用调仓后仓位）
                cash_pnl = 0.0
                if cash_mgmt_enabled and cash_returns is not None:
                    from market_platform.predictor.分析.资金.闲置管理 import compute_cash_return

                    cash_pnl = compute_cash_return(
                        new_position, row, cash_returns,
                        date_idx=pd.Timestamp(row["date"]),
                        weights=day_cash_weights,
                    )
                    defensive_returns[-1] = cash_pnl
                    offense_returns[-1] = period_pnl

                total_pnl = period_pnl + cash_pnl
                period_returns[-1] = total_pnl  # 修正 period_returns 记录
                equity = equity * (1.0 + total_pnl)
                cum_strategy_ret *= 1.0 + period_pnl
                cum_cash_ret *= 1.0 + cash_pnl

                if self.return_calibration_enabled and not pd.isna(label_ret):
                    dir_conf = self._directional_confidence(sizing_pred, p_down, p_flat, p_up)
                    self.return_mapper.record(sizing_pred, dir_conf, label_ret)
                if self.confidence_isotonic.enabled and not pd.isna(label_ret):
                    actual_dir = row.get("label_direction")
                    if pd.notna(actual_dir):
                        hit = int(int(sizing_pred) == int(actual_dir))
                        self.confidence_isotonic.record(max_conf, hit)

                # 连错反做：用原始预测 vs 实际更新历史（因果，不含当日反转后结果）
                actual_dir = row.get("label_direction")
                if pd.notna(actual_dir):
                    self._record_pred_outcome(raw_pred, int(actual_dir), max_conf)
                    self._记入分桶结果(max_conf, int(int(sizing_pred) == int(actual_dir)))

            current_train_end = test_end
            logger.info(f"  回测进度: {test_end}/{total_len} ({test_end/total_len*100:.0f}%)")

        if not predictions:
            logger.warning("⚠️ 没有产生任何预测")
            return result

        pred_arr = np.array(predictions)
        actual_arr = np.array(actuals)
        result.accuracy = (pred_arr == actual_arr).mean()
        if self.weekly_decision and score_flags:
            mask = np.asarray(score_flags[: len(pred_arr)], dtype=bool)
            if mask.any():
                result.accuracy = float((pred_arr[mask] == actual_arr[mask]).mean())
        result.llm_call_count = int(llm_call_count)
        logger.info(f"  LLM/预测调用次数: {llm_call_count}")

        result.confusion_matrix = np.zeros((3, 3))
        for p, a in zip(predictions, actuals):
            if pd.isna(a) or pd.isna(p):
                continue
            try:
                ai, pi = int(a), int(p)
            except (TypeError, ValueError):
                continue
            if ai not in (-1, 0, 1) or pi not in (-1, 0, 1):
                continue
            result.confusion_matrix[ai + 1, pi + 1] += 1

        period_returns_arr = np.array(period_returns, dtype=float)
        period_returns_arr = period_returns_arr[np.isfinite(period_returns_arr)]

        invested_periods = sum(1 for p in positions if p > self.trade_epsilon)
        result.periods_in_market = invested_periods / max(len(positions), 1)

        avg_position = float(np.mean(positions)) if positions else 0.0

        active_pnl = [r for r, p in zip(period_returns, positions) if p != 0]
        if active_pnl:
            result.win_rate = sum(1 for r in active_pnl if r > 0) / len(active_pnl)

        if len(period_returns_arr) > 0:
            result.total_return = float(np.prod(1 + period_returns_arr) - 1)
            n_periods = len(period_returns_arr)
            result.annualized_return = (1 + result.total_return) ** (
                self.periods_per_year / n_periods
            ) - 1
            result.sharpe_ratio = float(
                period_returns_arr.mean() / (period_returns_arr.std() + 1e-10)
            ) * np.sqrt(self.periods_per_year)

            cum_ret = np.cumprod(1 + period_returns_arr)
            running_max = np.maximum.accumulate(cum_ret)
            drawdown = (cum_ret - running_max) / running_max
            result.max_drawdown = float(drawdown.min())

        n_sig = len(predictions)
        # 防列长不一致（中途 continue / 幅度列后加）
        def _fit(xs, fill=np.nan):
            xs = list(xs)
            if len(xs) < n_sig:
                xs = xs + [fill] * (n_sig - len(xs))
            return xs[:n_sig]

        result.daily_signals = pd.DataFrame({
            "date": _fit(timestamps, None),
            "actual": _fit(actuals),
            "prediction": predictions,
            "signal": _fit(signals),
            "confidence": _fit(confidences),
            "pred_week_return_pct": _fit(pred_week_returns),
            "target_position": _fit(target_positions),
            "position": _fit(positions),
            "holding_position": _fit(holding_positions),
            "period_return": _fit(period_returns),
            "offense_return": _fit(offense_returns),
            "defensive_return": _fit(defensive_returns),
            "allocation_regime": _fit(allocation_regimes),
            "allocation_regime_label": _fit(allocation_regime_labels, ""),
            "cash_w_csi300": _fit(cash_weight_csi300),
            "cash_w_gold": _fit(cash_weight_gold),
            "cash_w_bond": _fit(cash_weight_bond),
            "cash_w_nasdaq": _fit(cash_weight_nasdaq),
            "cash_w_repo": _fit(cash_weight_repo),
            "cash_w_mm": _fit(cash_weight_mm),
            "llm_alloc_override": _fit(llm_override_flags, False),
            "group_w_offense": _fit(group_w_offense),
            "group_w_defense": _fit(group_w_defense),
            "group_w_cashflow": _fit(group_w_cashflow),
            "allocation_mode": ["unified_sharpe" if unified_mode else "legacy"] * n_sig,
        })


        oos_df = df.iloc[first_test_idx:].copy()
        if len(oos_df) >= 2:
            bar_ret = oos_df["close"].pct_change().dropna()
            start_close = oos_df.iloc[0]["close"]
            end_close = oos_df.iloc[-1]["close"]

            result.benchmark_start_date = oos_df.iloc[0]["date"]
            result.benchmark_end_date = oos_df.iloc[-1]["date"]
            result.benchmark_total_return = end_close / start_close - 1
            n_bars = len(bar_ret)
            result.benchmark_annualized_return = (
                (1 + result.benchmark_total_return) ** (self.periods_per_year / n_bars) - 1
                if n_bars > 0 else 0.0
            )

            cum_bh = (1 + bar_ret).cumprod()
            running_max = cum_bh.cummax()
            result.benchmark_max_drawdown = float(((cum_bh - running_max) / running_max).min())
            result.excess_return = result.total_return - result.benchmark_total_return

        logger.info(f"\n{'='*50}")
        logger.info(f"📊 回测结果汇总: {model_name}")
        logger.info(f"{'='*50}")
        if result.benchmark_start_date is not None:
            logger.info(
                f"  回测区间:        {result.benchmark_start_date.date()} "
                f"→ {result.benchmark_end_date.date()}"
            )
            logger.info(
                f"  大盘买入持有:    {result.benchmark_total_return:.2%} "
                f"(年化 {result.benchmark_annualized_return:.2%})"
            )
            logger.info(f"  大盘最大回撤:    {result.benchmark_max_drawdown:.2%}")
        logger.info(f"  预测准确率:      {result.accuracy:.4f}")
        logger.info(f"  调仓次数:        {result.total_trades}")
        logger.info(f"  持仓占比:        {result.periods_in_market:.1%}")
        logger.info(f"  平均仓位:        {avg_position:.1%}")
        logger.info(f"  持仓期胜率:      {result.win_rate:.4f}")
        logger.info(f"  策略总收益率:    {result.total_return:.4f}")
        logger.info(f"  策略年化收益率:  {result.annualized_return:.2%}")

        # 填充闲置资金信息
        result.avg_position = avg_position
        result.cash_mgmt_enabled = cash_mgmt_enabled
        if cash_mgmt_enabled:
            result.cash_return = cum_cash_ret - 1.0
            result.strategy_only_return = cum_strategy_ret - 1.0
        if unified_mode:
            # 挂到 result 上供存储层透传（动态属性）
            result.allocation_mode = "unified_sharpe"  # type: ignore[attr-defined]
            result.static_4020_return = static_4020 - 1.0  # type: ignore[attr-defined]
            result.equal_six_return = equal_six - 1.0  # type: ignore[attr-defined]
        else:
            result.allocation_mode = "legacy"  # type: ignore[attr-defined]

        # 评审 R10/R11:把"失败丢弃"与"口径降级"显式挂到结果上(供存储层/报告披露,
        # 避免静默缩短样本外期数或口径悄悄变化)
        result.训练失败块数 = self.训练失败块数  # type: ignore[attr-defined]
        result.预测失败bar数 = self.预测失败bar数  # type: ignore[attr-defined]
        result.丢弃bar数 = self.丢弃bar数  # type: ignore[attr-defined]
        result.统一夏普降级原因 = self.统一夏普降级原因  # type: ignore[attr-defined]
        if self.训练失败块数 or self.预测失败bar数:
            logger.warning(
                "⚠️ 本次回测丢弃样本: 训练失败块={}(未计入 {} 根 bar), 预测失败 bar={};"
                "年化/Sharpe/回撤均基于剩余期数计算",
                self.训练失败块数, self.丢弃bar数, self.预测失败bar数,
            )
        if self.统一夏普降级原因:
            logger.error("❌ 本次回测口径已降级: %s", self.统一夏普降级原因)
        logger.info(f"  策略夏普比率:    {result.sharpe_ratio:.4f}")
        logger.info(f"  策略最大回撤:    {result.max_drawdown:.4f}")
        if result.cash_mgmt_enabled and result.cash_return is not None:
            label_off = "进攻组贡献" if unified_mode else "策略净收益"
            label_def = "防守+现金流贡献" if unified_mode else "闲置资金收益"
            logger.info(f"  {label_def}:    {result.cash_return:+.2%}")
            strat_only = result.strategy_only_return or 0.0
            logger.info(f"     {label_off}:    {strat_only:.2%}")
            if unified_mode:
                logger.info(f"  静态40/40/20:    {static_4020 - 1.0:.2%}")
                logger.info(f"  等权六袖:        {equal_six - 1.0:.2%}")

        return result


def summary_report(results: Dict[str, BacktestResult]) -> pd.DataFrame:
    """生成多个模型对比汇总表"""
    rows = []
    for name, r in results.items():
        rows.append({
            "模型": name,
            "准确率": f"{r.accuracy:.2%}",
            "持仓胜率": f"{r.win_rate:.2%}",
            "策略收益": f"{r.total_return:.2%}",
            "大盘收益": f"{r.benchmark_total_return:.2%}",
            "超额收益": f"{r.excess_return:.2%}",
            "年化收益": f"{r.annualized_return:.2%}",
            "夏普比": f"{r.sharpe_ratio:.2f}",
            "最大回撤": f"{r.max_drawdown:.2%}",
            "调仓次数": r.total_trades,
            "持仓占比": f"{r.periods_in_market:.1%}",
        })
    return pd.DataFrame(rows).set_index("模型")

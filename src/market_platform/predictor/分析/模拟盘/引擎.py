"""
模拟实盘引擎 — 按回测策略规则自主调仓，记录交易与净值供参考。

与历史回测共用 BacktestEngine 仓位逻辑；每日收盘后拉数、研判、调仓。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
from loguru import logger

from market_platform.predictor.分析.回测.引擎 import BacktestEngine
from market_platform.predictor.分析.推断.语言模型预测 import LLMApiPredictor
from market_platform.predictor.配置 import get_config, get_bar_meta
from market_platform.predictor.存储.数据访问 import load_paper_trading_state, save_paper_trading_state


@dataclass
class PaperRuntime:
    position: float = 0.0
    weeks_held: int = 0
    weeks_since_exit: int = 0
    equity_ratio: float = 1.0
    peak_equity_ratio: float = 1.0
    entry_equity_ratio: Optional[float] = None
    emergency_cooldown_left: int = 0
    edge_history: List[float] = field(default_factory=list)
    first_bar_idx: int = 0
    sleeve_weights: Dict[str, float] = field(default_factory=dict)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _runtime_from_dict(data: dict) -> PaperRuntime:
    rt = data.get("runtime") or {}
    raw_sw = rt.get("sleeve_weights") or {}
    sleeve_weights = {
        str(k): float(v) for k, v in raw_sw.items()
    } if isinstance(raw_sw, dict) else {}
    return PaperRuntime(
        position=float(rt.get("position", 0.0)),
        weeks_held=int(rt.get("weeks_held", 0)),
        weeks_since_exit=int(rt.get("weeks_since_exit", 0)),
        equity_ratio=float(rt.get("equity_ratio", 1.0)),
        peak_equity_ratio=float(rt.get("peak_equity_ratio", 1.0)),
        entry_equity_ratio=rt.get("entry_equity_ratio"),
        emergency_cooldown_left=int(rt.get("emergency_cooldown_left", 0)),
        edge_history=[float(x) for x in rt.get("edge_history", [])],
        first_bar_idx=int(rt.get("first_bar_idx", 0)),
        sleeve_weights=sleeve_weights,
    )


def _runtime_to_dict(rt: PaperRuntime, *, allocator_state: dict | None = None) -> dict:
    out = {
        "position": rt.position,
        "weeks_held": rt.weeks_held,
        "weeks_since_exit": rt.weeks_since_exit,
        "equity_ratio": rt.equity_ratio,
        "peak_equity_ratio": rt.peak_equity_ratio,
        "entry_equity_ratio": rt.entry_equity_ratio,
        "emergency_cooldown_left": rt.emergency_cooldown_left,
        "edge_history": rt.edge_history[-500:],
        "first_bar_idx": rt.first_bar_idx,
        "sleeve_weights": dict(rt.sleeve_weights or {}),
    }
    if allocator_state is not None:
        out["allocator_state"] = allocator_state
    return out


_MISSING = object()


class PaperTradingEngine:
    """模拟实盘：自主研判 + 策略调仓，状态按 LLM 渠道持久化。"""

    def __init__(self, provider: str | None = None) -> None:
        self.cfg = get_config().get("paper_trading", {})
        llm_cfg = get_config().get("model", {}).get("llm", {})
        self.provider = provider or llm_cfg.get("active_api") or "deepseek"
        self.engine = BacktestEngine(config_override={"provider": self.provider})
        self._predictor = None
        self._cash_returns = None
        self._three_layer = None
        self._cash_mgmt_enabled = False
        self._unified_mode = False
        self._unified_allocator = None
        self._sleeve_returns = None
        self._dual_freq = bool(
            get_config().get("backtest", {}).get("strategy", {}).get("dual_freq_gate", False)
        )
        try:
            from market_platform.predictor.分析.特征.日信号复合 import is_weekly_decision_mode

            self._weekly_decision = is_weekly_decision_mode()
        except Exception:
            self._weekly_decision = bool(
                (get_config().get("backtest", {}).get("strategy", {}).get("weekly_decision") or {}).get(
                    "enabled"
                )
            )
        self._last_pred = 0
        self._last_proba = np.array([1 / 3, 1 / 3, 1 / 3], dtype=float)
        self._last_week_pred = None
        self._init_cash_mgmt()

    def _allocator_payload(self) -> dict | None:
        if self._unified_allocator is None:
            return None
        return self._unified_allocator.export_state()

    def _pack_runtime(
        self,
        rt: PaperRuntime,
        state: dict | None = None,
        *,
        allocator_state=_MISSING,
    ) -> dict:
        """序列化 runtime；默认带上当前 allocator，否则保留 state 里已有载荷。"""
        if allocator_state is _MISSING:
            allocator_state = self._allocator_payload()
            if allocator_state is None and state is not None:
                allocator_state = (state.get("runtime") or {}).get("allocator_state")
        return _runtime_to_dict(rt, allocator_state=allocator_state)

    def _restore_allocator(self, state: dict) -> None:
        if self._unified_allocator is None:
            return
        payload = (state.get("runtime") or {}).get("allocator_state")
        self._unified_allocator.import_state(payload)
        rt = _runtime_from_dict(state)
        self._unified_allocator.seed_from_sleeve_weights(rt.sleeve_weights)

    def _init_cash_mgmt(self) -> None:
        try:
            from market_platform.predictor.分析.资金.闲置管理 import is_unified_sharpe_mode

            self._unified_mode = is_unified_sharpe_mode()
        except Exception:
            self._unified_mode = False

        if self._unified_mode:
            try:
                from market_platform.predictor.分析.资金.闲置管理 import fetch_all_sleeve_returns
                from market_platform.predictor.分析.资金.统一夏普配置 import UnifiedSharpeAllocator

                start = pd.Timestamp(get_config()["data"]["start_date"])
                end = pd.Timestamp.now().normalize()
                self._sleeve_returns = fetch_all_sleeve_returns(start, end)
                self._cash_returns = self._sleeve_returns
                self._unified_allocator = UnifiedSharpeAllocator()
                self._cash_mgmt_enabled = True
                self._three_layer = None
                return
            except Exception as exc:
                logger.warning("模拟实盘统一夏普初始化失败: {}", exc)
                self._unified_mode = False

        cash_cfg = get_config().get("cash_management", {})
        self._cash_mgmt_enabled = bool(cash_cfg.get("enabled", False))
        if not self._cash_mgmt_enabled:
            return
        try:
            from market_platform.predictor.分析.资金.闲置管理 import fetch_cash_assets
            from market_platform.predictor.分析.资金.配置引擎 import ThreeLayerAllocator, is_three_layer_allocation

            start = pd.Timestamp(get_config()["data"]["start_date"])
            end = pd.Timestamp.now().normalize()
            self._cash_returns = fetch_cash_assets(start, end)
            if is_three_layer_allocation():
                self._three_layer = ThreeLayerAllocator()
        except Exception as exc:
            logger.warning("模拟实盘闲置资金模块初始化失败: {}", exc)
            self._cash_mgmt_enabled = False

    def _refresh_cash_returns(self, df: pd.DataFrame) -> None:
        """按最新行情区间刷新闲置资产收益，避免 ETF 缓存滞后。"""
        if self._unified_mode:
            try:
                from market_platform.predictor.分析.资金.闲置管理 import fetch_all_sleeve_returns

                start = pd.Timestamp(df.iloc[0]["date"])
                end = pd.Timestamp(df.iloc[-1]["date"])
                self._sleeve_returns = fetch_all_sleeve_returns(start, end, index_df=df)
                self._cash_returns = self._sleeve_returns
            except Exception as exc:
                logger.warning("刷新六袖收益失败: {}", exc)
            return
        if not self._cash_mgmt_enabled:
            return
        try:
            from market_platform.predictor.分析.资金.闲置管理 import fetch_cash_assets

            start = pd.Timestamp(get_config()["data"]["start_date"])
            end = pd.Timestamp(df["date"].max()).normalize()
            self._cash_returns = fetch_cash_assets(start, end)
        except Exception as exc:
            logger.warning("刷新闲置资金收益失败: {}", exc)

    @staticmethod
    def _bar_realized_return(df: pd.DataFrame, global_idx: int) -> Optional[float]:
        """当日已实现涨跌：close[t] / close[t-1] - 1（供实盘盯市）。"""
        if global_idx <= 0:
            return None
        prev = df.iloc[global_idx - 1].get("close")
        curr = df.iloc[global_idx].get("close")
        if pd.isna(prev) or pd.isna(curr) or float(prev) <= 0:
            return None
        return float(curr) / float(prev) - 1.0

    @property
    def initial_capital(self) -> float:
        return float(self.cfg.get("initial_capital", 1_000_000))

    def default_state(self) -> dict:
        # 与回测一致：冷却计数从 cooldown_periods 起，避免空仓启动时被误判为「刚清仓」
        rt = PaperRuntime(weeks_since_exit=int(self.engine.cooldown_periods))
        return {
            "llm_provider": self.provider,
            "initial_capital": self.initial_capital,
            "equity": self.initial_capital,
            "cash": self.initial_capital,
            "position": 0.0,
            "last_bar_date": None,
            "last_processed_date": None,
            "started_at": _utc_now_iso(),
            "updated_at": _utc_now_iso(),
            "auto_trade_enabled": bool(self.cfg.get("auto_run", True)),
            "total_trades": 0,
            "history": [],
            "runtime": _runtime_to_dict(rt),
        }

    def load_state(self) -> dict:
        state = load_paper_trading_state(self.provider)
        if not state:
            state = self.default_state()
            save_paper_trading_state(state, provider=self.provider)
            return state
        patched = False
        if state.get("llm_provider") != self.provider:
            state["llm_provider"] = self.provider
            patched = True
        # 修复旧版空仓启动时 weeks_since_exit 从 0 递增、长期无法建仓的问题
        if float(state.get("position", 0) or 0) <= self.engine.trade_epsilon:
            if int(state.get("total_trades", 0) or 0) == 0:
                rt = _runtime_from_dict(state)
                cooldown = int(self.engine.cooldown_periods)
                if rt.weeks_since_exit < cooldown:
                    rt.weeks_since_exit = cooldown
                    state["runtime"] = self._pack_runtime(rt, state)
                    patched = True
        history = state.get("history") or []
        deduped = self._dedupe_history(history)
        first_bar = int((_runtime_from_dict(state).first_bar_idx or 0))
        rt = self._runtime_from_history(deduped, first_bar_idx=first_bar)
        last_eq = float(deduped[-1].get("equity_ratio", 1.0)) if deduped else 1.0
        equity_stale = deduped and abs(rt.equity_ratio - last_eq) > 1e-6
        if len(deduped) != len(history) or equity_stale:
            state["history"] = deduped
            self._sync_state_from_history(state, rt)
            patched = True
        if patched:
            save_paper_trading_state(state, provider=self.provider)
        return state

    def reset_state(self) -> dict:
        state = self.default_state()
        save_paper_trading_state(state, provider=self.provider)
        return state

    @staticmethod
    def _dedupe_history(history: List[dict]) -> List[dict]:
        """同一交易日只保留最后一条记录（避免 force 重复执行堆叠）。"""
        by_date: Dict[str, dict] = {}
        for h in history:
            d = str(h.get("date", ""))[:10]
            if d:
                by_date[d] = h
        return [by_date[d] for d in sorted(by_date.keys())]

    def _runtime_from_history(self, history: List[dict], *, first_bar_idx: int = 0) -> PaperRuntime:
        """根据历史记录重建运行时状态（用于重跑/去重后回放）。"""
        eng = self.engine
        rt = PaperRuntime(
            weeks_since_exit=int(eng.cooldown_periods),
            first_bar_idx=int(first_bar_idx or 0),
        )
        for h in history:
            old_pos = float(h.get("old_position", 0) or 0)
            new_pos = float(h.get("new_position", 0) or 0)
            period_ret = float(h.get("period_return", 0) or 0)
            rt.equity_ratio *= 1.0 + period_ret
            rt.peak_equity_ratio = max(rt.peak_equity_ratio, rt.equity_ratio)
            if new_pos > eng.trade_epsilon:
                rt.weeks_held = rt.weeks_held + 1 if old_pos > eng.trade_epsilon else 1
                rt.weeks_since_exit = 0
                if rt.entry_equity_ratio is None:
                    rt.entry_equity_ratio = rt.equity_ratio
            else:
                rt.weeks_held = 0
                if old_pos > eng.trade_epsilon:
                    rt.weeks_since_exit = 1
                elif rt.weeks_since_exit > 0:
                    rt.weeks_since_exit += 1
                rt.entry_equity_ratio = None
            rt.position = new_pos
            sw = {}
            for key in ("csi300", "gold", "bond", "nasdaq", "repo", "mm"):
                ck = f"cash_w_{key}"
                if ck in h:
                    sw[key] = float(h.get(ck, 0) or 0)
            if sw:
                rt.sleeve_weights = sw
        return rt

    def _sync_state_from_history(self, state: dict, rt: PaperRuntime) -> None:
        """将 runtime / 汇总字段与 history 对齐。"""
        history = state.get("history") or []
        capital = float(state.get("initial_capital", self.initial_capital))
        eq = 1.0
        for h in history:
            eq *= 1.0 + float(h.get("period_return", 0) or 0)
            h["equity_ratio"] = round(eq, 6)
        state["runtime"] = self._pack_runtime(rt, state)
        state["position"] = rt.position
        state["equity"] = round(capital * rt.equity_ratio, 2)
        state["cash"] = round(state["equity"] * (1.0 - rt.position), 2)
        state["total_return"] = round(rt.equity_ratio - 1.0, 6)
        state["last_processed_date"] = history[-1]["date"] if history else None
        state["total_trades"] = sum(1 for h in history if h.get("trade_occurred"))

    def _get_predictor(self, df: pd.DataFrame):
        from market_platform.predictor.分析.推断.集合元模型 import MetaEnsemblePredictor, is_ensemble_provider

        if self._predictor is None:
            if is_ensemble_provider(self.provider):
                self._predictor = MetaEnsemblePredictor()
            else:
                self._predictor = LLMApiPredictor(provider=self.provider)
                # 模拟盘只用已有 llm_cache，禁止未命中写回污染库
                self._predictor.set_cache_write(False)
            self._predictor.bind_dataframe(df)
            self._predictor.train(df)
        else:
            self._predictor.bind_dataframe(df)
        return self._predictor

    def _predict_bar(self, model, lookback_df: pd.DataFrame):
        last_row = lookback_df.iloc[-1:]
        model.bind_dataframe(lookback_df)
        week_pred = None
        if (self._weekly_decision or self._dual_freq) and hasattr(model, "predict_bundle"):
            pred, proba, week_pred = model.predict_bundle(last_row)
        else:
            pred = model.predict(last_row)[0]
            proba = model.predict_proba(last_row)[0]
            if self._dual_freq:
                from market_platform.predictor.分析.提示词.构建器 import infer_week_direction
                week_pred = infer_week_direction(last_row.iloc[0])
        return int(pred), np.asarray(proba, dtype=float), week_pred

    def _process_bar(
        self,
        rt: PaperRuntime,
        df: pd.DataFrame,
        global_idx: int,
        model,
    ) -> tuple[PaperRuntime, dict]:
        eng = self.engine
        row = df.iloc[global_idx]
        bar_date = pd.Timestamp(row["date"]).strftime("%Y-%m-%d")
        lookback_df = df.iloc[: global_idx + 1]
        is_rebalance_day = eng._is_weekly_rebalance_day(
            global_idx,
            rt.first_bar_idx,
            bar_date=row["date"],
            trading_dates=pd.to_datetime(df["date"]),
        )

        if rt.position > eng.trade_epsilon and rt.entry_equity_ratio is None:
            rt.entry_equity_ratio = rt.equity_ratio
        if rt.position <= eng.trade_epsilon:
            rt.entry_equity_ratio = None
        rt.peak_equity_ratio = max(rt.peak_equity_ratio, rt.equity_ratio)

        need_predict = is_rebalance_day or not self._weekly_decision
        if need_predict:
            pred, proba, week_pred = self._predict_bar(model, lookback_df)
            self._last_pred, self._last_proba, self._last_week_pred = pred, proba, week_pred
        else:
            pred, proba, week_pred = self._last_pred, self._last_proba, self._last_week_pred
        p_down, p_flat, p_up = eng._parse_proba(proba)
        rt.edge_history.append(p_up - p_down)
        raw_conf = float(np.max(proba))
        max_conf = eng.confidence_isotonic.calibrate(raw_conf)
        proba = proba.copy()
        pred_idx = int(np.argmax(proba))
        proba[pred_idx] = max_conf
        rest = max(0.0, 1.0 - max_conf) / 2.0
        for i in range(3):
            if i != pred_idx:
                proba[i] = rest
        p_down, p_flat, p_up = eng._parse_proba(proba)

        label_ret = row.get("label_return")
        realized_ret = self._bar_realized_return(df, global_idx)
        # 实盘盯市：优先用已实现收盘涨跌；回测标签收益仅作后备（最新日通常为空）
        market_ret = realized_ret
        if market_ret is None and not pd.isna(label_ret):
            market_ret = float(label_ret)

        if self._unified_mode and self._unified_allocator is not None and self._sleeve_returns is not None:
            from market_platform.predictor.分析.资金.统一夏普配置 import compute_unified_period_pnl

            asof = pd.Timestamp(row["date"]).normalize()
            old_position = rt.position
            prev_weights = dict(rt.sleeve_weights or {})
            holding_weights = {
                k: float(prev_weights.get(k, 0.0) or 0.0)
                for k in ("csi300", "nasdaq", "gold", "bond", "mm", "repo")
            }
            # 无持久化状态时用隔夜权重种子，避免进程重启触发假强制调仓
            self._unified_allocator.seed_from_sleeve_weights(holding_weights)
            pw = self._unified_allocator.resolve(
                self._sleeve_returns,
                asof,
                row=row,
                pred=int(pred),
                confidence=float(max_conf),
                trading_dates=pd.to_datetime(df["date"]),
            )
            new_position = float(pw.csi300)
            target = new_position
            sleeve_hit = self._sleeve_returns[self._sleeve_returns["date"] == asof]
            # 禁止回退前一日收益：缺当日袖口行则该袖口收益按 0，避免混日 PnL
            if sleeve_hit.empty:
                day_row = pd.Series({"csi300_ret": float(market_ret or 0.0)})
            else:
                day_row = sleeve_hit.iloc[0].copy()
                if market_ret is not None:
                    day_row["csi300_ret"] = float(market_ret)
            # 隔夜仓吃当日涨跌；收盘调仓成本按 prev→new；新权重次日生效
            total_pnl, offense_pnl, defensive_pnl, cost_ratio = compute_unified_period_pnl(
                pw.weights,
                day_row,
                prev_weights=holding_weights,
                earn_weights=holding_weights,
                commission=eng.commission,
                slippage=eng.slippage,
            )
            rt.equity_ratio *= 1.0 + total_pnl
            sleeve_l1 = sum(
                abs(float(pw.weights.get(k, 0.0)) - float(prev_weights.get(k, 0.0)))
                for k in ("csi300", "nasdaq", "gold", "bond", "mm", "repo")
            )
            trade_occurred = sleeve_l1 > eng.trade_epsilon
            if new_position > 0:
                rt.weeks_held = rt.weeks_held + 1 if old_position > 0 else 1
                rt.weeks_since_exit = 0
            else:
                rt.weeks_held = 0
                if old_position > 0:
                    rt.weeks_since_exit = 1
                elif rt.weeks_since_exit > 0:
                    rt.weeks_since_exit += 1
            rt.position = new_position
            rt.sleeve_weights = {
                k: float(pw.weights.get(k, 0.0))
                for k in ("csi300", "nasdaq", "gold", "bond", "mm", "repo")
            }
            direction_map = {-1: "跌", 0: "平", 1: "涨"}
            record = {
                "date": bar_date,
                "close": float(row["close"]) if pd.notna(row.get("close")) else None,
                "prediction": pred,
                "direction": direction_map.get(pred, str(pred)),
                "confidence": round(max_conf, 4),
                "week_prediction": week_pred,
                "target_position": round(float(target), 4),
                "old_position": round(float(old_position), 4),
                "new_position": round(float(new_position), 4),
                "period_return": round(float(total_pnl), 6),
                "strategy_return": round(float(offense_pnl), 6),
                "cash_return": round(float(defensive_pnl), 6),
                "benchmark_return": round(float(market_ret), 6) if market_ret is not None else None,
                "cost_ratio": round(float(cost_ratio), 6),
                "equity_ratio": round(float(rt.equity_ratio), 6),
                "trade_occurred": trade_occurred,
                "is_rebalance_day": is_rebalance_day,
                "allocation_regime": pw.regime,
                "allocation_regime_label": pw.regime_label,
                "llm_alloc_override": bool(pw.risk_off),
                "allocation_mode": "unified_sharpe",
                "group_w_offense": round(float(pw.group_weights.get("offense", 0)), 4),
                "group_w_defense": round(float(pw.group_weights.get("defense", 0)), 4),
                "group_w_cashflow": round(float(pw.group_weights.get("cashflow", 0)), 4),
            }
            for key in ("csi300", "gold", "bond", "nasdaq", "repo", "mm"):
                record[f"cash_w_{key}"] = round(float(pw.weights.get(key, 0.0)), 6)
            # 各袖口当日贡献 = 隔夜权重 × 收益（毛贡献；成本单独在 cost_ratio）
            ret_cols = {
                "csi300": "csi300_ret",
                "nasdaq": "nasdaq_ret",
                "gold": "gold_ret",
                "bond": "bond_ret",
                "mm": "mm_ret",
                "repo": "repo_daily_ret",
            }
            for key, col in ret_cols.items():
                raw = day_row.get(col) if hasattr(day_row, "get") else None
                asset_ret = float(raw) if raw is not None and pd.notna(raw) else 0.0
                w = float(holding_weights.get(key, 0.0) or 0.0)
                record[f"cash_asset_ret_{key}"] = round(asset_ret, 8)
                record[f"cash_contrib_{key}"] = round(w * asset_ret, 8)
            return rt, record

        stop_ref = market_ret if market_ret is not None else (
            float(label_ret) if not pd.isna(label_ret) else None
        )
        forced_exit = (
            rt.position > 0
            and stop_ref is not None
            and eng.stop_loss is not None
            and float(stop_ref) <= -abs(eng.stop_loss)
        )

        target = eng._resolve_target_position(pred, proba, rt.edge_history, row=row)
        regime_base = eng._resolve_base_position(pred, max_conf=max_conf, row=row)
        target = eng._apply_vol_target_cap(row, target, regime_base, pred, max_conf)
        target = eng._apply_dual_freq_gate(target, pred, week_pred, row, max_conf)
        target = eng._lift_gated_target(
            target, regime_base, pred, max_conf, row=row, week_pred=week_pred,
        )

        resonance_add = (
            pred == 1
            and week_pred == 1
            and max_conf >= eng.high_conviction_threshold
        )

        systemic_triggered = eng._detect_systemic_risk(row, lookback_df, pred, max_conf, week_pred)
        allow_immediate = forced_exit
        if systemic_triggered and rt.emergency_cooldown_left <= 0:
            target = eng._trim_overlay(rt.position, target, regime_base, eng.sys_exit_scale)
            allow_immediate = True
            rt.emergency_cooldown_left = eng.emergency_cooldown_days

        target, tp_triggered = eng._apply_take_profit(
            target, rt.position, rt.equity_ratio, rt.peak_equity_ratio,
            rt.entry_equity_ratio, regime_base, row=row,
        )
        if tp_triggered and eng.tp_immediate:
            allow_immediate = True
            if rt.emergency_cooldown_left <= 0:
                rt.emergency_cooldown_left = eng.emergency_cooldown_days

        if forced_exit and rt.emergency_cooldown_left <= 0:
            target = eng._trim_overlay(
                rt.position, target, regime_base, eng.emergency_reduce_scale,
            )
            allow_immediate = True
            rt.emergency_cooldown_left = eng.emergency_cooldown_days
        elif target is None:
            target = rt.position

        day_cash_weights = None
        alloc_regime = None
        alloc_label = None
        llm_override = False
        if self._three_layer is not None and target is not None:
            target, alloc_dec = self._three_layer.apply_equity_adjustment(
                float(target), row, int(pred), max_conf, self._cash_returns,
            )
            day_cash_weights = alloc_dec.cash_weights
            alloc_regime = alloc_dec.regime
            alloc_label = alloc_dec.regime_label
            llm_override = alloc_dec.llm_override

        if allow_immediate or is_rebalance_day:
            target = eng._apply_execution_rules(
                rt.position, target, rt.weeks_held, rt.weeks_since_exit,
                force_reduce=allow_immediate,
            )
        else:
            target = rt.position

        old_position = rt.position
        new_position = eng._apply_rebalance_cadence(
            rt.position, target, is_rebalance_day, allow_immediate,
            row, pred, max_conf, forced_exit or allow_immediate, rt.weeks_held,
            resonance_add,
        )

        if rt.emergency_cooldown_left > 0:
            rt.emergency_cooldown_left -= 1

        cost_ratio = eng._transaction_cost(old_position, new_position)
        # 当日涨跌发生在调仓前的持仓上（隔夜仓 = old_position）
        period_pnl = 0.0
        if market_ret is not None:
            period_pnl = eng._period_return(old_position, float(market_ret))
        strategy_pnl = period_pnl - cost_ratio

        cash_pnl = 0.0
        cash_breakdown: Dict[str, object] = {}
        if self._cash_mgmt_enabled and self._cash_returns is not None:
            from market_platform.predictor.分析.资金.闲置管理 import compute_cash_return_breakdown

            cash_breakdown = compute_cash_return_breakdown(
                old_position, row, self._cash_returns,
                date_idx=pd.Timestamp(row["date"]),
                weights=day_cash_weights,
            )
            cash_pnl = float(cash_breakdown.get("total", 0.0) or 0.0)

        total_pnl = strategy_pnl + cash_pnl
        rt.equity_ratio *= 1.0 + total_pnl
        trade_occurred = abs(new_position - old_position) > eng.trade_epsilon

        if new_position > 0:
            rt.weeks_held = rt.weeks_held + 1 if old_position > 0 else 1
            rt.weeks_since_exit = 0
        else:
            rt.weeks_held = 0
            if old_position > 0:
                rt.weeks_since_exit = 1
            elif rt.weeks_since_exit > 0:
                rt.weeks_since_exit += 1

        rt.position = new_position

        benchmark_return = None
        if market_ret is not None:
            benchmark_return = round(float(market_ret), 6)

        direction_map = {-1: "跌", 0: "平", 1: "涨"}
        record = {
            "date": bar_date,
            "close": float(row["close"]) if pd.notna(row.get("close")) else None,
            "prediction": pred,
            "direction": direction_map.get(pred, str(pred)),
            "confidence": round(max_conf, 4),
            "week_prediction": week_pred,
            "target_position": round(float(target), 4),
            "old_position": round(float(old_position), 4),
            "new_position": round(float(new_position), 4),
            "period_return": round(float(total_pnl), 6),
            "strategy_return": round(float(strategy_pnl), 6),
            "cash_return": round(float(cash_pnl), 6),
            "benchmark_return": benchmark_return,
            "cost_ratio": round(float(cost_ratio), 6),
            "equity_ratio": round(float(rt.equity_ratio), 6),
            "trade_occurred": trade_occurred,
            "is_rebalance_day": is_rebalance_day,
            "allocation_regime": alloc_regime,
            "allocation_regime_label": alloc_label,
            "llm_alloc_override": llm_override,
            "allocation_mode": "legacy",
        }
        weights = cash_breakdown.get("weights") or {}
        contribs = cash_breakdown.get("contributions") or {}
        for key in ("gold", "bond", "nasdaq", "repo", "mm"):
            if key in weights:
                record[f"cash_w_{key}"] = round(float(weights[key]), 6)
            if key in contribs:
                record[f"cash_contrib_{key}"] = round(float(contribs[key]), 8)
        asset_rets = cash_breakdown.get("asset_returns") or {}
        for key in ("gold", "bond", "nasdaq", "repo", "mm"):
            if key in asset_rets:
                record[f"cash_asset_ret_{key}"] = round(float(asset_rets[key]), 8)
        return rt, record

    def run_cycle(
        self,
        *,
        refresh_data: bool | None = None,
        force: bool = False,
    ) -> dict:
        """执行一轮：可选刷新数据，处理所有未处理的 K 线。"""
        if refresh_data is None:
            refresh_data = bool(self.cfg.get("refresh_data", True))

        if refresh_data:
            from market_platform.predictor.脚本.运行管道 import step_data
            step_data()

        from market_platform.predictor.接口.市场分析 import load_feature_df

        df = load_feature_df().sort_values("date").reset_index(drop=True)
        self._refresh_cash_returns(df)
        state = self.load_state()
        self._restore_allocator(state)
        rt = _runtime_from_dict(state)
        last_processed = state.get("last_processed_date")
        _capital = float(state.get("initial_capital", self.initial_capital))  # 未被使用,保留原式以便对比

        if last_processed:
            mask = pd.to_datetime(df["date"]) > pd.Timestamp(last_processed)
        else:
            start_date = self.cfg.get("start_date")
            if start_date:
                mask = pd.to_datetime(df["date"]) >= pd.Timestamp(start_date)
            else:
                mask = pd.Series([False] * len(df))
                mask.iloc[-1] = True

        indices = df.index[mask].tolist()
        if not indices and not force:
            return {
                "status": "skipped",
                "message": "无新 K 线需处理",
                "last_processed_date": last_processed,
                "data_as_of": pd.Timestamp(df.iloc[-1]["date"]).strftime("%Y-%m-%d"),
            }

        if not indices and force:
            indices = [len(df) - 1]

        reprocess_dates = {
            pd.Timestamp(df.iloc[i]["date"]).strftime("%Y-%m-%d") for i in indices
        }
        if force and reprocess_dates:
            state["history"] = [
                h for h in (state.get("history") or [])
                if str(h.get("date", ""))[:10] not in reprocess_dates
            ]
            rt = self._runtime_from_history(
                state["history"],
                first_bar_idx=rt.first_bar_idx or (indices[0] if indices else 0),
            )
            # 强制重跑时丢弃含重跑日的 allocator，按截断后隔夜权重重新种子
            if self._unified_allocator is not None:
                self._unified_allocator.reset()
                self._unified_allocator.seed_from_sleeve_weights(rt.sleeve_weights)

        if rt.first_bar_idx == 0 and indices:
            rt.first_bar_idx = indices[0]

        model = self._get_predictor(df)
        processed: List[dict] = []

        for global_idx in indices:
            rt, record = self._process_bar(rt, df, global_idx, model)
            state["history"].append(record)
            processed.append(record)

        state["history"] = self._dedupe_history(state.get("history") or [])
        rt = self._runtime_from_history(
            state["history"],
            first_bar_idx=rt.first_bar_idx,
        )
        self._sync_state_from_history(state, rt)
        state["last_bar_date"] = pd.Timestamp(df.iloc[-1]["date"]).strftime("%Y-%m-%d")
        state["updated_at"] = _utc_now_iso()
        state["history"] = state["history"][-2000:]
        save_paper_trading_state(state, provider=self.provider)

        logger.info(
            "模拟实盘 {}: 处理 {} 根 K 线，仓位 {:.0%}，净值 {:.4f}",
            state["last_processed_date"],
            len(processed),
            rt.position,
            rt.equity_ratio,
        )
        return {
            "status": "ok",
            "processed_bars": len(processed),
            "records": processed,
            "state": self.build_status(state),
        }

    def build_status(self, state: Optional[dict] = None) -> dict:
        state = state or self.load_state()
        rt = _runtime_from_dict(state)
        capital = float(state.get("initial_capital", self.initial_capital))
        equity = float(state.get("equity", capital * rt.equity_ratio))
        meta = get_bar_meta()
        history = state.get("history") or []
        position = float(rt.position)
        idle_ratio = max(0.0, 1.0 - position)
        idle_total = round(equity * idle_ratio, 2)
        strategy_value = round(equity * position, 2)

        from market_platform.predictor.分析.资金.闲置管理 import ALLOCATION_LABELS, normalized_cash_weights

        latest = history[-1] if history else {}
        default_w = normalized_cash_weights()
        sleeve_keys = ("csi300", "nasdaq", "gold", "bond", "mm", "repo")
        if self._unified_mode:
            weights = {
                key: float(latest.get(f"cash_w_{key}", 0.0) or 0.0)
                for key in sleeve_keys
            }
            if not any(weights.values()) and position > 0:
                weights["csi300"] = position
            sleeve_breakdown = []
            for key in sleeve_keys:
                w = weights.get(key, 0.0)
                if w <= 1e-8:
                    continue
                sleeve_breakdown.append({
                    "key": key,
                    "label": ALLOCATION_LABELS.get(key, key),
                    "weight_in_idle": round(w, 6),  # 兼容旧前端字段名
                    "weight_in_equity": round(w, 6),
                    "amount": round(equity * w, 2),
                })
            offense_w = float(latest.get("group_w_offense", 0) or 0)
            if offense_w <= 0:
                offense_w = float(weights.get("csi300", 0) or 0) + float(weights.get("nasdaq", 0) or 0)
            return {
                "llm_provider": state.get("llm_provider") or self.provider,
                "initial_capital": capital,
                "equity": equity,
                "cash": 0.0,
                "strategy_value": equity,
                "idle_total": 0.0,
                "idle_ratio": 0.0,
                "idle_breakdown": sleeve_breakdown,
                "sleeve_breakdown": sleeve_breakdown,
                "allocation_mode": "unified_sharpe",
                "cash_mgmt_enabled": bool(self._cash_mgmt_enabled),
                "position": position,
                "offense_weight": round(offense_w, 4),
                "total_return": float(state.get("total_return", rt.equity_ratio - 1.0)),
                "total_return_pct": round(float(state.get("total_return", rt.equity_ratio - 1.0)) * 100, 2),
                "total_trades": int(state.get("total_trades", 0)),
                "last_bar_date": state.get("last_bar_date"),
                "last_processed_date": state.get("last_processed_date"),
                "started_at": state.get("started_at"),
                "updated_at": state.get("updated_at"),
                "auto_trade_enabled": bool(state.get("auto_trade_enabled", True)),
                "bar_freq": meta.get("bar_freq"),
                "bar_unit": meta.get("bar_unit"),
                "latest_signal": latest or None,
            }

        weights = {
            key: float(latest.get(f"cash_w_{key}", default_w.get(key, 0.0)) or 0.0)
            for key in ("gold", "bond", "nasdaq", "repo", "mm")
        }
        idle_breakdown = []
        if self._cash_mgmt_enabled and idle_ratio > 0:
            for key in ("gold", "bond", "nasdaq", "repo", "mm"):
                w = weights.get(key, 0.0)
                if w <= 0:
                    continue
                amount = round(idle_total * w, 2)
                idle_breakdown.append({
                    "key": key,
                    "label": ALLOCATION_LABELS.get(key, key),
                    "weight_in_idle": round(w, 6),
                    "weight_in_equity": round(idle_ratio * w, 6),
                    "amount": amount,
                })

        return {
            "llm_provider": state.get("llm_provider") or self.provider,
            "initial_capital": capital,
            "equity": equity,
            "cash": idle_total,
            "strategy_value": strategy_value,
            "idle_total": idle_total,
            "idle_ratio": round(idle_ratio, 6),
            "idle_breakdown": idle_breakdown,
            "allocation_mode": "legacy",
            "cash_mgmt_enabled": bool(self._cash_mgmt_enabled),
            "position": position,
            "total_return": float(state.get("total_return", rt.equity_ratio - 1.0)),
            "total_return_pct": round(float(state.get("total_return", rt.equity_ratio - 1.0)) * 100, 2),
            "total_trades": int(state.get("total_trades", 0)),
            "last_bar_date": state.get("last_bar_date"),
            "last_processed_date": state.get("last_processed_date"),
            "started_at": state.get("started_at"),
            "updated_at": state.get("updated_at"),
            "auto_trade_enabled": bool(state.get("auto_trade_enabled", True)),
            "bar_freq": meta.get("bar_freq"),
            "bar_unit": meta.get("bar_unit"),
            "latest_signal": latest or None,
        }

    def _benchmark_returns(self, history: List[dict]) -> np.ndarray:
        """从历史记录提取大盘 bar 收益；旧数据无 benchmark_return 时用收盘价推算。"""
        rets: List[float] = []
        prev_close: Optional[float] = None
        for h in history:
            raw = h.get("benchmark_return")
            if raw is not None:
                rets.append(float(raw))
                if h.get("close") is not None:
                    prev_close = float(h["close"])
                continue
            close = h.get("close")
            if prev_close is not None and close is not None and prev_close > 0:
                rets.append(float(close) / prev_close - 1.0)
            else:
                rets.append(np.nan)
            if close is not None:
                prev_close = float(close)
        return np.asarray(rets, dtype=float)

    def build_performance(self, state: Optional[dict] = None) -> dict:
        state = state or self.load_state()
        history = state.get("history") or []
        if not history:
            return {"available": False}

        meta = get_bar_meta()
        bar_freq = meta.get("bar_freq", "daily")
        periods_per_year = 52 if bar_freq == "weekly" else 252

        period_returns = np.asarray(
            [float(h.get("period_return", 0) or 0) for h in history], dtype=float,
        )
        strategy_returns = np.asarray(
            [float(h.get("strategy_return", 0) or 0) for h in history], dtype=float,
        )
        cash_returns = np.asarray(
            [float(h.get("cash_return", 0) or 0) for h in history], dtype=float,
        )
        positions = np.asarray(
            [
                float(h.get("group_w_offense", h.get("new_position", 0)) or 0)
                if self._unified_mode
                else float(h.get("new_position", 0) or 0)
                for h in history
            ],
            dtype=float,
        )
        benchmark_rets = self._benchmark_returns(history)

        n = len(period_returns)
        total_return = float(np.prod(1.0 + period_returns) - 1.0) if n else 0.0
        # 分项用「期初净值加权贡献」，保证进攻+防御(+成本已内含)可加总到总收益
        # 不可各自复利后再相加（会与 total_return 不一致）
        eq = 1.0
        strat_contrib = 0.0
        cash_contrib = 0.0
        for s_ret, c_ret, p_ret in zip(strategy_returns, cash_returns, period_returns):
            strat_contrib += float(s_ret) * eq
            cash_contrib += float(c_ret) * eq
            eq *= 1.0 + float(p_ret)
        strat_only = float(strat_contrib)
        cash_only = float(cash_contrib)

        annualized = (
            (1.0 + total_return) ** (periods_per_year / n) - 1.0 if n > 0 else 0.0
        )
        sharpe = (
            float(period_returns.mean() / (period_returns.std() + 1e-10))
            * np.sqrt(periods_per_year)
        )

        cum_ret = np.cumprod(1.0 + period_returns)
        running_max = np.maximum.accumulate(cum_ret)
        drawdown = (cum_ret - running_max) / running_max
        max_dd = float(drawdown.min()) if len(drawdown) else 0.0

        valid_bench = benchmark_rets[np.isfinite(benchmark_rets)]
        bench_total = float(np.prod(1.0 + valid_bench) - 1.0) if len(valid_bench) else None
        bench_annual = (
            (1.0 + bench_total) ** (periods_per_year / len(valid_bench)) - 1.0
            if bench_total is not None and len(valid_bench) > 0
            else None
        )
        if len(valid_bench):
            bench_cum = np.cumprod(1.0 + valid_bench)
            bench_running = np.maximum.accumulate(bench_cum)
            bench_dd = float(((bench_cum - bench_running) / bench_running).min())
        else:
            bench_dd = None

        excess = total_return - bench_total if bench_total is not None else None

        active = [(r, p) for r, p in zip(period_returns, positions) if p > 0.01]
        win_rate = sum(1 for r, _ in active if r > 0) / len(active) if active else None

        return {
            "available": True,
            "period": {"start": history[0]["date"], "end": history[-1]["date"], "bars": n},
            "bar_freq": bar_freq,
            "total_return": round(total_return, 6),
            "strategy_only_return": round(strat_only, 6),
            "cash_return": round(cash_only, 6),
            "benchmark_total_return": round(bench_total, 6) if bench_total is not None else None,
            "benchmark_annualized_return": round(bench_annual, 6) if bench_annual is not None else None,
            "excess_return": round(excess, 6) if excess is not None else None,
            "annualized_return": round(annualized, 6),
            "max_drawdown": round(max_dd, 6),
            "benchmark_max_drawdown": round(bench_dd, 6) if bench_dd is not None else None,
            "sharpe_ratio": round(sharpe, 4),
            "win_rate": round(win_rate, 4) if win_rate is not None else None,
            "avg_position": round(float(positions.mean()), 4),
            "periods_in_market": round(
                sum(1 for p in positions if p > 0.01) / max(len(positions), 1), 4,
            ),
            "total_trades": int(state.get("total_trades", 0)),
            "total_transaction_cost": round(
                sum(float(h.get("cost_ratio", 0) or 0) for h in history), 6,
            ),
            "cash_mgmt_enabled": self._cash_mgmt_enabled,
        }

    def _history_cash_weights(self, history: List[dict]) -> List[Optional[Dict[str, float]]]:
        from market_platform.predictor.分析.资金.闲置管理 import normalized_cash_weights

        default = normalized_cash_weights()
        out: List[Optional[Dict[str, float]]] = []
        for h in history:
            row_w: Dict[str, float] = {}
            keys = ("csi300", "gold", "bond", "nasdaq", "repo", "mm") if self._unified_mode else (
                "gold", "bond", "nasdaq", "repo", "mm",
            )
            for key in keys:
                val = h.get(f"cash_w_{key}")
                if val is not None:
                    row_w[key] = float(val)
            if self._unified_mode and "csi300" not in row_w:
                row_w["csi300"] = float(h.get("new_position", 0) or 0)
            if row_w:
                out.append(row_w)
            else:
                out.append(None if self._unified_mode else dict(default))
        return out

    def build_cash_detail(self, state: Optional[dict] = None) -> dict:
        """多资产配置：分品种权重、贡献与净值曲线。"""
        state = state or self.load_state()
        history = state.get("history") or []
        if not self._cash_mgmt_enabled or not history or self._cash_returns is None:
            return {"enabled": False}

        from market_platform.predictor.分析.资金.闲置管理 import (
            ALLOCATION_LABELS,
            SLEEVE_KEYS,
            build_cash_asset_curves,
            build_fund_allocation_history,
            normalized_cash_weights,
        )

        dates = pd.Series([h["date"] for h in history])
        positions = pd.Series([float(h.get("new_position", 0) or 0) for h in history])
        weight_history = self._history_cash_weights(history)

        curves = build_cash_asset_curves(
            dates,
            self._cash_returns,
            positions=None if self._unified_mode else positions,
            unified=bool(self._unified_mode),
        )
        allocation = build_fund_allocation_history(
            dates,
            positions,
            cash_mgmt_enabled=True,
            cash_returns=self._cash_returns,
            weight_history=weight_history,
            absolute_cash_weights=bool(self._unified_mode),
            strategy_label="沪深300" if self._unified_mode else None,
        )

        asset_keys = SLEEVE_KEYS if self._unified_mode else ("gold", "bond", "nasdaq", "repo", "mm")
        contrib_totals: Dict[str, float] = {k: 0.0 for k in asset_keys}
        contrib_cum = 1.0
        contrib_curve: List[float] = []
        asset_curves: Dict[str, List[float]] = {k: [] for k in asset_keys}
        asset_cum = {k: 1.0 for k in asset_keys}

        # 旧记录可能缺 cash_contrib_*：用权重 × 对齐日收益回补
        ret_cols = {
            "csi300": "csi300_ret",
            "nasdaq": "nasdaq_ret",
            "gold": "gold_ret",
            "bond": "bond_ret",
            "mm": "mm_ret",
            "repo": "repo_daily_ret",
        }
        aligned_rets = None
        if self._unified_mode and self._cash_returns is not None:
            from market_platform.predictor.分析.资金.闲置管理 import align_cash_returns_to_dates

            aligned_rets = align_cash_returns_to_dates(dates, self._cash_returns)

        for i, h in enumerate(history):
            day_total = 0.0
            has_any_contrib = any(
                h.get(f"cash_contrib_{k}") is not None for k in asset_keys
            )
            for key in asset_keys:
                c_raw = h.get(f"cash_contrib_{key}")
                if c_raw is not None:
                    c = float(c_raw or 0)
                elif self._unified_mode and aligned_rets is not None:
                    w = float(h.get(f"cash_w_{key}", 0) or 0)
                    col = ret_cols.get(key)
                    raw = aligned_rets.iloc[i].get(col) if col else None
                    asset_ret = float(raw) if raw is not None and pd.notna(raw) else 0.0
                    c = w * asset_ret
                elif (not self._unified_mode) and not has_any_contrib and h.get("cash_return"):
                    idle = max(0.0, 1.0 - float(h.get("new_position", 0) or 0))
                    w = normalized_cash_weights()
                    total_cash = float(h.get("cash_return", 0) or 0)
                    w_sum = sum(float(w.get(k, 0) or 0) for k in ("gold", "bond", "nasdaq", "repo", "mm")) or 1.0
                    c = total_cash * float(w.get(key, 0) or 0) / w_sum if idle > 0 and key in w else 0.0
                else:
                    c = 0.0
                contrib_totals[key] += c
                asset_cum[key] *= 1.0 + c
                asset_curves[key].append(round(asset_cum[key] - 1.0, 6))
                day_total += c
            if self._unified_mode:
                day_total = float(h.get("period_return", 0) or 0)
                contrib_cum *= 1.0 + day_total
            else:
                contrib_cum *= 1.0 + day_total
            contrib_curve.append(round(contrib_cum - 1.0, 6))

        latest = history[-1]
        latest_weights = {}
        for key in asset_keys:
            if self._unified_mode:
                fallback = float(latest.get("new_position", 0) or 0) if key == "csi300" else 0.0
                latest_weights[key] = float(latest.get(f"cash_w_{key}", fallback) or 0.0)
            else:
                latest_weights[key] = float(
                    latest.get(f"cash_w_{key}", normalized_cash_weights().get(key, 0.0)) or 0.0
                )
        latest_contribs = {}
        for key in asset_keys:
            raw = latest.get(f"cash_contrib_{key}")
            if raw is not None:
                latest_contribs[key] = float(raw or 0)
            elif self._unified_mode and aligned_rets is not None:
                w = float(latest.get(f"cash_w_{key}", 0) or 0)
                col = ret_cols.get(key)
                rv = aligned_rets.iloc[-1].get(col) if col else None
                asset_ret = float(rv) if rv is not None and pd.notna(rv) else 0.0
                latest_contribs[key] = round(w * asset_ret, 8)
            else:
                latest_contribs[key] = 0.0

        breakdown = []
        for key in asset_keys:
            if latest_weights.get(key, 0) <= 0 and contrib_totals[key] == 0:
                continue
            breakdown.append({
                "key": key,
                "label": ALLOCATION_LABELS.get(key, key),
                "weight": latest_weights.get(key, 0.0),
                "avg_weight": float(np.mean([
                    float(w.get(key, 0) or 0) for w in weight_history if w
                ])) if weight_history else latest_weights.get(key, 0.0),
                "total_contribution": round(contrib_totals[key], 6),
                "cumulative_curve": asset_curves[key],
            })

        return {
            "enabled": True,
            "allocation_mode": "unified_sharpe" if self._unified_mode else "legacy",
            "dates": [str(d)[:10] for d in dates.tolist()],
            "total_contribution": round(
                (contrib_cum - 1.0) if self._unified_mode else sum(contrib_totals.values()), 6
            ),
            "contribution_curve": contrib_curve,
            "breakdown": breakdown,
            "latest_weights": latest_weights,
            "latest_contributions": latest_contribs,
            "latest_regime": latest.get("allocation_regime_label"),
            "curves": curves if curves.get("enabled") else None,
            "allocation": allocation,
        }

    def build_charts(self, state: Optional[dict] = None) -> dict:
        state = state or self.load_state()
        history = state.get("history") or []
        if not history:
            return {"available": False}

        capital = float(state.get("initial_capital", self.initial_capital))
        # 曲线起点锚定初始资金，使图上首→末收益与卡片 total_return 一致；
        # 种子日用上一交易日（非整周回退），避免节假日错位。
        first_day = pd.Timestamp(history[0]["date"]).normalize()
        seed_day = first_day - pd.Timedelta(days=1)
        try:
            trading = None
            if self._sleeve_returns is not None and not self._sleeve_returns.empty:
                trading = pd.to_datetime(self._sleeve_returns["date"]).sort_values()
            elif self._cash_returns is not None and not getattr(self._cash_returns, "empty", True):
                trading = pd.to_datetime(self._cash_returns["date"]).sort_values()
            if trading is not None and len(trading):
                prior = trading[trading < first_day]
                if len(prior):
                    seed_day = pd.Timestamp(prior.iloc[-1]).normalize()
                else:
                    while seed_day.weekday() >= 5:
                        seed_day -= pd.Timedelta(days=1)
            else:
                while seed_day.weekday() >= 5:
                    seed_day -= pd.Timedelta(days=1)
        except Exception:
            while seed_day.weekday() >= 5:
                seed_day -= pd.Timedelta(days=1)
        seed_date = seed_day.strftime("%Y-%m-%d")
        dates = [seed_date] + [h["date"] for h in history]
        equity = [round(capital, 2)] + [round(capital * h["equity_ratio"], 2) for h in history]
        # 仓位与净值同周期：展示「隔夜仓」（当日赚的是旧仓），而非收盘调仓后的新仓。
        # 云端可能初始化失败导致 _unified_mode=False，仍按历史字段识别统一模式。
        unified_hist = bool(self._unified_mode) or any(
            h.get("allocation_mode") == "unified_sharpe" or h.get("group_w_offense") is not None
            for h in history
        )
        if unified_hist:
            offense_holding = [0.0]
            csi_holding = [0.0]
            prev_off = 0.0
            prev_csi = 0.0
            for h in history:
                offense_holding.append(prev_off)
                csi_holding.append(prev_csi)
                prev_off = float(h.get("group_w_offense", h.get("new_position", 0)) or 0)
                prev_csi = float(h.get("new_position", 0) or 0)
            positions = offense_holding
            csi_positions = csi_holding
        else:
            positions = [0.0] + [float(h.get("old_position", 0) or 0) for h in history]
            csi_positions = positions

        benchmark_rets = self._benchmark_returns(history)
        benchmark = [round(capital, 2)]
        cum = 1.0
        has_benchmark = False
        for ret in benchmark_rets:
            if not np.isfinite(ret):
                benchmark.append(None)
                continue
            cum *= 1.0 + float(ret)
            benchmark.append(round(capital * cum, 2))
            has_benchmark = True

        period_returns = np.asarray(
            [float(h.get("period_return", 0) or 0) for h in history], dtype=float,
        )
        # 回撤相对初始资金（含种子点 equity_ratio=1）
        cum_ret = np.concatenate([[1.0], np.cumprod(1.0 + period_returns)])
        running_max = np.maximum.accumulate(cum_ret)
        drawdown = ((cum_ret - running_max) / running_max * 100.0).tolist()

        bench_drawdown = None
        if has_benchmark:
            valid = benchmark_rets[np.isfinite(benchmark_rets)]
            if len(valid):
                bench_cum = np.concatenate([[1.0], np.cumprod(1.0 + valid)])
                bench_running = np.maximum.accumulate(bench_cum)
                full_dd: List[Optional[float]] = [0.0]
                vi = 1
                for ret in benchmark_rets:
                    if not np.isfinite(ret):
                        full_dd.append(None)
                        continue
                    dd = (bench_cum[vi] - bench_running[vi]) / bench_running[vi] * 100.0
                    full_dd.append(round(float(dd), 4))
                    vi += 1
                bench_drawdown = full_dd

        return {
            "available": True,
            "dates": dates,
            "equity": equity,
            "positions": positions,
            "csi_positions": csi_positions,
            "position_label": "进攻组权重(隔夜)" if unified_hist else "策略仓位(隔夜)",
            "drawdown": drawdown,
            "benchmark_equity": benchmark if has_benchmark else None,
            "benchmark_drawdown": bench_drawdown,
        }

    def set_auto_trade(self, enabled: bool) -> dict:
        state = self.load_state()
        state["auto_trade_enabled"] = bool(enabled)
        state["updated_at"] = _utc_now_iso()
        save_paper_trading_state(state, provider=self.provider)
        return self.build_status(state)

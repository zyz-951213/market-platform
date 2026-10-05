from __future__ import annotations

import logging
import threading
from datetime import date
from pathlib import Path

import yaml
from market_platform.agents.settings import AppConfig
from market_platform.agents.核心.任务.交易日历 import latest_expected_trading_day
from market_platform.agents.核心.任务.运行器 import run_auto_update
from market_platform.agents.核心.路径.项目 import profile_path

logger = logging.getLogger(__name__)

_DEFAULT_CONFIG = profile_path("自动更新.yaml")
_DEFAULT_APP_CONFIG = profile_path("默认.yaml")


def load_auto_update_settings(config_path: Path | None = None) -> dict:
    path = Path(config_path or _DEFAULT_CONFIG)
    if not path.exists():
        return {"enabled": False}
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    section = raw.get("auto_update", {})
    return section if isinstance(section, dict) else {}


def auto_update_enabled(settings: dict | None = None, config_path: Path | None = None) -> bool:
    import os

    if os.environ.get("MARKET_AGENTS_AUTO_UPDATE", "").lower() in ("0", "false", "no"):
        return False
    cfg = settings if settings is not None else load_auto_update_settings(config_path)
    return bool(cfg.get("enabled", False))


def load_cycle_config(config_path: Path | None = None) -> AppConfig:
    """自动更新用 自动更新.yaml 的数据/输出路径，其余策略配置沿用 默认.yaml。"""
    base = AppConfig.from_yaml(_DEFAULT_APP_CONFIG)
    path = Path(config_path or _DEFAULT_CONFIG)
    if path.exists() and path.resolve() != _DEFAULT_APP_CONFIG.resolve():
        override = AppConfig.from_yaml(path)
        base.data = override.data
        base.output = override.output
        base.features = override.features
        base.nmf = override.nmf
        base.batch = override.batch
    return base


class AutoUpdateScheduler:
    """Web 服务后台定时拉取过期行情并触发增量分析。"""

    def __init__(self, config_path: Path | None = None) -> None:
        self.config_path = Path(config_path or _DEFAULT_CONFIG)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._running = threading.Event()
        self._last_paper_expected_day: date | None = None

    @property
    def is_running(self) -> bool:
        return self._running.is_set()

    @property
    def is_active(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        settings = load_auto_update_settings(self.config_path)
        if not auto_update_enabled(settings):
            logger.info("Auto-update disabled (config=%s)", self.config_path)
            return
        if self.is_active:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="auto-update", daemon=True)
        self._thread.start()
        logger.info(
            "Auto-update scheduler started (interval=%ss, config=%s)",
            settings.get("loop_interval_seconds", 3600),
            self.config_path,
        )

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def run_once_async(self) -> bool:
        """后台触发一轮更新；若已有任务在跑则跳过。"""
        if self._running.is_set():
            logger.info("Auto-update already running, skip trigger")
            return False

        def _task() -> None:
            self._running.set()
            try:
                self._run_cycle()
            finally:
                self._running.clear()

        threading.Thread(target=_task, name="auto-update-once", daemon=True).start()
        return True

    def _loop(self) -> None:
        settings = load_auto_update_settings(self.config_path)
        delay = int(settings.get("startup_delay_seconds", 15))
        interval = int(settings.get("loop_interval_seconds", 3600))
        if delay > 0:
            logger.info("Auto-update first run in %ds", delay)
            if self._stop.wait(delay):
                return
        if bool(settings.get("paper_trading_on_startup", True)):
            self._run_paper_trading()
        while not self._stop.is_set():
            if not self._running.is_set():
                self._running.set()
                try:
                    self._run_cycle()
                finally:
                    self._running.clear()
            waited = 0
            while waited < interval and not self._stop.is_set():
                chunk = min(60, interval - waited)
                if self._stop.wait(chunk):
                    return
                waited += chunk
                self._maybe_run_paper_on_day_change()

    def _maybe_run_paper_on_day_change(self) -> None:
        """18:00 后交易日切换时立即触发模拟盘，避免等满 30 分钟更新周期。"""
        expected = latest_expected_trading_day()
        if self._last_paper_expected_day is None:
            self._last_paper_expected_day = expected
            return
        if self._last_paper_expected_day >= expected:
            return
        logger.info("Trading day advanced %s -> %s, triggering paper trading", self._last_paper_expected_day, expected)
        self._run_paper_trading()

    def _run_cycle(self) -> None:
        settings = load_auto_update_settings(self.config_path)
        if not self.config_path.exists():
            logger.warning("Auto-update config missing: %s", self.config_path)
            self._run_paper_trading()
            return
        config = load_cycle_config(self.config_path)
        try:
            result = run_auto_update(
                config,
                fetch_limit=int(settings.get("fetch_limit", 50)),
                analyze_limit=int(settings.get("analyze_limit", 50)),
                refresh_universe_list=bool(settings.get("refresh_universe_list", False)),
                refresh_market_data=bool(settings.get("refresh_market_data", True)),
                build_features=not bool(settings.get("skip_features", False)),
                optimize=not bool(settings.get("skip_optimize", False)),
            )
            logger.info(
                "Auto-update cycle: fetch %d/%d analyze %d/%d stale_before=%d (%.1fs)",
                result.fetch_ok,
                result.fetch_attempted,
                result.analyze_ok,
                result.analyze_attempted,
                result.stale_before,
                result.duration_seconds,
            )
        except RuntimeError as exc:
            logger.warning("Auto-update skipped: %s", exc)
        except Exception:  # noqa: BLE001
            logger.exception("Auto-update cycle failed")
        finally:
            if bool(settings.get("paper_trading_after_update", True)):
                self._run_paper_trading()

    def _run_paper_trading(self) -> None:
        settings = load_auto_update_settings(self.config_path)
        if not bool(settings.get("paper_trading_after_update", True)):
            return
        config = AppConfig.from_yaml(_DEFAULT_APP_CONFIG)
        if not config.paper_trading.enabled:
            logger.info("Paper trading disabled (默认.yaml paper_trading.enabled=false)")
            return
        try:
            from market_platform.agents.分析.模拟盘.引擎 import run_paper_trading_cycle

            result = run_paper_trading_cycle(config)
            if result.get("skipped"):
                logger.info("Paper trading skipped: %s", result.get("reason"))
                return
            picks = result.get("tomorrow_picks") or []
            logger.info(
                "Paper trading cycle: trade_date=%s picks=%d execution=%s notify=%s",
                result.get("trade_date"),
                len(picks),
                bool(result.get("execution")),
                bool(result.get("notify")),
            )
            self._last_paper_expected_day = latest_expected_trading_day()
        except Exception:  # noqa: BLE001
            logger.exception("Paper trading cycle failed")


_default_scheduler = AutoUpdateScheduler()


def get_auto_update_scheduler() -> AutoUpdateScheduler:
    return _default_scheduler

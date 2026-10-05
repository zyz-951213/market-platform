from __future__ import annotations

import logging
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from market_platform.agents.settings import AppConfig
from market_platform.agents.分析.批处理.增量 import (
    analyze_status,
    discover_pending,
    run_incremental_batch,
)
from market_platform.agents.核心.任务.交易日历 import latest_expected_trading_day
from market_platform.agents.核心.任务.陈旧检测 import (
    discover_fetch_candidates,
    load_status_file,
    save_status_file,
    summarize_freshness,
)
from market_platform.agents.核心.工具.读写 import ensure_dir
from market_platform.agents.采集.批量拉取 import (
    fetch_market_level_data,
    prepare_universe,
    refresh_symbol_lite,
)
from market_platform.agents.采集.股票池 import infer_market

logger = logging.getLogger(__name__)

_SHANGHAI = ZoneInfo("Asia/Shanghai")
_LOCK_NAME = ".auto_update.lock"


@dataclass
class AutoUpdateResult:
    expected_trading_day: str
    fetch_attempted: int
    fetch_ok: int
    fetch_error: int
    analyze_attempted: int
    analyze_ok: int
    analyze_error: int
    stale_before: int
    pending_before: int
    duration_seconds: float
    status: str = "ok"
    message: str = ""


@contextmanager
def _update_lock(data_root: Path):
    lock = Path(data_root) / _LOCK_NAME
    if lock.exists():
        raise RuntimeError("自动更新任务正在运行，请稍后再试")
    lock.write_text(str(datetime.now(_SHANGHAI).isoformat()), encoding="utf-8")
    try:
        yield
    finally:
        if lock.exists():
            lock.unlink(missing_ok=True)


def update_status(
    config: AppConfig,
    *,
    rescan: bool = False,
) -> dict:
    """返回数据新鲜度与上次自动更新记录。

    rescan=True 时会重扫日线新鲜度，并将结果写入数据库 app_state，
    供网站导航栏 /api/update/status 立即读到。
    """
    data_root = Path(config.data.root)
    output_root = Path(config.output.root)
    expected = latest_expected_trading_day()
    cached = load_status_file(data_root)
    if rescan:
        freshness = summarize_freshness(data_root, expected_date=expected)
    elif cached:
        freshness = {
            "tracked": cached.get("tracked", 0),
            "ready": cached.get("ready", 0),
            "stale": cached.get("stale", 0),
            "missing": cached.get("missing", 0),
            "suspended": cached.get("suspended", 0),
            "up_to_date": cached.get("up_to_date", 0),
            "latest_bar_date": cached.get("latest_bar_date"),
            "expected_trading_day": expected.isoformat(),
        }
    else:
        freshness = {
            "tracked": 0,
            "ready": 0,
            "stale": None,
            "missing": None,
            "suspended": None,
            "up_to_date": None,
            "latest_bar_date": None,
            "expected_trading_day": expected.isoformat(),
        }
    analyze = analyze_status(data_root, output_root)
    status = {
        **freshness,
        "analyze_pending": analyze.get("pending", 0),
        "analyzed": analyze.get("analyzed", 0),
        "data_ready": analyze.get("data_ready", 0),
        "last_run_at": cached.get("last_run_at"),
        "last_run_status": cached.get("last_run_status"),
        "last_fetch_ok": cached.get("last_fetch_ok", 0),
        "last_analyze_ok": cached.get("last_analyze_ok", 0),
        "is_running": (data_root / _LOCK_NAME).exists(),
    }
    if rescan:
        # 保留上次运行元数据，只刷新新鲜度字段，保证网站与数据库一致
        payload = {
            **cached,
            **freshness,
            "analyze_pending": status["analyze_pending"],
            "analyzed": status["analyzed"],
            "data_ready": status["data_ready"],
            "status_rescanned_at": datetime.now(_SHANGHAI).isoformat(timespec="seconds"),
        }
        save_status_file(data_root, payload)
    return status


def run_auto_update(
    config: AppConfig,
    *,
    fetch_limit: int = 50,
    analyze_limit: int = 50,
    refresh_universe_list: bool = False,
    refresh_market_data: bool = True,
    build_features: bool = True,
    optimize: bool = True,
) -> AutoUpdateResult:
    started = datetime.now(_SHANGHAI)
    data_root = ensure_dir(config.data.root)
    output_root = ensure_dir(config.output.root)
    config.data.start_date = config.data.resolved_start_date()
    expected = latest_expected_trading_day(now=started)

    with _update_lock(data_root):
        summary_before = summarize_freshness(data_root, expected_date=expected)
        analyze_before = analyze_status(data_root, output_root)

        if refresh_universe_list:
            prepare_universe(data_root, refresh_list=True)

        fetch_market_level_data(config, force_refresh=refresh_market_data)

        candidates = discover_fetch_candidates(data_root, expected_date=expected, limit=fetch_limit)
        fetched_symbols: list[tuple[str, str, str, str]] = []
        fetch_ok = fetch_err = 0
        for sym, market, name, sector in candidates:
            mkt = market or infer_market(sym)
            logger.info("Auto-update refresh %s.%s %s", sym, mkt, name)
            row = refresh_symbol_lite(
                config,
                sym,
                mkt,
                build_features=build_features,
                skip_news=True,
                expected_date=expected,
            )
            if row.status == "ok":
                fetch_ok += 1
                fetched_symbols.append((sym, mkt, name, sector))
            elif row.status == "skipped":
                continue
            else:
                fetch_err += 1

        pending = discover_pending(data_root, output_root, limit=analyze_limit, skip_analyzed=True)
        seen = {p[0] for p in pending}
        for item in fetched_symbols:
            if item[0] not in seen:
                pending.append(item)
                seen.add(item[0])
        pending = pending[:analyze_limit]

        analyze_ok = analyze_err = 0
        if pending:
            df = run_incremental_batch(config, pending, optimize=optimize)
            analyze_ok = int((df["status"] == "ok").sum()) if not df.empty else 0
            analyze_err = int((df["status"] == "error").sum()) if not df.empty else 0

        summary_after = summarize_freshness(data_root, expected_date=expected)
        elapsed = (datetime.now(_SHANGHAI) - started).total_seconds()
        result = AutoUpdateResult(
            expected_trading_day=expected.isoformat(),
            fetch_attempted=len(candidates),
            fetch_ok=fetch_ok,
            fetch_error=fetch_err,
            analyze_attempted=len(pending),
            analyze_ok=analyze_ok,
            analyze_error=analyze_err,
            stale_before=summary_before.get("stale", 0),
            pending_before=analyze_before.get("pending", 0),
            duration_seconds=elapsed,
            status="ok",
            message="",
        )
        payload = {
            "last_run_at": started.isoformat(timespec="seconds"),
            "last_run_status": result.status,
            "last_fetch_ok": fetch_ok,
            "last_fetch_error": fetch_err,
            "last_analyze_ok": analyze_ok,
            "last_analyze_error": analyze_err,
            "duration_seconds": elapsed,
            **summary_after,
            "result": result.__dict__,
        }
        save_status_file(data_root, payload)
        logger.info(
            "Auto-update done: fetch ok=%d err=%d | analyze ok=%d err=%d | stale %d -> %d",
            fetch_ok,
            fetch_err,
            analyze_ok,
            analyze_err,
            summary_before.get("stale", 0),
            summary_after.get("stale", 0),
        )
        return result

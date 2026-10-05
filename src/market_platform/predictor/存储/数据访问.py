"""PostgreSQL 统一数据访问层。"""

from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import pandas as pd

from market_platform.predictor.核心.路径.项目 import data_dir, repo_root
from market_platform.predictor.存储.存储配置 import DatabaseSettings, load_database_settings
from market_platform.predictor.存储.数据库 import PostgresStore

logger = logging.getLogger(__name__)

_CLASSIFICATION_KEYS = {
    "directional_accuracy", "flat_recall", "high_conf_accuracy", "pred_flat_rate",
}


@lru_cache(maxsize=1)
def _settings() -> DatabaseSettings:
    return load_database_settings()


@lru_cache(maxsize=1)
def _store() -> PostgresStore:
    s = _settings()
    effective = DatabaseSettings(
        enabled=True,
        dsn=s.dsn,
        search_path=s.search_path,
        write_files=s.write_files,
        file_fallback=s.file_fallback,
        read_from_pg=s.read_from_pg,
        batch_size=s.batch_size,
        connect_timeout=s.connect_timeout,
    )
    return PostgresStore(effective)


def pg_enabled() -> bool:
    return _settings().enabled and bool(_settings().dsn)


def write_files_enabled() -> bool:
    return not pg_enabled() or _settings().write_files


def _use_pg_read() -> bool:
    return pg_enabled() and _settings().read_from_pg


def _use_pg_write() -> bool:
    return pg_enabled()


def init_schema() -> None:
    _store().init_schema()


def ping() -> bool:
    if not pg_enabled():
        return False
    return _store().ping()


def _legacy_data_dir() -> Path:
    legacy = repo_root() / "data"
    return legacy if legacy.exists() else data_dir()


def _series_file(name: str) -> Path:
    return data_dir() / "raw" / name


def _series_key(name: str) -> str:
    return Path(name).stem if name.endswith(".parquet") else name


def load_series_df(name: str) -> pd.DataFrame | None:
    key = _series_key(name)
    if _use_pg_read():
        df = _store().load_time_series(key)
        if not df.empty:
            return df
        snap = _store().load_dataset_snapshot(key)
        if not snap.empty:
            return snap
    if _settings().file_fallback:
        for base in (data_dir(), _legacy_data_dir()):
            path = base / "raw" / name
            if path.exists():
                df = pd.read_parquet(path)
                if "date" in df.columns:
                    df["date"] = pd.to_datetime(df["date"])
                return df
    return None


def save_series_df(name: str, df: pd.DataFrame) -> None:
    if df is None or df.empty:
        return
    key = _series_key(name)
    out = df.copy()
    if "date" in out.columns:
        out["date"] = pd.to_datetime(out["date"])
        if _use_pg_write():
            _store().upsert_time_series(key, out)
    elif _use_pg_write():
        _store().save_dataset_snapshot(key, out)

    if write_files_enabled():
        path = _series_file(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        out.to_parquet(path, index=False)


def load_index_features(
    index_code: str,
    *,
    columns: list[str] | None = None,
    start: str | None = None,
    end: str | None = None,
    limit: int | None = None,
) -> pd.DataFrame | None:
    if _use_pg_read():
        df = _store().load_index_features(
            index_code, columns=columns, start=start, end=end, limit=limit,
        )
        if df is not None and not df.empty:
            return df.sort_values("date").reset_index(drop=True)
    if _settings().file_fallback and not columns and limit is None and start is None and end is None:
        for base in (data_dir(), _legacy_data_dir()):
            path = base / "processed" / f"{index_code}_with_features.parquet"
            if path.exists():
                return pd.read_parquet(path).sort_values("date").reset_index(drop=True)
    return None


def load_index_feature_overview(index_code: str) -> dict[str, Any] | None:
    if _use_pg_read() and hasattr(_store(), "load_index_feature_overview"):
        ov = _store().load_index_feature_overview(index_code)
        if ov:
            return ov
    df = load_index_features(index_code)
    if df is None or df.empty:
        return None
    latest = df.iloc[-1].to_dict()
    labeled = df.dropna(subset=["label_direction"]) if "label_direction" in df.columns else df.iloc[0:0]
    return {
        "total_bars": len(df),
        "labeled_bars": len(labeled),
        "start_date": df["date"].min(),
        "end_date": df["date"].max(),
        "latest": latest,
        "feature_columns": len(df.columns),
    }


def save_index_features(index_code: str, df: pd.DataFrame) -> None:
    if df is None or df.empty:
        return
    out = df.copy()
    out["date"] = pd.to_datetime(out["date"])
    if _use_pg_write():
        _store().upsert_index_features(index_code, out)
    if write_files_enabled():
        path = data_dir() / "processed" / f"{index_code}_with_features.parquet"
        path.parent.mkdir(parents=True, exist_ok=True)
        out.to_parquet(path, index=False)


def _meta_file(dataset_key: str) -> Path:
    return data_dir() / "raw" / f"{dataset_key}_meta.json"


def load_fetch_meta(dataset_key: str) -> Any | None:
    if _use_pg_read():
        payload = _store().load_fetch_meta(dataset_key)
        if payload is not None:
            return payload
    if _settings().file_fallback:
        path = _meta_file(dataset_key)
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    return None


def save_fetch_meta(dataset_key: str, payload: Any) -> None:
    if _use_pg_write():
        _store().save_fetch_meta(dataset_key, payload)
    if write_files_enabled():
        path = _meta_file(dataset_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_llm_cache(
    *,
    model: str,
    prompt_version: str,
    prompt_digest: str,
    bar_date: str,
) -> dict[str, Any] | None:
    if _use_pg_read():
        cached = _store().load_llm_cache(
            model=model,
            prompt_version=prompt_version,
            prompt_digest=prompt_digest,
            bar_date=bar_date,
        )
        if cached is not None:
            return cached
    if _settings().file_fallback:
        cache_key = f"{model}_{prompt_version}_{prompt_digest}"
        fname = f"{cache_key}_date_{bar_date}.json"
        for base in (data_dir(), _legacy_data_dir()):
            path = base / "cache" / "llm_api" / fname
            if path.exists():
                return json.loads(path.read_text(encoding="utf-8"))
    return None


def save_llm_cache(
    *,
    model: str,
    prompt_version: str,
    prompt_digest: str,
    bar_date: str,
    response: dict[str, Any],
) -> None:
    if _use_pg_write():
        _store().save_llm_cache(
            model=model,
            prompt_version=prompt_version,
            prompt_digest=prompt_digest,
            bar_date=bar_date,
            response=response,
        )
    if write_files_enabled():
        cache_key = f"{model}_{prompt_version}_{prompt_digest}"
        path = data_dir() / "cache" / "llm_api" / f"{cache_key}_date_{bar_date}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(response, ensure_ascii=False, indent=2), encoding="utf-8")


def save_backtest_payload(payload: dict[str, Any], *, is_latest: bool = True) -> None:
    body = dict(payload)
    if _use_pg_write():
        _store().save_backtest_run(body, is_latest=is_latest)
    if write_files_enabled():
        store = data_dir() / "cache" / "backtest"
        store.mkdir(parents=True, exist_ok=True)
        (store / "latest.json").write_text(
            json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        runs = store / "runs"
        runs.mkdir(parents=True, exist_ok=True)
        run_id = body.get("run_id", "unknown")
        (runs / f"{run_id}.json").write_text(
            json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        summary = {
            k: body[k]
            for k in (
                "run_id", "model_name", "bar_freq", "strategy_fingerprint", "data_as_of",
                "saved_at", "accuracy", "total_return", "annualized_return", "sharpe_ratio",
                "max_drawdown", "win_rate", "total_trades", "periods_in_market",
                "benchmark_total_return", "excess_return", "avg_position",
                "cash_mgmt_enabled", "cash_return", "strategy_only_return",
            )
            if k in body
        }
        with (store / "registry.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(summary, ensure_ascii=False) + "\n")


def load_latest_backtest() -> dict[str, Any] | None:
    if _use_pg_read():
        bt = _store().load_latest_backtest()
        if bt:
            return bt
    if _settings().file_fallback:
        for base in (data_dir(), _legacy_data_dir()):
            path = base / "cache" / "backtest" / "latest.json"
            if path.exists():
                return json.loads(path.read_text(encoding="utf-8"))
    return None


def load_latest_backtest_metrics() -> dict[str, Any] | None:
    """最新回测指标（不含 signals）。"""
    if _use_pg_read():
        bt = _store().load_latest_backtest_metrics()
        if bt:
            return bt
    bt = load_latest_backtest()
    if not bt:
        return None
    slim = dict(bt)
    slim.pop("signals", None)
    return slim


def load_backtest_by_model_name(model_name: str) -> dict[str, Any] | None:
    if _use_pg_read():
        bt = _store().load_backtest_by_model_name(model_name)
        if bt:
            return bt
    return None


def load_backtest_metrics_by_model_name(model_name: str) -> dict[str, Any] | None:
    if _use_pg_read():
        return _store().load_backtest_metrics_by_model_name(model_name)
    return None


def load_backtest_run(run_id: str) -> dict[str, Any] | None:
    if _use_pg_read():
        bt = _store().load_backtest_run(run_id)
        if bt:
            return bt
    if _settings().file_fallback:
        for base in (data_dir(), _legacy_data_dir()):
            path = base / "cache" / "backtest" / "runs" / f"{run_id}.json"
            if path.exists():
                return json.loads(path.read_text(encoding="utf-8"))
    return None


def list_backtest_runs(limit: int = 30) -> list[dict[str, Any]]:
    if _use_pg_read():
        runs = _store().list_backtest_runs(limit=limit)
        if runs:
            return runs
    if _settings().file_fallback:
        for base in (data_dir(), _legacy_data_dir()):
            path = base / "cache" / "backtest" / "registry.jsonl"
            if path.exists():
                lines = path.read_text(encoding="utf-8").strip().splitlines()
                items = [json.loads(line) for line in lines if line.strip()]
                return list(reversed(items[-limit:]))
    return []


def _paper_state_key(provider: str) -> str:
    return f"paper_trading:{provider}"


def _default_paper_provider() -> str:
    from market_platform.predictor.配置 import get_config

    return get_config().get("model", {}).get("llm", {}).get("active_api") or "deepseek"


def _paper_trading_file(provider: str | None = None) -> Path:
    prov = provider or _default_paper_provider()
    return data_dir() / "cache" / "paper_trading" / f"state_{prov}.json"


def load_paper_trading_state(provider: str | None = None) -> dict[str, Any] | None:
    prov = provider or _default_paper_provider()
    key = _paper_state_key(prov)
    if _use_pg_read():
        payload = _store().load_app_state(key)
        if payload is not None:
            return payload
        # 兼容旧全局 key：迁到当前主渠道
        legacy = _store().load_app_state("paper_trading")
        if legacy is not None and prov == _default_paper_provider():
            body = dict(legacy)
            body["llm_provider"] = prov
            save_paper_trading_state(body, provider=prov)
            return body
    if _settings().file_fallback:
        path = _paper_trading_file(prov)
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        # 兼容旧 state.json
        for base in (data_dir(), _legacy_data_dir()):
            legacy_path = base / "cache" / "paper_trading" / "state.json"
            if legacy_path.exists() and prov == _default_paper_provider():
                body = json.loads(legacy_path.read_text(encoding="utf-8"))
                body["llm_provider"] = prov
                save_paper_trading_state(body, provider=prov)
                return body
    return None


def save_paper_trading_state(payload: dict[str, Any], provider: str | None = None) -> None:
    body = dict(payload)
    prov = provider or body.get("llm_provider") or _default_paper_provider()
    body["llm_provider"] = prov
    key = _paper_state_key(prov)
    if _use_pg_write():
        _store().save_app_state(key, body)
        # 主渠道同步写旧 key，避免旧脚本读空
        if prov == _default_paper_provider():
            _store().save_app_state("paper_trading", body)
    if write_files_enabled():
        path = _paper_trading_file(prov)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
        if prov == _default_paper_provider():
            legacy = data_dir() / "cache" / "paper_trading" / "state.json"
            legacy.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")


def _auto_update_file() -> Path:
    return data_dir() / "cache" / "auto_update" / "state.json"


def load_auto_update_state() -> dict[str, Any]:
    if _use_pg_read():
        payload = _store().load_app_state("auto_update")
        if payload is not None:
            return payload
    if _settings().file_fallback:
        for base in (data_dir(), _legacy_data_dir()):
            path = base / "cache" / "auto_update" / "state.json"
            if path.exists():
                return json.loads(path.read_text(encoding="utf-8"))
    return {}


def save_auto_update_state(payload: dict[str, Any]) -> None:
    body = dict(payload)
    if _use_pg_write():
        _store().save_app_state("auto_update", body)
    if write_files_enabled():
        path = _auto_update_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")


def migrate_files_to_pg() -> dict[str, int]:
    """将本地文件数据一次性导入 PostgreSQL。"""
    from market_platform.predictor.配置 import get_config

    stats = {
        "index_features": 0,
        "time_series": 0,
        "snapshots": 0,
        "llm_cache": 0,
        "backtest_runs": 0,
        "fetch_meta": 0,
    }
    init_schema()
    store = _store()
    cfg = get_config()
    index_code = cfg["data"]["index_code"]

    for base in (data_dir(), _legacy_data_dir()):
        proc = base / "processed" / f"{index_code}_with_features.parquet"
        if proc.exists():
            df = pd.read_parquet(proc)
            stats["index_features"] += store.upsert_index_features(index_code, df)
            break

    date_suffix = re.compile(r"_date_(?P<bar_date>\d{4}-\d{2}-\d{2})$")
    digest_suffix = re.compile(r"_(?P<digest>[a-f0-9]{12})$")
    seen_llm: set[tuple] = set()
    for base in (data_dir(), _legacy_data_dir()):
        llm_dir = base / "cache" / "llm_api"
        if not llm_dir.exists():
            continue
        for f in llm_dir.glob("*.json"):
            dm = date_suffix.search(f.stem)
            if not dm:
                continue
            bar_date = dm.group("bar_date")
            prefix = f.stem[: dm.start()]
            gm = digest_suffix.search(prefix)
            if not gm:
                continue
            digest = gm.group("digest")
            model_ver = prefix[: gm.start()]
            parts = model_ver.rsplit("_", 1)
            if len(parts) != 2:
                continue
            model, ver = parts
            key = (model, ver, digest, bar_date)
            if key in seen_llm:
                continue
            seen_llm.add(key)
            response = json.loads(f.read_text(encoding="utf-8"))
            store.save_llm_cache(
                model=model, prompt_version=ver, prompt_digest=digest,
                bar_date=bar_date, response=response,
            )
            stats["llm_cache"] += 1

    for base in (data_dir(), _legacy_data_dir()):
        raw = base / "raw"
        if not raw.exists():
            continue
        for path in raw.rglob("*.parquet"):
            rel = path.relative_to(raw)
            key = str(rel.with_suffix("")).replace("\\", "/")
            if key.startswith("stock_fundamentals/"):
                df = pd.read_parquet(path)
                store.save_dataset_snapshot(key, df)
                stats["snapshots"] += 1
                continue
            df = pd.read_parquet(path)
            if "date" in df.columns:
                df["date"] = pd.to_datetime(df["date"])
                stats["time_series"] += store.upsert_time_series(key, df)
            else:
                store.save_dataset_snapshot(key, df)
                stats["snapshots"] += 1

        for path in raw.glob("*_meta.json"):
            key = path.stem.replace("_meta", "")
            payload = json.loads(path.read_text(encoding="utf-8"))
            store.save_fetch_meta(key, payload)
            stats["fetch_meta"] += 1

    for base in (data_dir(), _legacy_data_dir()):
        latest = base / "cache" / "backtest" / "latest.json"
        if latest.exists():
            payload = json.loads(latest.read_text(encoding="utf-8"))
            store.save_backtest_run(dict(payload), is_latest=True)
            stats["backtest_runs"] += 1
            runs_dir = base / "cache" / "backtest" / "runs"
            if runs_dir.exists():
                for run_file in runs_dir.glob("*.json"):
                    if run_file.stem == payload.get("run_id"):
                        continue
                    run_payload = json.loads(run_file.read_text(encoding="utf-8"))
                    store.save_backtest_run(run_payload, is_latest=False)
                    stats["backtest_runs"] += 1
            break

    return stats

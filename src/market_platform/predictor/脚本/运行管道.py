"""
一键跑全流程的 pipeline 脚本

用法:
    python 后端/脚本/运行管道.py --step all
    market-predictor pipeline -- --step data
"""

from market_platform.predictor.核心.路径.项目 import data_dir, repo_root

import argparse
import sys
import time
from typing import Optional

try:
    from dotenv import load_dotenv
    load_dotenv(repo_root() / ".env")
except ImportError:
    pass

import pandas as pd
import numpy as np
from loguru import logger

from market_platform.predictor.配置 import get_config
from market_platform.predictor.核心.工具 import setup_logging
from market_platform.predictor.采集 import fetch_index_daily, load_local_data, load_or_fetch_index, clean_daily_data, build_feature_pipeline
from market_platform.predictor.采集 import train_val_split, log_split_summary, resample_bars, merge_extra_features
from market_platform.predictor.采集.拆分 import temporal_train_val_split
from market_platform.predictor.分析.推断 import LLMApiPredictor
from market_platform.predictor.分析.回测 import BacktestEngine, BacktestResult, summary_report
from market_platform.predictor.接口.回测存储 import save_backtest_result
from market_platform.predictor.存储.数据访问 import load_index_features, save_index_features


def get_llm_predictor() -> LLMApiPredictor:
    return LLMApiPredictor()


def step_data(force_refresh: bool = False):
    """数据获取 + 特征工程"""
    cfg = get_config()
    data_cfg = cfg["data"]

    logger.info("=" * 50)
    logger.info("📥 Step 1: 数据获取")
    logger.info("=" * 50)

    # 加载本地数据，过期则自动从网络刷新
    df = load_or_fetch_index(
        index_code=data_cfg["index_code"],
        start_date=data_cfg["start_date"],
        end_date=data_cfg.get("end_date", "auto"),
        source=data_cfg["source"],
        force_refresh=force_refresh,
    )

    # 清洗
    df = clean_daily_data(df)

    # 合并北向资金等额外因子（日频）
    df = merge_extra_features(df)

    # 周频 / 日频聚合
    df = resample_bars(df)

    # 特征工程
    df = build_feature_pipeline(df)

    # Transformer 衍生特征（需先训练 transformer checkpoint）
    from market_platform.predictor.分析.推断.变换器特征 import add_transformer_derived_features
    df = add_transformer_derived_features(df)

    # 保存处理好的数据
    save_index_features(data_cfg["index_code"], df)
    logger.info(f"💾 特征数据已保存: {data_cfg['index_code']}_with_features ({len(df)} 行)")

    return df


def _load_processed_df(index_code: str) -> pd.DataFrame | None:
    return load_index_features(index_code)


def step_explore():
    """数据探索分析"""
    cfg = get_config()
    index_code = cfg["data"]["index_code"]

    df = _load_processed_df(index_code)
    if df is None or df.empty:
        logger.warning("⚠️ 没有处理好的数据，先执行 step data")
        df = step_data()
    else:
        df = df.copy()

    logger.info("=" * 50)
    logger.info("🔍 数据探索")
    logger.info("=" * 50)

    logger.info(f"\n📊 数据概览:")
    logger.info(f"  总记录数: {len(df)}")
    logger.info(f"  时间范围: {df['date'].min()} → {df['date'].max()}")
    logger.info(f"  总列数:   {len(df.columns)}")
    logger.info(f"  特征列:   {len(df.columns) - 6}")

    if "label_direction" in df.columns:
        label_dist = df["label_direction"].value_counts()
        logger.info(f"\n📈 标签分布:")
        logger.info(f"  涨 (+1): {label_dist.get(1, 0)} ({label_dist.get(1, 0)/len(df):.1%})")
        logger.info(f"  平 (0):  {label_dist.get(0, 0)} ({label_dist.get(0, 0)/len(df):.1%})")
        logger.info(f"  跌 (-1): {label_dist.get(-1, 0)} ({label_dist.get(-1, 0)/len(df):.1%})")

    if get_config().get("model", {}).get("llm", {}).get("enabled", False):
        from market_platform.predictor.分析.提示词.构建器 import build_analysis_prompt
        prompt, row, _, _ = build_analysis_prompt(df.dropna(subset=["label_direction"]))
        logger.info(f"\n🧠 LLM Prompt 样例 (最近一期 {pd.Timestamp(row['date']).date()}):")
        for line in prompt.split("\n")[:25]:
            logger.info(f"  {line}")
        logger.info("  ...")

    logger.info(f"\n📋 特征列列表:")
    exclude = {"date", "label_direction", "label_return", "open", "high", "low", "close", "volume"}
    features = [c for c in df.columns if c not in exclude]
    for f in features:
        logger.info(f"  - {f}")

    return df


def step_train(model_name: str = "llm"):
    """训练模型"""
    cfg = get_config()
    index_code = cfg["data"]["index_code"]
    df = _load_processed_df(index_code)

    if df is None or df.empty:
        logger.error("❌ 请先执行 step data 获取数据")
        return None, None

    df = df.dropna(subset=["label_direction"]).reset_index(drop=True)

    val_ratio = cfg.get("model", {}).get("split", {}).get("val_ratio", 0.2)
    train_df, val_df, holdout = train_val_split(df, val_ratio=val_ratio)
    log_split_summary(train_df, val_df, holdout, prefix="📊 ")

    if model_name == "transformer":
        from market_platform.predictor.分析.推断 import TransformerPredictor

        logger.info("=" * 50)
        logger.info("🤖 Step 2: 训练 Transformer")
        logger.info("=" * 50)

        predictor = TransformerPredictor()
        metrics = predictor.train(train_df, val_df)
        predictor.save()
        from market_platform.predictor.分析.推断.变换器特征 import add_transformer_derived_features
        enriched = add_transformer_derived_features(df)
        save_index_features(index_code, enriched)
        logger.info(f"💾 已写回 Transformer 衍生特征: {index_code}_with_features")

        return predictor, metrics

    elif model_name == "llm":
        logger.info("=" * 50)
        logger.info("☁️ Step 2: LLM 使用 DeepSeek API（无需本地训练）")
        logger.info("=" * 50)

        predictor = LLMApiPredictor()
        metrics = predictor.train(df)
        return predictor, metrics

    else:
        logger.error(f"❌ 未知模型: {model_name}（可选: llm, transformer）")
        return None, None


def run_llm_backtest(
    model_name: str = "llm",
    test_start_date: Optional[str] = None,
    *,
    save: bool = True,
    api_cutoff_date: Optional[str] = None,
    provider: Optional[str] = None,
    is_latest: bool = True,
) -> BacktestResult | None:
    """运行 LLM 回测；save=False 时仅返回结果不写入存储。"""
    cfg = get_config()
    index_code = cfg["data"]["index_code"]
    df = _load_processed_df(index_code)

    if df is None or df.empty:
        logger.error("❌ 请先执行 step data 获取数据")
        return None

    df = df.dropna(subset=["label_direction"]).reset_index(drop=True)
    try:
        from market_platform.predictor.分析.特征.日信号复合 import is_weekly_decision_mode as _wdm

        if _wdm() and "label_week_direction" in df.columns:
            df = df.dropna(subset=["label_week_direction"]).reset_index(drop=True)
    except Exception:
        pass

    if model_name != "llm":
        logger.error(f"❌ run_llm_backtest 仅支持 llm，收到: {model_name}")
        return None

    llm_cfg = cfg.get("model", {}).get("llm", {})
    strategy_cfg = cfg.get("backtest", {}).get("strategy", {})
    dual_freq = strategy_cfg.get("dual_freq_gate", False)
    try:
        from market_platform.predictor.分析.特征.日信号复合 import is_weekly_decision_mode

        weekly_decision = is_weekly_decision_mode()
    except Exception:
        weekly_decision = bool((strategy_cfg.get("weekly_decision") or {}).get("enabled"))
    channel = provider or llm_cfg.get("active_api") or "deepseek"
    cached_predictor: dict = {"api": None}

    from market_platform.predictor.分析.推断.集合元模型 import MetaEnsemblePredictor, is_ensemble_provider
    from market_platform.predictor.分析.推断.语言模型因子 import LlmFactorPredictor, is_llm_factor_provider
    from market_platform.predictor.分析.推断.角色调仓 import RoleEnsemblePredictor, is_role_provider
    from market_platform.predictor.分析.推断.擅长因子 import SpecialtyFactorPredictor, is_specialty_provider
    from market_platform.predictor.分析.推断.纪律策略 import DisciplineRulePredictor, is_discipline_provider

    use_ensemble = is_ensemble_provider(channel)
    use_llm_factor = is_llm_factor_provider(channel)
    use_role = is_role_provider(channel)
    use_specialty = is_specialty_provider(channel)
    use_discipline = is_discipline_provider(channel)

    def train_fn(train_df):
        api_cached = cached_predictor["api"] is not None
        retain_api = api_cached and not llm_cfg.get("backtest_retrain", False)
        if retain_api:
            return cached_predictor["api"]

        if use_discipline:
            predictor = DisciplineRulePredictor()
            predictor.reset_cache_stats()
            predictor.bind_dataframe(df)
            predictor.train(train_df)
        elif use_specialty:
            predictor = SpecialtyFactorPredictor()
            predictor.reset_cache_stats()
            predictor.bind_dataframe(df)
            predictor.train(train_df)
        elif use_role:
            predictor = RoleEnsemblePredictor()
            predictor.reset_cache_stats()
            predictor.bind_dataframe(df)
            predictor.train(train_df)
        elif use_ensemble:
            predictor = MetaEnsemblePredictor()
            predictor.reset_cache_stats()
            predictor.bind_dataframe(df)
            predictor.train(train_df)
        elif use_llm_factor:
            predictor = LlmFactorPredictor()
            predictor.reset_cache_stats()
            predictor.bind_dataframe(df)
            predictor.train(train_df)
        else:
            predictor = LLMApiPredictor(provider=channel)
            predictor.set_api_cutoff_date(api_cutoff_date)
            # 回测只读 llm_cache，禁止未命中时把新 digest 写回污染库
            predictor.set_cache_write(False)
            predictor.reset_cache_stats()
            predictor.bind_dataframe(df)
            predictor.train(train_df)
        cached_predictor["api"] = predictor
        return predictor

    def predict_fn(model, lookback_df):
        last_row = lookback_df.iloc[-1:]
        if len(last_row) == 0:
            return 0, np.array([1 / 3, 1 / 3, 1 / 3]), None
        model.bind_dataframe(lookback_df)
        week_pred = None
        # 周频硬切 / 双频：统一走 predict_bundle（周向为主）
        if (weekly_decision or dual_freq) and hasattr(model, "predict_bundle"):
            pred, proba, week_pred = model.predict_bundle(last_row)
        else:
            pred = model.predict(last_row)[0]
            proba = model.predict_proba(last_row)[0]
            if dual_freq:
                from market_platform.predictor.分析.提示词.构建器 import infer_week_direction
                week_pred = infer_week_direction(last_row.iloc[0])
        return pred, proba, week_pred

    override = {"test_start_date": test_start_date} if test_start_date else {}
    override["provider"] = channel
    engine = BacktestEngine(config_override=override)
    if test_start_date:
        logger.info(f"📅 回测样本外起点: {test_start_date}")
    from market_platform.predictor.接口.回测存储 import expected_model_label

    model_label = expected_model_label(provider=channel)
    if use_discipline:
        logger.info(f"📈 纪律策略回测: {channel} → {model_label}")
    elif use_specialty:
        logger.info(f"📈 擅长因子综合回测: {channel} → {model_label}")
    elif use_role:
        logger.info(f"📈 角色化调仓回测: {channel} → {model_label}")
    elif use_ensemble:
        logger.info(f"📈 LLM 集合元模型回测: {channel} → {model_label}")
    elif use_llm_factor:
        logger.info(f"📈 LLM因子模型回测: {channel} → {model_label}")
    else:
        logger.info(f"📈 LLM 渠道回测: {channel} → {model_label}")
    result = engine.run(df, train_fn, predict_fn, model_name=model_label)
    if result is not None and save:
        save_backtest_result(result, model_label, is_latest=is_latest)
    if result is not None and cached_predictor.get("api"):
        result.llm_cache_stats = cached_predictor["api"].cache_stats()  # type: ignore[attr-defined]
    return result


def run_transformer_backtest(
    test_start_date: Optional[str] = None,
    *,
    save: bool = True,
    is_latest: bool = False,
) -> BacktestResult | None:
    """仅用量价 Transformer 信号跑仓位回测（不调 LLM）。"""
    cfg = get_config()
    index_code = cfg["data"]["index_code"]
    df = _load_processed_df(index_code)
    if df is None or df.empty:
        logger.error("❌ 请先执行 step data / train transformer")
        return None

    needed = ["tf_pred_direction", "tf_proba_up", "tf_proba_down", "tf_proba_flat"]
    if any(c not in df.columns for c in needed):
        from market_platform.predictor.分析.推断.变换器特征 import add_transformer_derived_features

        df = add_transformer_derived_features(df)
        save_index_features(index_code, df)

    df = df.dropna(subset=["label_direction"]).reset_index(drop=True)
    if df["tf_pred_direction"].isna().all():
        logger.error("❌ 无有效 Transformer 预测，请先 --step train --model transformer")
        return None

    # 预计算按日索引，避免逐日重推理
    by_date = {
        pd.Timestamp(r["date"]).normalize(): r
        for _, r in df.iterrows()
    }

    def train_fn(train_df):
        return {"ready": True}

    def predict_fn(model, lookback_df):
        last = lookback_df.iloc[-1]
        d = pd.Timestamp(last["date"]).normalize()
        row = by_date.get(d, last)
        pred = int(row["tf_pred_direction"]) if pd.notna(row.get("tf_pred_direction")) else 0
        pu = float(row["tf_proba_up"]) if pd.notna(row.get("tf_proba_up")) else 1 / 3
        pf = float(row["tf_proba_flat"]) if pd.notna(row.get("tf_proba_flat")) else 1 / 3
        pdn = float(row["tf_proba_down"]) if pd.notna(row.get("tf_proba_down")) else 1 / 3
        proba = np.array([pdn, pf, pu], dtype=float)
        s = proba.sum()
        if s <= 0 or not np.isfinite(s):
            proba = np.array([1 / 3, 1 / 3, 1 / 3], dtype=float)
        else:
            proba = proba / s
        return pred, proba, None

    override = {"test_start_date": test_start_date} if test_start_date else {}
    override["provider"] = "transformer"
    engine = BacktestEngine(config_override=override)
    model_label = "Transformer@price-volume"
    logger.info(f"📈 Transformer 量价回测 → {model_label}")
    result = engine.run(df, train_fn, predict_fn, model_name=model_label)
    if result is not None and save:
        save_backtest_result(result, model_label, is_latest=is_latest)
    return result


def step_llm_cache(
    *,
    provider: Optional[str] = None,
    all_providers: bool = False,
    limit: int = 30,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> dict:
    """分批拉取 LLM 预测写入数据库 llm_cache（命中则跳过，不必一次全量）。

    周频决策模式下：仅自然周 week_end 构建 `build_weekly_decision_prompt` 并预热，
    与回测引擎调用路径一致。

    典型用法（每天/每批补一点，覆盖后再跑回测）：
      market-predictor pipeline -- --step llm-cache --all-providers --limit 30
      market-predictor pipeline -- --step llm-cache --provider tuzi --limit 50 --start 2020-01-01
    """
    from market_platform.predictor.分析.推断.语言模型预测 import list_llm_providers, provider_model_id
    from market_platform.predictor.分析.提示词.构建器 import (
        build_prompt_from_row,
        get_prompt_lookback,
        is_blind_prompt_enabled,
        _resolve_index_meta,
    )
    from market_platform.predictor.分析.特征.日信号复合 import (
        build_weekly_decision_prompt,
        is_week_end,
        is_weekly_decision_mode,
    )
    from market_platform.predictor.配置 import get_config as _gc

    cfg = _gc()
    index_code = cfg["data"]["index_code"]
    df = _load_processed_df(index_code)
    if df is None or df.empty:
        logger.error("❌ 请先执行 --step data")
        return {"status": "failed", "message": "无特征数据"}

    df = df.dropna(subset=["label_direction"]).sort_values("date").reset_index(drop=True)
    bt_start = start_date or cfg.get("backtest", {}).get("test_start_date")
    if bt_start:
        df = df[df["date"] >= pd.Timestamp(bt_start)].reset_index(drop=True)
    if end_date:
        df = df[df["date"] <= pd.Timestamp(end_date)].reset_index(drop=True)

    if all_providers:
        from market_platform.predictor.分析.推断.集合元模型 import is_ensemble_provider
        from market_platform.predictor.分析.推断.语言模型因子 import is_llm_factor_provider
        from market_platform.predictor.分析.推断.角色调仓 import is_role_provider
        from market_platform.predictor.分析.推断.擅长因子 import is_specialty_provider
        from market_platform.predictor.分析.推断.纪律策略 import is_discipline_provider
        providers = [
            p for p in list_llm_providers()
            if not is_ensemble_provider(p)
            and not is_llm_factor_provider(p)
            and not is_role_provider(p)
            and not is_specialty_provider(p)
            and not is_discipline_provider(p)
        ]
    else:
        providers = [provider or cfg.get("model", {}).get("llm", {}).get("active_api") or "deepseek"]
        from market_platform.predictor.分析.推断.集合元模型 import is_ensemble_provider
        from market_platform.predictor.分析.推断.语言模型因子 import is_llm_factor_provider
        from market_platform.predictor.分析.推断.角色调仓 import is_role_provider
        from market_platform.predictor.分析.推断.擅长因子 import is_specialty_provider
        from market_platform.predictor.分析.推断.纪律策略 import is_discipline_provider
        providers = [
            p for p in providers
            if not is_ensemble_provider(p)
            and not is_llm_factor_provider(p)
            and not is_role_provider(p)
            and not is_specialty_provider(p)
            and not is_discipline_provider(p)
        ]
        if not providers:
            return {"status": "skipped", "message": "元模型渠道无需预热 LLM 缓存"}

    lookback = get_prompt_lookback()
    blind = is_blind_prompt_enabled(for_live=False)
    weekly = is_weekly_decision_mode()
    index_name, _ = _resolve_index_meta()
    index_name = index_name or "沪深300"
    dates = pd.to_datetime(df["date"])
    if weekly:
        week_end_mask = [
            is_week_end(pd.Timestamp(d), dates) for d in dates
        ]
        target_idx = [i for i, m in enumerate(week_end_mask) if m]
        logger.info(
            f"🧊 周频预热：共 {len(target_idx)} 个 week_end（全表 {len(df)} 日）"
        )
    else:
        target_idx = list(range(len(df)))

    reports: dict[str, dict] = {}

    for name in providers:
        model = provider_model_id(name)
        predictor = LLMApiPredictor(provider=name)
        predictor.reset_cache_stats()
        fetched = 0
        skipped = 0
        errors = 0
        hard_streak = 0
        first_miss = None
        last_ok = None
        logger.info("=" * 50)
        logger.info(
            f"🧊 预热 LLM 缓存: {name} · {model} · limit={limit} · "
            f"{'week_ends' if weekly else 'bars'}={len(target_idx)}"
        )
        logger.info("=" * 50)

        for i in target_idx:
            if fetched >= max(1, int(limit)):
                break
            row = df.iloc[i]
            bar_date = pd.Timestamp(row["date"]).strftime("%Y-%m-%d")
            if weekly:
                prompt, _meta = build_weekly_decision_prompt(
                    df,
                    pd.Timestamp(row["date"]),
                    index_name=index_name,
                    blind=blind,
                )
            else:
                prompt = build_prompt_from_row(
                    row, df=df, idx=i, blind=blind, index_name=None, bar_unit=None,
                )
            # 已有缓存则跳过（不计入 limit）
            cached = predictor._load_cached(prompt, bar_date=bar_date)
            if cached is not None:
                skipped += 1
                continue
            if first_miss is None:
                first_miss = bar_date
            try:
                predictor.analyze_prompt(prompt, bar_date=bar_date, for_live=False)
                fetched += 1
                last_ok = bar_date
                hard_streak = 0
                if fetched % 5 == 0 or fetched == 1:
                    logger.info(f"  [{name}/{model}] 新拉取 {fetched}/{limit} · 最近 {bar_date}")
            except Exception as e:
                errors += 1
                logger.warning(f"  [{name}/{model}] {bar_date} 失败: {e}")
                err_s = str(e)
                transient = any(
                    k in err_s
                    for k in (
                        "负载较高", "429", "500", "502", "503", "504",
                        "upstream_error", "请稍候重试", "timeout", "Timeout",
                        "速率", "rate limit", "timed out",
                    )
                )
                if transient:
                    hard_streak = 0
                    pace = float((predictor.api_cfg or {}).get("min_interval_sec") or 0) or 8.0
                    wait = max(8.0, pace * 3.0)
                    if "429" in err_s or "速率" in err_s:
                        wait = max(wait, 30.0)
                    time.sleep(wait)
                else:
                    hard_streak += 1
                # 连续硬失败（解析/拒绝）过多才停；瞬时上游错误继续
                if hard_streak >= 5:
                    logger.error(f"  [{name}/{model}] 连续硬失败过多，停止该渠道本批")
                    break

        stats = predictor.cache_stats()
        report = {
            "provider": name,
            "model": model,
            "bars_scanned": len(target_idx),
            "weekly_decision": weekly,
            "cache_hits_skipped": skipped,
            "fetched": fetched,
            "errors": errors,
            "first_miss": first_miss,
            "last_fetched": last_ok,
            "lookback": lookback,
            "llm_cache_stats": stats,
            "done": fetched < max(1, int(limit)) and hard_streak < 5,
        }
        reports[name] = report
        logger.info(
            f"✅ {name}({model}): 跳过已有 {skipped}，新写入 {fetched}，错误 {errors}"
            + (" · 本渠道本区间已齐" if report["done"] else " · 仍有缺口，请继续跑同命令")
        )

    return {"status": "ok", "limit": limit, "channels": reports, "weekly_decision": weekly}


def run_all_provider_backtests(
    *,
    primary: str = "deepseek",
    test_start_date: Optional[str] = None,
) -> dict[str, BacktestResult | None]:
    """各 LLM 渠道全量回测入库；primary 渠道标记为 is_latest。
    集合元模型（ensemble）始终排在成员渠道之后。"""
    from market_platform.predictor.分析.推断.语言模型预测 import list_llm_providers
    from market_platform.predictor.分析.推断.集合元模型 import is_ensemble_provider
    from market_platform.predictor.分析.推断.语言模型因子 import is_llm_factor_provider
    from market_platform.predictor.分析.推断.角色调仓 import is_role_provider
    from market_platform.predictor.分析.推断.擅长因子 import is_specialty_provider
    from market_platform.predictor.分析.推断.纪律策略 import is_discipline_provider

    providers = list_llm_providers()
    if primary not in providers and providers:
        primary = providers[0]
    results: dict[str, BacktestResult | None] = {}
    members = [
        p for p in providers
        if not is_ensemble_provider(p)
        and not is_llm_factor_provider(p)
        and not is_role_provider(p)
        and not is_specialty_provider(p)
        and not is_discipline_provider(p)
    ]
    ensembles = [p for p in providers if is_ensemble_provider(p)]
    role_models = [p for p in providers if is_role_provider(p)]
    specialty_models = [p for p in providers if is_specialty_provider(p)]
    discipline_models = [p for p in providers if is_discipline_provider(p)]
    factor_models = [p for p in providers if is_llm_factor_provider(p)]
    # 成员先跑 → ensemble → role → specialty → discipline → llm_factor
    ordered: list[str] = []
    ordered.extend([p for p in members if p != primary])
    if primary in members:
        ordered.append(primary)
    ordered.extend([p for p in ensembles if p != primary])
    if primary in ensembles:
        ordered.append(primary)
    ordered.extend([p for p in role_models if p != primary])
    if primary in role_models:
        ordered.append(primary)
    ordered.extend([p for p in specialty_models if p != primary])
    if primary in specialty_models:
        ordered.append(primary)
    ordered.extend([p for p in discipline_models if p != primary])
    if primary in discipline_models:
        ordered.append(primary)
    ordered.extend([p for p in factor_models if p != primary])
    if primary in factor_models:
        ordered.append(primary)
    # 去重保序
    seen = set()
    ordered = [p for p in ordered if not (p in seen or seen.add(p))]

    for name in ordered:
        logger.info("=" * 50)
        logger.info(f"📡 渠道回测入库: {name}（primary={name == primary}）")
        logger.info("=" * 50)
        results[name] = run_llm_backtest(
            "llm",
            test_start_date=test_start_date,
            save=True,
            provider=name,
            is_latest=(name == primary),
        )
        stats = getattr(results[name], "llm_cache_stats", None) if results[name] else None
        logger.info(f"渠道 {name} 完成 cache={stats}")
    return results


def step_backtest(
    model_name: str = "llm",
    test_start_date: Optional[str] = None,
    *,
    provider: Optional[str] = None,
    all_providers: bool = False,
    primary: str = "deepseek",
):
    """回测评估"""
    logger.info("=" * 50)
    logger.info("📈 Step 3: 滚动窗口回测")
    logger.info("=" * 50)

    if model_name == "transformer":
        return run_transformer_backtest(
            test_start_date=test_start_date,
            save=True,
            is_latest=False,
        )

    if model_name == "llm":
        if all_providers:
            return run_all_provider_backtests(primary=primary, test_start_date=test_start_date)
        return run_llm_backtest(
            model_name,
            test_start_date,
            save=True,
            provider=provider,
            is_latest=True,
        )

    logger.error(f"❌ 未知模型: {model_name}（可选: llm, transformer）")
    return None


def main(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(description="Market Predictor Pipeline")
    parser.add_argument("--step", type=str, default="all",
                        choices=["all", "data", "explore", "train", "backtest", "llm-cache"])
    parser.add_argument("--model", type=str, default="llm",
                        choices=["transformer", "llm"])
    parser.add_argument("--backtest-from", type=str, default=None,
                        help="覆盖 test_start_date，仅重跑该日期之后的样本外区间（省 API）")
    parser.add_argument("--provider", type=str, default=None,
                        help="LLM 渠道 id：deepseek / tuzi / tuzi_sonnet / tuzi_gemini / tuzi_grok / kimi / glm / minimax / qwen / doubao")
    parser.add_argument("--all-providers", action="store_true",
                        help="对配置中全部 LLM 渠道分别回测/预热")
    parser.add_argument("--primary", type=str, default="deepseek",
                        help="--all-providers 回测时标记 is_latest 的主渠道（默认 deepseek）")
    parser.add_argument("--limit", type=int, default=30,
                        help="llm-cache 每渠道本批最多新拉取条数（默认 30）")
    parser.add_argument("--start", type=str, default=None,
                        help="llm-cache 起始日（默认 backtest.test_start_date）")
    parser.add_argument("--end", type=str, default=None,
                        help="llm-cache 结束日（默认数据末日）")
    args = parser.parse_args(argv)

    setup_logging()

    if args.step == "all":
        step_data()
        step_explore()
        step_train(args.model)
        step_backtest(
            args.model,
            test_start_date=args.backtest_from,
            provider=args.provider,
            all_providers=args.all_providers,
            primary=args.primary,
        )
    elif args.step == "data":
        step_data()
    elif args.step == "explore":
        step_explore()
    elif args.step == "train":
        step_train(args.model)
    elif args.step == "llm-cache":
        step_llm_cache(
            provider=args.provider,
            all_providers=args.all_providers,
            limit=args.limit,
            start_date=args.start or args.backtest_from,
            end_date=args.end,
        )
    elif args.step == "backtest":
        step_backtest(
            args.model,
            test_start_date=args.backtest_from,
            provider=args.provider,
            all_providers=args.all_providers,
            primary=args.primary,
        )


if __name__ == "__main__":
    main()

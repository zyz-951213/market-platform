"""
DeepSeek 云端 API 预测器 — 综合研判模式
"""

from __future__ import annotations

from market_platform.predictor.核心.路径.项目 import checkpoints_dir, data_dir

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import numpy as np
import pandas as pd
from loguru import logger

from market_platform.predictor.分析.推断.校准器 import PredictionCalibrator
from market_platform.predictor.分析.提示词.构建器 import parse_direction_json
from market_platform.predictor.分析.提示词.构建器 import (
    LABEL_TO_TEXT,
    build_analysis_prompt,
    build_prompt_from_row,
    get_llm_config,
    get_prompt_config,
    get_prompt_lookback,
    infer_week_direction,
    is_blind_prompt_enabled,
    is_dual_frequency_enabled,
)
from market_platform.predictor.存储.数据访问 import load_llm_cache, save_llm_cache


def _get_api_config(provider: Optional[str] = None) -> dict:
    """根据 active_api 或指定 provider 返回 API 配置。"""
    llm_cfg = get_llm_config()
    active = provider or llm_cfg.get("active_api", "deepseek")
    providers = llm_cfg.get("providers", {})
    if active in providers:
        return dict(providers[active])
    return dict(llm_cfg.get("api", {}))


def list_llm_providers(*, include_disabled: bool = False) -> list[str]:
    llm_cfg = get_llm_config()
    providers = llm_cfg.get("providers", {})
    if not providers:
        return ["deepseek"]
    if include_disabled:
        return list(providers.keys())
    out: list[str] = []
    for name, cfg in providers.items():
        if isinstance(cfg, dict) and cfg.get("enabled") is False:
            continue
        out.append(name)
    return out or ["deepseek"]


def list_member_providers() -> list[str]:
    """可调 API / 参与集合的底层渠道（排除元模型渠道，且跳过 enabled=false）。"""
    from market_platform.predictor.分析.推断.集合元模型 import is_ensemble_provider
    from market_platform.predictor.分析.推断.语言模型因子 import is_llm_factor_provider
    from market_platform.predictor.分析.推断.角色调仓 import is_role_provider
    from market_platform.predictor.分析.推断.擅长因子 import is_specialty_provider
    from market_platform.predictor.分析.推断.纪律策略 import is_discipline_provider

    return [
        p for p in list_llm_providers()
        if not is_ensemble_provider(p)
        and not is_llm_factor_provider(p)
        and not is_role_provider(p)
        and not is_specialty_provider(p)
        and not is_discipline_provider(p)
    ]


def provider_model_id(provider: Optional[str] = None) -> str:
    """渠道对应的完整 model 名（版本标识）。"""
    api_cfg = _get_api_config(provider)
    return str(api_cfg.get("model") or provider or "unknown")


def list_llm_channels(*, include_meta: bool = False) -> list[dict]:
    """渠道目录：id + 完整 model 名，供 UI/API 标注。

    默认只含可调 API 成员渠道（排除 ensemble / llm_factor 等元模型）。
    """
    llm_cfg = get_llm_config()
    providers = list_llm_providers() if include_meta else list_member_providers()
    active = llm_cfg.get("active_api", providers[0] if providers else "deepseek")
    items: list[dict] = []
    for name in providers:
        model = provider_model_id(name)
        items.append({
            "id": name,
            "model": model,
            "label": model,  # 界面一律显示完整 model 名
            "is_active": name == active,
        })
    return items


class LLMApiPredictor:
    """通过 DeepSeek Chat API 做方向研判"""

    CLASS_ORDER = [-1, 0, 1]

    def __init__(self, params: Optional[dict] = None, provider: Optional[str] = None):
        self.cfg = dict(params or get_llm_config())
        self.provider_name = provider or self.cfg.get("active_api", "deepseek")
        self.api_cfg = _get_api_config(self.provider_name)
        self._client = None
        self._is_trained = True  # API 模式无需本地训练
        self._last_result: Dict[str, Any] = {}
        self._df: Optional[pd.DataFrame] = None
        self.calibrator = PredictionCalibrator()
        self._cache_hits = 0
        self._cache_misses = 0
        self._api_cutoff_date: Optional[str] = None
        self._cache_write: bool = True  # False：只读缓存（回测禁止污染 llm_cache）
        # 只读缓存模式:未命中即报错,**绝不调用 API**(额度有限时的安全阀)
        self._cache_only: bool = False
        self._last_request_mono: float = 0.0

    def reset_cache_stats(self) -> None:
        self._cache_hits = 0
        self._cache_misses = 0

    def cache_stats(self) -> dict[str, int]:
        return {"hits": self._cache_hits, "misses": self._cache_misses}

    def set_api_cutoff_date(self, cutoff: Optional[str]) -> None:
        """增量更新：该日期及更早的 bar 仅读 DB/文件缓存，禁止调 API。"""
        self._api_cutoff_date = cutoff

    def set_cache_write(self, enabled: bool) -> None:
        """是否允许将新推理写入 llm_cache。回测应关闭，仅 llm-cache 预热写入。"""
        self._cache_write = bool(enabled)

    def set_cache_only(self, enabled: bool = True) -> None:
        """只读缓存:缓存未命中时**抛错**而不是调用 API。

        用途:LLM 额度有限时跑回测/复盘,确保只重放 `llm_cache` 里的既有结果,
        不会因为某个 bar 缺缓存而默默产生 API 费用。
        """
        self._cache_only = bool(enabled)

    def _throttle_before_request(self) -> None:
        """按渠道 min_interval_sec 限速，避免打穿速率配额。"""
        interval = float(self.api_cfg.get("min_interval_sec") or 0.0)
        if interval <= 0:
            return
        now = time.monotonic()
        wait = self._last_request_mono + interval - now
        if wait > 0:
            time.sleep(wait)
        self._last_request_mono = time.monotonic()

    def _cache_dir(self) -> Path:
        path = data_dir() / "cache" / "llm_api"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _prompt_digest(self, prompt: str) -> str:
        return hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:12]

    def _cache_path(self, prompt: str, bar_date: Optional[str] = None) -> Path:
        model = self.api_cfg.get("model", "deepseek-v4-pro")
        ver = get_prompt_config().get("version", "v1")
        digest = self._prompt_digest(prompt) if prompt else "noprompt"
        # 缓存键包含模型名，不同模型的缓存互不干扰
        cache_key = f"{model}_{ver}_{digest}"
        if bar_date:
            return self._cache_dir() / f"{cache_key}_date_{bar_date}.json"
        return self._cache_dir() / f"{cache_key}.json"

    def _load_cached(self, prompt: str, bar_date: Optional[str] = None) -> Optional[Dict[str, Any]]:
        if not bar_date:
            cache_path = self._cache_path(prompt, bar_date=bar_date)
            if cache_path.exists():
                return json.loads(cache_path.read_text(encoding="utf-8"))
            return None
        model = self.api_cfg.get("model", "deepseek-v4-pro")
        ver = get_prompt_config().get("version", "v1")
        digest = self._prompt_digest(prompt) if prompt else "noprompt"
        return load_llm_cache(
            model=model,
            prompt_version=ver,
            prompt_digest=digest,
            bar_date=bar_date,
        )

    def _save_cached(self, payload: Dict[str, Any], prompt: str, bar_date: Optional[str] = None):
        if not bar_date:
            cache_path = self._cache_path(prompt, bar_date=bar_date)
            cache_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8",
            )
            return
        model = self.api_cfg.get("model", "deepseek-v4-pro")
        ver = get_prompt_config().get("version", "v1")
        digest = self._prompt_digest(prompt) if prompt else "noprompt"
        save_llm_cache(
            model=model,
            prompt_version=ver,
            prompt_digest=digest,
            bar_date=bar_date,
            response=payload,
        )

    def _get_client(self):
        if self._client is not None:
            return self._client
        try:
            from openai import OpenAI
        except ImportError as e:
            raise ImportError("请安装: pip install openai") from e

        env_name = self.api_cfg.get("api_key_env", "DEEPSEEK_API_KEY")
        api_key = (
            self.api_cfg.get("api_key")
            or os.environ.get(env_name)
        )
        if isinstance(api_key, str):
            api_key = api_key.strip()
        if not api_key:
            raise RuntimeError(
                f"未配置 API Key（渠道 {self.provider_name}），"
                f"请在 .env 设置 {env_name}，或在配置 providers.{self.provider_name}.api_key / 本地.yaml 中填写"
            )

        base_url = self.api_cfg.get("base_url", "https://api.deepseek.com")
        # 中转偶发挂死无响应；必须有超时，否则回测进程会假死
        timeout = float(self.api_cfg.get("timeout", 120.0))
        try:
            from httpx import Timeout as HttpxTimeout

            to = HttpxTimeout(timeout, connect=min(15.0, timeout), write=30.0, pool=15.0)
        except Exception:
            to = timeout
        self._client = OpenAI(api_key=api_key, base_url=base_url, timeout=to)
        return self._client

    def _system_prompt(self, for_live: bool = False) -> str:
        prompt_cfg = get_prompt_config()
        model_l = str(self.api_cfg.get("model", "")).lower()
        weekly = False
        try:
            from market_platform.predictor.分析.特征.日信号复合 import is_weekly_decision_mode

            weekly = is_weekly_decision_mode()
        except Exception:
            weekly = False
        # Grok：人设易触发 Refusal；MiniMax-M3：长分析师 system 会无视 json_object 狂写 Markdown
        if (
            self.provider_name in ("tuzi_grok", "minimax")
            or "grok" in model_l
            or model_l.startswith("minimax")
        ):
            if weekly:
                base = (
                    "根据用户消息中本周日频信号复合，判断随后一周（约5个交易日累计）涨/跌/平，并给出幅度点估计。"
                    "涨：累计收益>+0.75%；跌：累计收益<-0.75%；否则平。"
                    "只使用用户提供的数据，禁止编造。"
                    '只输出 JSON：{"week_direction":"涨|跌|平","week_return_pct":0.0,'
                    '"week_confidence":0-1,"reason":"简短"}，'
                    "不要 markdown 或思考过程。"
                )
            elif is_dual_frequency_enabled():
                base = (
                    "根据用户消息中的指数行情与因子数据，同时判断下一交易日与随后一周的涨/跌/平。"
                    "日频：涨>+0.30%，跌<-0.30%，否则平；周频（5个交易日累计）：涨>+0.75%，跌<-0.75%，否则平。"
                    "只使用用户提供的数据，禁止编造；日周可不一致。"
                    "只输出一个 JSON 对象，不要 markdown、表格、标题或思考过程。"
                )
            else:
                base = (
                    "根据用户消息中的指数行情与因子数据，判断下一交易日涨/跌/平"
                    "（涨>+0.30%，跌<-0.30%，否则平）。"
                    "只使用用户提供的数据，禁止编造未给出的信息。"
                    "只输出一个 JSON 对象，不要 markdown、表格、标题或思考过程。"
                )
        else:
            if weekly and prompt_cfg.get("api_system_weekly"):
                base = prompt_cfg.get("api_system_weekly")
            else:
                base = prompt_cfg.get(
                    "api_system",
                    prompt_cfg.get(
                        "system",
                        "你是专业的A股指数量化分析师。请综合技术面、基本面、资金面与趋势信息做出审慎判断。"
                        "禁止编造未提供的数据；数据缺失时应在理由中说明。",
                    ),
                )
        if is_blind_prompt_enabled(for_live):
            base += (
                "\n【盲测附加约束】用户未提供公历日期或历史时期名称。"
                "不得根据训练记忆识别著名行情；仅依据相对走势与当期因子独立判断。"
            )
        return base

    def _user_prompt(self, prompt: str) -> str:
        """部分渠道把 JSON 格式要求附在 user 末尾，避免 system 约束被忽略。"""
        model_l = str(self.api_cfg.get("model", "")).lower()
        need_json_tail = (
            self.provider_name in ("tuzi_grok", "tuzi_gemini", "minimax", "kimi", "glm")
            or "grok" in model_l
            or model_l.startswith("minimax")
            or model_l.startswith("kimi")
            or model_l.startswith("glm")
        )
        if not need_json_tail:
            return prompt
        weekly = False
        try:
            from market_platform.predictor.分析.特征.日信号复合 import is_weekly_decision_mode

            weekly = is_weekly_decision_mode()
        except Exception:
            weekly = False
        if weekly:
            return (
                f"{prompt.rstrip()}\n\n"
                "请只输出如下 JSON（reason≤40字，不要 markdown 围栏，不要思考过程）：\n"
                '{"week_direction":"涨|跌|平","week_return_pct":0.0,'
                '"week_confidence":0.0-1.0,"reason":"简短理由"}'
            )
        return (
            f"{prompt.rstrip()}\n\n"
            "请只输出如下 JSON（reasoning≤40字，不要 markdown 围栏，不要思考过程）：\n"
            '{"direction":"涨|跌|平","confidence":0.0-1.0,'
            '"week_direction":"涨|跌|平","week_confidence":0.0-1.0,'
            '"reasoning":"简短理由"}'
        )

    @staticmethod
    def _is_transient_error(exc: BaseException) -> bool:
        msg = str(exc).lower()
        # 模型本身不可用：重试无意义
        if any(
            k in msg
            for k in (
                "model_not_found",
                "no available channel",
                "not support model",
                "不支持的模型",
                "可用渠道不存在",
            )
        ):
            return False
        keys = (
            "负载较高", "rate limit", "429", "500", "502", "503", "504",
            "timeout", "temporarily", "upstream_error", "overloaded",
            "请稍候重试",
            "pre_consume", "lock wait", "403", "quota",
            "connection", "read timed out", "timed out",
        )
        return any(k in msg for k in keys)

    def _parse_response(self, text: str) -> Dict[str, Any]:
        parsed = parse_direction_json(text)
        parsed["raw"] = text
        if not parsed.get("parse_ok"):
            raise ValueError(f"无法解析 API 方向 JSON: {(text or '')[:200]}")
        return parsed

    def _label_to_proba(self, label: int, confidence: float) -> np.ndarray:
        rest = max(0.0, 1.0 - confidence)
        other = rest / 2
        proba = {-1: other, 0: other, 1: other}
        proba[label] = confidence
        raw = np.array([proba[-1], proba[0], proba[1]], dtype=float)
        return self.calibrator.adjust_proba(raw)

    def analyze_prompt(
        self,
        prompt: str,
        use_cache: Optional[bool] = None,
        bar_date: Optional[str] = None,
        for_live: bool = False,
    ) -> Dict[str, Any]:
        use_cache = self.api_cfg.get("cache_enabled", True) if use_cache is None else use_cache
        if use_cache:
            cached = self._load_cached(prompt, bar_date=bar_date)
            if cached is not None:
                self._cache_hits += 1
                cached = dict(cached)
                cached["from_cache"] = True
                return cached

        if self._cache_only:
            raise RuntimeError(
                f"只读缓存模式:bar_date={bar_date} 在 llm_cache 中无命中,"
                "已拒绝调用 API(避免消耗额度)。如需覆盖该日期,请先预热 llm-cache。"
            )

        if (
            bar_date
            and self._api_cutoff_date
            and bar_date <= self._api_cutoff_date
        ):
            raise RuntimeError(
                f"增量更新缺少 LLM 缓存（bar_date={bar_date}），"
                "请先全量回测或检查数据库 llm_cache 表"
            )

        self._cache_misses += 1

        client = self._get_client()
        model = self.api_cfg.get("model", "deepseek-v4-pro")
        temperature = float(self.api_cfg.get("temperature", 0.1))
        max_tokens = int(self.api_cfg.get("max_tokens", 256))
        retry = int(self.api_cfg.get("max_retries", 2))
        sleep_sec = float(self.api_cfg.get("retry_sleep", 1.0))
        # DeepSeek V4 / 豆包 Seed 默认可能开 thinking（贵且挤占输出）；配置 thinking 时显式开关
        thinking = bool(self.api_cfg.get("thinking", False))
        model_s = str(model)
        want_json_object = bool(self.api_cfg.get("json_object", False))
        user_content = self._user_prompt(prompt)
        # kimi-k3：思考不可关，用 reasoning_effort=low|high|max（默认 max）
        reasoning_effort = self.api_cfg.get("reasoning_effort")
        model_l = model_s.lower()
        is_kimi_k3 = model_l in ("kimi-k3", "kimi-k3-turbo") or model_l.startswith("kimi-k3")
        is_kimi_k2 = model_l.startswith("kimi-k2")

        last_err = None
        for attempt in range(retry + 1):
            try:
                self._throttle_before_request()
                create_kwargs: Dict[str, Any] = {
                    "model": model,
                    "messages": [
                        {"role": "system", "content": self._system_prompt(for_live=for_live)},
                        {"role": "user", "content": user_content},
                    ],
                    "max_tokens": max_tokens,
                }
                # K3 文档：temperature/top_p 等固定，省略更稳妥
                if not is_kimi_k3:
                    create_kwargs["temperature"] = temperature
                if want_json_object:
                    create_kwargs["response_format"] = {"type": "json_object"}
                if is_kimi_k3:
                    create_kwargs["reasoning_effort"] = str(
                        reasoning_effort or "low"
                    )
                elif self.provider_name == "qwen" or model_s.startswith("qwen"):
                    create_kwargs["extra_body"] = {"enable_thinking": thinking}
                elif (
                    is_kimi_k2
                    or model_s.startswith("deepseek-v4")
                    or model_s.startswith("doubao-seed")
                    or model_s.lower().startswith("minimax")
                    or model_s.lower().startswith("glm")
                    or self.provider_name in ("doubao", "minimax", "glm", "kimi")
                    or "thinking" in self.api_cfg
                ):
                    # MiniMax-M3 开启侧用 adaptive；其它用 enabled
                    # GLM-4.7 等默认可能开 thinking，未显式 disabled 时单次 60s+
                    # kimi-k2.x 关 thinking 时 temperature 须为 0.6
                    on_type = (
                        "adaptive"
                        if (
                            model_s.lower().startswith("minimax")
                            or self.provider_name == "minimax"
                        )
                        else "enabled"
                    )
                    extra_body: Dict[str, Any] = {
                        "thinking": {"type": on_type if thinking else "disabled"},
                    }
                    # 百炼兼容口：仅 thinking.disabled 仍会出 reasoning_tokens；需 enable_thinking
                    base = str(self.api_cfg.get("base_url") or "")
                    if "dashscope" in base or model_s.lower().startswith("glm"):
                        extra_body["enable_thinking"] = bool(thinking)
                    create_kwargs["extra_body"] = extra_body
                if self.provider_name.startswith("tuzi") or attempt > 0:
                    logger.info(
                        f"LLM API[{self.provider_name}/{model}] "
                        f"live bar={bar_date} attempt={attempt + 1}/{retry + 1}"
                    )
                try:
                    response = client.chat.completions.create(**create_kwargs)
                except Exception as fmt_err:
                    # 仅当明确是 response_format 不支持时才降级；429/超时等不要双倍打接口
                    err_l = str(fmt_err).lower()
                    format_unsupported = any(
                        k in err_l
                        for k in (
                            "response_format",
                            "json_object",
                            "not supported",
                            "unsupported",
                            "invalid_request",
                            "unknown parameter",
                        )
                    ) and not self._is_transient_error(fmt_err)
                    if (
                        want_json_object
                        and "response_format" in create_kwargs
                        and format_unsupported
                    ):
                        logger.warning(
                            f"LLM API[{self.provider_name}/{model}] response_format 不可用，降级: {fmt_err}"
                        )
                        create_kwargs.pop("response_format", None)
                        response = client.chat.completions.create(**create_kwargs)
                    else:
                        raise
                msg = response.choices[0].message
                content = (msg.content or "").strip()
                reasoning = (getattr(msg, "reasoning_content", None) or "").strip()
                # 优先可解析的 content；Kimi 等可能先把推理放 reasoning_content、JSON 在 content
                text = content or reasoning
                # 兼容周频 week_direction / 日频 direction
                has_dir = '"direction"' in (content or "") or '"week_direction"' in (content or "")
                has_dir_r = '"direction"' in reasoning or '"week_direction"' in reasoning
                if content and reasoning and not has_dir and has_dir_r:
                    text = reasoning
                elif (not content or not has_dir) and reasoning:
                    text = f"{content}\n{reasoning}".strip() if content else reasoning
                parsed = self._parse_response(text)
                parsed["model"] = model
                parsed["provider"] = self.provider_name
                parsed["from_cache"] = False
                if use_cache and self._cache_write:
                    self._save_cached(parsed, prompt, bar_date=bar_date)
                return parsed
            except Exception as e:
                last_err = e
                logger.warning(
                    f"LLM API[{self.provider_name}/{model}] 调用失败 "
                    f"({attempt + 1}/{retry + 1}): {e}"
                )
                if attempt < retry:
                    wait = sleep_sec * (attempt + 1)
                    if self._is_transient_error(e):
                        # 429/配额：退避更久，避免双倍打穿限流
                        wait = max(wait * 3, 5.0)
                        err_l = str(e).lower()
                        err_s = str(e)
                        if (
                            "429" in err_l
                            or "速率" in err_s
                            or "rate limit" in err_l
                            or "overloaded" in err_l
                            or "1302" in err_s
                        ):
                            # 智谱账户级 1302：短退避只会延长惩罚窗口
                            wait = max(wait, 90.0 * (attempt + 1))
                            logger.info(
                                f"LLM API[{self.provider_name}/{model}] "
                                f"限流退避 {wait:.0f}s (attempt {attempt + 1})"
                            )
                    time.sleep(wait)
        raise RuntimeError(f"LLM API[{self.provider_name}/{model}] 调用失败: {last_err}")

    def bind_dataframe(self, df: pd.DataFrame):
        self._df = df.sort_values("date").reset_index(drop=True)

    def _resolve_row_context(self, df: pd.DataFrame, row_idx: int, for_live: bool = False):
        data = df.sort_values("date").reset_index(drop=True)
        row = data.iloc[row_idx]
        index_name, bar_unit = None, None
        blind = is_blind_prompt_enabled(for_live)
        try:
            from market_platform.predictor.分析.特征.日信号复合 import (
                build_weekly_decision_prompt,
                is_weekly_decision_mode,
            )

            if is_weekly_decision_mode():
                from market_platform.predictor.分析.提示词.构建器 import _resolve_index_meta

                index_name, _ = _resolve_index_meta()
                prompt, meta = build_weekly_decision_prompt(
                    data,
                    pd.Timestamp(row["date"]),
                    index_name=index_name or "沪深300",
                )
                # 透传状态路由元数据到 row 副本，便于日志/下游
                if isinstance(meta, dict) and meta.get("regime"):
                    row = row.copy()
                    row["regime"] = meta.get("regime")
                    row["regime_zh"] = meta.get("regime_zh")
                return prompt, row
        except Exception:
            pass
        prompt = build_prompt_from_row(
            row,
            index_name=index_name,
            bar_unit=bar_unit,
            df=data,
            idx=row_idx,
            blind=blind,
        )
        return prompt, row

    def _resolve_week_label(self, result: Dict[str, Any], row: pd.Series) -> int:
        if result.get("week_label") is not None:
            return int(result["week_label"])
        if result.get("label") is not None:
            # 周决策模式下主 label 即周向
            try:
                from market_platform.predictor.分析.特征.日信号复合 import is_weekly_decision_mode

                if is_weekly_decision_mode():
                    return int(result["label"])
            except Exception:
                pass
        return infer_week_direction(row)

    def _analyze_row(
        self, df: pd.DataFrame, row_idx: int, for_live: bool = False,
    ) -> Tuple[Dict[str, Any], pd.Series, pd.DataFrame]:
        data = df.sort_values("date").reset_index(drop=True)
        row = data.iloc[row_idx]
        lookback = get_prompt_lookback()
        start = max(0, row_idx - lookback + 1)
        window = data.iloc[start: row_idx + 1]
        prompt, _ = self._resolve_row_context(data, row_idx, for_live=for_live)
        bar_date = pd.Timestamp(row["date"]).strftime("%Y-%m-%d")
        result = self.analyze_prompt(prompt, bar_date=bar_date, for_live=for_live)
        result = self.calibrator.calibrate(
            result, row, window=window, provider=self.provider_name,
        )
        week_lab = self._resolve_week_label(result, row)
        result["week_label"] = week_lab
        # 周频硬切：对外主预测 = 周向（三分类）
        try:
            from market_platform.predictor.分析.特征.日信号复合 import is_weekly_decision_mode

            if is_weekly_decision_mode():
                result["label"] = int(week_lab)
                if result.get("week_confidence") is not None:
                    result["confidence"] = float(result["week_confidence"])
                # 幅度与方向符号对齐；缺失时用动量粗估
                mag = result.get("week_return_pct")
                if mag is None or (isinstance(mag, float) and not np.isfinite(mag)):
                    mom = row.get("momentum_5d")
                    mag = float(mom) * 100.0 if pd.notna(mom) else (0.5 if int(week_lab) > 0 else (-0.5 if int(week_lab) < 0 else 0.0))
                mag = float(mag)
                if int(week_lab) > 0:
                    mag = abs(mag) if mag != 0 else 0.1
                elif int(week_lab) < 0:
                    mag = -abs(mag) if mag != 0 else -0.1
                else:
                    mag = 0.0
                result["week_return_pct"] = float(max(-50.0, min(50.0, mag)))
        except Exception:
            pass
        self._last_result = dict(result)
        return result, row, window

    def predict_bundle(self, df: pd.DataFrame) -> Tuple[int, np.ndarray, int]:
        """返回 (主预测, proba, week_pred)。周频硬切下主预测=周向。"""
        if len(df) != 1:
            raise ValueError("predict_bundle 仅支持单行 DataFrame")
        full_df = self._df if self._df is not None else df
        full_df = full_df.sort_values("date").reset_index(drop=True)
        target_date = pd.Timestamp(df.iloc[0]["date"])
        hits = full_df.index[pd.to_datetime(full_df["date"]) == target_date]
        if len(hits) == 0:
            raise ValueError(f"未找到日期 {target_date.date()} 对应的 K 线")
        result, row, _ = self._analyze_row(full_df, int(hits[-1]))
        label = int(result["label"])
        proba = self._label_to_proba(label, result["confidence"])
        week_label = int(result["week_label"])
        from market_platform.predictor.核心.工具.结构化日志 import build_feature_snapshot, emit_event
        emit_event(
            "llm_predict",
            payload={
                "date": target_date.strftime("%Y-%m-%d"),
                "label": label,
                "confidence": float(result["confidence"]),
                "week_label": week_label,
                "week_return_pct": result.get("week_return_pct"),
            },
            feature_snapshot=build_feature_snapshot(row),
        )
        return label, proba, week_label

    def predict_proba_from_df_row(self, df: pd.DataFrame, row_idx: int) -> np.ndarray:
        result, row, _ = self._analyze_row(df, row_idx)
        return self._label_to_proba(result["label"], result["confidence"]).reshape(1, -1)

    def predict_proba(self, df: pd.DataFrame) -> np.ndarray:
        if len(df) != 1:
            raise ValueError("predict_proba 仅支持单行 DataFrame")
        full_df = self._df if self._df is not None else df
        full_df = full_df.sort_values("date").reset_index(drop=True)
        target_date = pd.Timestamp(df.iloc[0]["date"])
        hits = full_df.index[pd.to_datetime(full_df["date"]) == target_date]
        if len(hits) == 0:
            raise ValueError(f"未找到日期 {target_date.date()} 对应的 K 线")
        return self.predict_proba_from_df_row(full_df, int(hits[-1])).reshape(1, -1)

    def predict(self, df: pd.DataFrame) -> np.ndarray:
        proba = np.asarray(self.predict_proba(df), dtype=float)
        if proba.ndim == 1:
            proba = proba.reshape(1, -1)
        return np.array([self.CLASS_ORDER[int(i)] for i in np.argmax(proba, axis=1)])

    def analyze_latest(self, df: Optional[pd.DataFrame] = None, date: Optional[str] = None) -> Dict[str, Any]:
        data = df if df is not None else self._df
        if data is None:
            raise RuntimeError("请先 bind_dataframe() 或传入 df")
        blind = is_blind_prompt_enabled(for_live=True)
        prompt, row, idx, resolved = build_analysis_prompt(data, date=date, blind=blind)
        lookback = get_prompt_lookback()
        start = max(0, idx - lookback + 1)
        window = data.iloc[start: idx + 1]
        result = self.analyze_prompt(
            prompt,
            bar_date=pd.Timestamp(row["date"]).strftime("%Y-%m-%d"),
            for_live=True,
        )
        result = self.calibrator.calibrate(
            result, row, window=window, provider=self.provider_name,
        )
        result.update({
            "date": pd.Timestamp(row["date"]).strftime("%Y-%m-%d"),
            "requested_date": date,
            "date_aligned": resolved,
            "is_historical": bool(resolved and date),
            "close": float(row["close"]) if pd.notna(row.get("close")) else None,
            "prompt": prompt,
            "idx": idx,
        })
        if pd.notna(row.get("label_direction")):
            result["actual_label"] = int(row["label_direction"])
            result["actual_text"] = LABEL_TO_TEXT.get(int(row["label_direction"]))
        result["proba"] = self._label_to_proba(result["label"], result["confidence"]).tolist()
        result["provider_name"] = self.provider_name
        result["model"] = self.api_cfg.get("model", result.get("model"))
        # 周频硬切：主方向=周向；补幅度
        try:
            from market_platform.predictor.分析.特征.日信号复合 import is_weekly_decision_mode

            if is_weekly_decision_mode():
                week_lab = self._resolve_week_label(result, row)
                result["week_label"] = int(week_lab)
                result["label"] = int(week_lab)
                result["direction"] = LABEL_TO_TEXT.get(int(week_lab), result.get("direction"))
                if result.get("week_confidence") is not None:
                    result["confidence"] = float(result["week_confidence"])
                mag = result.get("week_return_pct")
                if mag is None:
                    mom = row.get("momentum_5d")
                    mag = float(mom) * 100.0 if pd.notna(mom) else (0.5 if int(week_lab) > 0 else (-0.5 if int(week_lab) < 0 else 0.0))
                mag = float(mag)
                if int(week_lab) > 0:
                    mag = abs(mag) if mag != 0 else 0.1
                elif int(week_lab) < 0:
                    mag = -abs(mag) if mag != 0 else -0.1
                else:
                    mag = 0.0
                result["week_return_pct"] = float(max(-50.0, min(50.0, mag)))
        except Exception:
            pass
        from market_platform.predictor.分析.提示词.构建器 import build_preview_payload
        result["preview"] = build_preview_payload(data, date=date)
        return result

    def train(self, df: pd.DataFrame, val_df: Optional[pd.DataFrame] = None) -> Dict:
        self.bind_dataframe(df)
        logger.info("☁️ API 模式跳过本地训练，将直接调用 DeepSeek 做推理")
        return {"train_acc": None, "val_acc": None, "provider": "deepseek_api"}

    def save(self, name: str = "llm_api"):
        meta = {"cfg": self.cfg, "provider": "deepseek_api"}
        path = checkpoints_dir() / name
        path.mkdir(parents=True, exist_ok=True)
        (path / "market_platform.predictor_meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8",
        )

    def load(self, name: str = "llm_api"):
        path = checkpoints_dir() / name / "market_platform.predictor_meta.json"
        if path.exists():
            meta = json.loads(path.read_text(encoding="utf-8"))
            self.cfg.update(meta.get("cfg", {}))
            self.api_cfg = dict(self.cfg.get("api", {}))
        self._is_trained = True

from __future__ import annotations

import pandas as pd
from market_platform.agents.分析.推断.残差 import explain_residuals_rule_based
from market_platform.agents.核心.大模型.客户端 import LLMClient, RuleLLMClient
from market_platform.agents.采集.新闻 import news_for_date


def _build_prompt(base_prompt: str, headlines: list[str]) -> str:
    news_block = "\n".join(f"- {h}" for h in headlines) if headlines else "- （无新闻）"
    return (
        f"{base_prompt}\n\n"
        f"相关新闻标题：\n{news_block}\n\n"
        "请用 2-3 句话从多 Agent 市场角度解释资金流残差原因，"
        "指出可能是哪类参与者（散户/机构/量化/宏观/游资）行为偏移。"
    )


def explain_residuals_with_llm(
    anomalies: pd.DataFrame,
    news_df: pd.DataFrame | None = None,
    minute_daily: pd.DataFrame | None = None,
    client: LLMClient | None = None,
    use_llm: bool = True,
) -> pd.DataFrame:
    """规则基线 + 可选 LLM 增强解释。"""
    base = explain_residuals_rule_based(anomalies, minute_daily)
    if base.empty:
        return base

    client = client or RuleLLMClient()
    llm_texts: list[str] = []
    sources: list[str] = []

    for _, row in base.iterrows():
        headlines = news_for_date(news_df, row["date"]) if news_df is not None else []
        prompt = _build_prompt(row["llm_prompt"], headlines)

        if use_llm and not isinstance(client, RuleLLMClient):
            llm_answer = client.complete(prompt)
            if llm_answer.startswith("（LLM 不可用"):
                llm_texts.append(row["explanation"])
                sources.append("rules_fallback")
            else:
                llm_texts.append(llm_answer)
                sources.append("llm")
        else:
            extra = ""
            if headlines:
                extra = f"；当日新闻：{headlines[0]}"
            llm_texts.append(row["explanation"] + extra)
            sources.append("rules")

    base["llm_explanation"] = llm_texts
    base["explanation_source"] = sources
    return base

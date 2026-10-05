"""Run LLM prediction for latest day (cron)."""
import sys
import pandas as pd
from loguru import logger

from market_platform.predictor.分析.推断 import LLMApiPredictor
from market_platform.predictor.分析.提示词.构建器 import get_prompt_config, is_blind_prompt_enabled, is_dual_frequency_enabled
from market_platform.predictor.存储.数据访问 import load_index_features
from market_platform.predictor.配置 import get_config

def main():
    cfg = get_config()
    index_code = cfg["data"]["index_code"]

    df = load_index_features(index_code)
    if df is None or df.empty:
        logger.error("❌ No feature data found, run --step data first")
        sys.exit(1)

    df = df.sort_values("date").reset_index(drop=True)
    latest = df.iloc[-1]
    latest_date = pd.Timestamp(latest["date"]).strftime("%Y-%m-%d")

    print(f"📅 最新数据日期: {latest_date}")
    print(f"📊 沪深300收盘: {latest.get('close', 'N/A')}")
    if pd.notna(latest.get("label_direction")):
        actual = {1:"涨",0:"平",-1:"跌"}.get(int(latest["label_direction"]), "?")
        print(f"  (当前为历史标签: {actual})")

    # Create predictor (uses default provider: deepseek)
    predictor = LLMApiPredictor()
    predictor.bind_dataframe(df)

    # Run analysis for latest
    result = predictor.analyze_latest()

    direction = result.get("direction", "?")
    confidence = result.get("confidence", "?")
    label = result.get("label", "?")
    reasoning = result.get("reasoning", "")
    provider = result.get("provider_name", "?")
    model = result.get("model", "?")
    from_cache = result.get("from_cache", False)

    print(f"\n{'='*50}")
    print("🔮 LLM 预测结果")
    print(f"{'='*50}")
    print(f"  提供商: {provider} / {model}")
    print(f"  预测方向: {direction} (label={label})")
    print(f"  置信度: {confidence}")
    print(f"  理由: {reasoning[:200]}")
    print(f"  来源: {'缓存' if from_cache else '实时API调用'}")

    # Sprawdź prompt pod kątem technicznych wskaźników
    raw_prompt = result.get("prompt", "")
    print(f"\n{'='*50}")
    print("📝 Prompt 摘要")
    print(f"{'='*50}")

    # Check prompt config for technical indicators
    prompt_cfg = get_prompt_config()
    include_technical = bool(prompt_cfg.get("include_technical", False))
    blind = is_blind_prompt_enabled(for_live=True)
    dual = is_dual_frequency_enabled()

    print(f"  include_technical (配置): {include_technical}")
    print(f"  blind_mode (配置): {blind}")
    print(f"  dual_frequency (配置): {dual}")
    print(f"  prompt_version: {prompt_cfg.get('version', '?')}")

    # Check if "不含收盘序列/技术因子" is in the prompt
    tech_disclaimer = "不含收盘序列/技术因子" in raw_prompt or "本 Prompt 不含" in raw_prompt
    has_price_data = any(k in raw_prompt for k in ["收盘", "close", "volume", "成交量"])
    has_tech = any(k in raw_prompt for k in ["RSI", "MACD", "boll", "均线"])

    print(f"\n  ✅ Prompt 明确声明不含技术因子: {tech_disclaimer}")
    print(f"  📊 Prompt 含价格数据: {has_price_data}")
    print(f"  📊 Prompt 含技术指标: {has_tech}")

    if raw_prompt:
        lines = raw_prompt.strip().split("\n")
        print("\n  📄 Prompt 预览 (前15行):")
        for line in lines[:15]:
            print(f"    {line}")
        print(f"    ... ({len(lines)} 行总计)")

    print(f"\n{'='*50}")
    print("✅ 预测完成")
    print(f"{'='*50}")

if __name__ == "__main__":
    main()

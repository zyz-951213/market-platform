"""国内最新渠道配置约束。"""

from market_platform.predictor.配置 import reload_config
from market_platform.predictor.分析.推断.语言模型预测 import list_member_providers, provider_model_id


def test_member_providers_are_domestic_latest():
    reload_config()
    members = list_member_providers()
    assert "deepseek" in members
    assert "qwen" in members
    assert "glm" in members
    assert all(not p.startswith("tuzi_") for p in members)
    assert provider_model_id("qwen") == "qwen3.7-max"
    assert provider_model_id("glm") == "glm-4.7"
    assert provider_model_id("deepseek") == "deepseek-v4-pro"
    assert provider_model_id("doubao") == "doubao-seed-2-0-lite-260428"
    assert provider_model_id("kimi") == "kimi-k2.6"
    assert provider_model_id("minimax") == "MiniMax-M3"

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Protocol


class LLMClient(Protocol):
    def complete(self, prompt: str) -> str: ...


@dataclass
class RuleLLMClient:
    """规则回退：不调用外部模型。"""

    def complete(self, prompt: str) -> str:
        if "残差 z=" in prompt or "z=" in prompt:
            return "规则解释：资金流偏离可能与当日放量及市场情绪变化有关，建议结合板块与宏观新闻复核。"
        return "规则解释：暂无足够上下文，保持观望并关注后续量价确认。"


@dataclass
class OllamaClient:
    """Ollama 本地模型客户端。"""

    model: str = "qwen2.5:7b"
    base_url: str = "http://127.0.0.1:11434"
    timeout: int = 120

    def complete(self, prompt: str) -> str:
        url = f"{self.base_url.rstrip('/')}/api/generate"
        payload = json.dumps(
            {
                "model": self.model,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0.3, "num_predict": 256},
            }
        ).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            text = data.get("response", "").strip()
            return text or "（模型返回为空）"
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            return f"（LLM 不可用: {exc}）"


def get_llm_client(provider: str, model: str = "qwen2.5:7b", base_url: str = "http://127.0.0.1:11434") -> LLMClient:
    if provider.lower() == "ollama":
        return OllamaClient(model=model, base_url=base_url)
    return RuleLLMClient()

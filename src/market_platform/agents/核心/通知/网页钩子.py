from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass


@dataclass
class NotifyResult:
    success: bool
    channel: str
    detail: str


def send_webhook(url: str, content: str, msgtype: str = "text") -> NotifyResult:
    """通用 Webhook 推送（兼容企业微信机器人）。"""
    if not url:
        return NotifyResult(False, "webhook", "empty url")

    if msgtype == "markdown":
        payload = {"msgtype": "markdown", "markdown": {"content": content}}
    else:
        payload = {"msgtype": "text", "text": {"content": content}}

    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read().decode("utf-8")
        return NotifyResult(True, "webhook", body)
    except (urllib.error.URLError, TimeoutError) as exc:
        return NotifyResult(False, "webhook", str(exc))

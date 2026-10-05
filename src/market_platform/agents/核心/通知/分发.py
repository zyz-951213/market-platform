from __future__ import annotations

import logging

from market_platform.agents.settings import NotifyConfig
from market_platform.agents.核心.通知.网页钩子 import send_webhook
from market_platform.agents.核心.通知.邮件 import NotifyResult, send_email

logger = logging.getLogger(__name__)


def dispatch_notification(
    config: NotifyConfig,
    title: str,
    body: str,
    *,
    dry_run: bool = False,
) -> list[NotifyResult]:
    """按配置发送 Webhook / 邮件通知。"""
    if dry_run:
        print(f"\n=== {title} ===\n{body}")
        return []

    if not config.enabled:
        logger.info("Notify disabled, skip dispatch")
        return []

    results: list[NotifyResult] = []
    if config.webhook_url:
        results.append(send_webhook(config.webhook_url, body, msgtype=config.msgtype))
    if config.email.enabled:
        results.append(send_email(config.email, title, body))

    for result in results:
        if result.success:
            logger.info("Notify via %s ok: %s", result.channel, result.detail)
        else:
            logger.warning("Notify via %s failed: %s", result.channel, result.detail)
    return results

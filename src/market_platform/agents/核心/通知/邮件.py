from __future__ import annotations

import logging
import smtplib
from dataclasses import dataclass
from email.mime.text import MIMEText

from market_platform.agents.settings import EmailNotifyConfig

logger = logging.getLogger(__name__)


@dataclass
class NotifyResult:
    success: bool
    channel: str
    detail: str


def send_email(config: EmailNotifyConfig, subject: str, body: str) -> NotifyResult:
    """通过 SMTP 发送邮件通知。"""
    if not config.enabled:
        return NotifyResult(False, "email", "email disabled")
    if not config.smtp_host or not config.to_addrs:
        return NotifyResult(False, "email", "smtp_host or to_addrs missing")

    from_addr = config.from_addr or config.username
    if not from_addr:
        return NotifyResult(False, "email", "from_addr missing")

    msg = MIMEText(body, _charset="utf-8")
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = ", ".join(config.to_addrs)

    try:
        if config.use_tls:
            server = smtplib.SMTP(config.smtp_host, config.smtp_port, timeout=20)
            server.starttls()
        else:
            server = smtplib.SMTP_SSL(config.smtp_host, config.smtp_port, timeout=20)
        if config.username:
            server.login(config.username, config.password)
        server.sendmail(from_addr, config.to_addrs, msg.as_string())
        server.quit()
        return NotifyResult(True, "email", f"sent to {len(config.to_addrs)} recipients")
    except (smtplib.SMTPException, OSError) as exc:
        logger.warning("Email send failed: %s", exc)
        return NotifyResult(False, "email", str(exc))

from __future__ import annotations

import os
import smtplib
import ssl
from email.message import EmailMessage


def _allowed_recipients() -> set[str]:
    return {item.strip().lower() for item in os.getenv("ALLOWED_RECIPIENTS", "").split(",") if item.strip()}


def validate_recipient(recipient: str) -> None:
    allowed = _allowed_recipients()
    if not allowed:
        raise ValueError("尚未配置 ALLOWED_RECIPIENTS，系统拒绝发送。")
    if recipient.strip().lower() not in allowed:
        raise ValueError("收件人不在演示白名单中。")


def send_jd_email(recipient: str, job_title: str, body: str, attachment: bytes) -> str:
    validate_recipient(recipient)
    host = os.getenv("SMTP_HOST", "").strip()
    username = os.getenv("SMTP_USERNAME", "").strip()
    password = os.getenv("SMTP_PASSWORD", "").strip()
    sender = os.getenv("SMTP_SENDER", "").strip() or username
    if not all([host, username, password, sender]):
        raise ValueError("SMTP 配置不完整，未执行发送。")

    port = int(os.getenv("SMTP_PORT", "465"))
    use_ssl = os.getenv("SMTP_USE_SSL", "true").lower() in {"1", "true", "yes"}
    message = EmailMessage()
    message["Subject"] = f"【人工审核完成】{job_title}招聘JD"
    message["From"] = sender
    message["To"] = recipient
    message.set_content(body + "\n\n---\n本JD已在发送前经过人工确认。")
    message.add_attachment(
        attachment,
        maintype="application",
        subtype="vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename=f"{job_title}_JD.docx",
    )

    context = ssl.create_default_context()
    if use_ssl:
        with smtplib.SMTP_SSL(host, port, context=context, timeout=20) as smtp:
            smtp.login(username, password)
            smtp.send_message(message)
    else:
        with smtplib.SMTP(host, port, timeout=20) as smtp:
            smtp.starttls(context=context)
            smtp.login(username, password)
            smtp.send_message(message)
    return message.get("Message-ID", "") or "smtp-accepted"


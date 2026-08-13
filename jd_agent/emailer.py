"""邮件发送 -- 带白名单校验的真实邮件发送"""

from __future__ import annotations

import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders

from dotenv import load_dotenv

load_dotenv()

ALLOWED_RECIPIENTS = [
    addr.strip()
    for addr in os.getenv("ALLOWED_RECIPIENTS", "").split(",")
    if addr.strip()
]

SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "") or os.getenv("SMTP_USERNAME", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")


def send_jd_email(
    recipient: str,
    job_title: str,
    jd_text: str,
    docx_bytes: bytes,
) -> str:
    """发送 JD 邮件，返回 message_id。失败时抛出异常。"""
    if not ALLOWED_RECIPIENTS:
        raise ValueError("未配置 ALLOWED_RECIPIENTS 白名单，邮件发送被禁用。")

    if recipient not in ALLOWED_RECIPIENTS:
        raise ValueError(f"收件人 {recipient} 不在白名单中。")

    if not SMTP_HOST or not SMTP_USER or not SMTP_PASSWORD:
        raise ValueError("未完整配置 SMTP_HOST、SMTP_USER 和 SMTP_PASSWORD，无法发送邮件。")

    msg = MIMEMultipart()
    msg["From"] = SMTP_USER
    msg["To"] = recipient
    msg["Subject"] = f"【招聘 JD】{job_title}"

    body = f"""
<html>
<body>
<h2>{job_title} - 岗位说明书</h2>
<pre style="white-space: pre-wrap; font-family: sans-serif;">{jd_text}</pre>
<hr>
<p style="color: #888; font-size: 12px;">本邮件由招聘协作 Agent 自动发送，附件为 Word 版 JD。</p>
</body>
</html>
"""
    msg.attach(MIMEText(body, "html", "utf-8"))

    # 添加附件
    attachment = MIMEBase("application", "octet-stream")
    attachment.set_payload(docx_bytes)
    encoders.encode_base64(attachment)
    attachment.add_header(
        "Content-Disposition",
        f'attachment; filename="{job_title}_JD.docx"',
    )
    msg.attach(attachment)

    if SMTP_PORT == 465:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=20) as server:
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.send_message(msg)
    else:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.send_message(msg)

    message_id = msg.get("Message-ID", "unknown")
    return message_id or "sent"

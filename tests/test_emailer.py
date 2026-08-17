from __future__ import annotations

import pytest

from jd_agent import emailer


class FakeSMTP:
    instances = []

    def __init__(self, host, port, timeout=20):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.logged_in = None
        self.message = None
        self.started_tls = False
        self.__class__.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def ehlo(self):
        return None

    def starttls(self):
        self.started_tls = True

    def login(self, user, password):
        self.logged_in = (user, password)

    def send_message(self, message):
        self.message = message

    def quit(self):
        return 221, b"bye"

    def close(self):
        return None


def configure(monkeypatch, port=465):
    FakeSMTP.instances.clear()
    monkeypatch.setattr(emailer, "ALLOWED_RECIPIENTS", ["owner@example.com"])
    monkeypatch.setattr(emailer, "SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr(emailer, "SMTP_PORT", port)
    monkeypatch.setattr(emailer, "SMTP_USER", "sender@example.com")
    monkeypatch.setattr(emailer, "SMTP_PASSWORD", "secret")


def test_email_rejects_recipient_outside_allowlist(monkeypatch):
    configure(monkeypatch)
    with pytest.raises(ValueError, match="不在白名单"):
        emailer.send_jd_email("other@example.com", "测试岗位", "正文", b"docx")


def test_email_normalizes_recipient_whitespace_and_case(monkeypatch):
    configure(monkeypatch, port=465)
    monkeypatch.setattr(emailer.smtplib, "SMTP_SSL", FakeSMTP)

    emailer.send_jd_email("  OWNER@EXAMPLE.COM\n", "测试岗位", "正文", b"docx")

    assert FakeSMTP.instances[0].message["To"] == "owner@example.com"


def test_email_requires_complete_smtp_configuration(monkeypatch):
    configure(monkeypatch)
    monkeypatch.setattr(emailer, "SMTP_PASSWORD", "")
    with pytest.raises(ValueError, match="未完整配置"):
        emailer.send_jd_email("owner@example.com", "测试岗位", "正文", b"docx")


def test_smtp_ssl_sends_escaped_html_and_attachment(monkeypatch):
    configure(monkeypatch, port=465)
    monkeypatch.setattr(emailer.smtplib, "SMTP_SSL", FakeSMTP)

    message_id = emailer.send_jd_email(
        "owner@example.com",
        "AI <产品经理>",
        "<script>alert(1)</script>",
        b"PK-test-docx",
    )

    instance = FakeSMTP.instances[0]
    assert instance.logged_in == ("sender@example.com", "secret")
    assert instance.message["To"] == "owner@example.com"
    assert message_id.startswith("<") and message_id.endswith(">")
    assert message_id.endswith("@example.com>")
    html_part = instance.message.get_payload()[0].get_payload(decode=True).decode("utf-8")
    assert "&lt;script&gt;" in html_part
    assert "<script>" not in html_part
    assert instance.message.get_payload()[1].get_payload(decode=True) == b"PK-test-docx"


def test_starttls_path_is_used_for_non_ssl_port(monkeypatch):
    configure(monkeypatch, port=587)
    monkeypatch.setattr(emailer.smtplib, "SMTP", FakeSMTP)

    emailer.send_jd_email("owner@example.com", "测试岗位", "正文", b"docx")

    assert FakeSMTP.instances[0].started_tls is True


def test_disconnect_during_quit_does_not_turn_completed_send_into_failure(monkeypatch):
    class QuitDisconnectSMTP(FakeSMTP):
        def quit(self):
            raise emailer.smtplib.SMTPServerDisconnected("Connection unexpectedly closed")

    configure(monkeypatch, port=587)
    monkeypatch.setattr(emailer.smtplib, "SMTP", QuitDisconnectSMTP)

    message_id = emailer.send_jd_email(
        "owner@example.com", "测试岗位", "正文", b"docx"
    )

    assert message_id.startswith("<")
    assert QuitDisconnectSMTP.instances[0].message is not None

"""Mailer: Gmail SMTP delivery with retry. Always sends something — an edition
or a failure notice — so silence always means 'the machine never ran'."""

import os
import smtplib
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional


class MailConfigError(RuntimeError):
    pass


def _config():
    addr = os.environ.get("GMAIL_ADDRESS", "").strip()
    pw = os.environ.get("GMAIL_APP_PASSWORD", "").replace(" ", "").strip()
    to = os.environ.get("DIGEST_TO", addr).strip()
    if not addr or not pw:
        raise MailConfigError(
            "GMAIL_ADDRESS / GMAIL_APP_PASSWORD not set in .env — "
            "create an App Password at myaccount.google.com/apppasswords"
        )
    return addr, pw, to


def send(subject: str, html_body: str, text_body: str,
         retries: int = 3, log=print) -> None:
    addr, pw, to = _config()
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = "AI Safety Digest <%s>" % addr
    msg["To"] = to
    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    last_exc: Optional[Exception] = None
    for attempt in range(1, retries + 1):
        try:
            with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as server:
                server.login(addr, pw)
                server.sendmail(addr, [to], msg.as_string())
            log("  email sent to %s" % to)
            return
        except (smtplib.SMTPException, OSError) as exc:
            last_exc = exc
            log("  WARN smtp attempt %d/%d failed: %s" % (attempt, retries, exc))
            if attempt < retries:
                time.sleep(5 * attempt)
    raise RuntimeError("email failed after %d attempts: %s" % (retries, last_exc))


def send_failure_notice(error: str, log_tail: str, log=print) -> None:
    """Best-effort failure email; never raises."""
    try:
        body = "The digest pipeline failed.\n\nError:\n%s\n\nLog tail:\n%s" % (error, log_tail)
        send("AI Safety Digest — RUN FAILED",
             "<pre style='font-family:monospace'>%s</pre>" % body.replace("<", "&lt;"),
             body, retries=2, log=log)
    except Exception as exc:  # noqa: BLE001
        log("  WARN could not send failure notice: %s" % exc)

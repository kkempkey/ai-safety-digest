"""Mailer: Gmail SMTP delivery with retry, falling back to sending through the
local Mail.app (plain text) when SMTP is unconfigured or rejected. Always sends
something — an edition or a failure notice — so silence always means 'the
machine never ran'."""

import os
import smtplib
import subprocess
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional


class MailConfigError(RuntimeError):
    pass


MAIL_APP_SCRIPT = """
on run argv
    set theSubject to item 1 of argv
    set theBody to item 2 of argv
    set theTo to item 3 of argv
    tell application "Mail"
        set m to make new outgoing message with properties {subject:theSubject, content:theBody, visible:false}
        tell m to make new to recipient at end of to recipients with properties {address:theTo}
        send m
    end tell
end run
"""


def send_via_mail_app(subject: str, text_body: str, to: str, log=print) -> None:
    """Send plain text through the Mail.app account already signed in on this
    Mac (no password needed). Requires the Automation permission for Mail."""
    proc = subprocess.run(
        ["osascript", "-", subject, text_body, to],
        input=MAIL_APP_SCRIPT, capture_output=True, text=True, timeout=60,
    )
    if proc.returncode != 0:
        raise RuntimeError("Mail.app send failed: %s" % (proc.stderr or "")[:300])
    log("  email sent via Mail.app to %s" % to)


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
    """Prefer Gmail SMTP (styled HTML). If it's unconfigured or rejected,
    fall back to Mail.app (plain text) so delivery never silently stops."""
    to = os.environ.get("DIGEST_TO", "").strip() or os.environ.get("GMAIL_ADDRESS", "").strip()
    try:
        _send_smtp(subject, html_body, text_body, retries, log)
        return
    except (MailConfigError, RuntimeError) as exc:
        log("  WARN smtp path unavailable (%s); falling back to Mail.app" % str(exc)[:120])
    if not to:
        raise MailConfigError("No DIGEST_TO/GMAIL_ADDRESS set in .env")
    send_via_mail_app(subject, text_body, to, log=log)


def _send_smtp(subject: str, html_body: str, text_body: str,
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

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from html import escape

from django.conf import settings
from django.core.mail import EmailMultiAlternatives, get_connection

from . import otp_service

logger = logging.getLogger("accounts")

# SMTP latency (2-15s against Gmail) must not block the HTTP response, so OTP
# emails are delivered from a background worker instead of the request thread.
#
# The dominant cost per email is the connect + STARTTLS + AUTH handshake to
# Gmail (~1-3s), not the message body. A single worker holding ONE warm SMTP
# connection eliminates that handshake on every code after the first, so
# delivery to any selected account is near-instant. Sends are serialized
# behind the worker, which is fine — a warm send is ~100-300ms.
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="otp-email")
_conn_lock = threading.Lock()
_connection = None


def _open_connection():
    """Return the shared SMTP connection, opening it if needed."""
    global _connection
    if _connection is None:
        conn = get_connection()
        conn.open()
        _connection = conn
    return _connection


def _close_connection():
    global _connection
    if _connection is not None:
        try:
            _connection.close()
        except Exception:
            pass
    _connection = None


def _build_message(email: str, otp: str) -> EmailMultiAlternatives:
    ttl_minutes = getattr(settings, "OTP_TTL_SECONDS", 300) // 60
    subject = "Your TextVibe verification code"
    text_body = (
        f"Your TextVibe verification code is {otp}. "
        f"It expires in {ttl_minutes} minute(s). Do not share it with anyone."
    )
    html_body = f"""
    <div style="font-family:Arial,sans-serif;background:#fff5f8;padding:24px;color:#1a1a2e">
      <div style="max-width:480px;margin:0 auto;background:#fff;border:1px solid #f3e8ff;border-radius:16px;padding:24px">
        <h1 style="margin:0 0 12px;font-size:24px">Text<span style="color:#ff6b9d">Vibe</span></h1>
        <p style="margin:0 0 16px">Use this verification code to sign in:</p>
        <div style="font-size:32px;font-weight:800;letter-spacing:8px;color:#8b5cf6;margin:18px 0">{escape(otp)}</div>
        <p style="margin:0;color:#6b7280">This code expires in {ttl_minutes} minute(s). Do not share it with anyone.</p>
      </div>
    </div>
    """
    from_email = getattr(settings, "DEFAULT_FROM_EMAIL", "")
    if from_email and "<" not in from_email:
        from_email = f"TextVibe <{from_email}>"

    message = EmailMultiAlternatives(
        subject=subject,
        body=text_body,
        from_email=from_email,
        to=[email],
    )
    message.attach_alternative(html_body, "text/html")
    return message


def send_otp_email(email: str, otp: str) -> None:
    message = _build_message(email, otp)

    # Send over the warm connection. Gmail may drop an idle connection, so on
    # any failure reconnect once and retry — the retry pays the handshake, the
    # happy path never does.
    with _conn_lock:
        try:
            message.connection = _open_connection()
            sent = message.send(fail_silently=False)
        except Exception:
            logger.warning("Warm SMTP connection failed; reconnecting", exc_info=True)
            _close_connection()
            message.connection = _open_connection()
            sent = message.send(fail_silently=False)

    if sent < 1:
        raise RuntimeError("SMTP accepted no email recipients.")
    logger.info("Sent TextVibe OTP email to %s", email)


def _send_otp_email_safe(email: str, otp: str) -> None:
    try:
        send_otp_email(email, otp)
    except Exception:
        logger.exception("Background OTP email to %s failed", email)
        try:
            # Delivery failed, so let the user re-request a code immediately
            # instead of waiting out the resend cooldown.
            otp_service.clear_cooldown(email)
        except Exception:
            logger.exception("Could not clear OTP cooldown for %s", email)


def queue_otp_email(email: str, otp: str) -> None:
    """Send the OTP email on a background worker so the HTTP response is not
    blocked by SMTP latency."""
    _executor.submit(_send_otp_email_safe, email, otp)

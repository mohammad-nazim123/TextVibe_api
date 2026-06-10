"""Pluggable OTP delivery.

The backend is chosen by settings.SMS_BACKEND:
- ``console`` (default): logs the message — read the code from the server logs
  during development. No external account required.
- ``twilio`` / ``msg91``: real SMS; credentials come from the environment.
"""

import logging

from django.conf import settings

logger = logging.getLogger("accounts")


def _message(otp: str) -> str:
    ttl_minutes = max(1, settings.OTP_TTL_SECONDS // 60)
    return (
        f"Your TextVibe verification code is {otp}. "
        f"It expires in {ttl_minutes} minute(s). Do not share it with anyone."
    )


def send_otp_sms(phone: str, otp: str) -> None:
    backend = settings.SMS_BACKEND
    message = _message(otp)

    if backend == "console":
        logger.info("[SMS:console] to=%s | %s", phone, message)
        return

    if backend == "twilio":
        _send_twilio(phone, message)
        return

    if backend == "msg91":
        _send_msg91(phone, message)
        return

    raise ValueError(f"Unknown SMS_BACKEND: {backend!r}")


def _send_twilio(phone: str, message: str) -> None:
    # Wire up when SMS_BACKEND=twilio. Requires `pip install twilio` and the
    # TWILIO_* env vars.
    from twilio.rest import Client  # noqa: PLC0415 (lazy import — optional dep)

    client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
    client.messages.create(body=message, from_=settings.TWILIO_FROM_NUMBER, to=phone)


def _send_msg91(phone: str, message: str) -> None:
    # Wire up when SMS_BACKEND=msg91 using the MSG91 OTP/SMS HTTP API and the
    # MSG91_* env vars.
    raise NotImplementedError("MSG91 backend not configured yet.")

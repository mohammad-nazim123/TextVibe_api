"""Redis-backed OTP issuing and verification.

Security properties:
- OTP is never stored in plaintext — only an HMAC-SHA256 digest keyed by
  SECRET_KEY is kept in Redis.
- Codes auto-expire via Redis TTL (OTP_TTL_SECONDS).
- A resend cooldown throttles re-requests (OTP_RESEND_COOLDOWN).
- Verification attempts are capped (OTP_MAX_ATTEMPTS); the code is destroyed once
  the cap is hit or on a successful match.
- Comparison is constant-time (hmac.compare_digest).
"""

import hashlib
import hmac
import secrets

import redis
from django.conf import settings

_client = redis.from_url(settings.REDIS_URL, decode_responses=True)


class OtpError(Exception):
    """Base class for OTP failures surfaced to the API."""


class OtpCooldownError(OtpError):
    def __init__(self, retry_after: int):
        self.retry_after = retry_after
        super().__init__(f"Please wait {retry_after}s before requesting a new code.")


class OtpMaxAttemptsError(OtpError):
    def __init__(self):
        super().__init__("Too many incorrect attempts. Request a new code.")


def _code_key(phone: str) -> str:
    return f"otp:{phone}"


def _attempts_key(phone: str) -> str:
    return f"otp_attempts:{phone}"


def _cooldown_key(phone: str) -> str:
    return f"otp_cooldown:{phone}"


def _digest(phone: str, otp: str) -> str:
    return hmac.new(
        settings.SECRET_KEY.encode(),
        f"{phone}:{otp}".encode(),
        hashlib.sha256,
    ).hexdigest()


def generate_and_store_otp(phone: str) -> str:
    """Create a 6-digit OTP, store its hash in Redis, and return the plaintext
    code (for delivery via SMS). Raises OtpCooldownError if requested too soon."""
    cooldown_ttl = _client.ttl(_cooldown_key(phone))
    if cooldown_ttl and cooldown_ttl > 0:
        raise OtpCooldownError(cooldown_ttl)

    otp = f"{secrets.randbelow(10 ** 6):06d}"

    pipe = _client.pipeline()
    pipe.setex(_code_key(phone), settings.OTP_TTL_SECONDS, _digest(phone, otp))
    pipe.delete(_attempts_key(phone))
    pipe.setex(_cooldown_key(phone), settings.OTP_RESEND_COOLDOWN, "1")
    pipe.execute()
    return otp


def verify_otp(phone: str, otp: str) -> bool:
    """Return True and destroy the OTP on a correct match. Return False if the
    code is wrong or expired. Raises OtpMaxAttemptsError when the attempt cap is
    exceeded."""
    stored = _client.get(_code_key(phone))
    if stored is None:
        return False  # expired or never issued

    attempts = _client.incr(_attempts_key(phone))
    if attempts == 1:
        _client.expire(_attempts_key(phone), settings.OTP_TTL_SECONDS)
    if attempts > settings.OTP_MAX_ATTEMPTS:
        _client.delete(_code_key(phone), _attempts_key(phone))
        raise OtpMaxAttemptsError()

    if hmac.compare_digest(stored, _digest(phone, otp)):
        _client.delete(_code_key(phone), _attempts_key(phone))
        return True
    return False

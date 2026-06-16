"""
Django settings for the TextVibe API.

Secrets are read from a gitignored .env file (see .env.example). Never hardcode
credentials here.
"""

import os
import sys
from datetime import timedelta
from pathlib import Path
from urllib.parse import parse_qsl, urlparse

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env", override=True)


def _env_bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def _env_list(name: str, default: str = "") -> list[str]:
    raw = os.getenv(name, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


# === Core ===
SECRET_KEY = os.environ["SECRET_KEY"]
DEBUG = _env_bool("DEBUG", False)

ALLOWED_HOSTS = [
    "localhost",
    "127.0.0.1",
    "54.169.137.220",
    "api-team-textvibe.educonnectz.in",
]

CSRF_TRUSTED_ORIGINS = [
    "https://api-team-textvibe.educonnectz.in",
    "http://api-team-textvibe.educonnectz.in",
]

CORS_ALLOWED_ORIGINS = [
    "https://api-team-textvibe.educonnectz.in",
    "http://api-team-textvibe.educonnectz.in",
]

DIRECT_DASHBOARD_EMAILS = [item.lower() for item in _env_list("DIRECT_DASHBOARD_EMAILS")]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Third party
    "rest_framework",
    "corsheaders",
    "rest_framework_simplejwt.token_blacklist",
    # Local
    "accounts",
    "payments",
]

MIDDLEWARE = [
    # First so it wraps the final response body; billboard JSON (repetitive
    # style_runs keys) compresses ~10x. Django >=4.2 pads gzip output against
    # BREACH, and the billboard payload is public data anyway.
    "django.middleware.gzip.GZipMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# === Database (Neon Postgres) ===
# Pattern provided by the project owner, hardened for Neon's PgBouncer pooler.
_pg = urlparse(os.environ["DATABASE_URL"])
_pg_options = dict(parse_qsl(_pg.query))
# Disable server-side prepared statements — required for the transaction-pooling
# (-pooler) Neon endpoint when using psycopg3.
_pg_options["prepare_threshold"] = None
# Keep the (now reused) connection to the remote pooler alive across idle gaps
# and fail fast on a dropped link instead of hanging the next query.
_pg_options.update(
    {
        "connect_timeout": 5,
        "keepalives": 1,
        "keepalives_idle": 30,
        "keepalives_interval": 10,
        "keepalives_count": 5,
    }
)

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": _pg.path.replace("/", ""),
        "USER": _pg.username,
        "PASSWORD": _pg.password,
        "HOST": _pg.hostname,
        "PORT": _pg.port or 5432,
        "OPTIONS": _pg_options,
        # Reuse the connection across requests (env-overridable). Safe with
        # Neon's transaction-mode PgBouncer — Django persists its link to the
        # pooler without pinning a Postgres backend — and removes the
        # per-request TLS/connect cost to us-east-2, the dominant send latency.
        "CONN_MAX_AGE": int(os.getenv("DB_CONN_MAX_AGE", "600")),
        # Ping a pooled connection before reuse; replace it if stale.
        "CONN_HEALTH_CHECKS": True,
    }
}

DISABLE_SERVER_SIDE_CURSORS = True

# Tests run against a local in-memory SQLite DB — Neon's pooler endpoint cannot
# create the throwaway test database.
if "test" in sys.argv:
    DATABASES["default"] = {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }

# === Auth ===
AUTH_USER_MODEL = "accounts.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
]

# === DRF ===
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": (
        "rest_framework.permissions.IsAuthenticated",
    ),
    "DEFAULT_THROTTLE_CLASSES": (
        "rest_framework.throttling.ScopedRateThrottle",
    ),
    "DEFAULT_THROTTLE_RATES": {
        "send_otp": "5/min",
        "verify_otp": "10/min",
    },
}

# === JWT ===
# Long-lived sessions by design: the user stays signed in until they log out.
# Rotation is off so a lost rotation response can never blacklist the stored
# refresh token (the bug that used to kick users back to the login screen).
# LogoutView still blacklists the refresh token on explicit logout.
SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(hours=24),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=365),
    "ROTATE_REFRESH_TOKENS": False,
    "BLACKLIST_AFTER_ROTATION": False,
    "UPDATE_LAST_LOGIN": True,
}

# === Caching ===
# LocMem on purpose: the Redis instance is remote, so a cache GET would cost a
# network round trip comparable to the Neon query it replaces. LocMem is
# in-process (microseconds) — right for the short-TTL response caching we do.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "textvibe",
        "OPTIONS": {"MAX_ENTRIES": 1000},
    }
}

# === Billboard long-poll ===
BILLBOARD_LONGPOLL_MAX_WAIT = 25  # seconds a /api/billboard/?wait= request may hold
# How long the cached latest-post id stays trusted before re-checking the DB.
# Also the worst-case extra latency if posts are created in a sibling process.
BILLBOARD_DB_RECHECK_SECONDS = float(os.getenv("BILLBOARD_DB_RECHECK_SECONDS", "2.0"))

# === Redis / OTP policy ===
REDIS_URL = os.environ["REDIS_URL"]
OTP_TTL_SECONDS = int(os.getenv("OTP_TTL_SECONDS", "300"))
OTP_MAX_ATTEMPTS = int(os.getenv("OTP_MAX_ATTEMPTS", "5"))
OTP_RESEND_COOLDOWN = int(os.getenv("OTP_RESEND_COOLDOWN", "60"))

# === SMS ===
SMS_BACKEND = os.getenv("SMS_BACKEND", "console")
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_FROM_NUMBER = os.getenv("TWILIO_FROM_NUMBER", "")
MSG91_AUTH_KEY = os.getenv("MSG91_AUTH_KEY", "")
MSG91_SENDER_ID = os.getenv("MSG91_SENDER_ID", "")
MSG91_TEMPLATE_ID = os.getenv("MSG91_TEMPLATE_ID", "")

# === Email (OTP delivery via Gmail) ===
# Dev default: prints email to the server console instead of sending it.
EMAIL_BACKEND = os.getenv(
    "EMAIL_BACKEND",
    "django.core.mail.backends.console.EmailBackend",
)
EMAIL_HOST = os.getenv("EMAIL_HOST", "smtp.gmail.com")
EMAIL_PORT = int(os.getenv("EMAIL_PORT", "587"))
EMAIL_USE_TLS = _env_bool("EMAIL_USE_TLS", True)
EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD", "").replace(" ", "")
DEFAULT_FROM_EMAIL = os.getenv("DEFAULT_FROM_EMAIL", "noreply@textvibe.com")
EMAIL_TIMEOUT = int(os.getenv("EMAIL_TIMEOUT", "15"))

# === CORS ===
CORS_ALLOWED_ORIGINS = _env_list("CORS_ALLOWED_ORIGINS")
# The billboard website is public read-only; allow any origin in development.
if DEBUG:
    CORS_ALLOW_ALL_ORIGINS = True

# === i18n ===
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

# === Static / Media ===
STATIC_URL = "static/"
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# === Logging (OTP console backend prints here) ===
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "simple": {"format": "[{levelname}] {asctime} {name}: {message}", "style": "{"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "simple"},
    },
    "root": {"handlers": ["console"], "level": "INFO"},
    "loggers": {
        "accounts": {"handlers": ["console"], "level": "INFO", "propagate": False},
    },
}

# === Production hardening (active when DEBUG is off) ===
if not DEBUG:
    SECURE_SSL_REDIRECT = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    X_FRAME_OPTIONS = "DENY"

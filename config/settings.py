"""
AshTender settings.  One module, driven entirely by the environment (.env).

Two principles:

*   **Safe by default.**  Every security setting is ON unless explicitly turned
    off.  Production needs no special settings file and no checklist; if an env
    var is forgotten, the system fails *closed* (HTTPS enforced, cookies
    secure, DEBUG off), never open.  Local development opts *out* of the
    http-hostile settings with a single flag, ``DJANGO_DEV_MODE=1``.

*   **Postgres-first.**  sqlite is used only under the test runner (detected
    automatically), so the suite runs on a laptop with no Postgres while
    production always gets the real backend.  Postgres-only features (pg_trgm,
    tsvector) are guarded at the call site by ``connection.vendor`` checks --
    see tenders/search.py.

Config comes from os.getenv via the four helpers below -- no django-environ.
A .env file next to manage.py is loaded before any of them run; it does not
override variables already set in the shell, so an explicit export still wins.
"""
import os
import sys
from pathlib import Path

from celery.schedules import crontab

BASE_DIR = Path(__file__).resolve().parent.parent

# Load .env next to manage.py before any env() call runs.
try:
    from dotenv import load_dotenv

    load_dotenv(BASE_DIR / ".env")
except ImportError:
    pass


def env(key, default=None):
    return os.getenv(key, default)


def env_bool(key, default=False):
    raw = os.getenv(key)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def env_int(key, default):
    raw = os.getenv(key)
    return int(raw) if raw not in (None, "") else default


def env_list(key, default=""):
    raw = os.getenv(key, default) or ""
    return [item.strip() for item in raw.split(",") if item.strip()]


# --- run mode -----------------------------------------------------------
# DEV_MODE relaxes the http-hostile production settings for local work. It is
# opt-in: absent it, you get production behaviour, which is the safe default.
DEV_MODE = env_bool("DJANGO_DEV_MODE", False)

# The test runner is detected rather than configured, so the suite needs no
# special settings module and no DJANGO_SETTINGS_MODULE juggling.
RUNNING_TESTS = "test" in sys.argv or env_bool("DJANGO_TESTING", False)

SECRET_KEY = env("DJANGO_SECRET_KEY", "dev-only-insecure-key-change-me")
DEBUG = env_bool("DJANGO_DEBUG", False)
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "rest_framework.authtoken",
    "django_filters",
    "treebeard",
    "core",
    "catalog",
    "tenancy",
    "vault",
    "tenders",
    "eligibility",
    "receivables",
    "llm",
    "bids",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "core.middleware.ActiveOrganizationMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
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

# --- database -----------------------------------------------------------
# Postgres everywhere except the test runner, which uses in-memory sqlite so
# the suite runs anywhere. Postgres-only paths are guarded by connection.vendor.
if RUNNING_TESTS:
    DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": env("POSTGRES_DB", "ashtender"),
            "USER": env("POSTGRES_USER", "ashtender"),
            "PASSWORD": env("POSTGRES_PASSWORD", ""),
            "HOST": env("POSTGRES_HOST", "127.0.0.1"),
            "PORT": env("POSTGRES_PORT", "5432"),
            "CONN_MAX_AGE": env_int("POSTGRES_CONN_MAX_AGE", 60),
        }
    }

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-gb"
TIME_ZONE = env("DJANGO_TIME_ZONE", "Africa/Accra")
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "media/"
MEDIA_ROOT = Path(env("DJANGO_MEDIA_ROOT", str(BASE_DIR / "media")))

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.TokenAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_FILTER_BACKENDS": ["django_filters.rest_framework.DjangoFilterBackend"],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.LimitOffsetPagination",
    "PAGE_SIZE": 50,
    "EXCEPTION_HANDLER": "core.exceptions.ashtender_exception_handler",
}

# --- security (safe by default; DEV_MODE relaxes for local http) --------
# ON unless DEV_MODE is set. Forgetting the flag in production is harmless --
# you get the secure behaviour. Forgetting to *set* a "secure=True" flag (the
# old split-settings risk) can't happen, because secure is the default here.
# The test runner is relaxed too: its requests are plain http and an SSL
# redirect would turn every API-isolation test into a 301.
if DEV_MODE or RUNNING_TESTS:
    SECURE_SSL_REDIRECT = False
    SESSION_COOKIE_SECURE = False
    CSRF_COOKIE_SECURE = False
    SECURE_HSTS_SECONDS = 0
    SECURE_HSTS_INCLUDE_SUBDOMAINS = False
    SECURE_HSTS_PRELOAD = False
else:
    SECURE_SSL_REDIRECT = env_bool("DJANGO_SECURE_SSL_REDIRECT", True)
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = env_int("DJANGO_HSTS_SECONDS", 31536000)
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True

SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
# Trust the reverse proxy's forwarded-proto header so SSL redirect works behind
# nginx/caddy terminating TLS. Harmless in dev where nothing sets the header.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# --- Celery -------------------------------------------------------------
CELERY_BROKER_URL = env("CELERY_BROKER_URL", "redis://127.0.0.1:6379/0")
CELERY_RESULT_BACKEND = env("CELERY_RESULT_BACKEND", "redis://127.0.0.1:6379/1")
CELERY_TASK_ALWAYS_EAGER = env_bool("CELERY_TASK_ALWAYS_EAGER", False)
CELERY_TASK_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TIMEZONE = TIME_ZONE

# Nightly, in Africa/Accra. Times are staggered so a slow PayerScore pass does
# not delay the eligibility sweep the morning digest depends on.
CELERY_BEAT_SCHEDULE = {
    "evaluate-all-tenants": {
        "task": "eligibility.tasks.evaluate_all_tenants",
        "schedule": crontab(hour=2, minute=0),
    },
    "recompute-payer-scores": {
        "task": "receivables.tasks.recompute_payer_scores",
        "schedule": crontab(hour=3, minute=30),
    },
    "flag-stalled-ipcs": {
        "task": "receivables.tasks.flag_stalled_ipcs",
        "schedule": crontab(hour=4, minute=0, day_of_week=1),
    },
    # Data acquisition. Harvesting runs several times a day because tender
    # windows are only 7-10 days and first-seen timing is a competitive edge;
    # discipline mining runs nightly after harvesting, before the 02:00
    # eligibility sweep, so newly harvested tenders are tagged in time for it.
    "harvest-all-sources": {
        "task": "tenders.tasks.harvest_all_sources",
        "schedule": crontab(minute=0, hour="*/4"),
    },
    "mine-disciplines": {
        "task": "tenders.tasks.mine_disciplines_task",
        "schedule": crontab(hour=1, minute=30),
    },
}

# --- LLM gateway (spec section 7) --------------------------------------
# OpenAI-compatible base URL.  No local inference: the VPS is low-RAM.
LLM_BASE_URL = env("LLM_BASE_URL", "https://ollama.com/v1")
LLM_API_KEY = env("LLM_API_KEY", "")
LLM_TIMEOUT_SECONDS = env_int("LLM_TIMEOUT_SECONDS", 120)
LLM_CIRCUIT_FAIL_THRESHOLD = env_int("LLM_CIRCUIT_FAIL_THRESHOLD", 5)
LLM_CIRCUIT_RESET_SECONDS = env_int("LLM_CIRCUIT_RESET_SECONDS", 300)
LLM_CACHE_ENABLED = env_bool("LLM_CACHE_ENABLED", True)
# Cross-border transfer of personal data.  MUST be declared to the Ghana Data
# Protection Commission at registration (Act 843 s.27(1)).  See docs/DPC.md.
LLM_PROVIDER_JURISDICTION = env("LLM_PROVIDER_JURISDICTION", "US")

# --- Eligibility engine tunables (spec section 3.8 / 6) ----------------
# Tender value within this fraction of a financial-class cap yields UNKNOWN,
# never FAIL, because the cap figures themselves are stale (spec 3.8).
ELIGIBILITY_CAP_BOUNDARY_FRACTION = float(env("ELIGIBILITY_CAP_BOUNDARY_FRACTION", "0.15"))
ELIGIBILITY_FINANCIAL_YEAR_LOOKBACK = env_int("ELIGIBILITY_FINANCIAL_YEAR_LOOKBACK", 5)
ELIGIBILITY_PAST_PROJECT_LOOKBACK_YEARS = env_int("ELIGIBILITY_PAST_PROJECT_LOOKBACK_YEARS", 5)

# --- PayerScore publication floor (spec section 8) ---------------------
PAYER_SCORE_MIN_CONTRACTS = env_int("PAYER_SCORE_MIN_CONTRACTS", 5)
PAYER_SCORE_MIN_TENANTS = env_int("PAYER_SCORE_MIN_TENANTS", 3)

# Under the test runner, keep the suite fast and network-free.
if RUNNING_TESTS:
    CELERY_TASK_ALWAYS_EAGER = True
    LLM_CACHE_ENABLED = True
    LLM_API_KEY = "test-key-not-used-network-is-mocked"
    PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"plain": {"format": "%(asctime)s %(levelname)s %(name)s %(message)s"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "plain"}},
    "root": {"handlers": ["console"], "level": env("DJANGO_LOG_LEVEL", "INFO")},
    "loggers": {
        "core.tenancy": {"handlers": ["console"], "level": "WARNING", "propagate": False},
    },
}

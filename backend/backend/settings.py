"""Django settings for the AutoQuant backend.

In dev (DJANGO_DEBUG=1) the defaults are friendly: localhost only, insecure
SECRET_KEY, SQLite in the backend dir. In prod (DJANGO_DEBUG=0) everything
sensitive is required via env (SECRET_KEY, ALLOWED_HOSTS,
CSRF_TRUSTED_ORIGINS) and cookies + proxy-SSL trust are flipped on so the
container can sit behind nginx (in compose) and a Cloudflare Tunnel.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _env_list(name: str, default: list[str]) -> list[str]:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return [s.strip() for s in raw.split(",") if s.strip()]


BASE_DIR = Path(__file__).resolve().parent.parent          # .../AutoQuant/backend
PROJECT_ROOT = BASE_DIR.parent                              # .../AutoQuant
sys.path.insert(0, str(PROJECT_ROOT))                       # so `import autoquant` works

# --------------------------------------------------------------------------- #
# Core
# --------------------------------------------------------------------------- #
DEBUG = _env_bool("DJANGO_DEBUG", True)

SECRET_KEY = os.environ.get(
    "DJANGO_SECRET_KEY",
    "django-insecure-dev-key-replace-via-DJANGO_SECRET_KEY-env-var",
)
if not DEBUG and SECRET_KEY.startswith("django-insecure-"):
    raise RuntimeError(
        "DJANGO_SECRET_KEY must be set to a non-default value when DJANGO_DEBUG=0"
    )

# 'testserver' kept so the Django test client works in pytest.
ALLOWED_HOSTS = _env_list(
    "DJANGO_ALLOWED_HOSTS", ["127.0.0.1", "localhost", "testserver"]
)
CSRF_TRUSTED_ORIGINS = _env_list("DJANGO_CSRF_TRUSTED_ORIGINS", [])

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "portfolio_app",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "backend.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "backend.wsgi.application"

# --------------------------------------------------------------------------- #
# Database -- SQLite, path overridable via DJANGO_DB_PATH so the Docker
# container can point at a mounted volume.
# --------------------------------------------------------------------------- #
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": os.environ.get("DJANGO_DB_PATH", str(BASE_DIR / "db.sqlite3")),
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Europe/Berlin"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --------------------------------------------------------------------------- #
# Production hardening (when DJANGO_DEBUG=0)
# --------------------------------------------------------------------------- #
# In the docker-compose setup, nginx (frontend container) terminates the
# HTTP-from-Cloudflare connection and proxies to gunicorn. Cloudflare delivers
# HTTPS from the public edge, terminating TLS there. nginx forwards
# X-Forwarded-Proto: https so Django trusts the request as secure.
if not DEBUG:
    USE_X_FORWARDED_HOST = True
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SESSION_COOKIE_SAMESITE = "Lax"
    CSRF_COOKIE_SAMESITE = "Lax"
    # HSTS is left for Cloudflare to set at the edge.

# --------------------------------------------------------------------------- #
# Auth redirects (SPA uses /api/auth/* JSON endpoints; /admin uses these)
# --------------------------------------------------------------------------- #
LOGIN_URL = "/admin/login/"
LOGIN_REDIRECT_URL = "/admin/"

"""
Django settings for the ISP Management Platform.
"""

import sys
from pathlib import Path
from datetime import timedelta

from django.core.exceptions import ImproperlyConfigured
from decouple import config, Csv

BASE_DIR = Path(__file__).resolve().parent.parent

_INSECURE_DEV_SECRET_KEY = "django-insecure-dev-key-change-in-production"
SECRET_KEY = config("SECRET_KEY", default=_INSECURE_DEV_SECRET_KEY)
DEBUG = config("DEBUG", default=True, cast=bool)

# Refuse to boot in production on the committed fallback key. SECRET_KEY
# doesn't only sign sessions here -- config/media_security.py derives the
# signature on every /media/ download link from it, so running on the
# public default would let anyone forge a link to any uploaded file
# (expense receipts, supplier invoices, staff sick notes). Failing loudly
# at startup beats silently serving those to whoever asks.
if not DEBUG and SECRET_KEY == _INSECURE_DEV_SECRET_KEY:
    raise ImproperlyConfigured(
        "SECRET_KEY is still the built-in development fallback while DEBUG=False. "
        "Set a real, random SECRET_KEY in the environment/.env before running in production."
    )
ALLOWED_HOSTS = config("ALLOWED_HOSTS", default="*", cast=Csv())
CORS_ALLOW_ALL_ORIGINS = config("CORS_ALLOW_ALL_ORIGINS", default=True, cast=bool)

# The same fail-loudly-at-startup treatment SECRET_KEY already gets, for
# the two other settings that default wide open. Both defaults are right
# for local development and wrong on a public host, and neither announces
# itself when it is wrong -- ALLOWED_HOSTS="*" accepts any Host header
# (so cache-poisoning and password-reset links pointed at an attacker's
# domain both become possible), and CORS_ALLOW_ALL_ORIGINS=True lets any
# website on the internet read authenticated API responses from a
# logged-in staff member's browser.
#
# Checked only when DEBUG is False, so a developer running locally is
# unaffected. On the VPS this turns two silent misconfigurations into a
# container that refuses to start and says which line to fix.
if not DEBUG:
    if "*" in ALLOWED_HOSTS:
        raise ImproperlyConfigured(
            'ALLOWED_HOSTS is still "*" while DEBUG=False. Set it to the real hostnames, '
            "e.g. ALLOWED_HOSTS=portal.skybre.co.za,154.65.111.61"
        )
    if CORS_ALLOW_ALL_ORIGINS:
        raise ImproperlyConfigured(
            "CORS_ALLOW_ALL_ORIGINS is True while DEBUG=False. Set it to False and list the "
            "real origins in CORS_ALLOWED_ORIGINS, e.g. CORS_ALLOWED_ORIGINS=https://portal.skybre.co.za"
        )

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # third party
    "rest_framework",
    "rest_framework_simplejwt",
    "corsheaders",
    "django_filters",
    # local apps
    "accounts",
    "customers",
    "billing",
    "network",
    "tickets",
    "scheduling",
    "inventory",
    "notifications",
    "payroll",
    "fleet",
    "radiusauth",
    "bankfeeds",
    "expenses",
    "audit",
    "sales",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "audit.middleware.AuditActorMiddleware",
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

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": config("DB_NAME", default="ispplatform"),
        "USER": config("DB_USER", default="postgres"),
        "PASSWORD": config("DB_PASSWORD", default="postgres"),
        "HOST": config("DB_HOST", default="localhost"),
        "PORT": config("DB_PORT", default="5432"),
    }
}

AUTH_USER_MODEL = "accounts.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 6}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
# South African Standard Time — UTC+2, no daylight saving, so this is a
# fixed offset year-round. USE_TZ stays True: everything is still stored
# in the database as UTC internally, this only affects how "today"/"now"
# get interpreted for display and for date boundaries (e.g. which day an
# invoice's auto_now_add date lands on if created late at night).
TIME_ZONE = "Africa/Johannesburg"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

# User-uploaded files (currently just supplier-invoice attachments on
# stock receipts). Served via a plain Django view in config/urls.py —
# fine at this data volume; revisit with a dedicated static host/nginx
# location if upload volume grows significantly.
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

# Where to write the generated FreeRADIUS clients.conf so the HOST can pick
# it up (see radiusauth/clients_conf.py). FreeRADIUS runs on the host, not
# in this container, so editing a RADIUS client in the admin panel has to
# reach it via a bind-mounted spool file that a host-side systemd path unit
# validates and installs.
#
# Blank/unset disables spooling entirely and every write becomes a no-op,
# so a dev machine -- or a deployment that hasn't added the bind mount yet
# -- behaves exactly as it did before, rather than erroring on every save.
RADIUS_CLIENTS_SPOOL = config("RADIUS_CLIENTS_SPOOL", default="")
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": (
        "rest_framework.permissions.IsAuthenticated",
    ),
    "DEFAULT_FILTER_BACKENDS": (
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.SearchFilter",
        "rest_framework.filters.OrderingFilter",
    ),
    "DEFAULT_PAGINATION_CLASS": "config.pagination.StandardPagination",
    "PAGE_SIZE": 25,
    # Only the unauthenticated customer usage page uses a throttle scope.
    # Everything else requires a JWT, so there is nothing to probe; that one
    # is reachable by anyone holding a URL, and a wrong token must not be
    # cheap to retry in bulk. Generous enough that a customer refreshing
    # their own page never notices.
    "DEFAULT_THROTTLE_RATES": {
        "public_usage": "60/min",
    },
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(hours=8),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
    "ROTATE_REFRESH_TOKENS": True,
}

# CORS_ALLOW_ALL_ORIGINS is read at the top of this file, next to
# ALLOWED_HOSTS, so the boot guard there can check it.
CORS_ALLOWED_ORIGINS = config("CORS_ALLOWED_ORIGINS", default="", cast=Csv())

# Required by Django once the site is served over HTTPS behind a reverse
# proxy — without this, POSTs to the Django admin (session/cookie-based,
# unlike the JWT-authenticated API) get rejected with a CSRF failure.
# e.g. CSRF_TRUSTED_ORIGINS=https://portal.skybre.co.za
CSRF_TRUSTED_ORIGINS = config("CSRF_TRUSTED_ORIGINS", default="", cast=Csv())

# Production hardening — all off by default (dev), flip on via env in production.
# Behind a reverse proxy (Nginx/Caddy) terminating TLS, also set:
#   SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
# and ensure the proxy sets X-Forwarded-Proto.
SECURE_SSL_REDIRECT = config("SECURE_SSL_REDIRECT", default=False, cast=bool)

# ...but never while running the test suite.
#
# `manage.py test` reads the same .env as the running site, and on the VPS
# that .env sets SECURE_SSL_REDIRECT=True (correctly — the site is
# HTTPS-only). The Django test client speaks plain HTTP, so with the
# redirect active EVERY request a test makes comes back as a 301 to the
# https:// URL and no view is ever reached. The symptom is brutal to read:
# dozens of "AssertionError: 301 != 200" plus
# "'HttpResponsePermanentRedirect' object has no attribute 'data'", spread
# across every app that has API tests, none of which is a real defect.
#
# It made the whole suite unrunnable on the production box, which is
# exactly where you most want to run it before trusting a deploy.
if "test" in sys.argv:
    SECURE_SSL_REDIRECT = False
SESSION_COOKIE_SECURE = config("SESSION_COOKIE_SECURE", default=False, cast=bool)
CSRF_COOKIE_SECURE = config("CSRF_COOKIE_SECURE", default=False, cast=bool)
if config("BEHIND_HTTPS_PROXY", default=False, cast=bool):
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# Outbound email (customer notifications — welcome, statements, invoices,
# payment reminders, suspension notices — see the notifications app).
# Defaults to Django's console backend so uncofigured/dev environments
# just print emails to stdout instead of failing loudly; set EMAIL_HOST in
# .env once real SMTP credentials (your domain's mail hosting, Google
# Workspace, or a transactional provider) are available to switch it on.
EMAIL_HOST = config("EMAIL_HOST", default="")
EMAIL_BACKEND = (
    "django.core.mail.backends.smtp.EmailBackend"
    if EMAIL_HOST
    else "django.core.mail.backends.console.EmailBackend"
)
EMAIL_PORT = config("EMAIL_PORT", default=587, cast=int)
EMAIL_HOST_USER = config("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = config("EMAIL_HOST_PASSWORD", default="")
EMAIL_USE_TLS = config("EMAIL_USE_TLS", default=True, cast=bool)
EMAIL_USE_SSL = config("EMAIL_USE_SSL", default=False, cast=bool)
DEFAULT_FROM_EMAIL = config("DEFAULT_FROM_EMAIL", default="Skybre <no-reply@skybre.co.za>")
COMPANY_NAME = config("COMPANY_NAME", default="Skybre")
# Base URL for links inside emails (e.g. the customer portal login link
# in the welcome email) — set to the real public site once DNS/HTTPS is live.
SITE_URL = config("SITE_URL", default="https://skybre.co.za")

import os

DEBUG = False

# Full path to the base project directory
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

INSTALLED_APPS = [
    "django.contrib.staticfiles",
    "django.contrib.postgres",
    "archmanweb",
]

# https://docs.djangoproject.com/en/3.1/topics/http/middleware/
MIDDLEWARE = [
    # https://docs.djangoproject.com/en/3.1/ref/middleware/#django.middleware.common.CommonMiddleware
    "django.middleware.common.CommonMiddleware",
    # https://docs.djangoproject.com/en/3.1/ref/csrf/
    "django.middleware.csrf.CsrfViewMiddleware",
    # https://docs.djangoproject.com/en/3.1/ref/clickjacking/
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    # https://docs.djangoproject.com/en/3.1/ref/middleware/#django.middleware.security.SecurityMiddleware
    "django.middleware.security.SecurityMiddleware",
    # https://django-csp.readthedocs.io/en/latest/
    "csp.middleware.CSPMiddleware",
]

# Referrer Policy
SECURE_REFERRER_POLICY = 'no-referrer-when-downgrade'

# X-XSS-Protection, enables cross-site scripting filter in most browsers
SECURE_BROWSER_XSS_FILTER = True

# CSP Settings
CSP_DEFAULT_SRC = ("'self'",)
CSP_SCRIPT_SRC = ("'none'",)
CSP_IMG_SRC = ("'self'",)
CSP_BASE_URI = ("'none'",)
CSP_FORM_ACTION = ("'self'",)
CSP_FRAME_ANCESTORS = ("'none'",)

# Base of the URL hierarchy
ROOT_URLCONF = "urls"

# requires CommonMiddleware
APPEND_SLASH = True

# URL to serve static files
STATIC_URL = "/static/"

# Location to collect static files
STATIC_ROOT = os.path.join(BASE_DIR, "collected_static")

# Look for more static files in these locations
# (use a tuple to keep the directory namespaced)
# https://docs.djangoproject.com/en/3.1/ref/settings/#prefixes-optional
STATICFILES_DIRS = (
    ("archlinux-common", os.path.join(BASE_DIR, "archlinux-common-style/css")),
    ("archlinux-common", os.path.join(BASE_DIR, "archlinux-common-style/img")),
)

# Static files backend that appends the MD5 hash of the file’s content to the filename
# (this allows us to use far future Expires headers)
STATICFILES_STORAGE = "django.contrib.staticfiles.storage.ManifestStaticFilesStorage"

# Internationalization
# https://docs.djangoproject.com/en/3.1/topics/i18n/
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = False
USE_L10N = False
USE_TZ = True

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "debug": DEBUG,
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
            ],
        },
    },
]

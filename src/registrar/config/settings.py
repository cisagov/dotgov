"""
Django settings for .gov registrar project.

For more information on this file, see
https://docs.djangoproject.com/en/4.0/topics/settings/

For the full list of settings and their values, see
https://docs.djangoproject.com/en/4.0/ref/settings/

IF you'd like to see all of these settings in the running app:

```shell
$ docker-compose exec app python manage.py shell
>>> from django.conf import settings
>>> dir(settings)
```

"""

import environs
from base64 import b64decode
from cfenv import AppEnv  # type: ignore
from pathlib import Path
from typing import Final
from botocore.config import Config
import json
import logging
from django.utils.log import ServerFormatter

# # #                          ###
#      Setup code goes here      #
# # #                          ###

env = environs.Env()

# Get secrets from Cloud.gov user provided service, if exists
# If not, get secrets from environment variables
key_service = AppEnv().get_service(name="getgov-credentials")


# Get secrets from Cloud.gov user provided s3 service, if it exists
s3_key_service = AppEnv().get_service(name="getgov-s3")

if key_service and key_service.credentials:
    if s3_key_service and s3_key_service.credentials:
        # Concatenate the credentials from our S3 service into our secret service
        key_service.credentials.update(s3_key_service.credentials)
    secret = key_service.credentials.get
else:
    secret = env


# # #                          ###
#   Values obtained externally   #
# # #                          ###

path = Path(__file__)

env_db_url = env.dj_db_url("DATABASE_URL")
env_debug = env.bool("DJANGO_DEBUG", default=False)
env_is_production = env.bool("IS_PRODUCTION", default=False)
env_log_level = env.str("DJANGO_LOG_LEVEL", "DEBUG")
env_base_url: str = env.str("DJANGO_BASE_URL")
env_getgov_public_site_url = env.str("GETGOV_PUBLIC_SITE_URL", "")
env_oidc_active_provider = env.str("OIDC_ACTIVE_PROVIDER", "identity sandbox")

secret_login_key = b64decode(secret("DJANGO_SECRET_LOGIN_KEY", ""))
secret_key = secret("DJANGO_SECRET_KEY")

secret_aws_ses_key_id = secret("AWS_ACCESS_KEY_ID", None)
secret_aws_ses_key = secret("AWS_SECRET_ACCESS_KEY", None)

# These keys are present in a getgov-s3 instance, or they can be defined locally
aws_s3_region_name = secret("region", None) or secret("AWS_S3_REGION", None)
secret_aws_s3_key_id = secret("access_key_id", None) or secret("AWS_S3_ACCESS_KEY_ID", None)
secret_aws_s3_key = secret("secret_access_key", None) or secret("AWS_S3_SECRET_ACCESS_KEY", None)
secret_aws_s3_bucket_name = secret("bucket", None) or secret("AWS_S3_BUCKET_NAME", None)

# Passphrase for the encrypted metadata email
secret_encrypt_metadata = secret("SECRET_ENCRYPT_METADATA", None)

secret_registry_cl_id = secret("REGISTRY_CL_ID")
secret_registry_password = secret("REGISTRY_PASSWORD")
secret_registry_cert = b64decode(secret("REGISTRY_CERT", ""))
secret_registry_key = b64decode(secret("REGISTRY_KEY", ""))
secret_registry_key_passphrase = secret("REGISTRY_KEY_PASSPHRASE", "")
secret_registry_hostname = secret("REGISTRY_HOSTNAME")

# region: Basic Django Config-----------------------------------------------###

# Build paths inside the project like this: BASE_DIR / "subdir".
# (settings.py is in `src/registrar/config/`: BASE_DIR is `src/`)
BASE_DIR = path.resolve().parent.parent.parent

# SECURITY WARNING: don't run with debug turned on in production!
# TODO - Investigate the behaviour of this flag. Does not appear
# to function for the IS_PRODUCTION flag.
DEBUG = env_debug

# Controls production specific feature toggles
IS_PRODUCTION = env_is_production
SECRET_ENCRYPT_METADATA = secret_encrypt_metadata

# Applications are modular pieces of code.
# They are provided by Django, by third-parties, or by yourself.
# Installing them here makes them available for execution.
# Do not access INSTALLED_APPS directly. Use `django.apps.apps` instead.
INSTALLED_APPS = [
    # let's be sure to install our own application!
    # it needs to be listed before django.contrib.admin
    # otherwise Django would find the default template
    # provided by django.contrib.admin first and use
    # that instead of our custom templates.
    "registrar",
    # Django automatic admin interface reads metadata
    # from database models to provide a quick, model-centric
    # interface where trusted users can manage content
    "django.contrib.admin",
    # vv Required by django.contrib.admin vv
    # the "user" model! *\o/*
    "django.contrib.auth",
    # audit logging of changes to models
    # it needs to be listed before django.contrib.contenttypes
    # for a ContentType query in fixtures.py
    "auditlog",
    # generic interface for Django models
    "django.contrib.contenttypes",
    # required for CSRF protection and many other things
    "django.contrib.sessions",
    # framework for displaying messages to the user
    "django.contrib.messages",
    # ^^ Required by django.contrib.admin ^^
    # collects static files from each of your applications
    # (and any other places you specify) into a single location
    # that can easily be served in production
    "django.contrib.staticfiles",
    # application used for integrating with Login.gov
    "djangooidc",
    # library to simplify form templating
    "widget_tweaks",
    # library for Finite State Machine statuses
    "django_fsm",
    # library for phone numbers
    "phonenumber_field",
    # Our internal API application
    "api",
    # Only for generating documentation, uncomment to run manage.py generate_puml
    # "puml_generator",
    # supports necessary headers for Django cross origin
    "corsheaders",
    # library for multiple choice filters in django admin
    "django_admin_multiple_choice_list_filter",
    # library for export and import of data
    "import_export",
    # Waffle feature flags
    "waffle",
]

# Middleware are routines for processing web requests.
# Adding them here turns them "on"; Django will perform the
# specified routines on each incoming request and outgoing response.
MIDDLEWARE = [
    # django-allow-cidr: enable use of CIDR IP ranges in ALLOWED_HOSTS
    "allow_cidr.middleware.AllowCIDRMiddleware",
    # django-cors-headers: listen to cors responses
    "corsheaders.middleware.CorsMiddleware",
    # custom middleware to stop caching from CloudFront
    "registrar.registrar_middleware.NoCacheMiddleware",
    # serve static assets in production
    "whitenoise.middleware.WhiteNoiseMiddleware",
    # provide security enhancements to the request/response cycle
    "django.middleware.security.SecurityMiddleware",
    # store and retrieve arbitrary data on a per-site-visitor basis
    "django.contrib.sessions.middleware.SessionMiddleware",
    # add a few conveniences for perfectionists, see documentation
    "django.middleware.common.CommonMiddleware",
    # add protection against Cross Site Request Forgeries by adding
    # hidden form fields to POST forms and checking requests for the correct value
    "django.middleware.csrf.CsrfViewMiddleware",
    # add `user` (the currently-logged-in user) to incoming HttpRequest objects
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    # Require login for every single request by default
    "login_required.middleware.LoginRequiredMiddleware",
    # provide framework for displaying messages to the user, see documentation
    "django.contrib.messages.middleware.MessageMiddleware",
    # provide clickjacking protection via the X-Frame-Options header
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    # django-csp: enable use of Content-Security-Policy header
    "csp.middleware.CSPMiddleware",
    # django-auditlog: obtain the request User for use in logging
    "auditlog.middleware.AuditlogMiddleware",
    # Used for waffle feature flags
    "waffle.middleware.WaffleMiddleware",
    "registrar.registrar_middleware.CheckUserProfileMiddleware",
    "registrar.registrar_middleware.CheckPortfolioMiddleware",
]

# application object used by Django's built-in servers (e.g. `runserver`)
WSGI_APPLICATION = "registrar.config.wsgi.application"

# endregion
# region: Assets and HTML and Caching---------------------------------------###

# https://docs.djangoproject.com/en/4.0/howto/static-files/


CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.db.DatabaseCache",
        "LOCATION": "cache_table",
    }
}

# Absolute path to the directory where `collectstatic`
# will place static files for deployment.
# Do not use this directory for permanent storage -
# it is for Django!
STATIC_ROOT = BASE_DIR / "registrar" / "public"

STATICFILES_DIRS = [
    BASE_DIR / "registrar" / "assets",
]

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        # look for templates inside installed apps
        #     required by django-debug-toolbar
        "APP_DIRS": True,
        "OPTIONS": {
            # IMPORTANT security setting: escapes HTMLEntities,
            #     helping to prevent XSS attacks
            "autoescape": True,
            # context processors are callables which return
            #     dicts - Django merges them into the context
            #     dictionary used to render the templates
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "registrar.context_processors.language_code",
                "registrar.context_processors.canonical_path",
                "registrar.context_processors.is_demo_site",
                "registrar.context_processors.is_production",
                "registrar.context_processors.org_user_status",
                "registrar.context_processors.add_path_to_context",
                "registrar.context_processors.portfolio_permissions",
                "registrar.context_processors.is_widescreen_mode",
            ],
        },
    },
]

# Stop using table-based default form renderer which is deprecated
FORM_RENDERER = "django.forms.renderers.DjangoDivFormRenderer"

MESSAGE_STORAGE = "django.contrib.messages.storage.session.SessionStorage"

# IS_DEMO_SITE controls whether or not we show our big red "TEST SITE" banner
# underneath the "this is a real government website" banner.
IS_DEMO_SITE = True

# endregion
# region: Database----------------------------------------------------------###

# Wrap each view in a transaction on the database
# A decorator can be used for views which have no database activity:
#     from django.db import transaction
#     @transaction.non_atomic_requests
env_db_url["ATOMIC_REQUESTS"] = True

DATABASES = {
    # dj-database-url package takes the supplied Postgres connection string
    # and converts it into a dictionary with the correct USER, HOST, etc
    "default": env_db_url,
}

# Specify default field type to use for primary keys
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Use our user model instead of the default
AUTH_USER_MODEL = "registrar.User"

# endregion
# region: Email-------------------------------------------------------------###

# Configuration for accessing AWS SES
AWS_ACCESS_KEY_ID = secret_aws_ses_key_id
AWS_SECRET_ACCESS_KEY = secret_aws_ses_key
AWS_REGION = "us-gov-west-1"

# Configuration for accessing AWS S3
AWS_S3_ACCESS_KEY_ID = secret_aws_s3_key_id
AWS_S3_SECRET_ACCESS_KEY = secret_aws_s3_key
AWS_S3_REGION = aws_s3_region_name
AWS_S3_BUCKET_NAME = secret_aws_s3_bucket_name

# https://boto3.amazonaws.com/v1/documentation/latest/guide/retries.html#standard-retry-mode
AWS_RETRY_MODE: Final = "standard"
# base 2 exponential backoff with max of 20 seconds:
AWS_MAX_ATTEMPTS = 3
BOTO_CONFIG = Config(retries={"mode": AWS_RETRY_MODE, "max_attempts": AWS_MAX_ATTEMPTS})

# email address to use for various automated correspondence
# also used as a default to and bcc email
DEFAULT_FROM_EMAIL = "help@get.gov <help@get.gov>"

# connect to an (external) SMTP server for sending email
EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"

# TODO: configure these when the values are known
# EMAIL_HOST = ""
# EMAIL_HOST_PASSWORD = ""
# EMAIL_HOST_USER = ""
# EMAIL_PORT = 587

# for mail sent with mail_admins or mail_managers
EMAIL_SUBJECT_PREFIX = "[Attn: .gov admin] "

# use a TLS (secure) connection when talking to the SMTP server
# TLS generally uses port 587
EMAIL_USE_TLS = True

# mutually exclusive with EMAIL_USE_TLS = True
# SSL generally uses port 465
EMAIL_USE_SSL = False

# timeout in seconds for blocking operations, like the connection attempt
EMAIL_TIMEOUT = 30

# email address to use for sending error reports
SERVER_EMAIL = "root@get.gov"

# endregion

# region: Waffle feature flags-----------------------------------------------------------###
# If Waffle encounters a reference to a flag that is not in the database, create the flag automagically.
WAFFLE_CREATE_MISSING_FLAGS = True

# The model that will be used to keep track of flags. Extends AbstractUserFlag.
# Used to replace the default flag class (for customization purposes).
WAFFLE_FLAG_MODEL = "registrar.WaffleFlag"

# endregion

# region: Headers-----------------------------------------------------------###

# Content-Security-Policy configuration
# this can be restrictive because we have few external scripts
allowed_sources = ("'self'",)
CSP_DEFAULT_SRC = allowed_sources
# Most things fall back to default-src, but the following do not and should be
# explicitly set
CSP_FRAME_ANCESTORS = allowed_sources
CSP_FORM_ACTION = allowed_sources

# Google analytics requires that we relax our otherwise
# strict CSP by allowing scripts to run from their domain
# and inline with a nonce, as well as allowing connections back to their domain.
# Note: If needed, we can embed chart.js instead of using the CDN
CSP_DEFAULT_SRC = ("'self'",)
CSP_STYLE_SRC = [
    "'self'",
    "https://www.ssa.gov/accessibility/andi/andi.css",
    "https://cdn.jsdelivr.net/npm/select2@4.1.0-rc.0/dist/css/select2.min.css",
]
CSP_SCRIPT_SRC_ELEM = [
    "'self'",
    "https://www.googletagmanager.com/",
    "https://cdn.jsdelivr.net/npm/chart.js",
    "https://www.ssa.gov",
    "https://ajax.googleapis.com",
    "https://cdn.jsdelivr.net/npm/select2@4.1.0-rc.0/dist/js/select2.min.js",
]
CSP_CONNECT_SRC = ["'self'", "https://www.google-analytics.com/", "https://www.ssa.gov/accessibility/andi/andi.js"]
CSP_INCLUDE_NONCE_IN = ["script-src-elem", "style-src"]
CSP_IMG_SRC = ["'self'", "https://www.ssa.gov/accessibility/andi/icons/"]

# Cross-Origin Resource Sharing (CORS) configuration
# Sets clients that allow access control to manage.get.gov
# TODO: remove :8080 to see if we can have all localhost access
CORS_ALLOWED_ORIGINS = ["http://localhost:8080", "https://beta.get.gov", "https://get.gov"]
CORS_ALLOWED_ORIGIN_REGEXES = [r"https://[\w-]+\.sites\.pages\.cloud\.gov"]

# Content-Length header is set by django.middleware.common.CommonMiddleware

# X-Frame-Options header is set by
#     django.middleware.clickjacking.XFrameOptionsMiddleware
#     and configured in the Security and Privacy section of this file.
# Strict-Transport-Security is set by django.middleware.security.SecurityMiddleware
#     and configured in the Security and Privacy section of this file.

# prefer contents of X-Forwarded-Host header to Host header
# as Host header may contain a proxy rather than the actual client
USE_X_FORWARDED_HOST = True

# endregion
# region: Internationalisation----------------------------------------------###

# https://docs.djangoproject.com/en/4.0/topics/i18n/

# Charset to use for HttpResponse objects; used in Content-Type header
DEFAULT_CHARSET = "utf-8"

# provide fallback language if translation file is missing or
# user's locale is not supported - requires USE_I18N = True
LANGUAGE_CODE = "en-us"

# allows language cookie to be sent if the user
# is coming to our site from an external page.
LANGUAGE_COOKIE_SAMESITE = None

# only send via HTTPS connection
LANGUAGE_COOKIE_SECURE = True

# to display datetimes in templates
# and to interpret datetimes entered in forms
TIME_ZONE = "UTC"

# enable Django's translation system
USE_I18N = True

# enable localized formatting of numbers and dates
USE_L10N = True

# make datetimes timezone-aware by default
USE_TZ = True

# setting for phonenumber library
PHONENUMBER_DEFAULT_REGION = "US"

# endregion
# region: Logging-----------------------------------------------------------###

# A Python logging configuration consists of four parts:
#   Loggers
#   Handlers
#   Filters
#   Formatters
# https://docs.djangoproject.com/en/4.1/topics/logging/

# Log a message by doing this:
#
#   import logging
#   logger = logging.getLogger(__name__)
#
# Then:
#
#   logger.debug("We're about to execute function xyz. Wish us luck!")
#   logger.info("Oh! Here's something you might want to know.")
#   logger.warning("Something kinda bad happened.")
#   logger.error("Can't do this important task. Something is very wrong.")
#   logger.critical("Going to crash now.")


class JsonFormatter(logging.Formatter):
    """Formats logs into JSON for better parsing"""

    def __init__(self):
        super().__init__(datefmt="%d/%b/%Y %H:%M:%S")

    def format(self, record):
        log_record = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "name": record.name,
            "lineno": record.lineno,
            "message": record.getMessage(),
        }
        return json.dumps(log_record)


class JsonServerFormatter(ServerFormatter):
    """Formats server logs into JSON for better parsing"""

    def format(self, record):
        formatted_record = super().format(record)
        if not hasattr(record, "server_time"):
            record.server_time = self.formatTime(record, self.datefmt)
        log_entry = {"server_time": record.server_time, "level": record.levelname, "message": formatted_record}
        return json.dumps(log_entry)


# default to regular formatted logs
server_handlers, console_handlers = ["django.server"], ["console"]

# if not running locally, include json handlers for error logs
if "localhost" not in env_base_url:
    server_handlers.append("json.server")
    console_handlers.append("json")

LOGGING = {
    "version": 1,
    # Don't import Django's existing loggers
    "disable_existing_loggers": True,
    # define how to convert log messages into text;
    # each handler has its choice of format
    "formatters": {
        "verbose": {
            "format": "[%(asctime)s] %(levelname)s [%(name)s:%(lineno)s] %(message)s",
            "datefmt": "%d/%b/%Y %H:%M:%S",
        },
        "simple": {
            "format": "%(levelname)s %(message)s",
        },
        "django.server": {
            "()": "django.utils.log.ServerFormatter",
            "format": "[{server_time}] {message}",
            "style": "{",
        },
        "json.server": {
            "()": JsonServerFormatter,
        },
        "json": {
            "()": JsonFormatter,
        },
    },
    # define where log messages will be sent;
    # each logger can have one or more handlers
    "handlers": {
        "console": {
            "level": env_log_level,
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
        "django.server": {
            "level": "INFO",
            "class": "logging.StreamHandler",
            "formatter": "django.server",
        },
        "json":{
            "level": "ERROR",
            "class": "logging.StreamHandler",
            "formatter": "json",
        },
        "json.server": {
            "level": "ERROR",
            "class": "logging.StreamHandler",
            "formatter": "json.server",
        }
        
        # No file logger is configured,
        # because containerized apps
        # do not log to the file system.
    },
    # define loggers: these are "sinks" into which
    # messages are sent for processing
    "loggers": {
        # Django's generic logger
        "django": {
            "handlers": [console_handlers],
            "level": "INFO",
            "propagate": False,
        },
        # Django's template processor
        "django.template": {
            "handlers": [console_handlers],
            "level": "INFO",
            "propagate": False,
        },
        # Django's runserver
        "django.server": {
            "handlers": [server_handlers],
            "level": "INFO",
            "propagate": False,
        },
        # Django's runserver requests
        "django.request": {
            "handlers": [server_handlers],
            "level": "INFO",
            "propagate": False,
        },
        # OpenID Connect logger
        "oic": {
            "handlers": [console_handlers],
            "level": "INFO",
            "propagate": False,
        },
        # Django wrapper for OpenID Connect
        "djangooidc": {
            "handlers": [console_handlers],
            "level": "INFO",
            "propagate": False,
        },
        # Our app!
        "registrar": {
            "handlers": [console_handlers],
            "level": "DEBUG",
            "propagate": False,
        },
    },
    # root logger catches anything, unless
    # defined by a more specific logger
    "root": {
        "handlers": ["console"],
        "level": "INFO",
    },
}

# endregion
# region: Login-------------------------------------------------------------###

# list of Python classes used when trying to authenticate a user
AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
    "djangooidc.backends.OpenIdConnectBackend",
]

# this is where unauthenticated requests are redirected when using
# the login_required() decorator, LoginRequiredMixin, or AccessMixin
LOGIN_URL = "/openid/login"

# We don't want the OIDC app to be login-required because then it can't handle
# the initial login requests without erroring.
LOGIN_REQUIRED_IGNORE_PATHS = [
    r"/openid/(.+)$",
]

# where to go after logging out
LOGOUT_REDIRECT_URL = "https://get.gov/"

# disable dynamic client registration,
# only the OP inside OIDC_PROVIDERS will be available
OIDC_ALLOW_DYNAMIC_OP = False

# which provider to use if multiple are available
# (code does not currently support user selection)
# See above for the default value if the env variable is missing
OIDC_ACTIVE_PROVIDER = env_oidc_active_provider


OIDC_PROVIDERS = {
    "identity sandbox": {
        "srv_discovery_url": "https://idp.int.identitysandbox.gov",
        "behaviour": {
            # the 'code' workflow requires direct connectivity from us to Login.gov
            "response_type": "code",
            "scope": ["email", "profile:name", "phone"],
            "user_info_request": ["email", "first_name", "last_name", "phone"],
            "acr_value": "http://idmanagement.gov/ns/assurance/ial/1",
            "step_up_acr_value": "http://idmanagement.gov/ns/assurance/ial/2",
        },
        "client_registration": {
            "client_id": "cisa_dotgov_registrar",
            "redirect_uris": [f"{env_base_url}/openid/callback/login/"],
            "post_logout_redirect_uris": [f"{env_base_url}/openid/callback/logout/"],
            "token_endpoint_auth_method": ["private_key_jwt"],
            "sp_private_key": secret_login_key,
        },
    },
    "login.gov production": {
        "srv_discovery_url": "https://secure.login.gov",
        "behaviour": {
            # the 'code' workflow requires direct connectivity from us to Login.gov
            "response_type": "code",
            "scope": ["email", "profile:name", "phone"],
            "user_info_request": ["email", "first_name", "last_name", "phone"],
            "acr_value": "http://idmanagement.gov/ns/assurance/ial/1",
            "step_up_acr_value": "http://idmanagement.gov/ns/assurance/ial/2",
        },
        "client_registration": {
            "client_id": ("urn:gov:cisa:openidconnect.profiles:sp:sso:cisa:dotgov_registrar"),
            "redirect_uris": [f"{env_base_url}/openid/callback/login/"],
            "post_logout_redirect_uris": [f"{env_base_url}/openid/callback/logout/"],
            "token_endpoint_auth_method": ["private_key_jwt"],
            "sp_private_key": secret_login_key,
        },
    },
}

# endregion
# region: Routing-----------------------------------------------------------###

# ~ Set by django.middleware.common.CommonMiddleware
# APPEND_SLASH = True
# PREPEND_WWW = False

# full Python import path to the root URLconf
ROOT_URLCONF = "registrar.config.urls"

# URL to use when referring to static files located in STATIC_ROOT
# Must be relative and end with "/"
STATIC_URL = "public/"

# Base URL of our separate static public website. Used by the
# {% public_site_url subdir/path %} template tag
GETGOV_PUBLIC_SITE_URL = env_getgov_public_site_url

# endregion
# region: Registry----------------------------------------------------------###

# SECURITY WARNING: keep all registry variables in production secret!
SECRET_REGISTRY_CL_ID = secret_registry_cl_id
SECRET_REGISTRY_PASSWORD = secret_registry_password
SECRET_REGISTRY_CERT = secret_registry_cert
SECRET_REGISTRY_KEY = secret_registry_key
SECRET_REGISTRY_KEY_PASSPHRASE = secret_registry_key_passphrase
SECRET_REGISTRY_HOSTNAME = secret_registry_hostname

# endregion
# region: Security and Privacy----------------------------------------------###

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = secret_key

# Use this variable for doing SECRET_KEY rotation, see documentation
SECRET_KEY_FALLBACKS: "list[str]" = []

# ~ Set by django.middleware.security.SecurityMiddleware
# SECURE_CONTENT_TYPE_NOSNIFF = True
# SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"
# SECURE_REDIRECT_EXEMPT = []
# SECURE_REFERRER_POLICY = "same-origin"
# SECURE_SSL_HOST = None

# ~ Overridden from django.middleware.security.SecurityMiddleware
# adds the includeSubDomains directive to the HTTP Strict Transport Security header
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
# adds the preload directive to the HTTP Strict Transport Security header
SECURE_HSTS_PRELOAD = True
# TODO: set this value to 31536000 (1 year) for production
SECURE_HSTS_SECONDS = 300
# redirect all non-HTTPS requests to HTTPS
SECURE_SSL_REDIRECT = True

# ~ Set by django.middleware.common.CommonMiddleware
# DISALLOWED_USER_AGENTS = []

# The host/domain names that Django can serve.
# This is a security measure to prevent HTTP Host header attacks,
# which are possible even under many seemingly-safe
# web server configurations.
ALLOWED_HOSTS = [
    "getgov-stable.app.cloud.gov",
    "getgov-staging.app.cloud.gov",
    "getgov-development.app.cloud.gov",
    "getgov-el.app.cloud.gov",
    "getgov-ad.app.cloud.gov",
    "getgov-ms.app.cloud.gov",
    "getgov-ag.app.cloud.gov",
    "getgov-litterbox.app.cloud.gov",
    "getgov-hotgov.app.cloud.gov",
    "getgov-cb.app.cloud.gov",
    "getgov-bob.app.cloud.gov",
    "getgov-meoward.app.cloud.gov",
    "getgov-backup.app.cloud.gov",
    "getgov-ky.app.cloud.gov",
    "getgov-es.app.cloud.gov",
    "getgov-nl.app.cloud.gov",
    "getgov-rh.app.cloud.gov",
    "getgov-za.app.cloud.gov",
    "getgov-gd.app.cloud.gov",
    "getgov-rb.app.cloud.gov",
    "getgov-ko.app.cloud.gov",
    "getgov-ab.app.cloud.gov",
    "getgov-rjm.app.cloud.gov",
    "getgov-dk.app.cloud.gov",
    "manage.get.gov",
]

# Extend ALLOWED_HOSTS.
# IP addresses can also be hosts, which are used by internal
# load balancers for health checks, etc.
ALLOWED_CIDR_NETS = ["10.0.0.0/8"]

# ~ Below are some protections from cross-site request forgery.
# This is canonically done by including a nonce value
# in pages sent to the user, which the user is expected
# to send back. The specifics of implementation are
# intricate and varied.

# Store the token server-side, do not send it
# to the user via a cookie. This means each page
# which requires protection must place the token
# in the HTML explicitly, otherwise the user will
# get a 403 error when they submit.
CSRF_USE_SESSIONS = True

# Expiry of CSRF cookie, in seconds.
# None means "use session-based CSRF cookies".
CSRF_COOKIE_AGE = None

# Prevent JavaScript from reading the CSRF cookie.
# Has no effect with CSRF_USE_SESSIONS = True.
CSRF_COOKIE_HTTPONLY = True

# Only send the cookie via HTTPS connections.
# Has no effect with CSRF_USE_SESSIONS = True.
CSRF_COOKIE_SECURE = True

# Protect from non-targeted attacks by obscuring
# the CSRF cookie name from the default.
# Has no effect with CSRF_USE_SESSIONS = True.
CSRF_COOKIE_NAME = "CrSiReFo"

# Prevents CSRF cookie from being sent if the user
# is coming to our site from an external page.
# Has no effect with CSRF_USE_SESSIONS = True.
CSRF_COOKIE_SAMESITE = "Strict"

# Change header name to match cookie name.
# Has no effect with CSRF_USE_SESSIONS = True.
CSRF_HEADER_NAME = "HTTP_X_CRSIREFO"

# Max parameters that may be received via GET or POST
# TODO: 1000 is the default, may need to tune upward for
# large DNS zone files, if records are represented by
# individual form fields.
DATA_UPLOAD_MAX_NUMBER_FIELDS = 1000

# age of session cookies, in seconds (28800 = 8 hours)
SESSION_COOKIE_AGE = 28800

# instruct the browser to forbid client-side JavaScript
# from accessing the cookie
SESSION_COOKIE_HTTPONLY = True

# are we a spring boot application? who knows!
SESSION_COOKIE_NAME = "JSESSIONID"

# Allows session cookie to be sent if the user
# is coming to our site from an external page
# unless it is via "risky" paths, i.e. POST requests
SESSION_COOKIE_SAMESITE = "Lax"

# instruct browser to only send cookie via HTTPS
SESSION_COOKIE_SECURE = True

# session engine to cache session information
SESSION_ENGINE = "django.contrib.sessions.backends.cache"

# ~ Set by django.middleware.clickjacking.XFrameOptionsMiddleware
# prevent clickjacking by instructing the browser not to load
# our site within an iframe
# X_FRAME_OPTIONS = "Deny"

# endregion
# region: Testing-----------------------------------------------------------###

# Additional directories searched for fixture files.
# The fixtures directory of each application is searched by default.
# Must use unix style "/" path separators.
FIXTURE_DIRS: "list[str]" = []

# endregion


# # #                          ###
#      Development settings      #
# # #                          ###

if DEBUG:
    # used by debug() context processor
    INTERNAL_IPS = [
        "127.0.0.1",
        "::1",
    ]

    # allow dev laptop and docker-compose network to connect
    ALLOWED_HOSTS += ("localhost", "app")
    SECURE_SSL_REDIRECT = False
    SECURE_HSTS_PRELOAD = False

    # discover potentially inefficient database queries
    # TODO: use settings overrides to ensure this always is True during tests
    INSTALLED_APPS += ("nplusone.ext.django",)
    MIDDLEWARE += ("nplusone.ext.django.NPlusOneMiddleware",)
    # turned off for now, because django-auditlog has some issues
    NPLUSONE_RAISE = False
    NPLUSONE_WHITELIST = [
        {"model": "admin.LogEntry", "field": "user"},
    ]

    # insert the amazing django-debug-toolbar
    INSTALLED_APPS += ("debug_toolbar",)
    MIDDLEWARE.insert(0, "debug_toolbar.middleware.DebugToolbarMiddleware")

    DEBUG_TOOLBAR_CONFIG = {
        # due to Docker, bypass Debug Toolbar's check on INTERNAL_IPS
        "SHOW_TOOLBAR_CALLBACK": lambda _: True,
    }

# From https://django-auditlog.readthedocs.io/en/latest/upgrade.html
# Run:
# cf run-task getgov-<> --wait --command 'python manage.py auditlogmigratejson --traceback' --name auditlogmigratejson
# on our staging and stable, then remove these 2 variables or set to False
AUDITLOG_TWO_STEP_MIGRATION = False

AUDITLOG_USE_TEXT_CHANGES_IF_JSON_IS_NOT_PRESENT = False

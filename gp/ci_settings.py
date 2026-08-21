"""PostgreSQL settings for the test suite.

Used two ways, deliberately by the same file so the local loop cannot drift
from the one CI runs:

* CI copies this over ``gp/local_settings.py`` and sets ``DB_HOST`` and friends.
* ``test-venv.sh`` selects it with ``GP_SITE_SETTINGS=gp.ci_settings``, leaving
  an existing ``gp/local_settings.py`` alone.

The name, user, and password default to the ones ``compose.yaml`` creates. The
port deliberately does not: it defaults to PostgreSQL's, and ``test-venv.sh``
supplies the port the container is published on.
"""
import os


DEBUG = False

ALLOWED_HOSTS = ['testserver']
CSRF_TRUSTED_ORIGINS = ['http://localhost']

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'HOST': os.environ.get('DB_HOST', '127.0.0.1'),
        'PORT': os.environ.get('DB_PORT', ''),
        'NAME': os.environ.get('DB_NAME', 'garden_tracker'),
        'USER': os.environ.get('DB_USER', 'garden_tracker'),
        'PASSWORD': os.environ.get('DB_PASS', 'garden_tracker'),
    },
}

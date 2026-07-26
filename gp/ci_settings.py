"""Site-specific settings used by the continuous integration workflow."""
import os


DEBUG = False

ALLOWED_HOSTS = ['testserver']
CSRF_TRUSTED_ORIGINS = ['http://localhost']

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'HOST': os.environ['DB_HOST'],
        'NAME': os.environ['DB_NAME'],
        'USER': os.environ['DB_USER'],
        'PASSWORD': os.environ['DB_PASS'],
    },
}

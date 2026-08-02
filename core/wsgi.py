"""
WSGI config for core project.

It exposes the WSGI callable as a module-level variable named ``application``.

Production serves this entry point with Gunicorn:

    gunicorn core.wsgi:application --config deploy/gunicorn.conf.py

ASGI remains available for future use but is not the production contract.
See docs/DEPLOYMENT.md.

For more information on this file, see
https://docs.djangoproject.com/en/6.0/howto/deployment/wsgi/
"""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')

application = get_wsgi_application()

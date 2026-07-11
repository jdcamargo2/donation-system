# Despliegue de SIGEDON con PostgreSQL

PostgreSQL es obligatorio cuando `DJANGO_DEBUG=False`. SQLite queda limitado al
desarrollo local explícito y no demuestra la semántica de bloqueo usada por los
servicios financieros.

## Variables requeridas

```text
DJANGO_DEBUG=False
DJANGO_SECRET_KEY=<valor aleatorio largo>
ALLOWED_HOSTS=sigedon.example.org
DATABASE_ENGINE=postgresql
POSTGRES_DB=sigedon
POSTGRES_USER=sigedon_app
POSTGRES_PASSWORD=<secreto administrado fuera del repositorio>
POSTGRES_HOST=<host interno de PostgreSQL>
POSTGRES_PORT=5432
DATABASE_CONN_MAX_AGE=60
```

`SECURE_SSL_REDIRECT` vale `True` por defecto en producción.
`SECURE_HSTS_SECONDS` vale `3600`; increméntelo después de verificar HTTPS.
`SECURE_HSTS_INCLUDE_SUBDOMAINS` y `SECURE_HSTS_PRELOAD` son `False` por defecto.
Active `SECURE_PROXY_SSL_HEADER_ENABLED=True` únicamente si un proxy confiable
elimina el encabezado recibido del cliente y establece `X-Forwarded-Proto`.

## Preparación

Cree la base y el usuario desde una sesión administrativa de PostgreSQL, sin
guardar contraseñas en el historial del shell:

```sql
CREATE ROLE sigedon_app LOGIN;
CREATE DATABASE sigedon OWNER sigedon_app;
```

Entregue la contraseña mediante el gestor de secretos del entorno. Después de
instalar `requirements.txt` y exportar las variables:

```bash
./venv/bin/python manage.py migrate
./venv/bin/python manage.py collectstatic --noinput
./venv/bin/python manage.py check --deploy
```

No use SQLite en producción. Para ejecutar las pruebas concurrentes reales:

```bash
DATABASE_ENGINE=postgresql \
POSTGRES_DB=sigedon_test \
POSTGRES_USER=... \
POSTGRES_PASSWORD=... \
POSTGRES_HOST=localhost \
./venv/bin/python manage.py test \
  apps.operations.tests.test_concurrency --verbosity 2
```

Django creará y eliminará la base de test; el usuario PostgreSQL necesita
permiso para crear bases. La suite se omite explícitamente bajo SQLite y debe
ejecutarse contra una base PostgreSQL aislada antes de cada release financiero;
nunca use la base operativa.

# Render environment-variable registry

Canonical registry of environment variables for SIGEDON on Render’s **native
Python** runtime. Names match `core/settings.py`, `deploy/runtime_config.py`,
and `deploy/render/` scripts exactly. **Never store real values in this file.**

## Current truth

* No Render service, PostgreSQL instance, R2 bucket, or production domain exists
  yet.
* This registry prepares provisioning as a controlled configuration exercise.
* CI success alone does **not** authorize production traffic.

## Build vs runtime private storage

| Phase | `SIGEDON_PRIVATE_STORAGE` | Notes |
| --- | --- | --- |
| Build (`./deploy/render/build.sh`) | forced `filesystem` | Temporary `SIGEDON_MEDIA_ROOT`; no private files accessed; no R2 secrets required for collectstatic |
| Runtime / post-deploy | `r2` (final accepted mode) | Real R2 credentials; configuration verifier rejects filesystem as final Render mode |

Release (`pre_deploy`) and post-deploy must validate the **final** runtime mode.
The build/runtime split must not hide misconfiguration.

## PostgreSQL credential sets

| Set | Variables used by Django | Used for |
| --- | --- | --- |
| Migrator / owner | `POSTGRES_USER` / `POSTGRES_PASSWORD` (owner) | migrate, hardening SQL, grants |
| Runtime | `POSTGRES_USER` / `POSTGRES_PASSWORD` (app) | Gunicorn, ordinary commands, `verify_postgres_security` |

Optional remapping for one-off pre-deploy only:

| Variable | Secret? | Purpose |
| --- | --- | --- |
| `POSTGRES_MIGRATOR_USER` | no (identifier) | Temporarily exported as `POSTGRES_USER` inside `pre_deploy.sh` |
| `POSTGRES_MIGRATOR_PASSWORD` | **yes** | Temporarily exported as `POSTGRES_PASSWORD` inside `pre_deploy.sh` |

**First-deploy preference:** do **not** leave migrator credentials on the Web
Service. Run migrations from an authorized one-off shell/job, then configure the
web process with runtime credentials only. Render cannot separately scope
pre-deploy secrets from the web process on a single service.

`DATABASE_URL` is not supported and must not be introduced as an alias.

## Registry

| Variable | Required phase | Secret? | Example format | Validation |
| --- | --- | --- | --- | --- |
| `DJANGO_DEBUG` | runtime | no | `False` | Must be `False` on Render |
| `DJANGO_SECRET_KEY` | runtime | **yes** | long random string | Required when `DJANGO_DEBUG=False` |
| `ALLOWED_HOSTS` | runtime | no | `<service>.onrender.com` then custom hosts | Non-empty; no accidental localhost |
| `CSRF_TRUSTED_ORIGINS` | runtime | no | `https://<service>.onrender.com` | HTTPS-only origins |
| `DATABASE_ENGINE` | runtime / migrate | no | `postgresql` | Must be `postgresql` |
| `POSTGRES_DB` | runtime / migrate | no | provider database name | Required for PostgreSQL |
| `POSTGRES_USER` | runtime / migrate | no | runtime role name (e.g. `sigedon_app`) | Runtime ≠ owner |
| `POSTGRES_PASSWORD` | runtime / migrate | **yes** | strong unique password | Required for PostgreSQL |
| `POSTGRES_HOST` | runtime / migrate | no | private hostname | Required |
| `POSTGRES_PORT` | runtime / migrate | no | `5432` | Positive integer |
| `DATABASE_CONN_MAX_AGE` | runtime | no | `60` | Integer ≥ 0 |
| `POSTGRES_MIGRATOR_USER` | one-off migrate only | no | owner role name | Optional; unsafe if left on web service |
| `POSTGRES_MIGRATOR_PASSWORD` | one-off migrate only | **yes** | strong unique password | Optional; unsafe if left on web service |
| `SECURE_SSL_REDIRECT` | runtime | no | `True` | Default True when not DEBUG |
| `SECURE_HSTS_SECONDS` | runtime | no | `0` then `3600` then higher | Start low; increase after stable TLS |
| `SECURE_HSTS_INCLUDE_SUBDOMAINS` | runtime | no | `False` | Only when all subdomains HTTPS |
| `SECURE_HSTS_PRELOAD` | runtime | no | `False` | Must stay False for initial launch |
| `SECURE_PROXY_SSL_HEADER_ENABLED` | runtime | no | `True` | Required behind Render/Cloudflare proxy |
| `SIGEDON_PRIVATE_STORAGE` | runtime | no | `r2` | Final Render mode must be `r2` |
| `SIGEDON_PRIVATE_FILE_DELIVERY` | runtime | no | `stream` or `signed_redirect` | Enum |
| `SIGEDON_MEDIA_ROOT` | build (filesystem) / filesystem runtime only | no | absolute path outside repo | Not accepted as final Render media mode |
| `R2_ACCOUNT_ID` | runtime when r2 | no | Cloudflare account id | Required in r2 mode |
| `R2_ACCESS_KEY_ID` | runtime when r2 | **yes** | API token id | Required in r2 mode |
| `R2_SECRET_ACCESS_KEY` | runtime when r2 | **yes** | API token secret | Required in r2 mode |
| `R2_BUCKET_NAME` | runtime when r2 | no | private bucket name | Required in r2 mode |
| `R2_ENDPOINT_URL` | optional | no | `https://<account>.r2.cloudflarestorage.com` | HTTPS; else derived |
| `R2_REGION_NAME` | runtime when r2 | no | `auto` | Default `auto` |
| `R2_SIGNED_URL_EXPIRY_SECONDS` | runtime when r2 | no | `300` | 60–900 |
| `R2_ADDRESSING_STYLE` | runtime when r2 | no | `path` | `path` or `virtual` |
| `KOBO_ENABLED` | runtime | no | `False` then `True` | Keep False until webhook ready |
| `KOBO_BASE_URL` | when Kobo enabled | no | `https://…` | Required if enabled |
| `KOBO_API_TOKEN` | when Kobo enabled | **yes** | token | Required if enabled |
| `KOBO_WEBHOOK_USERNAME` | when Kobo enabled | no | username | Required if enabled |
| `KOBO_WEBHOOK_SECRET` | when Kobo enabled | **yes** | secret | Required if enabled |
| `KOBO_HTTP_CONNECT_TIMEOUT` | optional | no | `5` | Float seconds |
| `KOBO_HTTP_READ_TIMEOUT` | optional | no | `15` | Float seconds |
| `KOBO_HTTP_MAX_ATTEMPTS` | optional | no | `3` | Integer |
| `KOBO_HTTP_RETRY_BASE_DELAY` | optional | no | `0.5` | Float seconds |
| `KOBO_HTTP_RETRY_MAX_DELAY` | optional | no | `8` | Float seconds |
| `KOBO_HTTP_RETRY_AFTER_MAX_DELAY` | optional | no | `60` | Float seconds |
| `KOBO_HTTP_MAX_PAGES` | optional | no | `100` | Integer |
| `KOBO_SYNC_OVERLAP_SECONDS` | optional | no | `300` | Integer |
| `KOBO_SYNC_LEASE_SECONDS` | optional | no | `900` | Integer |
| `KOBO_MAX_ATTACHMENT_BYTES` | optional | no | `10485760` | Integer |
| `KOBO_ATTACHMENT_PROCESSING_TIMEOUT_SECONDS` | optional | no | `900` | Integer |
| `KOBO_WEBHOOK_MAX_BYTES` | optional | no | `1048576` | Integer |
| `PORT` | runtime | no | platform-injected | Gunicorn bind `0.0.0.0:$PORT` |
| `GUNICORN_BIND` | optional | no | `0.0.0.0:10000` | Overrides PORT when set |
| `GUNICORN_WORKERS` | optional | no | `2` | Positive integer |
| `GUNICORN_THREADS` | optional | no | `1` | Positive integer |
| `GUNICORN_TIMEOUT` | optional | no | `60` | Positive integer |
| `GUNICORN_GRACEFUL_TIMEOUT` | optional | no | `30` | Positive integer |
| `GUNICORN_KEEPALIVE` | optional | no | `5` | Positive integer |
| `GUNICORN_LOG_LEVEL` | optional | no | `info` | Gunicorn log level |
| `GUNICORN_ACCESS_LOG` | optional | no | `-` | stdout |
| `GUNICORN_ERROR_LOG` | optional | no | `-` | stderr |
| `DJANGO_LOG_LEVEL` | optional | no | `INFO` | Django logger |
| `SIGEDON_LOG_LEVEL` | optional | no | `INFO` | Application logger |
| `KOBO_LOG_LEVEL` | optional | no | `INFO` | Kobo logger |
| `SIGEDON_RENDER_INSTALL_DEPS` | build optional | no | `YES` | Only for local/offline harnesses |
| `SIGEDON_BUILD_MEDIA_ROOT` | build optional | no | absolute temp path | Outside repo |
| `PYTHON_BIN` | scripts optional | no | `python` | Interpreter for scripts |
| `SIGEDON_PREFLIGHT_SHOW_MIGRATE_PLAN` | preflight optional | no | `YES` | Shows plan without applying |

### Forbidden / undocumented aliases

Do **not** configure:

* `DATABASE_URL`
* `SECRET_KEY` / `DEBUG` (use `DJANGO_SECRET_KEY` / `DJANGO_DEBUG`)
* `DJANGO_ALLOWED_HOSTS`
* `WEB_CONCURRENCY`
* `REDIS_URL`
* Docker-only variables
* `R2_PUBLIC_URL`, `AWS_S3_CUSTOM_DOMAIN`, `AWS_S3_URL_PROTOCOL`

## Classification summary

**Required non-secret (runtime):** `DJANGO_DEBUG`, `ALLOWED_HOSTS`,
`CSRF_TRUSTED_ORIGINS`, `DATABASE_ENGINE`, `POSTGRES_PORT`,
`SIGEDON_PRIVATE_STORAGE`, `SIGEDON_PRIVATE_FILE_DELIVERY`, `R2_REGION_NAME`,
`R2_SIGNED_URL_EXPIRY_SECONDS`, `KOBO_ENABLED`, Gunicorn knobs as needed,
`SECURE_PROXY_SSL_HEADER_ENABLED`.

**Required secrets:** `DJANGO_SECRET_KEY`, `POSTGRES_PASSWORD`,
`R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, and when Kobo enabled
`KOBO_API_TOKEN`, `KOBO_WEBHOOK_SECRET`.

**Required provider identifiers:** `POSTGRES_HOST`, `POSTGRES_DB`,
`POSTGRES_USER`, `R2_ACCOUNT_ID`, `R2_BUCKET_NAME`.

**Optional:** email SMTP (only if password-reset/email features are enabled),
error-monitoring SaaS (recommended before public traffic; not an absolute code
blocker if logs/alerts work), backup scheduler credentials on a separate job.

## Verification

```bash
python manage.py verify_render_configuration
```

Configuration-only; no network. See also
`./deploy/render/post_deploy_verify.sh`.

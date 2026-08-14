# Render deployment scripts (native Python)

This directory defines SIGEDON’s **Render native-Python** deployment command
contract. It does **not** provision infrastructure.

## Current truth

| Item | Status |
| --- | --- |
| Scripts / runbooks | Prepared in this repository |
| Render Web Service | **Not** provisioned |
| Render PostgreSQL | **Not** provisioned |
| Cloudflare R2 bucket | **Not** provisioned |
| Staging deployment | **Not** executed |
| Real R2 probe | **Not** executed |
| Public-production approval | **None** |

No Docker. No `render.yaml` Blueprint in this checkpoint (commands + runbooks
only; no speculative plan identifiers).

## Three phases

| Phase | Command | Mutates DB? | Starts server? |
| --- | --- | --- | --- |
| Build | `./deploy/render/build.sh` | No | No |
| Pre-deploy / release | `./deploy/render/pre_deploy.sh` | Yes (migrate + role sync) | No |
| Start | `./deploy/start_web.sh` | No | Yes (Gunicorn) |

Post-deploy verification (operator-triggered, runtime credentials):

```bash
./deploy/render/post_deploy_verify.sh
./deploy/render/post_deploy_verify.sh --probe-private-storage
```

## Build (`build.sh`)

Responsibilities:

1. Confirm Python 3.12.x.
2. Optional `pip install` only when `SIGEDON_RENDER_INSTALL_DEPS=YES`.
3. `python -m pip check` (offline integrity).
4. `collectstatic --noinput`.
5. `verify_deployment_assets`.

Render’s native Python runtime installs `requirements.txt` **before** the custom
Build Command when that file is present. Do not duplicate install on Render
unless the platform contract changes.

**Build private-storage mode:** forces `SIGEDON_PRIVATE_STORAGE=filesystem` with
a temporary `SIGEDON_MEDIA_ROOT` so settings import does not require R2 secrets.
Runtime may use `r2`. Release and post-deploy must validate the **final** mode;
this split must not hide runtime misconfiguration.

Never: migrate, Gunicorn, backup, restore, R2 probe, demo seed.

## Pre-deploy (`pre_deploy.sh`)

Canonical order:

1. `check --deploy`
2. `makemigrations --check --dry-run`
3. `migrate --plan`
4. `migrate --noinput`
5. `migrate --check`
6. `sync_sigedon_roles`
7. `reconcile_operational_code_sequences`
8. `./deploy/preflight.sh`

Does **not** run `verify_postgres_security` (runtime-role gate).
Does **not** run `verify_private_storage --probe`.
Does **not** run backup automatically.

### Credential strategy (first deploy)

Render Web Services normally share one environment across build/pre-deploy/web.
If the platform cannot scope secrets by process, **prefer**:

1. One-off shell/job with **owner/migrator** credentials → run `pre_deploy.sh`.
2. Web service env contains **runtime** credentials only.
3. Leave Pre-Deploy Command empty or disabled until a scoped migrator job exists.

Optional: `POSTGRES_MIGRATOR_USER` / `POSTGRES_MIGRATOR_PASSWORD` temporarily
remap to `POSTGRES_USER` / `POSTGRES_PASSWORD` inside this script only. Do not
leave migrator secrets on the long-lived web process.

## Start

```bash
./deploy/start_web.sh
```

Uses `deploy/gunicorn.conf.py`. Bind `0.0.0.0:$PORT`. Overrides:
`GUNICORN_BIND`, `GUNICORN_WORKERS`, `GUNICORN_THREADS`, `GUNICORN_TIMEOUT`,
`GUNICORN_GRACEFUL_TIMEOUT`, `GUNICORN_KEEPALIVE`, `GUNICORN_LOG_LEVEL`,
`GUNICORN_ACCESS_LOG`, `GUNICORN_ERROR_LOG` — ver
[DEPLOYMENT.md §6](../../docs/DEPLOYMENT.md#6-servidor-de-aplicación-gunicorn).
No migrate, no collectstatic, no R2 probe.

## Health check

Render Health Check Path: `/readyz/`

| Path | Meaning |
| --- | --- |
| `/healthz/` | Process liveness |
| `/readyz/` | DB reachable + migrations applied |

## Related runbooks

* [RENDER_FIRST_DEPLOY.md](../../docs/runbooks/RENDER_FIRST_DEPLOY.md)
* [RENDER_ENVIRONMENT.md](../../docs/runbooks/RENDER_ENVIRONMENT.md)
* [STAGING_ACCEPTANCE.md](../../docs/runbooks/STAGING_ACCEPTANCE.md)
* [PRODUCTION_GO_NO_GO.md](../../docs/runbooks/PRODUCTION_GO_NO_GO.md)
* [CLOUDFLARE_R2.md](../../docs/runbooks/CLOUDFLARE_R2.md)
* [deploy/postgresql/README.md](../postgresql/README.md)
* [docs/DEPLOYMENT.md](../../docs/DEPLOYMENT.md)

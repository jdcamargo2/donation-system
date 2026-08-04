# Render first-deployment runbook

Controlled sequence to bring SIGEDON online on **Render native Python** with
Cloudflare DNS/proxy and private R2. This checkpoint prepares the repository;
**no infrastructure is provisioned here.**

## Current truth

| Item | Status |
| --- | --- |
| Deployment scripts / runbooks | Prepared |
| Render Web Service | Not created |
| Render PostgreSQL | Not created |
| Cloudflare R2 | Not created |
| Custom domain | Not purchased / not connected |
| Staging deployment | Not executed |
| Real R2 probe | Not executed |
| Production go/no-go | Not approved |

Do not imply the application is live.

## Final architecture

```text
Cloudflare DNS/proxy
        ↓
Render native Python Web Service
        ├── Django 6
        ├── ./deploy/start_web.sh → Gunicorn
        ├── WhiteNoise static serving
        ├── Kobo webhook (/integrations/kobo/webhook/)
        ├── /healthz/
        └── /readyz/
                ↓
        Render PostgreSQL (owner migrate / runtime app)

Private documents → Cloudflare R2 private bucket
```

No Docker. Migrations never run inside Gunicorn. Static collection runs in build.
`verify_postgres_security` runs under the **runtime** role only.

## Render dashboard field map

| Render field | Value / command |
| --- | --- |
| Runtime | Python |
| Branch | approved production branch |
| Build Command | `./deploy/render/build.sh` |
| Pre-Deploy Command | **deployment-strategy dependent** (see below) |
| Start Command | `./deploy/start_web.sh` |
| Health Check Path | `/readyz/` |
| Auto-Deploy | disabled initially |
| Region | same as PostgreSQL |
| Environment | variables from [RENDER_ENVIRONMENT.md](RENDER_ENVIRONMENT.md) |

Do not encode plan names or prices as code. Select the Render Python patch
version from Render’s available list at provision time (repository declares
`.python-version` → `3.12`).

### Pre-Deploy Command strategy

**Initial (recommended):** leave Pre-Deploy empty. Run
`./deploy/render/pre_deploy.sh` from an authorized one-off shell/job with
**owner** credentials. Configure the Web Service with **runtime** credentials
only so migrator secrets never sit on the web process.

**Later evolution:** scoped migrator job, or temporary `POSTGRES_MIGRATOR_*`
remapping inside `pre_deploy.sh` without leaving those secrets on the long-lived
service.

## Sequence

Mark **[infra]** steps that require future paid/provisioned infrastructure.

1. Verify `main` (or approved branch) and that the first GitHub Actions run is
   green. CI alone does not authorize traffic.
2. **[infra]** Provision Render PostgreSQL (same region as the future web
   service).
3. **[infra]** Create owner/migrator and runtime roles using
   `deploy/postgresql/harden_runtime_role.sql` as the source of truth. Do not
   create roles from the application. Do not embed passwords in scripts.
4. **[infra]** Connect via an authorized one-off shell with **owner**
   credentials. Run `./deploy/render/pre_deploy.sh` (migrate, sync roles,
   reconcile sequences, release preflight).
5. Apply runtime hardening grants as owner. Switch client credentials to the
   **runtime** role.
6. Run `python manage.py verify_postgres_security` under the runtime role
   (or `./deploy/render/post_deploy_verify.sh` after the web env exists).
7. **[infra]** Provision Render native Python Web Service.
8. Configure Build Command = `./deploy/render/build.sh`.
9. Configure Start Command = `./deploy/start_web.sh`.
10. Configure Health Check Path = `/readyz/`.
11. Enter **runtime** PostgreSQL variables only (`DATABASE_ENGINE=postgresql`,
    `POSTGRES_*` for `sigedon_app`). Set `DJANGO_DEBUG=False`, secret key,
    hosts/origins for the temporary domain, `SECURE_PROXY_SSL_HEADER_ENABLED=True`.
12. Deploy on the Render temporary domain
    (`ALLOWED_HOSTS=<service-name>.onrender.com`,
    `CSRF_TRUSTED_ORIGINS=https://<service-name>.onrender.com`).
13. Validate static assets, login, `/healthz/`, `/readyz/`. Keep
    `KOBO_ENABLED=False`. Keep `SIGEDON_PRIVATE_STORAGE` unset/filesystem only
    until R2 gate passes — **final** Render mode must be `r2` before accepting
    real documents (verifier rejects filesystem as final mode).
14. **[infra]** Provision private R2 bucket (no public development URL / r2.dev).
    Create least-privilege token. Enter `R2_*` and set
    `SIGEDON_PRIVATE_STORAGE=r2`.
15. Run `python manage.py verify_render_configuration` and
    `verify_private_storage --configuration-only`.
16. **[infra]** Run real storage probe:
    `python manage.py verify_private_storage --probe` (or
    `./deploy/render/post_deploy_verify.sh --probe-private-storage`).
17. Run authorized upload / preview / download and forbidden-access tests.
18. **[infra]** Run backup + isolated restore drill (BD + objects). Copy backup
    **off-host**. See [Backup scheduling](#backup-scheduling-on-render).
19. Configure Kobo only after web/DB validation (see [Kobo](#kobo-activation)).
20. Execute [STAGING_ACCEPTANCE.md](STAGING_ACCEPTANCE.md) with all four roles.
21. **[infra]** Connect custom domain in Render (DNS only first). Keep temporary
    domain until rollback policy allows removal.
22. **[infra]** Activate Cloudflare DNS (nameservers) with proxy **DNS-only**
    initially. Verify Render TLS.
23. Activate Cloudflare proxy with SSL/TLS **Full (strict)**. Retest CSRF,
    login, redirects, readiness, static files, Kobo webhook.
24. Complete [PRODUCTION_GO_NO_GO.md](PRODUCTION_GO_NO_GO.md).
25. Open public traffic only after **GO** (or documented CONDITIONAL GO).

## Host and origin rollout

### Phase 1 — Render temporary domain

```env
ALLOWED_HOSTS=<service-name>.onrender.com
CSRF_TRUSTED_ORIGINS=https://<service-name>.onrender.com
```

### Phase 2 — Custom domain, DNS only

Add `example.org` and `www.example.org` (placeholders — do not hardcode the
real future domain in code). Do not remove the temporary domain until rollback
policy says it is safe.

### Phase 3 — Cloudflare proxy

After Render TLS is healthy: activate proxy; **Full (strict)**; verify
`X-Forwarded-Proto`; retest CSRF/login/webhooks; prevent caching authenticated
HTML and private content.

## HTTPS / HSTS rollout

| Stage | Guidance |
| --- | --- |
| Initial HTTPS staging | `SECURE_HSTS_SECONDS=0` or short; preload False |
| Stable domain/TLS | short HSTS duration (e.g. 3600) |
| Sustained validation | increase deliberately |
| Preload | **Do not** enable for initial launch |

Existing settings: `SESSION_COOKIE_SECURE` / `CSRF_COOKIE_SECURE` follow
`not DEBUG`. `SECURE_SSL_REDIRECT` defaults True when not DEBUG. Enable
`SECURE_PROXY_SSL_HEADER_ENABLED=True` behind the proxy. Django defaults cover
`SECURE_CONTENT_TYPE_NOSNIFF`, `X_FRAME_OPTIONS`, and referrer policy unless a
proven gap appears — do not change without evidence.

## PostgreSQL owner / runtime

| Role | Used for | Must not |
| --- | --- | --- |
| Owner / migrator | migrate, schema ownership, triggers/functions, grants, approved repair | run the web process |
| Runtime (`sigedon_app`) | Gunicorn, ordinary commands, `verify_postgres_security` | alter schema, disable triggers, own tables |

Never grant schema ownership to the runtime web role to work around platform
limitations.

## R2 activation gate

Before accepting real production documents with `SIGEDON_PRIVATE_STORAGE=r2`:

1. Bucket created private; public development URL disabled.
2. Least-privilege token created.
3. Structural configuration validation succeeds.
4. `verify_private_storage --probe` succeeds.
5. Authorized upload / preview / download succeed; unauthorized fails.
6. Signed redirect or stream behavior confirmed.
7. Object backup succeeds; isolated restore succeeds; backup copied off-host.
8. Operator records the result.

Until these pass, no real production document may be accepted. Rolling
application code back does **not** roll R2 objects back. Never switch to empty
filesystem storage after R2 activation.

## Initial data / administrator

**Allowed:** migrate; `sync_sigedon_roles`; initialize required non-demo catalog
through existing idempotent commands; create **one** named administrator via
controlled interactive `createsuperuser` (password not in shell history).

**Forbidden:** `seed_sigedon_demo`; demo projects/donations; known/default
passwords; generic shared admin; automatic superuser from hardcoded env
credentials; sample institutions unless explicitly approved as real data.

### First-admin procedure

1. Named individual account; institution-controlled email.
2. Strong unique password entered interactively (not in argv/history dumps).
3. MFA at the platform/account level where available.
4. Immediate login and permission verification.
5. Additional administrators through application policy — never shared passwords.
6. Do not automate a known password into deployment.

## Kobo activation

Webhook route (actual): `/integrations/kobo/webhook/`

1. Keep `KOBO_ENABLED=False` during initial web/database validation.
2. Enable configuration against staging/temporary HTTPS domain when supported.
3. Configure final HTTPS webhook endpoint in Kobo.
4. Verify method/authentication (**HTTP Basic** with
   `KOBO_WEBHOOK_USERNAME` / `KOBO_WEBHOOK_SECRET`; keep
   `KOBO_WEBHOOK_ALLOW_LEGACY_SECRET_HEADER=False`); send a controlled test
   submission.
5. Verify idempotent replay and malformed payload rejection.
6. Verify logs redact token/secret.
7. Verify Cloudflare does not challenge/block the webhook.
8. Update callback after domain switch.

Do not contact Kobo from this checkpoint.

## Cloudflare activation contract

Illustrative operator steps (dashboard labels may change):

1. Add domain to Cloudflare; review imported DNS.
2. Change registrar nameservers to Cloudflare.
3. Add domain in Render; create Render-required DNS records.
4. Keep proxy **DNS-only** initially; verify Render TLS.
5. Remove conflicting records (e.g. unsupported AAAA where applicable).
6. Activate proxy; set SSL/TLS **Full (strict)**.
7. Retest login, CSRF, redirects, readiness, static files, and Kobo.
8. Configure cache rules to avoid authenticated/private content.

## Backup scheduling on Render

systemd/cron examples under `deploy/backups/examples/` are **host** oriented.
They cannot be assumed to run inside a Render Web Service.

* Use an authorized Render Cron Job or external scheduler (**not** created in
  this checkpoint).
* Invoke the existing backup runner with runtime DB + R2 credentials.
* Ensure destination is off-host/persistent.
* Prevent overlap via the existing lock contract.
* Monitor exit status and stale success markers.
* Ephemeral Render disk is **not** durable backup storage.

## Error monitoring and email

| Concern | Classification |
| --- | --- |
| Error monitoring (e.g. Sentry) | Recommended before public traffic; not an absolute code blocker if logs and alert paths work |
| Email | Required only if enabled features depend on it; console backend forbidden in production when email features are active; SMTP credentials are secrets; provider selection deferred |

Do not add Sentry or an email provider in this checkpoint.

## Filesystem assumptions

* Source/build filesystem is ephemeral.
* Static files are collected into the deploy artifact and served by WhiteNoise.
* Private media on Render must use R2 for the final accepted mode.
* Local runtime filesystem is not a durable backup.
* Backup staging needs adequate temporary space; output must leave the host.

## Rollback

See [PRODUCTION_GO_NO_GO.md](PRODUCTION_GO_NO_GO.md) and
[DEPLOYMENT.md](../DEPLOYMENT.md) §17. Two classes:

1. **Application rollback** — prior known-good deploy; do not reverse migrations
   automatically; verify schema compatibility; retest readiness and private
   storage.
2. **Database recovery** — approved backup/PITR only; maintenance window;
   explicit authorization; isolated validation; consider DB + object storage
   consistency together.

No automatic destructive rollback script.

## Related

* [RENDER_ENVIRONMENT.md](RENDER_ENVIRONMENT.md)
* [STAGING_ACCEPTANCE.md](STAGING_ACCEPTANCE.md)
* [PRODUCTION_GO_NO_GO.md](PRODUCTION_GO_NO_GO.md)
* [CLOUDFLARE_R2.md](CLOUDFLARE_R2.md)
* [deploy/render/README.md](../../deploy/render/README.md)
* [deploy/postgresql/README.md](../../deploy/postgresql/README.md)

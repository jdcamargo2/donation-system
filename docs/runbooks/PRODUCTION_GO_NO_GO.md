# Production go / no-go checklist

Decision gate for opening **public** traffic to SIGEDON. A single green
`/readyz/` does **not** imply GO. CI success alone does **not** authorize
production traffic.

**Decision values:** `GO` | `CONDITIONAL GO` | `NO-GO`

**Current truth:** no public-production approval exists. No staging deployment
has been completed in this checkpoint.

| Field | Value |
| --- | --- |
| Candidate commit / deploy | |
| Temporary / custom host | |
| Technical operator | |
| Named approver | |
| Decision | |
| Timestamp (UTC) | |

---

## Absolute blockers (any FAIL ⇒ NO-GO)

| # | Blocker | Status | Evidence | Operator | Timestamp |
| --- | --- | --- | --- | --- | --- |
| B1 | First GitHub Actions run not green | | | | |
| B2 | Pending migrations | | | | |
| B3 | `check --deploy` unresolved | | | | |
| B4 | Runtime-role `verify_postgres_security` failed or not run | | | | |
| B5 | R2 real probe failed or not run (when claiming R2) | | | | |
| B6 | Private download authorization failed | | | | |
| B7 | No verified backup | | | | |
| B8 | No isolated restore drill | | | | |
| B9 | `/readyz/` unhealthy | | | | |
| B10 | TLS / hosts / CSRF incorrect | | | | |
| B11 | Test users / default passwords remain | | | | |
| B12 | Kobo final webhook untested when Kobo is required | | | | |
| B13 | Staging role acceptance incomplete (all four roles) | | | | |
| B14 | Rollback procedure untested / undocumented | | | | |
| B15 | Production traffic before go/no-go approval | | Forbidden | | |

---

## Supporting confirmations

| # | Item | Status | Notes |
| --- | --- | --- | --- |
| S1 | Build command `./deploy/render/build.sh` | | |
| S2 | Start command `./deploy/start_web.sh` | | |
| S3 | Health check `/readyz/` | | |
| S4 | No Docker runtime for initial deploy | | |
| S5 | Migrator credentials not left on web process | | |
| S6 | Domain DNS-only phase completed before proxy | | |
| S7 | Cloudflare SSL/TLS **Full (strict)** when proxied | | |
| S8 | HSTS preload still disabled for initial launch | | |
| S9 | Staging acceptance attached | | [STAGING_ACCEPTANCE.md](STAGING_ACCEPTANCE.md) |

---

## Rollback contract (must be acknowledged)

### Application rollback

* Revert to prior known-good commit/deploy.
* Do **not** reverse migrations automatically.
* Verify schema compatibility with the rolled-back code.
* Rerun readiness (`/readyz/`) and private-storage behavior checks.

### Database rollback / recovery

* Only through approved backup / PITR procedure.
* Maintenance window + explicit authorization.
* Isolated validation before traffic.
* Consider DB and object-storage consistency together.

### R2 complications

* Rolling application code back does **not** roll objects back.
* Object migration may be forward-compatible or not — verify explicitly.
* Never switch to empty filesystem storage after R2 activation.

Do not add or run an automatic destructive rollback script.

---

## Decision

| Option | When |
| --- | --- |
| **GO** | All absolute blockers PASS; staging acceptance complete; approver + operator named |
| **CONDITIONAL GO** | Non-blocker residuals documented with owners and time-boxed mitigations; no absolute blocker FAIL |
| **NO-GO** | Any absolute blocker FAIL or incomplete evidence |

Approver signature: ______________________  
Operator signature: ______________________  

Related: [RENDER_FIRST_DEPLOY.md](RENDER_FIRST_DEPLOY.md),
[STAGING_ACCEPTANCE.md](STAGING_ACCEPTANCE.md),
[RENDER_ENVIRONMENT.md](RENDER_ENVIRONMENT.md).

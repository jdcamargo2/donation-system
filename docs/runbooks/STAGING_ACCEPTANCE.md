# Staging acceptance checklist

Canonical checklist before connecting a custom domain or opening broader
traffic. Record each row with status, evidence, operator, and timestamp.

**Statuses:** `PASS` | `FAIL` | `NOT APPLICABLE`

**Current truth:** no staging deployment has been executed yet. This document is
the acceptance contract for a future staging run.

| Field | Value |
| --- | --- |
| Environment | (e.g. Render temporary domain) |
| Commit / deploy id | |
| Operator | |
| Date (UTC) | |

---

## Infrastructure

| # | Check | Status | Evidence / reference | Operator | Timestamp |
| --- | --- | --- | --- | --- | --- |
| I1 | Web service healthy (process up) | | | | |
| I2 | PostgreSQL reachable privately | | | | |
| I3 | Runtime role restricted (`verify_postgres_security` = 0) | | | | |
| I4 | Static assets load (WhiteNoise / hashed) | | | | |
| I5 | R2 bucket private (or N/A if probe gate not yet claimed) | | | | |
| I6 | TLS valid on active host | | | | |
| I7 | `GET /readyz/` → 200 | | | | |
| I8 | `GET /healthz/` → 200 | | | | |
| I9 | Logs visible on platform (stdout/stderr) | | | | |
| I10 | Start command remains `./deploy/start_web.sh` | | | | |
| I11 | Health check path is `/readyz/` (not `/health/`) | | | | |

## Authentication

| # | Check | Status | Evidence / reference | Operator | Timestamp |
| --- | --- | --- | --- | --- | --- |
| A1 | Login succeeds for named admin | | | | |
| A2 | Logout succeeds | | | | |
| A3 | Invalid login rejected | | | | |
| A4 | Session expiry behavior acceptable | | | | |
| A5 | CSRF protects state-changing POSTs | | | | |
| A6 | Password reset (only if email configured) | | | | |

## Roles

Test all **four** canonical roles with **distinct** accounts:

* Administrador SIGEDON
* Operador de campo
* Auditor externo
* Comité de proyectos

For each role verify navigation, lists, detail pages, allowed actions, forbidden
states, and private downloads.

| # | Role | Status | Evidence / reference | Operator | Timestamp |
| --- | --- | --- | --- | --- | --- |
| R1 | Administrador SIGEDON | | | | |
| R2 | Operador de campo | | | | |
| R3 | Auditor externo | | | | |
| R4 | Comité de proyectos | | | | |

## Domain workflows

| # | Check | Status | Evidence / reference | Operator | Timestamp |
| --- | --- | --- | --- | --- | --- |
| D1 | Institutions | | | | |
| D2 | Projects | | | | |
| D3 | Donations | | | | |
| D4 | Allocations | | | | |
| D5 | Expenses | | | | |
| D6 | Expense Requests | | | | |
| D7 | Supporting documents | | | | |
| D8 | Advances | | | | |
| D9 | Reviews | | | | |
| D10 | Audit trail | | | | |
| D11 | Public portal | | | | |
| D12 | Kobo import (if enabled; else N/A) | | | | |

## Integrity

| # | Check | Status | Evidence / reference | Operator | Timestamp |
| --- | --- | --- | --- | --- | --- |
| N1 | USD constraints enforced | | | | |
| N2 | Balances coherent | | | | |
| N3 | Terminal actions via POST only | | | | |
| N4 | Append-only AuditLog | | | | |
| N5 | Append-only ExpenseRequestEvent | | | | |
| N6 | Operational codes | | | | |
| N7 | Concurrency-sensitive paths smoke-tested | | | | |

## Private files

| # | Check | Status | Evidence / reference | Operator | Timestamp |
| --- | --- | --- | --- | --- | --- |
| P1 | Upload authorized | | | | |
| P2 | Preview authorized | | | | |
| P3 | Download authorized | | | | |
| P4 | Replacement | | | | |
| P5 | Missing object handled | | | | |
| P6 | Forbidden access fails | | | | |
| P7 | Signed URL expiry (if `signed_redirect`) | | | | |
| P8 | Backup + isolated restore drill | | | | |
| P9 | Real `verify_private_storage --probe` (when claiming R2) | | | | |

## Operational

| # | Check | Status | Evidence / reference | Operator | Timestamp |
| --- | --- | --- | --- | --- | --- |
| O1 | Logs include request IDs | | | | |
| O2 | 403 / 404 / 500 behavior acceptable | | | | |
| O3 | Backup produced and copied off-host | | | | |
| O4 | Restore drill recorded | | | | |
| O5 | Rollback procedure reviewed / dry-run documented | | | | |
| O6 | Alert path known | | | | |
| O7 | No demo users / default passwords remain | | | | |
| O8 | `verify_render_configuration` = 0 | | | | |

## Sign-off

| Role | Name | Decision | Timestamp |
| --- | --- | --- | --- |
| Technical operator | | | |
| Approver | | | |

Staging acceptance incomplete ⇒ production go/no-go must be **NO-GO**.

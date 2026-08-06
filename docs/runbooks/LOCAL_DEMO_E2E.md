# Local demo end-to-end guide (DEMO-E2E-1)

Use this runbook only on a **disposable local database** with `DEBUG=True`.
Do not run against production, staging shared data, Render, R2, or Kobo.

## Setup

Prefer a dedicated database (example name: `sigedon_demo_e2e`):

```bash
export DJANGO_DEBUG=True
export DATABASE_ENGINE=postgresql
# Point DATABASE_* / POSTGRES_* at the disposable database only.

python manage.py migrate
python manage.py seed_sigedon_demo --password '<LOCAL_ONLY_PASSWORD>'
python manage.py seed_sigedon_demo --verify
python manage.py runserver
```

Password policy:

* Pass `--password '<LOCAL_ONLY_PASSWORD>'` or set `SIGEDON_DEMO_PASSWORD`.
* Empty passwords are rejected.
* The command never prints the password.
* Do not put a real password in documentation, `.env.example`, tickets, or screenshots.

`--reset` is **not** implemented: projects cannot be deleted safely, so use a disposable
database instead of attempting partial demo cleanup.

## Users

| Username | Role |
|---|---|
| `admin_demo` | Administrador SIGEDON |
| `operador_demo` | Operador de campo |
| `auditor_demo` | Auditor externo |
| `comite_demo` | Comité de proyectos |

All four use the password you supplied to the seed command.

## Stable demo codes

* Projects: `PRJ-DEMO-001` … `PRJ-DEMO-007`
* Donations: `DON-DEMO-001` … `DON-DEMO-005`
* Expense request scenarios are keyed by purpose markers `[DEMO-ER:…]`
* Project update scenarios are keyed by titles `[DEMO-UPD:…]`

## Recommended test order

### 1. Operator (`operador_demo`)

1. Log in.
2. Open assigned/territorial projects (`PRJ-DEMO-001` … `PRJ-DEMO-005`).
3. Create or edit an unpublished project update; attach a tiny PDF.
4. Open Expense Requests; confirm own pending/withdrawn history is visible.
5. Create a new Expense Request on an allocation with available balance and submit it
   (status becomes pending decision).
6. Confirm committee decision actions and admin fulfillment actions are unavailable.

Expected: operator can report and request; cannot decide or fulfill.

### 2. Committee (`comite_demo`)

1. Log in and open the decision queue / Expense Request list.
2. Open the pending demo request (`[DEMO-ER:pending]…`).
3. Return/edit path is not a separate domain state: pending requests remain editable by
   the requester; committee can deny or approve.
4. Approve one pending request (or inspect the seeded approved/reserved case).
5. Deny another (or inspect `[DEMO-ER:denied]`).
6. Confirm fulfillment controls are unavailable.

Expected: committee can decide requests and review published updates; cannot register expenses.

### 3. Administrator (`admin_demo`)

1. Inspect the global dashboard queues.
2. Fulfill an approved/reserved request (seeded `[DEMO-ER:approved]` or one you approved).
3. Confirm the resulting expense has supporting documentation and append-only events.
4. Inspect institutions, donations, allocations, and projects.
5. Confirm DonationAdmin remains read-only for unsafe mutations as designed.
6. Confirm `PRJ-DEMO-001` is public and `PRJ-DEMO-007` is closed.

Expected: admin can fulfill reserved requests and manage operational entities within role limits.

### 4. Auditor (`auditor_demo`)

1. Inspect the financial chain donation → allocation → expense / request.
2. Open request timelines and private documents through authorized views.
3. Confirm create/update/delete actions are unavailable.

Expected: read-only inspection across financial and request history.

### 5. Public portal (anonymous)

1. Open the public portal.
2. Confirm `PRJ-DEMO-001` (public) is visible.
3. Confirm private projects such as `PRJ-DEMO-002` are absent.
4. Confirm only published updates appear for public projects.
5. Open the published demo update (`[DEMO-UPD:published]…`) and confirm
   **Documentos del avance** lists the explicitly public attachment
   (`Evidencia DEMO pública`) and omits the private one (`Evidencia DEMO interna`).
6. Download the public document anonymously; confirm no login is required and the
   URL is under `/avances/.../documentos/.../descargar/` (not `/media/` or an
   authenticated private download path). The legacy `/transparency/updates/.../documents/...`
   path still works but only as a permanent `301` redirect to the canonical URL.
7. Guess a private attachment public URL → expect `404`.
8. Confirm remediation attachments and other private supporting/legal files are
   inaccessible anonymously.
9. On the public home financial cards, confirm labels **Donaciones vinculadas**
   and **Disponible por ejecutar**, the scope note (active visible projects
   only), and that public totals differ from the internal dashboard
   (demo default: linked donations `200000`, assigned `40000`, executed
   `4500`, available `35500` vs internal received `225000` / assigned
   `160000`). Do not expect the two surfaces to match.
10. After a disposable public-relevant financial mutation (or publish of
    `PRJ-DEMO-002`), reload the public home and confirm metrics refresh without
    waiting for cache TTL. Prefer focused automated cache tests if no disposable
    demo row is available.

Expected: public surface shows published project/update content and only
explicitly public update documents; financial cards reflect visible-project
scope with clarified labels; cache invalidates after successful commits.

### BUG-E2E checklist (local)

| ID | Check | Result |
|---|---|---|
| BUG-E2E-001 | Closed project freezes advances/documents | PASS |
| BUG-E2E-002 | Only explicitly public update attachments appear/download on the public portal | PASS |
| BUG-E2E-004 | Operators can start Expense Requests from the list via a clear CTA | PASS |
| BUG-E2E-005 | Public financial labels clarified; RECEIVED linked donations; cache invalidates after financial commits | PASS |

Internal check for BUG-E2E-002: `admin_demo` can publish/unpublish an attachment
on an active project; `operador_demo` cannot; a closed project cannot change
attachment publicity.

## Verification and reruns

```bash
python manage.py seed_sigedon_demo --password '<LOCAL_ONLY_PASSWORD>'
python manage.py seed_sigedon_demo --password '<LOCAL_ONLY_PASSWORD>'
python manage.py seed_sigedon_demo --verify
```

Second seed run should keep entity counts, event counts, and balances stable.
Verification performs no writes and exits non-zero when the matrix is incomplete.

## Cleanup

Drop the disposable database when finished. Do not attempt to delete `PRJ-DEMO-*`
projects in place: project deletion is forbidden by domain rules.

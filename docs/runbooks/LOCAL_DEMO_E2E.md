# Local demo end-to-end (Public Demo / Portfolio Edition)

Use this runbook only on a **disposable local PostgreSQL database** with
`DEBUG=True`. Do not run against production, staging, Render, R2, or live Kobo.

All demo data is **completely fictional**: República de Monteluz / Valle Sereno.

## Setup (PostgreSQL recommended)

```bash
export DJANGO_DEBUG=True
export DATABASE_ENGINE=postgresql
export POSTGRES_DB=sigedon_demo_e2e
# POSTGRES_USER / PASSWORD / HOST / PORT → only the disposable database.

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

`--reset` is **not** implemented: projects cannot be deleted safely. Use a
disposable database instead of partial cleanup.

SQLite (`DATABASE_ENGINE=sqlite` with `DEBUG=True`) exists only as a lightweight
development alternative; it is not the recommended demo path and does not
validate PostgreSQL locking/triggers.

## Kobo in this demo

`KOBO_ENABLED=False` (default): disconnected showcase hub. No real credentials,
no remote calls. Operational remote routes 404; remote commands `CommandError`.
See [KOBO.md](../KOBO.md).

## Users

| Username | Role |
|---|---|
| `admin_demo` | Administrador SIGEDON |
| `operador_demo` | Operador de campo |
| `auditor_demo` | Auditor externo |
| `comite_demo` | Comité de proyectos |

All four use the password supplied to the seed command.

## Stable demo codes

* Projects: `PRJ-DEMO-001` … `PRJ-DEMO-007`
* Donations: `DON-DEMO-001` … `DON-DEMO-005`
* Expense request scenarios: purpose markers `[DEMO-ER:…]`
* Project update scenarios: titles `[DEMO-UPD:…]`

## Recommended test order

### 1. Operator (`operador_demo`)

1. Log in at `/accounts/login/` → `/panel/`.
2. Open assigned/territorial projects (`PRJ-DEMO-001` … `PRJ-DEMO-005`).
3. Create or edit an unpublished project update; attach a tiny PDF.
4. Open Expense Requests; confirm own pending/withdrawn history is visible.
5. Create a new Expense Request on an allocation with available balance
   (status becomes pending decision).
6. Confirm committee decision actions and admin fulfillment actions are unavailable.

Expected: operator can report and request; cannot decide or fulfill.

### 2. Committee (`comite_demo`)

1. Log in and open the Expense Request decision queue.
2. Open the pending demo request (`[DEMO-ER:pending]…`).
3. Pending requests remain editable by the requester; committee can deny or approve.
4. Approve one pending request (or inspect the seeded approved/reserved case).
5. Deny another (or inspect `[DEMO-ER:denied]`).
6. Confirm fulfillment controls are unavailable.

Expected: committee can decide requests and review published updates; cannot register expenses.

### 3. Administrator (`admin_demo`)

1. Inspect the global dashboard queues.
2. Fulfill an approved/reserved request (`[DEMO-ER:approved]` or one you approved).
3. Confirm the resulting expense has supporting documentation and append-only events.
4. Inspect institutions, donations, allocations, and projects.
5. Confirm DonationAdmin remains read-only for unsafe mutations.
6. Confirm `PRJ-DEMO-001` is public and `PRJ-DEMO-007` is closed.

Expected: admin can fulfill reserved requests and manage operational entities within role limits.

### 4. Auditor (`auditor_demo`)

1. Inspect the financial chain donation → allocation → expense request → expense.
2. Open request timelines and private documents through authorized views.
3. Confirm create/update/delete actions are unavailable.

Expected: read-only inspection across financial and request history.

### 5. Public portal (anonymous)

1. Open `http://127.0.0.1:8000/`.
2. Confirm `PRJ-DEMO-001` (public) is visible.
3. Confirm private projects such as `PRJ-DEMO-002` are absent.
4. Confirm only published updates appear for public projects.
5. Open the published demo update (`[DEMO-UPD:published]…`) and confirm
   **Documentos del avance** lists the explicitly public attachment
   (`Evidencia DEMO pública`) and omits the private one (`Evidencia DEMO interna`).
6. Download the public document anonymously; URL under
   `/avances/.../documentos/.../descargar/` (not `/media/`).
7. Guess a private attachment public URL → expect `404`.
8. Confirm remediation attachments and other private files are inaccessible anonymously.
9. On the public home financial cards, confirm labels **Donaciones vinculadas**
   and **Disponible por ejecutar**. Public totals differ from the internal
   dashboard by design (visible-project scope).

Expected: public surface shows published project/update content and only
explicitly public update documents.

## Verification and reruns

```bash
python manage.py seed_sigedon_demo --password '<LOCAL_ONLY_PASSWORD>'
python manage.py seed_sigedon_demo --password '<LOCAL_ONLY_PASSWORD>'
python manage.py seed_sigedon_demo --verify
```

Second seed run should keep entity counts, event counts, and balances stable.
`--verify` performs no writes and exits non-zero when the matrix is incomplete.

## Cleanup

Drop the disposable database when finished. Do not attempt to delete
`PRJ-DEMO-*` projects in place: project deletion is forbidden by domain rules.

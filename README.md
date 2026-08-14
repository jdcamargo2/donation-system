# SIGEDON

**Sistema Integral de Gestión, Seguimiento y Trazabilidad de Donaciones.**

Aplicación web institucional para registrar, controlar, auditar y transparentar
donaciones monetarias y su ejecución en proyectos.

> **Public Demo / Portfolio Edition**
>
> - Datos **completamente ficticios** (República de Monteluz / Valle Sereno).
> - Infraestructura privada (Render, R2, scheduler de backups) **no incluida**.
> - Algunas capacidades implementadas se presentan **desconectadas**.
> - Una integración desconectada **no** significa una feature no implementada.

## Highlights

- Django 6.0.6, Python 3.12, PostgreSQL
- RBAC (cuatro roles canónicos)
- Ciclo de donaciones, asignaciones, solicitudes de gasto, gastos y evidencias
- Avances de proyecto y transparencia pública
- Auditoría append-only
- Integración KoboToolbox opcional (desconectada por defecto)
- Health/readiness, tooling de backup y despliegue
- 2,384 automated tests (checkpoint local verificado)

## Demo scope

| Capability | Status |
| --- | --- |
| Core operations | Active |
| PostgreSQL | Active (recommended) |
| Public portal | Active |
| Demo seed | Active |
| KoboToolbox | Disconnected demo |
| Filesystem private storage | Active local |
| Cloudflare R2 | Supported / not provisioned |
| Render deployment | Tooling included / not provisioned |
| Backup automation | Tooling included / scheduler not provisioned |
| GitHub Actions | Workflow defined / remote checkpoint not verified |

## Core flow

```text
Institution
→ Donation
→ Fund Allocation
→ Project
→ Expense Request
→ Approval
→ Expense
→ Evidence
→ Project Update
→ Audit
→ Public Transparency
```

## Quick Start

PostgreSQL es el camino principal. Requiere Python 3.12 y un servidor
PostgreSQL local.

```bash
python3.12 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Editar `.env`:

```env
DJANGO_DEBUG=True
DJANGO_SECRET_KEY=change-me
ALLOWED_HOSTS=localhost,127.0.0.1
DATABASE_ENGINE=postgresql
POSTGRES_DB=db_sigedon
POSTGRES_USER=postgres
POSTGRES_PASSWORD=<local>
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
SIGEDON_DEMO_PASSWORD=<local-only>
KOBO_ENABLED=False
```

Crear la base y arrancar:

```bash
createdb db_sigedon   # o equivalente en su instancia PostgreSQL
python manage.py migrate
python manage.py seed_sigedon_demo
python manage.py runserver
```

`runserver` es solo desarrollo. Producción: Gunicorn
([Despliegue](docs/DEPLOYMENT.md#6-servidor-de-aplicación-gunicorn)).

Rutas:

- Portal público: http://127.0.0.1:8000/
- Panel interno: http://127.0.0.1:8000/panel/
- Admin Django: http://127.0.0.1:8000/admin/

SQLite (`DATABASE_ENGINE=sqlite` con `DEBUG=True`) existe como alternativa
ligera de desarrollo; no es este Quick Start y no valida locking/triggers.

## Demo accounts

| Username | Role |
| --- | --- |
| `admin_demo` | Administrador SIGEDON |
| `operador_demo` | Operador de campo |
| `auditor_demo` | Auditor externo |
| `comite_demo` | Comité de proyectos |

Password: el definido con `SIGEDON_DEMO_PASSWORD` o `--password`.
El comando nunca lo imprime. Recorrido por roles:
[LOCAL_DEMO_E2E.md](docs/runbooks/LOCAL_DEMO_E2E.md).

## KoboToolbox

`KOBO_ENABLED=False`: modo demo desconectado (hub showcase sintético, sin
credenciales, rutas remotas 404, comandos remotos `CommandError`).
La integración está implementada; activarla requiere configuración externa.
Detalle: [KOBO.md](docs/KOBO.md).

## Testing

Current verified local checkpoint: 2,384 tests passing on PostgreSQL.

```bash
python manage.py test --noinput
```

Iteración: `python manage.py test --keepdb --noinput` (no sustituye un
checkpoint limpio).

GitHub Actions workflow is included; the current remote CI run has not
been verified as part of this public-demo checkpoint.

Fuente: [TESTING.md](docs/TESTING.md).

## Documentation

* [Arquitectura](docs/ARCHITECTURE.md)
* [Modelo de dominio](docs/DOMAIN_MODEL.md)
* [Flujos](docs/FLOWS.md)
* [Roles y permisos](docs/ROLES_AND_PERMISSIONS.md)
* [Seguridad](docs/SECURITY.md)
* [Portal público](docs/PUBLIC_PORTAL.md)
* [KoboToolbox](docs/KOBO.md)
* [Operación](docs/OPERATIONS.md)
* [Pruebas](docs/TESTING.md)
* [Despliegue](docs/DEPLOYMENT.md)
* [Demo local E2E](docs/runbooks/LOCAL_DEMO_E2E.md)
* [Alcance y límites](docs/MVP_SCOPE.md)
* [Decisiones técnicas](docs/DECISIONS.md)

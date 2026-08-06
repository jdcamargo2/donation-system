# SIGEDON

**Sistema Integral de Gestión, Seguimiento y Trazabilidad de Donaciones**

SIGEDON es una aplicación web institucional diseñada para registrar, controlar, auditar y transparentar donaciones monetarias destinadas a proyectos sociales, pastorales o humanitarios.

El sistema permite seguir la cadena completa de trazabilidad:

```text
Institución
→ Donación
→ Asignación de fondos
→ Proyecto
→ Solicitud de gasto
→ Gasto
→ Evidencia
→ Avance
→ Revisión institucional
→ Auditoría
→ Transparencia pública
```

## Estado actual

El MVP se encuentra funcional y cubierto por pruebas automatizadas.

| Indicador                             | Estado          |
| ------------------------------------- | --------------- |
| Pruebas automatizadas                 | 1562            |
| Estado de la suite                    | OK              |
| Migraciones pendientes                | `0030` (evento→gasto PROTECT; aplicar en entorno autorizado) |
| Django system check                   | Sin incidencias |
| Base de datos soportada en producción | PostgreSQL      |

La cifra de pruebas representa el estado de referencia actual de esta rama y
debe actualizarse cuando el total cambie de forma estable.

## Stack tecnológico

* Python 3.12
* Django 6.0.6
* PostgreSQL mediante Psycopg 3
* Gunicorn (WSGI) como servidor de aplicación en producción
* WhiteNoise para servir estáticos recopilados en producción (`STATIC_ROOT`;
  sin Nginx dedicado ni disco persistente para estáticos)
* Logging de runtime a stdout/stderr con correlación `X-Request-ID` (sin SaaS)
* Sondas de liveness/readiness: `/healthz/` y `/readyz/` (sin auth; ver
  [Despliegue §6.3](docs/DEPLOYMENT.md#63-sondas-http-healthz-y-readyz)).
  Media privada: preview inline siempre vía stream + CSP; `signed_redirect`
  solo para descargas con parámetros de respuesta. Endpoint R2 estricto a
  Cloudflare por defecto.* SQLite únicamente para desarrollo local con `DEBUG=True`
* Django Templates
* Bootstrap 5.3.3, Bootstrap Icons 1.11.3 y SweetAlert2 11.26.25 vendorizados
  en `static/vendor/` (sin CDN en runtime; ver
  [`static/vendor/THIRD_PARTY_ASSETS.md`](static/vendor/THIRD_PARTY_ASSETS.md))
* `django-bootstrap5`
* HTMX 2.0.10 vendorizado, limitado al componente interno de hitos
* `django-countries`
* `python-dotenv`
* HTML, CSS y JavaScript sin proceso de compilación con Node.js
* Despliegue inicial: runtime Python nativo en Render (sin Docker). Scripts y
  runbooks RENDER-3 preparados (`deploy/render/`,
  [primer deploy](docs/runbooks/RENDER_FIRST_DEPLOY.md)); **ningún** servicio
  Render/Cloudflare/R2/dominio provisionado aún. Media privada final en Render:
  R2 tras probe real (ver [runbook R2](docs/runbooks/CLOUDFLARE_R2.md))

## Módulos implementados

### Operación interna

* Instituciones
* Proyectos
* Donaciones
* Asignaciones de fondos
* Solicitudes de gasto
* Gastos
* Documentos de soporte
* Avances de proyectos
* Evidencias de avances
* Revisión institucional
* Decisiones del Comité
* Auditoría append-only
* Exportaciones CSV

### Transparencia pública

* Página pública de proyectos
* Detalle público de proyectos
* Avances publicados
* Métricas agregadas
* Datos JSON autorizados
* Exclusión de información privada o anulada

### Integración con KoboToolbox

* Descubrimiento de activos
* Registro versionado de formularios
* Configuración y activación de activos
* Recepción mediante webhook
* Sincronización y reconciliación
* Staging del payload original
* Normalización de las fichas 1, 10 y 11
* Enrutamiento hacia proyectos
* Importación automática de submissions resueltas
* Hub global de incidencias operativas
* Historial Kobo por proyecto y datos importados
* Descarga protegida de archivos adjuntos
* Historial técnico de procesamiento

## Aplicaciones Django activas

```text
apps.operations
apps.public_portal
apps.integrations.kobo
web
```

El paquete `web` se conserva como componente histórico de compatibilidad para las pruebas y la organización de templates. Actualmente, no contiene modelos ni vistas productivas propias.

## Roles operativos

Cuatro roles funcionales canónicos:

* Administrador SIGEDON
* Operador de campo
* Auditor externo
* Comité de proyectos

Un usuario ordinario puede tener como máximo un rol funcional SIGEDON (cero o
uno). Los permisos técnicos de Kobo (`kobo.*`), el superusuario de Django y el
usuario público no autenticado son controles separados, no roles funcionales
adicionales.

La matriz completa de roles y permisos se encuentra en:

* [Roles y permisos](docs/ROLES_AND_PERMISSIONS.md)

## Instalación local

### 1. Crear el entorno virtual

```bash
python3.12 -m venv venv
source venv/bin/activate
```

### 2. Instalar las dependencias

```bash
pip install -r requirements.txt
```

### 3. Crear el archivo de entorno

```bash
cp .env.example .env
```

Configuración mínima para desarrollo:

```env
DJANGO_DEBUG=True
DJANGO_SECRET_KEY=
DATABASE_ENGINE=sqlite
ALLOWED_HOSTS=localhost,127.0.0.1
KOBO_ENABLED=False
```

### 4. Aplicar las migraciones

```bash
python manage.py migrate
```

### 5. Crear un superusuario

```bash
python manage.py createsuperuser
```

### 6. Sincronizar los roles

```bash
python manage.py sync_sigedon_roles
```

### 7. Ejecutar el servidor

> [!WARNING]
> `python manage.py runserver` es **solo para desarrollo local**. No es un
> servidor de aplicación de producción. En producción use Gunicorn; ver
> [Despliegue](docs/DEPLOYMENT.md#6-servidor-de-aplicación-gunicorn).

```bash
python manage.py runserver
```

## Rutas locales

### Portal público de transparencia (raíz canónica)

```text
http://127.0.0.1:8000/
```

### Panel interno

```text
http://127.0.0.1:8000/panel/
```

### Administración de Django

```text
http://127.0.0.1:8000/admin/
```

> [!NOTE]
> `/transparency/**` queda como redirección permanente `301` (`GET`/`HEAD`)
> hacia las rutas canónicas anteriores; las rutas operativas antiguas fuera
> de `/panel/` (por ejemplo `/projects/`) devuelven `404` sin redirección.
> Detalle: [Portal público](docs/PUBLIC_PORTAL.md#2-rutas-públicas).

## Comandos administrativos

```bash
python manage.py sync_sigedon_roles
python manage.py seed_sigedon_demo
python manage.py register_kobo_forms
python manage.py discover_kobo_assets
python manage.py process_kobo_submissions
python manage.py reconcile_kobo_submissions
python manage.py verify_postgres_security
python manage.py verify_private_storage
python manage.py verify_private_storage --probe   # staging/release con infra real
python manage.py verify_render_configuration      # contrato Render (sin red)
python manage.py migrate_private_media            # migración filesystem → storage destino
python manage.py export_private_objects           # export portable de objetos privados
```

> [!NOTE]
> `verify_private_storage` valida el contrato del storage privado. Con R2,
> `--probe` requiere cuenta/bucket reales y **aún no** forma parte del
> entorno actual (sin Cloudflare provisionado). Ver
> [runbook R2](docs/runbooks/CLOUDFLARE_R2.md).

> [!NOTE]
> `verify_postgres_security` exige PostgreSQL y las credenciales del rol
> runtime final. Verifica append-only de `AuditLog`/`ExpenseRequestEvent`,
> constraints críticos y postura de privilegios; no repara. Obligatorio antes
> de aceptar tráfico o un restore. Detalle: `deploy/postgresql/README.md`.

> [!WARNING]
> `seed_sigedon_demo` es exclusivamente local (`DEBUG=True`): usa ORM directo,
> ofrece idempotencia parcial y no reproduce toda la trazabilidad ni las reglas
> del flujo operativo. Cuando `DEBUG=False` el comando se rechaza antes de
> escribir. Nunca debe ejecutarse sobre una base de producción ni en
> release/preflight. Consulte
> [Operación y mantenimiento](docs/OPERATIONS.md#2-datos-de-demostración).

> [!NOTE]
> `process_kobo_submissions` y `reconcile_kobo_submissions` salen con código
> distinto de cero si hubo errores de lote; éxitos previos del mismo lote pueden
> permanecer comprometidos. No usar `|| true`. Ver
> [OPERATIONS.md §5](docs/OPERATIONS.md#5-procesamiento-y-reconciliación-de-kobo).

## Integración continua

GitHub Actions (`.github/workflows/ci.yml`) es el CI canónico del repositorio.

* Disparadores: `pull_request`, `push` a `main`, `workflow_dispatch`.
* Runtime: Python 3.12 + PostgreSQL 16 (`TZ=UTC`); sin fallback a SQLite en jobs de integración.
* Jobs bloqueantes de merge: static/hygiene, migrate-from-zero + artefactos, suite crítica, suite completa.
  La suite completa espera **ambos** jobs previos (`postgres-migrations` y `critical-tests`) tras `static`.
* No despliega, no requiere secretos reales, no ejecuta backup/restore/seed ni llamadas Kobo remotas.
* No declara producción aprobada; gates de staging/runtime (p. ej. `verify_postgres_security` con rol restringido) permanecen fuera de CI.
* Reproducción local: `./deploy/ci/run_static_checks.sh`, `./deploy/ci/run_critical_tests.sh` (PostgreSQL), `python manage.py test --noinput`.
* Detalle: [Pruebas](docs/TESTING.md#18-integración-continua-github-actions) y [Despliegue](docs/DEPLOYMENT.md).

## Verificación

Antes de considerar válido un cambio, se recomienda ejecutar:

```bash
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test
git diff --check
```

## Documentación

* [Alcance del MVP](docs/MVP_SCOPE.md)
* [Arquitectura](docs/ARCHITECTURE.md)
* [Modelo de dominio](docs/DOMAIN_MODEL.md)
* [Flujos funcionales](docs/FLOWS.md)
* [Roles y permisos](docs/ROLES_AND_PERMISSIONS.md)
* [Seguridad](docs/SECURITY.md)
* [Integración con KoboToolbox](docs/KOBO.md)
* [Operación y mantenimiento](docs/OPERATIONS.md)
* [Pruebas](docs/TESTING.md)
* [Portal público](docs/PUBLIC_PORTAL.md)
* [Despliegue](docs/DEPLOYMENT.md)
* [Primer deploy Render](docs/runbooks/RENDER_FIRST_DEPLOY.md)
* [Registro de entorno Render](docs/runbooks/RENDER_ENVIRONMENT.md)
* [Aceptación staging](docs/runbooks/STAGING_ACCEPTANCE.md)
* [Go/no-go producción](docs/runbooks/PRODUCTION_GO_NO_GO.md)
* [Runbook Cloudflare R2](docs/runbooks/CLOUDFLARE_R2.md)
* [Decisiones técnicas](docs/DECISIONS.md)
* [Instrucciones para agentes](docs/AGENTS.md)

## Regla de fuente de verdad

En caso de contradicción, debe utilizarse el siguiente orden de prioridad:

```text
Código productivo
→ Migraciones
→ Pruebas automatizadas
→ Documentación vigente
→ Documentos históricos
```

Los documentos ubicados en `docs/audits/` describen estados anteriores del sistema y no sustituyen la documentación vigente.

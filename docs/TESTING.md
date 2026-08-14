# Pruebas de SIGEDON

Fuente canónica de testing. Distingue el checkpoint local verificado del
workflow de GitHub Actions (definido, ejecución remota no verificada en esta
edición pública).

## 1. Checkpoint local verificado

```text
Current verified local checkpoint: 2,384 tests passing on PostgreSQL.
python manage.py test --noinput
Ran 2384 tests in ~1004 s
OK
EXIT 0
```

El tiempo (~1004 s) es un checkpoint histórico opcional, no una norma.

Este número es el estado de referencia **local** de esta rama. Debe
actualizarse cuando el total cambie de forma estable.

| Superficie | Estado |
| --- | --- |
| Local full suite (`python manage.py test --noinput` sobre PostgreSQL) | **Verificada** |
| GitHub Actions (`.github/workflows/ci.yml`) | Workflow definido; ejecución remota actual **no verificada** |

No afirmar «CI green» ni «CI passing» sin evidencia de una corrida remota.

Migraciones de referencia: `operations` hasta `0033`, Kobo hasta `0018`.
`python manage.py makemigrations --check --dry-run` está limpio.

## 2. Ejecución completa

```bash
python manage.py test --noinput
```

Requiere PostgreSQL. Resultado esperado: todas las pruebas aprobadas, EXIT 0,
ninguna migración inesperada.

### Iteración con `--keepdb`

```bash
python manage.py test --keepdb --noinput
```

`--keepdb` acelera el ciclo de desarrollo reutilizando la base de prueba.
No sustituye un checkpoint limpio (sin `--keepdb`) cuando hay que validar
reconstrucción desde cero.

## 3. Áreas cubiertas

### 3.1. `apps.operations`

Modelos, servicios, constraints, códigos, estados de proyecto
(`ACTIVE`/`CLOSED`), publicación, bloqueos de eliminación, auditoría, archivos
protegidos, permisos, avances/revisiones, CSV, flujos E2E, seguridad de
`seed_sigedon_demo`, matriz demo de cuatro roles.

### 3.2. `apps.integrations.kobo`

Cliente, contratos, Fichas 1/10/11, staging, routing, adjuntos, webhook,
reconciliación, importación, modo desconectado (`KOBO_ENABLED=False`) y
comandos remotos. Bindings asset-proyecto son históricos: no gobiernan el
runtime.

### 3.3. `apps.public_portal`

Proyectos `ACTIVE` + `is_public=True`, avances publicados, adjuntos de avance
explícitamente públicos, métricas agregadas, JSON, exclusión de anulados.

### 3.4. `core`

Settings, producción, SQLite solo como alternativa explícita de desarrollo,
secretos, `ALLOWED_HOSTS`, PostgreSQL, runtime/Gunicorn, Render, higiene,
CI workflow.

### 3.5. `web`

Dashboard, formularios, vistas, auditoría, búsquedas, CSV, regresiones.

## 4. Pruebas sobre PostgreSQL

Las pruebas concurrentes y de triggers append-only requieren PostgreSQL.

SIGEDON opera exclusivamente en USD. En SQLite esas pruebas pueden omitirse
(`skipUnless`); una omisión en SQLite no cuenta como validación.

## 5. Suites focales útiles

```bash
python manage.py test web.tests.test_dashboard --noinput
python manage.py test apps.operations.tests.test_roles --noinput
python manage.py test apps.public_portal.tests --noinput
python manage.py test apps.integrations.kobo.tests --noinput
python manage.py test apps.integrations.kobo.tests.test_disconnected_demo --noinput
python manage.py test apps.operations.tests.test_seed_demo_command --noinput
```

### Solicitudes de gasto

```bash
python manage.py test \
  apps.operations.tests.test_expense_request_models \
  apps.operations.tests.test_expense_request_roles \
  apps.operations.tests.test_expense_request_services \
  apps.operations.tests.test_expense_request_fulfillment \
  apps.operations.tests.test_expense_request_annulment \
  apps.operations.tests.test_expense_request_concurrency \
  apps.operations.tests.test_expense_request_ui \
  --noinput
```

`test_expense_request_concurrency` se omite bajo SQLite.

### Verificación operativa PostgreSQL

```bash
python manage.py test \
  apps.operations.tests.test_verify_postgres_security \
  apps.operations.tests.test_verify_postgres_security_command \
  --noinput
```

### Seguridad de comandos, backup (mocks) y runtime

```bash
python manage.py test \
  apps.operations.tests.test_management_command_safety \
  apps.integrations.kobo.tests.test_process_kobo_submissions_command \
  apps.integrations.kobo.tests.test_reconcile_kobo_submissions_command \
  apps.operations.tests.test_backup_scripts \
  apps.operations.tests.test_backup_automation \
  core.tests.test_runtime_startup \
  core.tests.test_render_deployment_contract \
  core.tests.test_render_configuration \
  core.tests.test_ci_workflow \
  core.tests.test_repository_hygiene \
  --noinput
```

Los tests de backup usan mocks; no ejecutan backup/restore de producción.

### Arranque de producción, estáticos y sondas

* `core.tests.test_runtime_startup`
* `core.tests.test_whitenoise_static` / `core.tests.test_static_resilience`
* `core.tests.test_health_endpoints`
* `core.tests.test_request_ids` / `core.tests.test_logging_config`

Contrato: [DEPLOYMENT.md](DEPLOYMENT.md#6-servidor-de-aplicación-gunicorn),
[DEPLOYMENT.md §6.3](DEPLOYMENT.md#63-sondas-http-healthz-y-readyz).

## 6. Verificación de migraciones

```bash
python manage.py makemigrations --check --dry-run
```

Esperado: `No changes detected`.

## 7. Verificación del sistema

```bash
python manage.py check
```

Esperado: `System check identified no issues`.

## 8. Calidad del diff

```bash
git diff --check
```

## 9. Indicadores de campos obligatorios

Contrato `*` en formularios operativos: `field.field.required` y
`templates/web/includes/ops_form_field_label.html`.
Módulo: `web.tests.test_required_field_indicators`.

## 10. Regla para nuevos cambios

Todo cambio funcional debe incluir pruebas del caso válido, permiso
insuficiente, estado inválido, validación, inmutabilidad, auditoría y
concurrencia cuando afecte saldos, reservas o códigos.

## 11. Pruebas de permisos

Acceso con permiso correcto, `403` sin autorización, ausencia de datos
sensibles, protección de URL. Ocultar un botón no basta.

## 12. Pruebas de acciones terminales

`POST` + CSRF, permiso, estado inicial, transición, motivo, auditoría,
bloqueo posterior. La acción no puede ejecutarse por `GET`.

## 13. Pruebas de archivos

Autenticación, autorización, pertenencia al padre, preview/download,
cabeceras `nosniff` / `no-store`, ausencia de `/media/` directo.

Módulo: `apps.operations.tests.test_protected_file_preview`.
Helpers de storage remoto simulado: `core.tests.storage_backends`.

### 13.1. Storage privado R2 (preparación vs conectividad real)

El código soporta `SIGEDON_PRIVATE_STORAGE=r2`, pero **no** se ha
provisionado cuenta/bucket. La suite valida contratos locales y doubles;
no sustituye `verify_private_storage` / `--probe` en staging.
Runbook: [CLOUDFLARE_R2.md](runbooks/CLOUDFLARE_R2.md).

## 14. Pruebas de auditoría

Creación atómica, actor/entidad/resumen, prohibición de edición y borrado.

## 15. Pruebas del portal público

Solo `ACTIVE` + `is_public=True`; solo avances publicados; solo adjuntos de
avance con `is_public=True`; exclusión de anulados y de payloads Kobo.

## 16. Pruebas de KoboToolbox

Webhook, idempotencia, payload original, normalizadores, routing territorial,
límites de adjuntos, reconciliación. Con `KOBO_ENABLED=False`: hub showcase,
rutas remotas 404, comandos remotos `CommandError`.
[KOBO.md](KOBO.md).

Helpers de transporte: `apps.integrations.kobo.tests.helpers`.
Locking real: PostgreSQL (`Requires PostgreSQL row-level locking`).

## 17. Flujo recomendado antes de integrar cambios

```bash
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test --noinput
git diff --check
```

Cuando el cambio afecte concurrencia, ejecutar contra PostgreSQL.

## 18. Integración continua (GitHub Actions)

El flujo canónico es [`.github/workflows/ci.yml`](../.github/workflows/ci.yml).

GitHub Actions workflow is included; the current remote CI run has not been
verified as part of this public-demo checkpoint.

### Disparadores

* `pull_request`
* `push` a `main`
* `workflow_dispatch`

Las ejecuciones obsoletas del mismo ref se cancelan (`concurrency.cancel-in-progress`).

### Runtime canónico

* Python 3.12
* PostgreSQL 16 (service container; sin fallback silencioso a SQLite)
* `TZ=UTC`
* `KOBO_ENABLED=False`
* credenciales ficticias `sigedon_ci`

### Jobs (merge-blocking)

| Check name | Propósito |
| --- | --- |
| CI / Static and repository checks | higiene, `pip check`, `manage.py check`, `makemigrations --check`, `bash -n` |
| CI / PostgreSQL migration and artifact verification | migrate from zero, `check --deploy`, `collectstatic` + `verify_deployment_assets` |
| CI / Critical PostgreSQL tests | suite focalizada de integridad/despliegue |
| CI / Full PostgreSQL suite | `python manage.py test --noinput` |

Grafo: `static` → (`postgres-migrations` ∥ `critical-tests`) → `full-suite`.

CI **no** despliega, **no** ejecuta backup/restore reales, **no** corre
`seed_sigedon_demo` y **no** llama a Kobo remoto.

`verify_postgres_security` con éxito de rol runtime permanece como **gate de
staging**.

### Scripts locales

```bash
./deploy/ci/run_static_checks.sh
export DATABASE_ENGINE=postgresql
./deploy/ci/run_critical_tests.sh
./deploy/ci/run_migration_gates.sh
```

## 19. Criterio de aceptación

Un cambio está listo cuando las pruebas nuevas cubren el comportamiento, la
regresión local pasa, no hay migraciones pendientes, `check` y `git diff --check`
están limpios, y la documentación refleja el comportamiento real.

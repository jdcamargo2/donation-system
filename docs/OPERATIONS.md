# Operación de una instancia de SIGEDON

Cómo operar una instancia ya instalada. Clone, venv e instalación inicial:
[README](../README.md). Despliegue: [DEPLOYMENT.md](DEPLOYMENT.md). Backups:
[deploy/backups/README.md](../deploy/backups/README.md).

Kobo desconectado (`KOBO_ENABLED=False`) es el default de esta edición:
hub showcase sintético, sin credenciales reales. La operación remota se
documenta aquí y en [KOBO.md](KOBO.md) **cuando la integración está activa**.

## 1. Operación diaria

* Confirmar que el proceso web responde (`/healthz/`, `/readyz/`).
* Revisar logs de stdout/stderr (correlación `X-Request-ID`).
* Comprobar colas internas: solicitudes de gasto pendientes/reservadas,
  avances inéditos, incidencias Kobo si `KOBO_ENABLED=True`.
* No usar `runserver` en producción. Contrato: Gunicorn
  ([DEPLOYMENT.md §6](DEPLOYMENT.md#6-servidor-de-aplicación-gunicorn)).

## 2. Datos de demostración

`seed_sigedon_demo` es **solo desarrollo** (`DEBUG=True`). Guía:
[LOCAL_DEMO_E2E.md](runbooks/LOCAL_DEMO_E2E.md).

* Rechaza ejecución cuando `DEBUG=False` (`CommandError`) antes de escribir.
* Universo ficticio: República de Monteluz / Valle Sereno.
* Usuarios: `admin_demo`, `operador_demo`, `auditor_demo`, `comite_demo`.
* Contraseña: `SIGEDON_DEMO_PASSWORD` o `--password`. Nunca imprimirla ni
  versionarla.
* `--reset` no está implementado: usar una base desechable.

```bash
python manage.py seed_sigedon_demo --password '<LOCAL_ONLY_PASSWORD>'
python manage.py seed_sigedon_demo --verify
```

No forma parte de release, preflight ni arranque web.

## 3. Sincronización de roles

```bash
python manage.py sync_sigedon_roles
```

Matriz canónica: [Roles y permisos](ROLES_AND_PERMISSIONS.md).

El comando asegura los cuatro grupos (`Administrador SIGEDON`,
`Operador de campo`, `Auditor externo`, `Comité de proyectos`), **reemplaza**
sus permisos desde código, **no toca** membresías y es idempotente.

Ejecutarlo en despliegue inicial, restauración y cuando cambie la matriz.
No editar permisos de grupos canónicos en Django admin.

Bootstrap de cuentas institucionales (superusuario +
`/panel/usuarios/`): [DEPLOYMENT.md §7](DEPLOYMENT.md#7-usuarios-y-roles).

## 4. Comandos operativos

```bash
python manage.py sync_sigedon_roles
python manage.py reconcile_operational_code_sequences
python manage.py verify_postgres_security
python manage.py verify_private_storage
python manage.py verify_private_storage --probe   # infra real; no en este checkpoint
python manage.py verify_render_configuration      # contrato Render, sin red
python manage.py verify_restored_data
python manage.py verify_deployment_assets
python manage.py export_private_objects
python manage.py migrate_private_media
```

Kobo (solo con `KOBO_ENABLED=True`; con `False` salen `CommandError`):

```bash
python manage.py register_kobo_forms
python manage.py discover_kobo_assets
python manage.py process_kobo_submissions
python manage.py reconcile_kobo_submissions
```

`verify_postgres_security` exige PostgreSQL y las credenciales del **rol
runtime**. No repara. Detalle: [deploy/postgresql/README.md](../deploy/postgresql/README.md).

## 5. Procesamiento y reconciliación de Kobo

Con `KOBO_ENABLED=False` estos comandos fallan de forma limpia
(`Kobo integration is disabled.`) sin abrir HTTP. Arquitectura y knobs:
[KOBO.md](KOBO.md).

Cuando la integración está activa:

```bash
python manage.py process_kobo_submissions
python manage.py reconcile_kobo_submissions
```

| Resultado | Exit |
| --- | --- |
| Lote completado sin errores (incluye “nada elegible”) | `0` |
| Fallos de lote o de configuración | distinto de `0` |

Exit distinto de cero no implica cero trabajo comprometido. No enmascarar con
`|| true`. `--dry-run` en reconciliación no escribe.

Tras un fallo: hub de incidencias, `KoboProcessingEvent` y logs correlacionados.

## 6. Logging y correlación

SIGEDON emite logs a **stdout/stderr**. Recolección y retención son de la
plataforma.

Variables (detalle de niveles): `DJANGO_LOG_LEVEL`, `SIGEDON_LOG_LEVEL`,
`KOBO_LOG_LEVEL` (default `INFO` en producción).

* Cabecera: `X-Request-ID`. IDs inbound inválidos se reemplazan.
* No registrar cuerpos, query strings completas, cookies, Authorization,
  secretos ni payloads Kobo crudos.

| Fuente | Uso |
| --- | --- |
| Runtime logs | ciclo de vida, fallos de petición/integración |
| `AuditLog` / `ExpenseRequestEvent` / `KoboProcessingEvent` | evidencia de negocio durable |

No usar logs de runtime como prueba de una mutación financiera.

Sondas: [DEPLOYMENT.md §6.3](DEPLOYMENT.md#63-sondas-http-healthz-y-readyz).
`/readyz/` no consulta Kobo, caché, media ni R2.

## 7. Gestión de archivos

* Estáticos: WhiteNoise desde `STATIC_ROOT` tras `collectstatic`.
* Media privada: `SIGEDON_PRIVATE_STORAGE=filesystem` (local) o `r2`
  (soportado, **no provisionado**). Nunca montar `MEDIA_URL` público.
* Acceso solo por endpoints protegidos. `FileField.url` no es autorización.
* Producción filesystem: `SIGEDON_MEDIA_ROOT` absoluto persistente.
* Tope de carga operativa: `SIGEDON_MAX_PRIVATE_UPLOAD_BYTES` (default 10 MiB).

R2: [CLOUDFLARE_R2.md](runbooks/CLOUDFLARE_R2.md).
Archivos en despliegue: [DEPLOYMENT.md §8](DEPLOYMENT.md#8-gestión-de-archivos).

## 8. Mantenimiento de base de datos

Producción y demo recomendada: PostgreSQL. Antes de migrar: backup, credenciales,
`showmigrations` / `migrate --plan`, ventana controlada.

```bash
python manage.py showmigrations
python manage.py migrate --plan
python manage.py migrate
python manage.py check
python manage.py reconcile_operational_code_sequences
```

No editar migraciones aplicadas. No inventar códigos operativos a mano.

Backups y restore: [deploy/backups/README.md](../deploy/backups/README.md).
No duplicar aquí el procedimiento.

## 9. Auditoría

`AuditLog` no se limpia, edita ni elimina con scripts ordinarios. Los eventos
técnicos de Kobo no sustituyen la auditoría funcional.

## 10. Verificaciones habituales

```bash
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py verify_postgres_security   # rol runtime, PostgreSQL
```

Suite: [TESTING.md](TESTING.md). Checkpoint local verificado: 2,384 tests en
PostgreSQL. GitHub Actions está definido; la corrida remota de esta edición
**no está verificada**.

## 11. Troubleshooting

### Secuencia operativa faltante

Síntoma: no se genera `PRJ`/`DON`/`ASG`/`SGS`/`GAS`. Verificar migraciones y
`OperationalCodeSequence`. `reconcile_operational_code_sequences` es
detect-only; no repara.

### Kobo no procesa submissions

Si `KOBO_ENABLED=False`, el fallo es el modo demo desconectado (esperado).
Si está habilitado: credenciales, activo, zona pastoral, eventos de
procesamiento. Nunca copiar tokens. Ver [KOBO.md](KOBO.md).

### `403 Forbidden`

Autenticación, grupo, `sync_sigedon_roles`, permiso de la vista. La ausencia
de un botón no prueba autorización.

### Archivos que no descargan

Modo de storage, existencia del objeto, permisos, relación con la entidad.
No “arreglar” exponiendo `FileField.url`. Con R2, no cambiar a filesystem vacío.

### Migraciones pendientes

```bash
python manage.py makemigrations --check --dry-run
python manage.py showmigrations
```

No generar una migración automática sin entender la causa.

## 12. Runbooks

* Demo local: [LOCAL_DEMO_E2E.md](runbooks/LOCAL_DEMO_E2E.md)
* Despliegue: [DEPLOYMENT.md](DEPLOYMENT.md)
* Render: [RENDER_FIRST_DEPLOY.md](runbooks/RENDER_FIRST_DEPLOY.md),
  [RENDER_ENVIRONMENT.md](runbooks/RENDER_ENVIRONMENT.md)
* R2: [CLOUDFLARE_R2.md](runbooks/CLOUDFLARE_R2.md)
* Staging / go-no-go: [STAGING_ACCEPTANCE.md](runbooks/STAGING_ACCEPTANCE.md),
  [PRODUCTION_GO_NO_GO.md](runbooks/PRODUCTION_GO_NO_GO.md)
* PostgreSQL roles: [deploy/postgresql/README.md](../deploy/postgresql/README.md)

# Backup, restauración y automatización operativa de SIGEDON

Capa manual verificable **más** automatización operativa (lock, retención,
markers, hook de alerta, drill aislado y ejemplos de scheduler). Soporta
`format_version` 1 (filesystem) y 2 (object/r2) sin romper sets existentes.
La entrega de alertas y el scheduling siguen siendo **propiedad de la
plataforma**; el repositorio aporta contratos y ejemplos.

## Artefacto

Dos layouts según `SIGEDON_PRIVATE_STORAGE` en el momento del backup.
Filesystem conserva `format_version: 1` por compatibilidad; object mode usa
`format_version: 2`.

### Filesystem (default) — `format_version: 1`

```text
<SIGEDON_BACKUP_ROOT>/<backup_id>/
    database.dump      # pg_dump --format=custom
    media.tar.gz       # copia de MEDIA_ROOT
    manifest.json      # format_version 1 + checksums
    .sigedon-verified  # opcional; escrito tras verify en el pipeline programado
```

`media.tar.gz` contiene archivos regulares bajo `MEDIA_ROOT` (rutas relativas).
Se omiten `staticfiles` y enlaces simbólicos (incluidos externos).

### Object storage (`SIGEDON_PRIVATE_STORAGE=r2`) — `format_version: 2`

```text
<SIGEDON_BACKUP_ROOT>/<backup_id>/
    database.dump
    objects/                 # export_private_objects (claves FileField)
    object-manifest.json     # path/size/sha256 por objeto
    manifest.json            # format_version 2; private_storage.mode=object
    .sigedon-verified
```

El manifiesto v2 referencia checksums/conteos de `object-manifest.json` (**sin**
credenciales, endpoint ni bucket). No se empaqueta `media.tar.gz` ni se exige
`SIGEDON_MEDIA_ROOT` para el backup en modo r2. `verify_backup.sh` despacha por
`format_version` (v1 nunca se reinterpreta como object). Restore de v2 object
usa `restore_private_objects` hacia filesystem aislado para drills offline; un
drill R2 real queda pendiente de infraestructura.

Estado: R2 está implementado en código; **no** hay cuenta/bucket/credenciales
provisionadas. Default operativo: filesystem. Detalle:
[`docs/runbooks/CLOUDFLARE_R2.md`](../../docs/runbooks/CLOUDFLARE_R2.md).

### Distinguir incompletos vs verificados

| Estado | Identificación |
|---|---|
| Incompleto / staging | directorios `.sigedon-backup.*` (temp); se eliminan en fallo |
| Publicado no confiable | `<backup_id>/` que **falla** `verify_backup.sh` |
| Verificado | pasa `verify_backup.sh` (y puede tener `.sigedon-verified`) |

La retención **solo** puede borrar sets verificados bajo `SIGEDON_BACKUP_ROOT`.

## Consistencia

Estrategia: **ventana de mantenimiento obligatoria**.

Antes de `backup_sigedon.sh` / `run_scheduled_backup.sh` debe detenerse:

- procesos web;
- workers;
- comandos de procesamiento Kobo;
- uploads / tráfico de escritura.

El script exige `SIGEDON_MAINTENANCE_CONFIRMED=YES` y no puede comprobar por sí
solo que esos procesos estén detenidos. El scheduler de plataforma debe
exportar esa variable **solo** tras quietar escritores.

En modo object (r2), la ventana reduce el drift entre PostgreSQL y el storage
privado; **no** es un snapshot atómico perfecto entre proveedores.

## Scripts

| Script | Rol |
|---|---|
| `backup_sigedon.sh` | Publica PostgreSQL + media (v1) u objects (v2 r2); lock exclusivo |
| `verify_backup.sh` | Verificación de solo lectura (despacha por format_version) |
| `restore_sigedon.sh` | Restore aislado (v1 media / v2 objects→filesystem; nunca producción) |
| `run_scheduled_backup.sh` | Pipeline: lock → backup → verify → retención → marker/alerta |
| `apply_retention.sh` | Borra solo sets verificados fuera de política |
| `run_restore_drill.sh` | Drill aislado + marker; Django opcional; no afirma drill R2 real |
| `lib/common.sh` | Helpers compartidos (no ejecutar directamente) |
| `examples/` | cron/systemd/env/hook de ejemplo (plataforma) |

## Variables

| Variable | Uso |
|---|---|
| `SIGEDON_BACKUP_ROOT` | Destino de backups (se crea con `0700` si no existe) |
| `SIGEDON_PRIVATE_STORAGE` | `filesystem` (default) o `r2`; selecciona layout del set |
| `SIGEDON_MEDIA_ROOT` | Obligatorio en modo filesystem; **debe coincidir** con Django. No requerido para empaquetar backup en modo r2 |
| `POSTGRES_DB` / `USER` / `HOST` / `PORT` | Cliente PostgreSQL |
| `PGPASSWORD` | Opcional; preferir `~/.pgpass` |
| `SIGEDON_MAINTENANCE_CONFIRMED` | Debe ser `YES` para backup |
| `SIGEDON_BACKUP_KEEP_COUNT` | Conservar los N verificados más recientes |
| `SIGEDON_BACKUP_KEEP_DAYS` | Conservar verificados con edad ≤ N días |
| `SIGEDON_BACKUP_ALERT_HOOK` | Ejecutable local opcional (alerta acotada) |
| `SIGEDON_BACKUP_ALERT_HOOK_TIMEOUT_SECONDS` | Timeout del hook (default 30, máx 120) |
| `SIGEDON_BACKUP_ALERT_ON_SUCCESS` | `YES` para invocar hook también en éxito |
| `SIGEDON_RESTORE_DB` | Base destino aislada (obligatoria) |
| `SIGEDON_RESTORE_MEDIA_ROOT` | Destino nuevo/vacío de media |
| `SIGEDON_RESTORE_CONFIRM` | Debe ser `YES` para recreate |
| `SIGEDON_RESTORE_ALLOWED_PREFIXES` | Por defecto `test_restore_\|staging_restore_` |
| `SIGEDON_RESTORE_DRILL_ENABLED` | Debe ser `YES` para `run_restore_drill.sh` |
| `SIGEDON_RESTORE_DRILL_RUN_DJANGO` | `YES` ejecuta la cadena Django post-restore |

Los scripts **no leen ni modifican** `.env`. No hay valores productivos por
defecto.

## Lock exclusivo

Un solo backup/retención a la vez por despliegue:

```text
<SIGEDON_BACKUP_ROOT>/.sigedon-ops.lock
```

**Modelo:** un lock global en FD 9, adquirido por el punto de entrada
(`run_scheduled_backup.sh`, `backup_sigedon.sh` o `apply_retention.sh`). El
runner lo mantiene durante todo el pipeline (backup → verify → retención).

**Hijos del pipeline:** heredan el FD 9 abierto. `acquire_backup_lock` comprueba
que `/proc/self/fd/9` apunta al lock file y reafirma `flock -n` sobre ese mismo
open-file description. No reabre el archivo (un `exec 9>` nuevo fallaría con
código 8 aunque el padre ya tenga el lock). No existe un atajo de operador por
variable de entorno: sin FD 9 heredado, la adquisición es obligatoria.

`flock -n` → código 8 si otro job tiene el lock. No envolver el runner con un
`flock` externo sobre el mismo archivo (doble adquisición).

## Markers de estado (monitoring)

```text
<SIGEDON_BACKUP_ROOT>/.sigedon-backup-status.json
<SIGEDON_BACKUP_ROOT>/.sigedon-restore-drill-status.json
```

Campos: `job`, `status` (`success`|`failure`), `exit_code`, `phase`,
`backup_id`, `message` acotado, `finished_at_utc`. Sin secretos, sin rutas
productivas, sin contenido de dumps.

Un backup fallido **nunca** escribe `status=success`.

## Retención

```bash
export SIGEDON_BACKUP_KEEP_COUNT=7
export SIGEDON_BACKUP_KEEP_DAYS=30
./deploy/backups/apply_retention.sh
```

Reglas:

- solo hijos directos de `SIGEDON_BACKUP_ROOT` con `backup_id` válido;
- solo tras pasar `verify_backup.sh`;
- no borra symlinks, temps `.sigedon-*`, ni el root;
- con ambos KEEP_*: se conserva si está en los N más recientes **o** dentro de D días.

## Hook de alerta

Opcional. La plataforma implementa la entrega. Contrato:

```text
argv: <job> <status> <exit_code> <phase> <backup_id>
env:  SIGEDON_ALERT_*
```

Ejemplo: `examples/sigedon-backup-alert.hook.example`. Timeout acotado; fallo del
hook no enmascara el exit code del job.

## Uso

### Backup manual

```bash
export SIGEDON_MAINTENANCE_CONFIRMED=YES
export SIGEDON_BACKUP_ROOT=/ruta/fuera/del/repo/backups
export SIGEDON_MEDIA_ROOT=/ruta/absoluta/media
export POSTGRES_DB=...
export POSTGRES_USER=sigedon_owner
export POSTGRES_HOST=...
export POSTGRES_PORT=5432

./deploy/backups/backup_sigedon.sh
```

### Pipeline programado

```bash
export SIGEDON_MAINTENANCE_CONFIRMED=YES   # solo tras quietar escritores
export SIGEDON_BACKUP_ROOT=...
export SIGEDON_MEDIA_ROOT=...
export SIGEDON_BACKUP_KEEP_COUNT=7
export SIGEDON_BACKUP_KEEP_DAYS=30
# export SIGEDON_BACKUP_ALERT_HOOK=/ruta/hook
export POSTGRES_DB=...
export POSTGRES_USER=...
export POSTGRES_HOST=...
export POSTGRES_PORT=5432

./deploy/backups/run_scheduled_backup.sh
```

### Verificación

```bash
./deploy/backups/verify_backup.sh /ruta/al/<backup_id>
```

### Restore aislado / drill

```bash
export SIGEDON_RESTORE_DRILL_ENABLED=YES
export SIGEDON_RESTORE_DB=test_restore_sigedon_20260714
export SIGEDON_RESTORE_MEDIA_ROOT=/tmp/sigedon_restore_media
export SIGEDON_RESTORE_CONFIRM=YES
export POSTGRES_DB=sigedon_activo_no_destino
export POSTGRES_USER=...
export POSTGRES_HOST=...
export POSTGRES_PORT=5432

./deploy/backups/run_restore_drill.sh
# o: ./deploy/backups/restore_sigedon.sh /ruta/al/<backup_id>
```

**Prohibido:** automatizar restore sobre la base de producción. Los timers de
ejemplo llaman solo a `run_restore_drill.sh` con prefijos aislados.

### Post-restore

Con el entorno apuntando a la base y media restauradas bajo el **rol runtime**:

```bash
export POSTGRES_DB=<SIGEDON_RESTORE_DB>
export SIGEDON_MEDIA_ROOT=<SIGEDON_RESTORE_MEDIA_ROOT>
python manage.py migrate --check
python manage.py check --deploy
python manage.py verify_postgres_security
python manage.py reconcile_operational_code_sequences
python manage.py verify_restored_data
```

## Scheduling (ejemplos de plataforma)

Ver `examples/`:

- `sigedon-backup.cron.example`
- `sigedon-backup.service.example` + `sigedon-backup.timer.example`
- `sigedon-restore-drill.service.example` + `sigedon-restore-drill.timer.example`
- `sigedon-backup.env.example`

El repositorio no instala unidades systemd ni crontab.

## RPO / RTO

**No son un SLA aprobado.** Los valores siguientes son **propuestas no
aprobadas / placeholders de decisión de despliegue**; no constituyen un
compromiso institucional hasta aprobación explícita del entorno.

| Objetivo | Propuesta (no aprobada) | Notas |
|---|---|---|
| RPO | ≤ 24 h | alineada al ejemplo de backup diario (`examples/*.timer`); decidir en despliegue |
| RTO | ≤ 4 h | **placeholder**; debe validarse con un restore drill representativo medido |

El RTO solo puede afirmarse tras un drill de restauración representativo con
wall-clock documentado. Sin drill medido, no declarar RTO cumplido. Ninguno de
los dos valores es un SLA aprobado todavía.

Documentar en el runbook del entorno la decisión adoptada y el tiempo real del
último drill exitoso.

## Infraestructura aún pendiente (fuera de scripts)

- cifrado en reposo / en tránsito de artefactos;
- copia off-site;
- proveedor cloud de backup / SDK externo (no se introduce);
- backup/restore de objetos R2 vía scripts (export/restore implementados;
  cuenta/bucket aún no provisionados; drill R2 real pendiente).

## Códigos de salida

| Código | Significado |
|---|---|
| 0 | Éxito |
| 2 | Variables ausentes / uso incorrecto |
| 3 | Validación de artefacto o herramientas |
| 4 | Rechazo de seguridad / confirmación |
| 5 | Conflicto (backup existente) |
| 6 | Fallo de dump |
| 7 | Fallo de media / export de objetos privados |
| 8 | Lock exclusivo no disponible |
| 9 | Fallo / rechazo de retención |

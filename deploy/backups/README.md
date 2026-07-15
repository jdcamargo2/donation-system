# Backup y restauración manual de SIGEDON

Primera versión **manual**, segura y verificable. No incluye cron, systemd,
almacenamiento cloud ni cifrado en los scripts.

## Artefacto

```text
<SIGEDON_BACKUP_ROOT>/<backup_id>/
    database.dump      # pg_dump --format=custom
    media.tar.gz       # copia de MEDIA_ROOT
    manifest.json      # metadatos + checksums
```

`media.tar.gz` contiene archivos regulares bajo `MEDIA_ROOT` (rutas relativas).
Se omiten `staticfiles` y enlaces simbólicos (incluidos externos).

## Consistencia

Estrategia: **ventana de mantenimiento obligatoria**.

Antes de `backup_sigedon.sh` debe detenerse:

- procesos web;
- workers;
- comandos de procesamiento Kobo;
- uploads / tráfico de escritura.

El script exige `SIGEDON_MAINTENANCE_CONFIRMED=YES` y no puede comprobar por sí
solo que esos procesos estén detenidos.

## Variables

| Variable | Uso |
|---|---|
| `SIGEDON_BACKUP_ROOT` | Destino de backups (se crea con `0700` si no existe) |
| `SIGEDON_MEDIA_ROOT` | Raíz de media a respaldar (debe existir; no se crea) |
| `POSTGRES_DB` | Base origen del dump |
| `POSTGRES_USER` | Usuario del cliente PostgreSQL |
| `POSTGRES_HOST` | Host |
| `POSTGRES_PORT` | Puerto |
| `PGPASSWORD` | Opcional; preferir `~/.pgpass` |
| `SIGEDON_MAINTENANCE_CONFIRMED` | Debe ser `YES` para backup |
| `SIGEDON_RESTORE_DB` | Base destino aislada (obligatoria) |
| `SIGEDON_RESTORE_MEDIA_ROOT` | Destino nuevo/vacío de media |
| `SIGEDON_RESTORE_CONFIRM` | Debe ser `YES` para recreate |
| `SIGEDON_RESTORE_ALLOWED_PREFIXES` | Por defecto `test_restore_\|staging_restore_` |

Los scripts **no leen ni modifican** `.env`. No hay valores productivos por
defecto.

Autenticación preferida: archivo `~/.pgpass` (permisos `0600`). `PGPASSWORD`
es aceptable en sesión interactiva controlada; nunca se imprime.

## Uso

### Backup

```bash
export SIGEDON_MAINTENANCE_CONFIRMED=YES
export SIGEDON_BACKUP_ROOT=/ruta/fuera/del/repo/backups
# Si no existe, el script lo crea con permisos 0700 (no hace falta mkdir -p).
export SIGEDON_MEDIA_ROOT=/ruta/absoluta/media
# SIGEDON_MEDIA_ROOT debe existir; el script no lo crea.
export POSTGRES_DB=...
export POSTGRES_USER=sigedon_owner   # o rol con privilegio de dump
export POSTGRES_HOST=...
export POSTGRES_PORT=5432
# Preferir ~/.pgpass en lugar de PGPASSWORD

./deploy/backups/backup_sigedon.sh
```

### Verificación

```bash
./deploy/backups/verify_backup.sh /ruta/al/<backup_id>
```

### Restore aislado

```bash
export SIGEDON_RESTORE_DB=test_restore_sigedon_20260714
export SIGEDON_RESTORE_MEDIA_ROOT=/tmp/sigedon_restore_media
export SIGEDON_RESTORE_CONFIRM=YES
export POSTGRES_DB=sigedon_activo_no_destino
export POSTGRES_USER=...
export POSTGRES_HOST=...
export POSTGRES_PORT=5432

./deploy/backups/restore_sigedon.sh /ruta/al/<backup_id>
```

Rechazos de seguridad:

- `SIGEDON_RESTORE_DB` vacío o igual a `POSTGRES_DB`;
- nombre sin prefijo seguro;
- media destino no vacío;
- falta de confirmación explícita.

### Post-restore

Con el entorno apuntando a la base y media restauradas (sin tocar `.env` de
producción):

```bash
python manage.py migrate --check
python manage.py check
python manage.py verify_postgres_security
python manage.py reconcile_operational_code_sequences
python manage.py verify_restored_data
```

`reconcile_operational_code_sequences` funciona en modo detect-only y es de
solo lectura: no crea ni ajusta secuencias. El comando falla ante los estados
`MISSING_SEQUENCE`, `LAGGING_SEQUENCE` e `INVALID_SEQUENCE`, correspondientes a
una secuencia ausente, atrasada o inválida. No existe reparación automática;
cualquier corrección debe revisarse y ejecutarse manualmente.

## Pruebas trimestrales

Probar restauración en base con prefijo `test_restore_` o `staging_restore_`,
verificar con `verify_backup.sh` + `verify_restored_data`, y documentar el
tiempo real observado. **RPO/RTO no están definidos** hasta medir restauraciones
reales.

## Infraestructura pendiente

Fuera del alcance de esta fase (requisito operativo, no implementado aquí):

- cifrado en reposo / en tránsito de artefactos;
- copia off-site;
- automatización de frecuencia y retención;
- cron / systemd.

## Códigos de salida (orientativos)

| Código | Significado |
|---|---|
| 0 | Éxito |
| 2 | Variables ausentes / uso incorrecto |
| 3 | Validación de artefacto o herramientas |
| 4 | Rechazo de seguridad / confirmación |
| 5 | Conflicto (backup existente) |
| 6 | Fallo de dump |
| 7 | Fallo de media |

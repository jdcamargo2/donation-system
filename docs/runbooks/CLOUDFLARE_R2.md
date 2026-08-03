# Runbook: Cloudflare R2 (media privada)

Preparación RENDER-2/3: el soporte R2 está **implementado en código**. No hay
cuenta Cloudflare, bucket ni credenciales provisionadas todavía. No se ha
completado una prueba real de conectividad. El default de desarrollo local
sigue siendo `SIGEDON_PRIVATE_STORAGE=filesystem`. En el **destino Render
final**, filesystem no es un modo aceptado para documentos productivos: R2
solo se activa tras la puerta documentada en
[RENDER_FIRST_DEPLOY.md](RENDER_FIRST_DEPLOY.md). Activar R2 es
**configuración** (cuenta, bucket, secretos, verificaciones), no otro rewrite.

## Estado actual vs futuro

| Estado | Realidad |
| --- | --- |
| Código | `core.private_storage`, `django-storages` / `boto3`, checks, comandos |
| Default local | `SIGEDON_PRIVATE_STORAGE=filesystem` |
| Render final | `r2` tras probe + aceptación (verificador rechaza filesystem) |
| Cuenta / bucket / token | **No** provisionados |
| `verify_private_storage --probe` real | **No** ejecutado contra Cloudflare |
| Archivos productivos en R2 | Ninguno |
| Staging / go-no-go | **No** ejecutados |

### Pasos futuros de provisionamiento (orden)

1. Crear cuenta Cloudflare; habilitar R2.
2. Crear **un** bucket privado.
3. **No** habilitar URL pública de desarrollo / `r2.dev`.
4. Crear token de API con alcance mínimo al bucket (Object Read & Write).
5. Guardar secretos en Render (nunca en el repo).
6. Definir `SIGEDON_PRIVATE_STORAGE=r2` y variables `R2_*`.
7. Ejecutar `python manage.py check --deploy` y
   `python manage.py verify_private_storage`.
8. En staging/release: `python manage.py verify_private_storage --probe`.
9. Smoke: upload autorizado → download autorizado → rechazo no autorizado.
10. Backup + restore drill aislado (BD + objetos privados).
11. Solo entonces aceptar archivos reales.

## Variables requeridas

```env
SIGEDON_PRIVATE_STORAGE=r2
SIGEDON_PRIVATE_FILE_DELIVERY=stream   # o signed_redirect
R2_ACCOUNT_ID=
R2_ACCESS_KEY_ID=
R2_SECRET_ACCESS_KEY=
R2_BUCKET_NAME=
# R2_ENDPOINT_URL=                     # opcional; si falta se deriva del account id
R2_REGION_NAME=auto
R2_SIGNED_URL_EXPIRY_SECONDS=300       # 60–900
R2_ADDRESSING_STYLE=path
```

La sola presencia de `R2_*` **no** selecciona R2: hace falta
`SIGEDON_PRIVATE_STORAGE=r2` explícito. En modo filesystem,
`SIGEDON_MEDIA_ROOT` sigue siendo obligatorio en producción.

Rechazadas si están definidas: `R2_PUBLIC_URL`, `AWS_S3_CUSTOM_DOMAIN`,
`AWS_S3_URL_PROTOCOL`.

## Principios

* Cambiar la ubicación del storage no debilita la autorización.
* `FileField.url` **nunca** es autorización.
* Bucket privado; `default_acl=None`; `querystring_auth=True`;
  `file_overwrite=False`.
* URL firmadas de corta vida; quien tenga una no expirada puede usarla hasta
  el expiry; autorizar **antes** de generarlas; **nunca** registrarlas en logs.
* WhiteNoise solo estáticos; staticfiles **nunca** usan R2.
* `/readyz/` **no** llama a R2; usar `verify_private_storage --probe` en
  staging/release.
* Con objetos productivos en R2, volver ciegamente a un filesystem vacío hace
  que los documentos parezcan ausentes.

## Bucket privado y token de mínimo privilegio

* Un bucket dedicado a media privada de SIGEDON.
* Sin acceso público, sin `r2.dev`, sin custom domain público.
* Token de alcance de bucket (no cuenta completa) con lectura/escritura de
  objetos; sin permisos administrativos innecesarios.
* Guardar `R2_ACCESS_KEY_ID` / `R2_SECRET_ACCESS_KEY` solo en secretos de
  plataforma.

## Rotación de credenciales

1. Crear token nuevo (mismo alcance).
2. Actualizar secretos en Render / entorno.
3. Reiniciar o redeploy del proceso web.
4. `verify_private_storage --probe`.
5. Revocar el token anterior tras confirmar lecturas/escrituras.
6. Si hubo exposición: seguir [Compromiso de credenciales](#compromiso-de-credenciales).

## URL firmadas

* Modo `SIGEDON_PRIVATE_FILE_DELIVERY=signed_redirect`: Django autoriza y
  redirige a una URL firmada de corta vida.
* Modo `stream` (default): el contenido pasa por la aplicación tras autorizar.
* Expiry: `R2_SIGNED_URL_EXPIRY_SECONDS` (default 300; rango 60–900).
* Tratar URL firmadas como secretos temporales: no logs, no tickets, no
  analytics.

## Sonda de conectividad

```bash
python manage.py verify_private_storage          # solo configuración (sin red)
python manage.py verify_private_storage --probe  # write/read/delete acotado
```

`--probe` requiere infraestructura real. No imprime endpoint, bucket, claves
ni URL firmadas. **No** forma parte de `/readyz/`.

## Smoke de upload / download

En staging, con un usuario autorizado:

1. Subir un documento desechable por un flujo operativo.
2. Preview/download autorizados → OK.
3. Usuario sin permiso / anónimo → 403 o login (según ruta).
4. Confirmar que no hay URL pública directa al objeto.
5. Eliminar el registro de prueba por método autorizado.

## Rollback a filesystem

Solo **antes** de que existan objetos productivos en R2:

1. `SIGEDON_PRIVATE_STORAGE=filesystem`
2. Montar `SIGEDON_MEDIA_ROOT` persistente y válido.
3. `check --deploy` + `verify_private_storage`.

Si ya hay objetos en R2: migrar con `migrate_private_media` / export-import;
no apuntar a un volumen vacío.

## Migración de media (`migrate_private_media`)

```bash
python manage.py migrate_private_media --dry-run
python manage.py migrate_private_media --apply
python manage.py migrate_private_media --apply --verify
# --delete-source solo tras verificación exitosa del destino
```

Default: dry-run. No sobrescribe contenido remoto distinto; no imprime
secretos ni URL.

## Backup / restore

* Pipeline `deploy/backups/` empaqueta `MEDIA_ROOT` en `media.tar.gz`
  (filesystem). Con R2 activo eso **no** cubre el bucket.
* Exportar objetos referenciados: `python manage.py export_private_objects
  --output-directory <dir>`.
* Restore drill: BD aislada + objetos privados restaurados/verificados
  **antes** de aceptar tráfico.
* Ver `deploy/backups/README.md`.

## Incidente: storage no disponible

Síntomas: fallos de upload/download, errores de storage en logs (sin secretos).

1. Confirmar modo (`SIGEDON_PRIVATE_STORAGE`) y secretos presentes (sin
   volcarlos).
2. `verify_private_storage` y, si aplica, `--probe`.
3. Revisar estado Cloudflare R2 / red de salida de Render.
4. No “arreglar” exponiendo `FileField.url` ni abriendo el bucket.
5. Si el fallo es prolongado: modo mantenimiento; comunicar impacto en
   documentos privados; no cambiar a filesystem vacío si R2 ya tiene objetos.

## Compromiso de credenciales

1. Revocar el token expuesto de inmediato.
2. Emitir token nuevo; actualizar secretos; redeploy.
3. Auditar acceso al bucket (objetos tocados, listados).
4. Rotar cualquier URL firmada emitida (esperar expiry o rotar claves).
5. Revisar logs de aplicación y plataforma (sin re-loguear secretos).
6. Evaluar si hace falta re-cifrar o re-subir objetos según política
   institucional.

## Referencias

* [DEPLOYMENT.md §8](../DEPLOYMENT.md#8-gestión-de-archivos)
* [SECURITY.md §6](../SECURITY.md#6-archivos-privados)
* [ARCHITECTURE.md §5](../ARCHITECTURE.md#5-gestión-de-archivos)
* [OPERATIONS.md §7](../OPERATIONS.md#7-gestión-de-archivos)
* [TESTING.md §13.1](../TESTING.md#131-storage-privado-r2-preparación-vs-conectividad-real)
* `.env.example` (placeholders comentados)

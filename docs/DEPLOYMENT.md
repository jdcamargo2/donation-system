# Despliegue de SIGEDON

Este documento describe cómo **desplegar** una instancia de SIGEDON.

Operación diaria: [OPERATIONS.md](OPERATIONS.md).
Quick Start local: [README](../README.md).
Backups: [deploy/backups/README.md](../deploy/backups/README.md).
Kobo: [KOBO.md](KOBO.md) (desconectado por defecto; no asumir live).
Variables Render: [RENDER_ENVIRONMENT.md](runbooks/RENDER_ENVIRONMENT.md).
R2: [CLOUDFLARE_R2.md](runbooks/CLOUDFLARE_R2.md).

**Estado de esta edición:** tooling Render implementado; **no** existe
deployment productivo verificado. R2 soportado en código, **no** provisionado.
Scripts testeados ≠ plataforma real verificada.

## 1. Requisitos

El entorno de producción requiere:

* Python 3.12;
* PostgreSQL;
* Gunicorn sirviendo `core.wsgi:application` (contrato WSGI canónico);
* WhiteNoise sirviendo estáticos recopilados desde `STATIC_ROOT` (sin Nginx
  dedicado ni disco persistente para estáticos);
* un proxy inverso con HTTPS;
* almacenamiento persistente para **media privada** (no para estáticos);
* variables de entorno seguras;
* acceso controlado a logs y respaldos;
* un mecanismo de supervisión y reinicio del proceso de aplicación.

### Destino inicial en Render (runtime Python nativo)

El despliegue inicial en Render usa el **runtime Python nativo** (sin Docker).
Scripts y runbooks RENDER-3 están **preparados**; **no** hay Web Service,
PostgreSQL, bucket R2, dominio ni credenciales reales provisionados. No se ha
ejecutado staging ni go/no-go de producción.

```text
./deploy/render/build.sh
  → (Render ya instaló requirements.txt en runtime nativo)
  → collectstatic + verify_deployment_assets
  → private storage de build: filesystem temporal (sin secretos R2)

./deploy/render/pre_deploy.sh   # owner/migrator; preferir one-off shell
  → check --deploy → migrate → sync_sigedon_roles → reconcile → preflight

./deploy/start_web.sh
  → Gunicorn (deploy/gunicorn.conf.py) + WhiteNoise
  → Health check de plataforma: /readyz/

./deploy/render/post_deploy_verify.sh   # credenciales runtime
  → verify_postgres_security + verify_render_configuration (+ probe opcional)
```

Media privada **final** aceptada en Render: `SIGEDON_PRIVATE_STORAGE=r2` tras
probe y aceptación. Filesystem no es modo final en Render. Detalle:
[RENDER_FIRST_DEPLOY.md](runbooks/RENDER_FIRST_DEPLOY.md),
[RENDER_ENVIRONMENT.md](runbooks/RENDER_ENVIRONMENT.md),
[CLOUDFLARE_R2.md](runbooks/CLOUDFLARE_R2.md),
[deploy/render/README.md](../deploy/render/README.md).

## 2. Variables obligatorias

Configuración mínima:

```env
DJANGO_DEBUG=False
DJANGO_SECRET_KEY=
ALLOWED_HOSTS=
DATABASE_ENGINE=postgresql

POSTGRES_DB=
POSTGRES_USER=
POSTGRES_PASSWORD=
POSTGRES_HOST=
POSTGRES_PORT=5432

DATABASE_CONN_MAX_AGE=60
```

### Reglas

* `DJANGO_SECRET_KEY` debe contener un valor seguro y no predecible.
* `ALLOWED_HOSTS` debe incluir únicamente los dominios autorizados.
* `DATABASE_ENGINE` debe permanecer en `postgresql`.
* Las credenciales no deben almacenarse en el repositorio.
* El archivo `.env` no debe versionarse.
* `DATABASE_CONN_MAX_AGE` debe ajustarse según la infraestructura y el pool de conexiones.

## 3. Seguridad HTTPS

Configuración de referencia:

```env
SECURE_SSL_REDIRECT=True
SECURE_HSTS_SECONDS=3600
SECURE_HSTS_INCLUDE_SUBDOMAINS=False
SECURE_HSTS_PRELOAD=False
SECURE_PROXY_SSL_HEADER_ENABLED=False
```

También deben configurarse los orígenes HTTPS autorizados:

```env
CSRF_TRUSTED_ORIGINS=https://sigedon.example.org
```

### Consideraciones

* `SECURE_SSL_REDIRECT=True` obliga a utilizar HTTPS.
* `SECURE_HSTS_SECONDS` debe aumentarse progresivamente después de verificar el despliegue.
* `SECURE_HSTS_INCLUDE_SUBDOMAINS` solo debe activarse si todos los subdominios utilizan HTTPS.
* `SECURE_HSTS_PRELOAD` no debe activarse sin comprender las implicaciones permanentes del preload.
* `SECURE_PROXY_SSL_HEADER_ENABLED` depende de la configuración real del proxy.
* La aplicación debe confiar únicamente en cabeceras insertadas por un proxy controlado.

Cuando el proxy termina TLS y envía tráfico HTTP interno, debe verificarse la configuración de:

```python
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
```

No debe habilitarse esta opción si clientes externos pueden falsificar directamente esa cabecera.

## 4. Configuración de KoboToolbox

Kobo **no** se asume live en el primer deploy. Default: `KOBO_ENABLED=False`.

Cuando se active la integración operativa, seguir [KOBO.md](KOBO.md).
Secretos: `KOBO_API_TOKEN`, `KOBO_WEBHOOK_SECRET`. Catálogo de knobs HTTP:
esa misma fuente, no `.env.example`.

## 5. Preparación del despliegue

La fuente canónica del arranque en producción es este documento. Migraciones,
`collectstatic`, sincronización de roles y verificaciones de seguridad son
pasos de **release**, no del proceso web.

### 5.1. Instalar dependencias

```bash
python -m pip install -r requirements.txt
```

`requirements.txt` incluye Gunicorn (`gunicorn==23.0.0`) como servidor WSGI
de producción, WhiteNoise (`whitenoise==6.12.0`) para estáticos recopilados,
y `django-storages[s3]` / `boto3` para el backend opcional de media privada
en Cloudflare R2. No se declara Uvicorn/Daphne ni workers async/gevent.
La presencia de esas dependencias **no** activa R2: el default sigue siendo
`SIGEDON_PRIVATE_STORAGE=filesystem`.

#### Build canónico en Render (sin migraciones)

```bash
./deploy/render/build.sh
```

Render nativo instala `requirements.txt` **antes** del Build Command. El
script vuelve a instalar solo si `SIGEDON_RENDER_INSTALL_DEPS=YES` (harness
local). Ejecuta `pip check`, `collectstatic --noinput` y
`verify_deployment_assets`. Fuerza storage de build en filesystem temporal.
No ejecuta migraciones, seeds, sync de roles, backup/restore, Gunicorn ni
`verify_postgres_security`.

#### Pre-deploy / release (owner)

```bash
./deploy/render/pre_deploy.sh
```

Preferir one-off con credenciales owner; no dejar secretos migrator en el
Web Service si la plataforma no puede acotarlos por proceso. No incluye
`verify_postgres_security` ni `--probe` de R2.

#### Start canónico (sin collectstatic ni migrate)

```bash
./deploy/start_web.sh
```

Equivale a `gunicorn core.wsgi:application --config deploy/gunicorn.conf.py`.
Bind `0.0.0.0` y puerto desde `PORT`. Health check de Render: `/readyz/`.

### 5.2. Secuencia de release (mutaciones controladas)

En Render, preferir el wrapper canónico (owner credentials):

```bash
./deploy/render/pre_deploy.sh
```

Orden equivalente (Gunicorn **nunca** ejecuta estos pasos; `collectstatic`
pertenece al **build**):

```bash
python manage.py check --deploy
python manage.py makemigrations --check --dry-run
python manage.py migrate --plan
python manage.py migrate --noinput
python manage.py migrate --check
python manage.py sync_sigedon_roles
python manage.py reconcile_operational_code_sequences
./deploy/preflight.sh
# Tras cambiar a credenciales runtime:
python manage.py verify_postgres_security
python manage.py verify_render_configuration
# Opcional explícito: verify_private_storage --probe
```

Notas:

* `migrate` corre una vez bajo el propietario del esquema;
* `collectstatic` corre en **build** (`./deploy/render/build.sh`), no en
  pre-deploy ni en Gunicorn;
* `sync_sigedon_roles` corre una vez por release;
* `verify_postgres_security` **debe** ejecutarse con el rol runtime final
  (`sigedon_app`); un éxito bajo `sigedon_owner`/superusuario no valida el
  contrato. Exige PostgreSQL (otro motor → código distinto de 0). Verifica
  append-only de `AuditLog` y `ExpenseRequestEvent` (catálogo + sondas con
  rollback), constraints financieros críticos y privilegios runtime sobre
  `operations_auditlog`. No repara nada; el fallo bloquea tráfico.
  `preflight.sh` y `pre_deploy.sh` no lo invocan; usar
  `./deploy/render/post_deploy_verify.sh`;
* `verify_render_configuration` valida el contrato Render sin red;
* Aceptación staging / go-no-go:
  [STAGING_ACCEPTANCE.md](runbooks/STAGING_ACCEPTANCE.md),
  [PRODUCTION_GO_NO_GO.md](runbooks/PRODUCTION_GO_NO_GO.md);
* `reconcile_operational_code_sequences` es **detect-only** (solo lectura):
  falla ante secuencias ausentes/atrasadas/inválidas y no repara automáticamente;
* `./deploy/preflight.sh` es la puerta de readiness **de release** (estática)
  **después** de las mutaciones y **antes** de aceptar tráfico; no aplica
  migraciones ni inicia Gunicorn. No sustituye a las sondas HTTP de runtime:
  * `preflight` = validación de release/despliegue (`check --deploy`,
    `makemigrations --check`, `migrate --check`, assets);
  * `/healthz/` = liveness del proceso Django;
  * `/readyz/` = readiness runtime (BD por defecto + migraciones aplicadas).
* **No** ejecutar `seed_sigedon_demo` en release/preflight/arranque: es solo
  desarrollo y se rechaza cuando `DEBUG=False`.
* **No** invocar `process_kobo_submissions` ni `reconcile_kobo_submissions` en
  el arranque web ni en preflight; son recuperación operativa explícita. Sus
  códigos de salida distintos de cero pueden coexistir con commits parciales
  exitosos (ver [OPERATIONS.md §5](OPERATIONS.md#5-procesamiento-y-reconciliación-de-kobo)).
  No enmascarar con `|| true`.
* Runbook de roles: [Operaciones §3](OPERATIONS.md#3-sincronización-de-roles).
* Sondas HTTP: [§6.3](#63-sondas-http-healthz-y-readyz).

Variables opcionales de preflight:

* `PYTHON_BIN` — intérprete (por defecto `python`);
* `SIGEDON_PREFLIGHT_SHOW_MIGRATE_PLAN=YES` — muestra `migrate --plan` sin aplicar.

### 5.3. Prerrequisitos de media y estáticos

* Con `SIGEDON_PRIVATE_STORAGE=filesystem` (default): montar el volumen
  persistente y exportar `SIGEDON_MEDIA_ROOT` **antes** del preflight
  (`check --deploy` valida existencia/permisos).
* Con `SIGEDON_PRIVATE_STORAGE=r2`: provisionar bucket privado + secretos
  `R2_*` en la plataforma **antes** de aceptar tráfico; ejecutar
  `python manage.py verify_private_storage` y, en staging/release,
  `verify_private_storage --probe`. `/readyz/` **no** llama a R2.
  Ver [CLOUDFLARE_R2.md](runbooks/CLOUDFLARE_R2.md). Hoy no hay cuenta R2
  provisionada ni sonda real completada.
* `collectstatic` debe haber poblado `STATIC_ROOT` (por defecto
  `<BASE_DIR>/staticfiles`) con sentinelas locales: CSS de aplicación
  y vendor UI (Bootstrap 5.3.3, Bootstrap Icons 1.11.3,
  SweetAlert2 11.26.25). En producción, WhiteNoise sirve esos artefactos
  con nombres hashed (`CompressedManifestStaticFilesStorage`) y compresión
  gzip; no se necesita disco persistente ni Nginx para estáticos.
* El comando `verify_deployment_assets` comprueba rutas **lógicas** vía
  staticfiles storage (compatible con manifest) sin mutar archivos. Es
  puerta de release/preflight tras `collectstatic`.
* El panel interno no requiere CDN públicos en runtime para estas
  dependencias; inventario y licencias en
  [`static/vendor/THIRD_PARTY_ASSETS.md`](../static/vendor/THIRD_PARTY_ASSETS.md).
* La plataforma/CDN de borde **no** debe cachear HTML autenticado ni media
  privada. WhiteNoise aplica caché immutable a assets hashed; las
  respuestas HTML de la aplicación no las cachea WhiteNoise.
* Actualizaciones futuras de vendor siguen exigiendo `collectstatic` +
  tests/preflight.

### Orden recomendado

```text
Instalar dependencias
→ Validar variables / montar media
→ Crear respaldo
→ Migraciones (owner)
→ collectstatic
→ sync_sigedon_roles
→ verify_postgres_security / reconcile (detect-only)
→ ./deploy/preflight.sh
→ Sustituir proceso web (Gunicorn)
→ Comprobaciones funcionales
```

## 6. Servidor de aplicación (Gunicorn)

SIGEDON usa **Gunicorn con workers síncronos** como servidor de aplicación
canónico de producción, sirviendo el entry point WSGI:

```text
core.wsgi:application
```

ASGI (`core.asgi:application`) permanece disponible para uso futuro, pero
**no** es el contrato de producción de este checkpoint.

### Comando canónico

```bash
gunicorn core.wsgi:application --config deploy/gunicorn.conf.py
```

Equivalente con wrapper del repositorio (sin migraciones ni collectstatic):

```bash
./deploy/start_web.sh
```

Configuración: `deploy/gunicorn.conf.py` (overrides por entorno; ver tabla).
Valores conservadores iniciales:

| Variable | Default |
| --- | --- |
| `PORT` / bind | `0.0.0.0:8000` (`GUNICORN_BIND` sustituye) |
| `GUNICORN_WORKERS` | `2` |
| `GUNICORN_THREADS` | `1` |
| `worker_class` | `sync` |
| `GUNICORN_TIMEOUT` | `60` |
| `GUNICORN_GRACEFUL_TIMEOUT` | `30` |
| `GUNICORN_KEEPALIVE` | `5` |
| access / error log | stdout / stderr (`-`) |
| daemon / reload / preload | desactivados |

### Logging runtime y correlación

Los logs de proceso van a **stdout/stderr**. La plataforma es responsable de
recolección, retención, rotación y alertas. No hay FileHandler ni cliente SaaS
de logging en la aplicación.

Variables consumidas:

| Variable | Default | Uso |
| --- | --- | --- |
| `DJANGO_LOG_LEVEL` | `INFO` (prod) / `WARNING` (DEBUG) | logger `django` |
| `SIGEDON_LOG_LEVEL` | `INFO` | logger `sigedon` |
| `KOBO_LOG_LEVEL` | `INFO` | logger `sigedon.kobo` |

Contrato con Gunicorn:

* access log de Gunicorn: una línea por petición (stdout);
* `django.request`: solo WARNING/ERROR (evita duplicar acceso ordinario);
* logs de aplicación: ciclo de vida, integraciones y fallos;
* cabecera de respuesta `X-Request-ID` en todas las respuestas HTTP;
* IDs inbound válidos (8–64, `[A-Za-z0-9._-]`) se aceptan; el resto se reemplaza;
* IDs generados están garantizados en logs Django y en la cabecera de respuesta;
* Gunicorn no enriquece el access log con el ID generado por middleware en este
  checkpoint; un proxy futuro puede propagar el mismo `X-Request-ID`.

Al arrancar, verificar en logs (sin volcar entorno):

* versión/arranque de Gunicorn;
* bind address;
* arranque de workers;
* fallos de arranque de Django;
* fallos de preflight en logs de release.

Nunca registrar: `SECRET_KEY`, URI completa de BD, tokens Kobo, dumps de entorno,
cuerpos de petición, payloads Kobo crudos ni cabeceras sensibles.

### Capacidad de workers y PostgreSQL

```text
conexiones potenciales ≈ GUNICORN_WORKERS × GUNICORN_THREADS
```

Ese producto debe quedar **por debajo** del cupo de conexiones del rol runtime,
dejando margen para comandos de release, backup/restore, administración,
comandos Kobo y monitoreo. No usar `(2 × CPU) + 1` como default incondicional.
Las operaciones financieras usan transacciones y bloqueos de fila: más workers
no garantizan más throughput.

### Apagado elegante

* El gestor de procesos envía `SIGTERM`; Gunicorn deja de aceptar trabajo nuevo
  y espera hasta `graceful_timeout` (default 30s).
* `TimeoutStopSec` (systemd) u homogeneo de plataforma debe ser **mayor** que
  `GUNICORN_GRACEFUL_TIMEOUT`.
* `timeout` (default 60s) mata workers atascados; transacciones DB en vuelo
  pueden revertirse si el proceso es hard-killed.
* No hay jobs en background dentro de los workers web.
* Webhooks y peticiones deben completar dentro de los timeouts del proxy y de
  Gunicorn.

### Desarrollo

```bash
python manage.py runserver
```

es **solo desarrollo**. No usarlo en producción. Gunicorn no es necesario para
ejecutar la suite de tests.

El proceso web debe gestionarse con supervisión capaz de reiniciar ante fallos,
conservar logs, aplicar límites de recursos e iniciar tras reinicio del host.

### 6.1. Ejemplo systemd / VPS

```ini
[Service]
WorkingDirectory=/path/to/sigedon
EnvironmentFile=/path/to/sigedon.env
ExecStart=/path/to/venv/bin/gunicorn core.wsgi:application --config /path/to/sigedon/deploy/gunicorn.conf.py
Restart=on-failure
RestartSec=5
TimeoutStopSec=45
KillSignal=SIGTERM
```

Requisitos: sin `runserver`; sin migraciones en `ExecStart`; la cuenta del
servicio lee código/estáticos y escribe `SIGEDON_MEDIA_ROOT`; no usar
credenciales owner de PostgreSQL en el servicio web. El `ExecStart` es
Gunicorn únicamente: no incluir `seed_sigedon_demo`,
`process_kobo_submissions` ni `reconcile_kobo_submissions`.

### 6.2. Ejemplo contenedor / plataforma

* Fase de **release/job**: migraciones, `collectstatic`, sync de roles,
  verificaciones y `./deploy/preflight.sh` **una vez**.
* Comando **web**: solo Gunicorn (`./deploy/start_web.sh` o el comando canónico).
* Volumen de media persistente montado **antes** del preflight.
* Tras un release/preflight exitoso, la plataforma debe sondear `/healthz/` y
  `/readyz/` (ver §6.3). El arranque del proceso web sigue dependiendo del
  release/preflight; las sondas no reemplazan esa puerta.
* La plataforma envía `SIGTERM`; Gunicorn aplica apagado elegante.
* No se añade Dockerfile en este checkpoint (el repositorio no lo exige).

Arquitectura:

```text
Cliente
→ Proxy HTTPS
→ Gunicorn (WSGI, core.wsgi:application)
→ Django
→ PostgreSQL
```

### 6.3. Sondas HTTP (`/healthz/` y `/readyz/`)

Endpoints de aplicación (anónimos, mínimos, no cacheables):

| Sonda | Ruta | Significado | Listo | No listo |
| ----- | ---- | ----------- | ----- | -------- |
| Liveness | `GET /healthz/` | el proceso Django puede responder | `200` `{"status":"ok"}` | n/a (si el proceso no responde, la plataforma lo retira) |
| Readiness | `GET /readyz/` | BD `default` alcanzable **y** sin migraciones pendientes | `200` `{"status":"ready"}` | `503` `{"status":"not_ready"}` |

Contrato:

* Liveness **no** consulta BD, caché, Kobo, APIs externas, media ni migraciones.
* Readiness **solo** comprueba conectividad de la BD por defecto (`SELECT 1` en
  cada probe) y el plan de migraciones (vía `MigrationExecutor`; no ejecuta
  `migrate`). El resultado del plan puede cachearse en proceso durante
  `SIGEDON_READINESS_MIGRATION_CACHE_SECONDS` (default 15; rango 0–300; `0`
  desactiva el caché). Tras aplicar migraciones, un worker puede retener un
  resultado «pending» como máximo el TTL.
* Ninguna sonda depende de Kobo, caché, object storage remoto (R2), datos de
  negocio ni autenticación. `/readyz/` **no** llama a R2 en cada probe.
* Respuestas sin versión, entorno, host, nombre de BD, migraciones, credenciales
  ni mensajes de excepción. Cabeceras: `Cache-Control: no-store`, `Pragma:
  no-cache`, `X-Content-Type-Options: nosniff`, más `X-Request-ID` del middleware.
* La validación de media persistente (filesystem) permanece en
  `check --deploy` / preflight; la conectividad R2 se verifica con
  `verify_private_storage --probe` en staging/release, no en `/readyz/`.

Semántica durante el despliegue:

| Situación | `/healthz/` | `/readyz/` |
| --------- | ----------- | ---------- |
| Migraciones aún no aplicadas | puede ser `200` | `503` |
| BD no disponible | `200` | `503` |
| Kobo caído o deshabilitado | sin efecto | sin efecto |
| Caché no disponible | sin efecto | sin efecto |
| Media no escribible tras el arranque | fuera del alcance de estas sondas | fuera del alcance de estas sondas |

`preflight` ≠ sondas HTTP: el preflight no arranca un servidor web temporal; las
sondas no sustituyen `migrate --check` ni `check --deploy` en el release.

#### Ejemplos de plataforma (valores orientativos, no universales)

Load balancer / contenedor:

* Liveness path: `/healthz/`
* Readiness path: `/readyz/`
* Estados esperados: liveness `200`; readiness `200` (fallo `503`)
* Temporización sugerida (ajustar al entorno): initial delay según el despliegue;
  period 10–30 s; timeout 2–5 s; failure threshold 3

systemd / VPS (smoke post-arranque en el host, no exige `curl` dentro del
contenedor de la app):

```bash
curl --fail --silent --show-error \
  http://127.0.0.1:8000/healthz/ >/dev/null

curl --fail --silent --show-error \
  http://127.0.0.1:8000/readyz/ >/dev/null
```

Las comprobaciones externas de producción deben usar HTTPS a través del proxy
inverso cuando corresponda.

## 7. Usuarios y roles

No existe autorregistro público. El alta de cuentas institucionales es un
procedimiento administrativo explícito, no un formulario expuesto.

### Procedimiento de despliegue (bootstrap → alta institucional)

1. Aplicar migraciones (`python manage.py migrate`).
2. Si no existe ningún superusuario, crearlo mediante una shell de despliegue
   (no expuesta por HTTP):

   ```bash
   python manage.py createsuperuser
   ```

3. Iniciar sesión en `/accounts/login/` con esa cuenta superusuario.
4. Abrir `/panel/usuarios/` (solo visible/accesible para superusuarios; el
   enlace «Gestión de usuarios» del panel se oculta a cualquier otro usuario).
5. Crear las cuentas institucionales necesarias desde `/panel/usuarios/`,
   asignando a cada una exactamente un rol funcional canónico. La creación
   fija automáticamente `must_change_password=True` y una contraseña
   temporal; nunca `is_staff`/`is_superuser`.
6. Entregar las credenciales temporales a cada persona por un canal externo
   seguro (nunca en el propio sistema, tickets ni logs).
7. Cada usuario cambia su contraseña en el primer inicio de sesión
   (`/accounts/password_change/`); hasta entonces,
   `MustChangePasswordMiddleware` bloquea el acceso a `/panel/**` y a
   `/admin/` y solo permite logout, estáticos y sondas de salud.

### Reglas

* El superusuario debe reservarse para administración técnica y para el
  bootstrap inicial; `/admin/` queda reservado para recuperación técnica, no
  para la gestión operativa diaria de usuarios institucionales.
* No debe utilizarse como cuenta operativa diaria.
* El rol funcional «Administrador SIGEDON» **no** concede autoridad de
  identidad: no puede crear, editar, activar, desactivar ni restablecer
  contraseñas de otras cuentas. Solo `request.user.is_superuser` puede
  invocar esas operaciones (`/panel/usuarios/`).
* Un usuario ordinario puede tener como máximo un rol funcional canónico.
* Cada usuario debe contar únicamente con los permisos necesarios.
* Los permisos técnicos generales `kobo.*` (distintos de los territoriales
  automáticos) deben asignarse por separado.
* Las cuentas compartidas deben evitarse.
* No existe alta de cuentas por autorregistro público en ninguna ruta.
* Las rutas de restablecimiento de contraseña por correo
  (`/accounts/password_reset/` y equivalentes) están **deshabilitadas**
  (`404`) hasta que exista infraestructura SMTP revisada; el restablecimiento
  institucional se realiza desde `/panel/usuarios/` (solo superusuario).
* Desactivar una cuenta invalida de inmediato sus sesiones activas (backend
  de sesiones en base de datos); restablecer su contraseña también invalida
  sus sesiones, conservando la del actor cuando corresponde.
* Las credenciales iniciales deben cambiarse de forma segura y se fuerza su
  cambio en el primer inicio de sesión.

Detalle de roles y admin: [Roles y permisos](ROLES_AND_PERMISSIONS.md).
Runbook de sincronización: [Operaciones §3](OPERATIONS.md#3-sincronización-de-roles).

## 8. Gestión de archivos

Deben separarse:

```text
Archivos estáticos
Multimedia pública
Archivos privados
```

### Archivos estáticos

Los sirve **WhiteNoise** desde `STATIC_ROOT` tras `collectstatic`. WhiteNoise
es solo estáticos: **nunca** usa R2 ni el storage privado.

```bash
python manage.py collectstatic --noinput
```

### Multimedia pública

Solo debe contener archivos autorizados para exposición directa.

### Archivos privados

* no deben exponerse directamente mediante el proxy ni `FileField.url`;
* deben descargarse mediante endpoints autorizados (autorización **antes**
  de stream o de generar URL firmada);
* requieren autenticación y permisos cuando corresponda;
* el backend de almacenamiento se selecciona con `SIGEDON_PRIVATE_STORAGE`
  (`filesystem` | `r2`); cambiar de ubicación **no** debilita la autorización;
* deben incluirse en la estrategia de respaldo.

**Estado actual (RENDER-3 preparación):** scripts/runbooks Render listos; el
código soporta R2. Default local: `SIGEDON_PRIVATE_STORAGE=filesystem`. Destino
Render final: R2 tras probe. No hay cuenta Cloudflare, bucket ni credenciales
provisionadas; no se ha completado una prueba real de conectividad ni staging.
Provisionar R2/Render es configuración + secretos + verificaciones, no otro
rewrite. Runbooks: [CLOUDFLARE_R2.md](runbooks/CLOUDFLARE_R2.md),
[RENDER_FIRST_DEPLOY.md](runbooks/RENDER_FIRST_DEPLOY.md).

Variables (sin valores reales):

```env
SIGEDON_PRIVATE_STORAGE=filesystem   # o r2
SIGEDON_PRIVATE_FILE_DELIVERY=stream # o signed_redirect (descargas; inline siempre stream)
# Solo si SIGEDON_PRIVATE_STORAGE=r2:
# R2_ACCOUNT_ID=
# R2_ACCESS_KEY_ID=
# R2_SECRET_ACCESS_KEY=
# R2_BUCKET_NAME=
# R2_ENDPOINT_URL=          # opcional; debe coincidir con Cloudflare R2 del account
# R2_ALLOW_CUSTOM_ENDPOINT=False
# R2_REGION_NAME=auto
# R2_SIGNED_URL_EXPIRY_SECONDS=300   # 60–900
# R2_ADDRESSING_STYLE=path
# SIGEDON_READINESS_MIGRATION_CACHE_SECONDS=15
```

Contrato R2 cuando se active: bucket privado (sin URL pública / r2.dev);
`default_acl=None`; `querystring_auth=True`; `file_overwrite=False`. Las URL
firmadas son de corta vida; quien las tenga puede usarlas hasta el expiry;
nunca registrarlas en logs. Con objetos reales en R2, volver a un filesystem
vacío hace que los documentos parezcan ausentes.

El proxy no debe mapear públicamente el directorio de media privada
(`SIGEDON_MEDIA_ROOT` / `MEDIA_ROOT`) ni el bucket R2. La ruta histórica
`private_media/` describe la intención de aislamiento; el código usa storage
privado + endpoints autorizados.

### Contrato de volumen persistente (`SIGEDON_MEDIA_ROOT`)

Cuando `SIGEDON_PRIVATE_STORAGE=filesystem` y `DJANGO_DEBUG=False`, Django
exige `SIGEDON_MEDIA_ROOT` con una ruta absoluta a un volumen de filesystem
persistente. No se acepta el valor por defecto `BASE_DIR/media` ni rutas
dentro del repositorio. En modo `r2`, `MEDIA_ROOT` es un placeholder local
no servido; la persistencia vive en el bucket.

Ejemplo genérico:

```text
Host/persistent volume:
  /srv/sigedon/media

Application path (mismo árbol montado):
  /var/lib/sigedon/media

Environment:
  SIGEDON_MEDIA_ROOT=/var/lib/sigedon/media
```

Ejemplo con contenedor (si el despliegue usa contenedores):

```text
Volume mount:
  host /srv/sigedon/media  →  container /var/lib/sigedon/media

Environment inside the container:
  SIGEDON_MEDIA_ROOT=/var/lib/sigedon/media
```

No se asume Docker ni un orquestador concreto: el requisito es un mount
persistente con la misma ruta vista por Django y por los scripts de backup.

#### Propiedad y permisos

* la cuenta del proceso de aplicación debe poder leer y escribir el directorio;
* el directorio no debe ser world-readable;
* el reverse proxy no debe exponerlo;
* la cuenta de backup necesita el mínimo acceso de lectura requerido;
* los procedimientos de restore deben preservar una propiedad adecuada.

No se fija un nombre de usuario concreto: depende de la plataforma.

#### Puerta de despliegue

Antes de abrir tráfico:

```bash
python manage.py check --deploy
```

debe pasar el chequeo de persistencia de media (`sigedon.E001`…`sigedon.E006`).
La importación de settings valida solo la forma de la configuración; el
despliegue debe crear/montar el directorio antes del arranque. Django no
crea el volumen de producción.

#### Smoke test de persistencia tras reinicio (staging)

1. subir un documento desechable por un flujo autorizado de staging;
2. verificar preview/download protegido;
3. reiniciar o recrear el proceso/contenedor de la aplicación;
4. verificar que el mismo archivo protegido sigue descargable;
5. eliminar el registro de prueba por un método autorizado de staging.

No realizar pruebas destructivas en producción.
## 9. Base de datos

PostgreSQL es obligatorio en producción.

SQLite está prohibido cuando:

```env
DJANGO_DEBUG=False
```

### Reglas

* Las migraciones deben ejecutarse sobre PostgreSQL.
* Debe existir un respaldo antes de migraciones relevantes.
* Las credenciales deben tener privilegios limitados.
* La conexión debe utilizar cifrado cuando la infraestructura lo permita.
* Deben monitorearse espacio, conexiones y rendimiento.
* Las pruebas concurrentes críticas deben validarse sobre PostgreSQL.

Antes de aplicar migraciones:

```bash
python manage.py showmigrations
python manage.py migrate --plan
```

### 9.1. Separación de roles PostgreSQL

SIGEDON distingue dos roles conceptuales:

* `sigedon_owner`: propietario del esquema, ejecuta migraciones.
* `sigedon_app`: rol runtime utilizado por web, workers y comandos ordinarios.

El script `deploy/postgresql/harden_runtime_role.sql` es una plantilla comentada que:

* crea o ajusta ambos roles;
* revoca `SUPERUSER`, `CREATEDB`, `CREATEROLE`, `REPLICATION` y `BYPASSRLS` de `sigedon_app`;
* otorga `CONNECT`, `USAGE` sobre el esquema y privilegios mínimos (`SELECT`, `INSERT`, `UPDATE`, `DELETE`) sobre tablas operativas, y `USAGE`/`SELECT` sobre secuencias;
* limita `sigedon_app` sobre `operations_auditlog` a `SELECT` e `INSERT`, revocando explícitamente `UPDATE`, `DELETE`, `TRUNCATE`, `TRIGGER` y `REFERENCES`;
* define `ALTER DEFAULT PRIVILEGES` para que las tablas y secuencias futuras hereden el mismo alcance mínimo.

Debe ejecutarse conectado como `sigedon_owner` o un administrador equivalente, nunca con `sigedon_app`, y sus nombres de base/rol deben adaptarse a cada entorno.

### Dos modos de credenciales

**Migraciones.** Ejecutar temporalmente con las credenciales de `sigedon_owner`:

```bash
python manage.py migrate
```

**Runtime.** Ejecutar web, workers y comandos ordinarios con `sigedon_app`. Después de aplicar migraciones, las variables `POSTGRES_USER`/`POSTGRES_PASSWORD` del servicio deben cambiarse de vuelta a `sigedon_app` antes de servir tráfico.

### Reglas

* Nunca usar `postgres` como usuario runtime en producción.
* No almacenar credenciales de `sigedon_owner` en el servicio web ni en los workers.
* Los triggers append-only de `operations_auditlog` (migración `0018`) y
  `operations_expenserequestevent` (migración `0029`) son defensa adicional,
  no un sustituto de la separación de roles.
* Un superusuario de PostgreSQL puede administrar o desactivar triggers o
  privilegios; esa capacidad se reserva para administración técnica explícita.
* Tras aplicar grants, cambiar a credenciales runtime y verificar:

```bash
python manage.py verify_postgres_security
```

Un éxito con el rol propietario no sustituye esta pasada bajo el rol runtime.
Ver `deploy/postgresql/README.md`.

## 10. Proxy inverso

El proxy HTTPS debe encargarse de:

* terminación TLS;
* redirección HTTP a HTTPS;
* cabeceras de seguridad;
* entrega de archivos estáticos;
* límites de tamaño de solicitud;
* timeouts;
* reenvío correcto de IP y protocolo;
* bloqueo del acceso directo a archivos privados.

Debe configurarse un tamaño máximo de cuerpo compatible con:

* formularios;
* soportes financieros;
* adjuntos de avances;
* adjuntos Kobo.

El límite del proxy no debe ser menor que el límite aceptado por la aplicación cuando se esperen archivos de ese tamaño.

## 11. Checklist posterior al despliegue

Después de iniciar o actualizar la aplicación:

1. verificar que el proceso está activo;
2. comprobar el acceso mediante HTTPS;
3. verificar el inicio de sesión;
4. probar el dashboard con distintos roles;
5. abrir el portal público;
6. comprobar una descarga privada autorizada;
7. verificar que una descarga no autorizada sea rechazada;
8. revisar la consulta de auditoría;
9. verificar la creación transaccional de códigos;
10. confirmar la conexión con PostgreSQL;
11. probar el webhook Kobo, cuando esté habilitado;
12. ejecutar smoke tests;
13. revisar logs;
14. confirmar que no se expongan secretos;
15. verificar que no existan migraciones pendientes;
16. ejecutar `python manage.py verify_postgres_security` y confirmar código de salida 0.

Comandos útiles:

```bash
python manage.py check --deploy
python manage.py makemigrations --check --dry-run
python manage.py verify_postgres_security
```

## 12. Smoke tests

Las comprobaciones mínimas posteriores al despliegue deben incluir:

* respuesta correcta de la página de inicio;
* autenticación;
* autorización por rol;
* carga del dashboard;
* consulta de un proyecto;
* acceso al portal público;
* respuesta de los endpoints JSON;
* descarga protegida;
* acceso al panel administrativo;
* recepción del webhook Kobo, cuando corresponda.

Los smoke tests no deben alterar datos reales de forma irreversible.

## 13. Logs y monitoreo

Contrato de logging y troubleshooting: [OPERATIONS.md](OPERATIONS.md).
La plataforma recolecta stdout/stderr. No registrar secretos ni payloads Kobo.

## 14. Copias de seguridad

Fuente canónica: [deploy/backups/README.md](../deploy/backups/README.md).
R2: [CLOUDFLARE_R2.md](runbooks/CLOUDFLARE_R2.md).
Scheduler **no** provisionado en este checkpoint.

## 15. Restauración

Solo entornos aislados (`test_restore_` / `staging_restore_`). Procedimiento:
[deploy/backups/README.md](../deploy/backups/README.md). Tras restore, validar
con el **rol runtime**: `verify_postgres_security`,
`reconcile_operational_code_sequences`, `verify_restored_data`,
`sync_sigedon_roles`.

## 16. Actualización de una versión

Flujo recomendado (release separado del arranque web):

```text
Activar modo de mantenimiento, si corresponde
→ Crear respaldo
→ Descargar nueva versión
→ Instalar dependencias
→ Secuencia de release (§5.2: migrate, collectstatic, roles, preflight)
→ Sustituir proceso web (Gunicorn; §6)
→ Ejecutar verificaciones
→ Retirar modo de mantenimiento
```

Comandos de referencia: ver [§5.2](#52-secuencia-de-release-mutaciones-controladas)
y el comando canónico en [§6](#6-servidor-de-aplicación-gunicorn). No ejecutar
`migrate` ni `collectstatic` dentro de los workers.

## 17. Reversión

Antes de desplegar debe existir una estrategia de reversión.

Debe contemplar:

* artefacto/versión anterior de la aplicación (código + dependencias pinneadas);
* respaldo previo;
* compatibilidad de migraciones;
* restauración de archivos;
* procedimiento de reinicio del proceso Gunicorn sin reaplicar un release parcial;
* responsables de aprobar la reversión.

No todas las migraciones pueden revertirse de forma segura. La estrategia debe revisarse antes de ejecutar cambios destructivos.

## 18. Criterio de despliegue completado

El despliegue se considera completado cuando:

* la aplicación responde mediante HTTPS;
* PostgreSQL está conectado;
* las migraciones están aplicadas;
* los archivos estáticos están disponibles;
* los archivos privados permanecen protegidos;
* los roles están sincronizados;
* el panel interno funciona;
* el portal público funciona;
* la auditoría puede consultarse;
* Kobo funciona cuando está habilitado;
* los logs no muestran errores críticos;
* existe un respaldo verificable;
* las comprobaciones posteriores resultan satisfactorias.

## Integración continua y protección de ramas

El repositorio incluye GitHub Actions (`.github/workflows/ci.yml`).
**No hay evidencia verificada** de una corrida remota verde en este
checkpoint público. No hay workflow de despliegue automático.

Cuando se proteja `main`, los jobs requeridos **deben** estar verdes
(`GitHub Actions required jobs must be green`):

### Runtime CI

* Python 3.12
* PostgreSQL 16 (service container)
* Credenciales ficticias (`sigedon_ci`); sin secretos de repositorio
* `KOBO_ENABLED=False`

### Checks que deben bloquear el merge

Configurar branch protection manualmente (no vía workflow) con:

* `CI / Static and repository checks`
* `CI / PostgreSQL migration and artifact verification`
* `CI / Critical PostgreSQL tests`
* `CI / Full PostgreSQL suite`

Grafo: `static` → (`postgres-migrations` ∥ `critical-tests`) → `full-suite`.
Eso es el contrato del workflow, no un hecho de ejecución remota en este
checkpoint.

### Checks que permanecen fuera de CI (deployment-blocking)

El éxito de CI **no** autoriza producción. Además se requiere, según
[§5](#5-preparación-del-despliegue) y operación:

* backup aprobado / política de restore-drill;
* preflight de staging;
* `verify_postgres_security` con el **rol runtime** restringido;
* readiness de staging;
* configuración de plataforma (proxy, volúmenes, secretos);
* si se activa R2: secretos `R2_*`, `verify_private_storage` y
  `verify_private_storage --probe` en staging (no en `/readyz/`);
* `verify_render_configuration`;
* [STAGING_ACCEPTANCE.md](runbooks/STAGING_ACCEPTANCE.md) y
  [PRODUCTION_GO_NO_GO.md](runbooks/PRODUCTION_GO_NO_GO.md).

`collectstatic` + `verify_deployment_assets` sí se ejercitan en CI sobre un
`STATIC_ROOT` temporal (`core.ci_settings` + `SIGEDON_CI_STATIC_ROOT`).
Los tests de contrato Render viven en
`core.tests.test_render_deployment_contract` y
`core.tests.test_render_configuration` (incluidos en la suite crítica).
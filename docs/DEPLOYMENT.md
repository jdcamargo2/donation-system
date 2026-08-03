# Despliegue de SIGEDON

Este documento describe los requisitos mínimos, la configuración y las verificaciones necesarias para desplegar SIGEDON en un entorno de producción.

## 1. Requisitos

El entorno de producción requiere:

* Python 3.12;
* PostgreSQL;
* Gunicorn sirviendo `core.wsgi:application` (contrato WSGI canónico);
* un proxy inverso con HTTPS;
* almacenamiento persistente para archivos;
* variables de entorno seguras;
* acceso controlado a logs y respaldos;
* un mecanismo de supervisión y reinicio del proceso de aplicación.

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

Cuando la integración esté habilitada:

```env
KOBO_ENABLED=True
KOBO_BASE_URL=
KOBO_API_TOKEN=
KOBO_WEBHOOK_USERNAME=
KOBO_WEBHOOK_SECRET=
KOBO_HTTP_READ_TIMEOUT=15
KOBO_MAX_ATTACHMENT_BYTES=10485760
KOBO_ATTACHMENT_PROCESSING_TIMEOUT_SECONDS=900
KOBO_WEBHOOK_MAX_BYTES=1048576
```

### Reglas

* `KOBO_API_TOKEN` y `KOBO_WEBHOOK_SECRET` deben mantenerse fuera del repositorio.
* El webhook debe publicarse únicamente mediante HTTPS.
* Las credenciales deben rotarse cuando exista sospecha de exposición.
* El tamaño máximo de adjuntos debe ajustarse a la capacidad del servidor.
* `KOBO_ATTACHMENT_PROCESSING_TIMEOUT_SECONDS` controla la recuperación de reservas `PROCESSING` atascadas; no requiere una tarea periódica adicional.
* `KOBO_WEBHOOK_MAX_BYTES` limita el cuerpo entrante antes de deserializarlo.
* Los secretos no deben aparecer en logs.
* La activación de Kobo requiere activos, definiciones y bindings correctamente configurados.

## 5. Preparación del despliegue

La fuente canónica del arranque en producción es este documento. Migraciones,
`collectstatic`, sincronización de roles y verificaciones de seguridad son
pasos de **release**, no del proceso web.

### 5.1. Instalar dependencias

```bash
pip install -r requirements.txt
```

`requirements.txt` incluye Gunicorn (`gunicorn==23.0.0`) como servidor WSGI
de producción. No se declara Uvicorn/Daphne ni workers async/gevent.

### 5.2. Secuencia de release (mutaciones controladas)

Ejecutar **una vez por despliegue**, con las credenciales apropiadas
(`sigedon_owner` para migraciones; runtime `sigedon_app` donde corresponda).
Los workers de Gunicorn **nunca** ejecutan estos pasos.

```bash
python manage.py check --deploy
python manage.py makemigrations --check --dry-run
python manage.py migrate --plan
python manage.py migrate
python manage.py collectstatic --noinput
python manage.py sync_sigedon_roles
python manage.py verify_postgres_security
python manage.py reconcile_operational_code_sequences
./deploy/preflight.sh
```

Notas:

* `migrate` corre una vez bajo el propietario del esquema;
* `collectstatic` debe completarse antes de reemplazar el proceso web;
* `sync_sigedon_roles` corre una vez por release;
* `verify_postgres_security` **debe** ejecutarse con el rol runtime final
  (`sigedon_app`); un éxito bajo `sigedon_owner`/superusuario no valida el
  contrato. Exige PostgreSQL (otro motor → código distinto de 0). Verifica
  append-only de `AuditLog` y `ExpenseRequestEvent` (catálogo + sondas con
  rollback), constraints financieros críticos y privilegios runtime sobre
  `operations_auditlog`. No repara nada; el fallo bloquea tráfico.
  `preflight.sh` no lo invoca (sigue siendo solo lectura de release);
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

* Montar el volumen persistente y exportar `SIGEDON_MEDIA_ROOT` **antes** del
  preflight (`check --deploy` valida existencia/permisos).
* `collectstatic` debe haber poblado `STATIC_ROOT` (por defecto
  `<BASE_DIR>/staticfiles`) con sentinelas locales (`web/css/sigedon.css`,
  logos ILDE). El comando `verify_deployment_assets` lo comprueba sin mutar
  archivos. Assets CDN externos (p. ej. Bootstrap) no forman parte de esta
  puerta.

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

Configuración: `deploy/gunicorn.conf.py` (overrides por entorno; ver
`.env.example`). Valores conservadores iniciales:

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
* Readiness **solo** comprueba conectividad de la BD por defecto y el plan de
  migraciones (vía `MigrationExecutor`; no ejecuta `migrate`).
* Ninguna sonda depende de Kobo, caché, object storage remoto, datos de negocio
  ni autenticación.
* Respuestas sin versión, entorno, host, nombre de BD, migraciones, credenciales
  ni mensajes de excepción. Cabeceras: `Cache-Control: no-store`, `Pragma:
  no-cache`, `X-Content-Type-Options: nosniff`, más `X-Request-ID` del middleware.
* La validación de media persistente permanece en `check --deploy` / preflight;
  `/readyz/` **no** escribe en el volumen de media en cada sonda.

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

### Crear un superusuario administrativo

```bash
python manage.py createsuperuser
```

Después deben crearse las cuentas operativas y asignarse los grupos correspondientes desde el panel de administración.

### Reglas

* El superusuario debe reservarse para administración técnica.
* No debe utilizarse como cuenta operativa diaria.
* Un usuario ordinario puede tener como máximo un rol funcional canónico.
* Cada usuario debe contar únicamente con los permisos necesarios.
* Los permisos técnicos generales `kobo.*` (distintos de los territoriales
  automáticos) deben asignarse por separado.
* Las cuentas compartidas deben evitarse.
* Las credenciales iniciales deben cambiarse de forma segura.

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

Pueden ser servidos por el proxy o por un servicio de almacenamiento adecuado después de ejecutar:

```bash
python manage.py collectstatic --noinput
```

### Multimedia pública

Solo debe contener archivos autorizados para exposición directa.

### Archivos privados

* no deben exponerse directamente mediante el proxy;
* deben descargarse mediante endpoints autorizados;
* requieren autenticación y permisos cuando corresponda;
* deben almacenarse de forma persistente fuera del árbol efímero de la aplicación;
* deben incluirse en la estrategia de respaldo.

El proxy no debe mapear públicamente el directorio de media privada
(`SIGEDON_MEDIA_ROOT` / `MEDIA_ROOT`). La ruta histórica `private_media/`
describe la intención de aislamiento; el código actual usa `MEDIA_ROOT` no
público + endpoints autorizados.

### Contrato de volumen persistente (`SIGEDON_MEDIA_ROOT`)

Cuando `DJANGO_DEBUG=False`, Django exige la variable de entorno
`SIGEDON_MEDIA_ROOT` con una ruta absoluta a un volumen de filesystem
persistente. No se acepta el valor por defecto `BASE_DIR/media` ni rutas
dentro del repositorio.

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

Deben revisarse:

* errores HTTP `500`;
* accesos `403` inesperados;
* fallos de base de datos;
* errores de migración;
* fallos del webhook;
* submissions detenidas;
* errores de descarga de archivos;
* consumo de disco;
* conexiones PostgreSQL;
* reinicios del proceso.

Los logs no deben contener:

* contraseñas;
* tokens;
* secretos;
* payloads sensibles completos;
* datos personales innecesarios.

Debe existir una política de rotación y retención.

## 14. Copias de seguridad

Scripts manuales: `deploy/backups/` (`backup_sigedon.sh`, `verify_backup.sh`, `restore_sigedon.sh`). Detalle operativo en `deploy/backups/README.md` y `docs/OPERATIONS.md`.

Esta fase entrega el procedimiento verificable; **no** fija todavía frecuencia ni retención automatizadas (sin cron/systemd).

Debe definirse a nivel de infraestructura una política que incluya:

* frecuencia (pendiente de automatizar);
* cifrado (requisito de infraestructura; no implementado en scripts);
* retención (pendiente);
* almacenamiento externo / off-site (requisito de infraestructura);
* responsables;
* restauración probada (trimestral como mínimo);
* protección de acceso;
* eliminación segura.

Los respaldos de aplicación cubren:

* PostgreSQL (`pg_dump --format=custom`);
* `MEDIA_ROOT` (`media.tar.gz`);
* manifiesto con checksums (`manifest.json`).

La configuración crítica y los secretos deben respaldarse mediante un sistema seguro aparte. Preferir `~/.pgpass` para autenticación de cliente.

Consistencia: **ventana de mantenimiento** con `SIGEDON_MAINTENANCE_CONFIRMED=YES`.

Un respaldo no se considera válido hasta pasar `verify_backup.sh` y una restauración aislada de prueba.

**RPO/RTO no están definidos** hasta medir una restauración real.

## 15. Restauración

Restaurar solo a entornos aislados (`SIGEDON_RESTORE_DB` con prefijo `test_restore_` o `staging_restore_`), nunca sobre la base activa (`POSTGRES_DB`) ni sobre un `MEDIA_ROOT` no vacío. No modificar `.env`.

Después de restaurar un entorno aislado y aplicar cualquier migración
requerida, **cambiar a las credenciales del rol runtime** y validar:

```bash
export POSTGRES_DB=<SIGEDON_RESTORE_DB>
export SIGEDON_MEDIA_ROOT=<SIGEDON_RESTORE_MEDIA_ROOT>
python manage.py migrate --check
python manage.py check --deploy
python manage.py verify_postgres_security
python manage.py reconcile_operational_code_sequences
python manage.py verify_restored_data
python manage.py sync_sigedon_roles
```

Fallar la aceptación del restore si `verify_postgres_security` no sale 0.
Restaurar filas con éxito es insuficiente si faltan triggers append-only,
constraints críticos o grants del rol runtime.

Provisionar el volumen de media, restaurar el archivo `media.tar.gz`, restaurar
PostgreSQL, apuntar `SIGEDON_MEDIA_ROOT` al árbol restaurado y solo entonces
abrir tráfico. `verify_restored_data` valida correspondencia FileField↔blob por
existencia en storage (no checksums de contenido); **no** sustituye
`verify_postgres_security`.
`reconcile_operational_code_sequences` funciona en modo detect-only y es de
solo lectura: no crea ni ajusta secuencias. El comando falla ante una secuencia
ausente (`MISSING_SEQUENCE`), atrasada (`LAGGING_SEQUENCE`) o inválida
(`INVALID_SEQUENCE`). No existe reparación automática; cualquier corrección
debe revisarse y ejecutarse manualmente.

También debe verificarse:

* consistencia de archivos;
* secuencias operativas;
* permisos;
* usuarios;
* integridad de auditoría;
* configuración Kobo;
* acceso al portal público;
* descargas privadas.

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

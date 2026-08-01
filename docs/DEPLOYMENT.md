# Despliegue de SIGEDON

Este documento describe los requisitos mínimos, la configuración y las verificaciones necesarias para desplegar SIGEDON en un entorno de producción.

## 1. Requisitos

El entorno de producción requiere:

* Python 3.12;
* PostgreSQL;
* un servidor WSGI o ASGI;
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

### 5.1. Instalar dependencias

```bash
pip install -r requirements.txt
```

### 5.2. Aplicar migraciones

```bash
python manage.py migrate
```

### 5.3. Recopilar archivos estáticos

```bash
python manage.py collectstatic --noinput
```

### 5.4. Sincronizar roles

Sincroniza los cuatro roles funcionales canónicos. No modifica membresías de
usuarios. Runbook completo: [Operaciones §3](OPERATIONS.md#3-sincronización-de-roles).

```bash
python manage.py sync_sigedon_roles
```

### 5.5. Verificar la configuración

```bash
python manage.py check
```

Para una verificación específica de producción:

```bash
python manage.py check --deploy
```

### Orden recomendado

```text
Instalar dependencias
→ Validar variables
→ Crear respaldo
→ Aplicar migraciones
→ Recopilar estáticos
→ Sincronizar roles
→ Verificar configuración
→ Reiniciar aplicación
→ Ejecutar comprobaciones funcionales
```

## 6. Servidor de aplicación

SIGEDON debe ejecutarse mediante un servidor WSGI o ASGI apropiado para producción.

Ejemplos de arquitectura:

```text
Cliente
→ Proxy HTTPS
→ Servidor WSGI o ASGI
→ Django
→ PostgreSQL
```

El servidor de desarrollo:

```bash
python manage.py runserver
```

no debe utilizarse en producción.

El proceso debe gestionarse mediante una herramienta de supervisión capaz de:

* iniciar la aplicación;
* reiniciarla ante fallos;
* conservar logs;
* aplicar límites de recursos;
* iniciar el servicio después de reiniciar el servidor.

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
* deben almacenarse de forma persistente;
* deben incluirse en la estrategia de respaldo.

El proxy no debe mapear públicamente el directorio `private_media/`.

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
* El trigger append-only instalado en `operations_auditlog` (migración `0018_auditlog_append_only_trigger`) es una defensa adicional, no un sustituto de la separación de roles.
* Un superusuario de PostgreSQL puede administrar o desactivar el trigger o los privilegios; esa capacidad se reserva para administración técnica explícita.
* Verificar la configuración runtime con:

```bash
python manage.py verify_postgres_security
```

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

Después de restaurar un entorno aislado:

```bash
python manage.py migrate --check
python manage.py check
python manage.py verify_postgres_security
python manage.py reconcile_operational_code_sequences
python manage.py verify_restored_data
python manage.py sync_sigedon_roles
```

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

Flujo recomendado:

```text
Activar modo de mantenimiento, si corresponde
→ Crear respaldo
→ Descargar nueva versión
→ Instalar dependencias
→ Aplicar migraciones
→ Recopilar estáticos
→ Sincronizar roles
→ Reiniciar procesos
→ Ejecutar verificaciones
→ Retirar modo de mantenimiento
```

Comandos de referencia:

```bash
pip install -r requirements.txt
python manage.py migrate
python manage.py collectstatic --noinput
python manage.py sync_sigedon_roles
python manage.py check --deploy
```

## 17. Reversión

Antes de desplegar debe existir una estrategia de reversión.

Debe contemplar:

* versión anterior del código;
* respaldo previo;
* compatibilidad de migraciones;
* restauración de archivos;
* procedimiento de reinicio;
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

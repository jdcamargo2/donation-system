# Operación y mantenimiento de SIGEDON

> Kobo: las Fichas 1, 10 y 11 se administran desde el panel operativo de KoboToolbox.
> No configure bindings asset-proyecto: el UID identifica la ficha y el proyecto
> se resuelve por zona pastoral e identidad territorial.

Este documento describe las tareas habituales de preparación, operación, verificación y mantenimiento de SIGEDON.

## 1. Preparación local

### 1.1. Crear y activar el entorno virtual

```bash
python3.12 -m venv venv
source venv/bin/activate
```

### 1.2. Instalar dependencias

```bash
pip install -r requirements.txt
```

### 1.3. Crear el archivo de entorno

```bash
cp .env.example .env
```

Antes de continuar, debe completarse la configuración mínima requerida en `.env`.

### 1.4. Aplicar migraciones

```bash
python manage.py migrate
```

Las migraciones `0026`–`0030` de `operations` introducen `ExpenseRequest`,
constraints, la secuencia operativa `expense_request`/`SGS`, el trigger
append-only de `ExpenseRequestEvent` y el `PROTECT` del FK `expense` en eventos
(compatible con el trigger ante borrados de gasto). Deben aplicarse solo sobre
bases autorizadas; no se documenta aquí su aplicación sobre `db_sigedon` activa.

Tras migrar un entorno desechable o de staging, sincronizar roles y verificar
secuencias:

```bash
python manage.py sync_sigedon_roles
python manage.py reconcile_operational_code_sequences
```

`reconcile_operational_code_sequences` es detect-only e incluye el namespace
`expense_request` (`SGS`). `sync_sigedon_roles` aplica la matriz canónica de
permisos de solicitud de gasto (incluida fulfillment/anulación administrativa)
sin mutar eventos. La UI de cumplimiento (ER5) usa `fulfill_expenserequest` y no
requiere sync adicional de roles. La UI de adjuntos de solicitud (ER6) reutiliza
`add_expenserequestattachment`, `delete_expenserequestattachment` y
`view_expenserequestattachment` ya presentes en la matriz canónica; no introduce
permisos nuevos ni sync de roles. ER7 solo ajusta atajos de dashboard y
documentación; no cambia permisos ni esquema. No ejecutar sync de roles ni
migraciones contra `db_sigedon` activo durante checkpoints de desarrollo de
solicitudes.

### 1.5. Crear un superusuario

```bash
python manage.py createsuperuser
```

### 1.6. Sincronizar roles y permisos

Sincroniza los cuatro roles canónicos desde código. No modifica membresías.
Detalle: [§3 Sincronización de roles](#3-sincronización-de-roles).

```bash
python manage.py sync_sigedon_roles
```

### 1.7. Ejecutar el servidor local

> [!WARNING]
> Django `runserver` no es un servidor de aplicación de producción. El contrato
> de producción es Gunicorn (`docs/DEPLOYMENT.md`).

```bash
python manage.py runserver
```

## 1.8. Logging operativo y correlación de peticiones

SIGEDON emite logs de runtime a **stdout/stderr**. La recolección, retención,
rotación, control de acceso y alertas son responsabilidad de la plataforma.

* Cabecera de correlación: `X-Request-ID`.
* IDs inbound inválidos, malformados o demasiado largos se reemplazan; el valor
  inválido no se registra ni se refleja.
* Ante un error 500, el usuario/operador puede aportar el `X-Request-ID` de la
  respuesta para diagnóstico.
* No se registran cuerpos, query strings completas, cookies, Authorization,
  secretos, payloads Kobo crudos ni datos personales/financieros privados.
* Los logs Kobo usan identificadores internos seguros (`submission_id`, stage,
  status, clase de excepción).
* Retención: política de plataforma; no retener indefinidamente; no pegar logs
  crudos en trackers públicos.

### Separación AuditLog vs logs de runtime

| Fuente | Uso |
| --- | --- |
| Runtime logs (stdout/stderr) | ciclo de vida del proceso, fallos de petición, fallos de integración, diagnóstico operativo, despliegue |
| `AuditLog` / `ExpenseRequestEvent` / `KoboProcessingEvent` | eventos de negocio durables, historial actor/acción, transiciones de dominio, historial de procesamiento |

No duplicar cada fila de `AuditLog` en logs de runtime. No usar logs de runtime
como evidencia de una mutación financiera exitosa. Este checkpoint no añade
`request_id` al esquema de auditoría en base de datos.

## 2. Datos de demostración

`seed_sigedon_demo` genera datos locales para explorar la interfaz, realizar
revisiones manuales y preparar capturas de pantalla. No representa una carga
productiva ni una verificación integral de las reglas operativas.

### Política de producción (absoluta)

* **Solo desarrollo:** el comando exige `DEBUG=True` (`DJANGO_DEBUG=True`).
* Cuando `DEBUG=False`, el comando **rechaza la ejecución antes de cualquier
  escritura** y termina con código distinto de cero (`CommandError`:
  `seed_sigedon_demo is disabled when DEBUG=False.`).
* No existe bypass por `--force`, `--allow-production` ni variables de entorno.
* **No forma parte** de release, preflight, arranque web ni comprobación de
  disponibilidad productiva.
* No debe usarse para probar que producción “funciona”.

### Precondición

* Ejecutar únicamente en un entorno local o efímero con `DEBUG=True`.
* Utilizar una base de datos no productiva donde se acepten datos de demostración.

Comando:

```bash
python manage.py seed_sigedon_demo
```

### Opciones

```text
--password
--skip-users
```

`SIGEDON_DEMO_PASSWORD` y `--password` son **solo desarrollo**. El comando nunca
imprime la contraseña; como máximo indica `Demo credentials configured.`

### Reglas

* **No debe ejecutarse en producción** (y el runtime lo impide cuando
  `DEBUG=False`).
* Usa ORM directo intencionalmente y no pasa por todos los services de dominio.
* No genera trazabilidad completa en `AuditLog`.
* Puede crear gastos sin `SupportingDocument`, aunque el flujo UI operativo lo exige.
* Usa códigos explícitos reservados para demostración.
* Su idempotencia es parcial: actualiza entidades clave, pero no garantiza que
  una base previamente modificada vuelva a un estado canónico.
* Las credenciales demo no deben reutilizarse en entornos reales.

### Postcondición

* Quedan disponibles las entidades mínimas para navegar y revisar la interfaz.
* Los datos resultantes no deben considerarse evidencia de cumplimiento de
  todas las reglas, auditorías o invariantes del flujo operativo.

## 3. Sincronización de roles

Comando autoritativo para las matrices de los cuatro roles funcionales canónicos:

```bash
python manage.py sync_sigedon_roles
```

La matriz completa de permisos por rol está en
[Roles y permisos](ROLES_AND_PERMISSIONS.md).

### Cuándo ejecutarlo

* despliegue inicial o actualización del sistema;
* restauración de base de datos;
* cambios en la matriz de roles definida en código;
* incorporación o eliminación de modelos/permisos protegidos por los roles.

### Contrato exacto (cuatro grupos)

El comando:

* asegura que existen exactamente estos cuatro grupos canónicos:
  `Administrador SIGEDON`, `Operador de campo`, `Auditor externo`,
  `Comité de proyectos`;
* **reemplaza** la matriz de permisos de cada grupo canónico desde el código;
* **no toca** membresías de usuarios (`auth_user_groups`);
* **preserva** grupos técnicos / no canónicos;
* **no elimina** grupos técnicos;
* es **idempotente**.

### Salida esperada

```text
Roles operativos de SIGEDON sincronizados.
- Administrador SIGEDON: <n> permisos
- Operador de campo: <n> permisos
- Auditor externo: <n> permisos
- Comité de proyectos: <n> permisos
```

### Verificación posterior

```bash
# Contar grupos canónicos (esperado: 4)
python manage.py shell -c "from django.contrib.auth.models import Group; from apps.operations.role_services import operation_role_names; print(Group.objects.filter(name__in=operation_role_names()).count())"

# Listar permisos de un rol
python manage.py shell -c "from django.contrib.auth.models import Group; g=Group.objects.get(name='Comité de proyectos'); print(g.permissions.count()); print(*sorted(f'{p.content_type.app_label}.{p.codename}' for p in g.permissions.all()), sep='\n')"
```

### Advertencias

* No editar manualmente permisos de grupos canónicos en Django admin: están
  protegidos como solo lectura y, en cualquier caso, `sync_sigedon_roles`
  sobrescribe esas matrices.
* El comando no asigna ni quita usuarios de grupos.
* Operador de campo no forma parte de la audiencia de administración
  territorial Kobo. Tras un despliegue que corrija la matriz canónica,
  `sync_sigedon_roles` retira `kobo.view_territorial_administration` del
  grupo Operador; las membresías de usuarios permanecen intactas.

## 4. Operación inicial de KoboToolbox

### 4.1. Registrar definiciones soportadas

```bash
python manage.py register_kobo_forms
```

### 4.2. Verificar activos disponibles

```bash
python manage.py discover_kobo_assets --dry-run
```

### 4.3. Registrar activos descubiertos

```bash
python manage.py discover_kobo_assets
```

### 4.4. Configuración posterior

Después del descubrimiento se debe:

1. seleccionar el activo correcto;
2. asociarlo con una definición soportada;
3. configurar la zona pastoral hacia un proyecto;
4. activar el activo;
5. verificar las credenciales del webhook;
6. comprobar la recepción de submissions;
7. comprobar que el webhook materializa automáticamente una submission de prueba.

El descubrimiento no activa automáticamente un activo ni configura su routing.

## 5. Procesamiento y reconciliación de Kobo

### Procesar submissions pendientes

```bash
python manage.py process_kobo_submissions
```

Opciones disponibles:

```text
--limit
--submission-id
--download-attachments
```

### Reconciliar submissions remotas

```bash
python manage.py reconcile_kobo_submissions
```

Opciones disponibles:

```text
--asset-uid
--limit
--dry-run
```

La reconciliación recupera submissions ausentes en staging, pero no sustituye la
validación, normalización, asignación territorial ni importación automática.

### Códigos de salida (contrato operativo)

Ambos comandos siguen la misma taxonomía:

| Resultado | Exit |
| --- | --- |
| Lote seleccionado completado sin errores (incluye “nada elegible”) | `0` |
| Uno o más registros fallaron, o fallo fatal de configuración/init | distinto de `0` |

Matices importantes para cron, systemd y jobs de release:

* **Exit distinto de cero no implica cero trabajo comprometido.** Registros
  previos del mismo lote pueden haberse persistido con éxito.
* Tras un fallo parcial, inspeccione el hub Kobo, `KoboProcessingEvent` y los
  logs de runtime correlacionados; no asuma rollback total.
* Es seguro reejecutar según las reglas de idempotencia existentes.
* No enmascare el fallo con `|| true`.

Ejemplo de orquestación:

```bash
if ! python manage.py process_kobo_submissions; then
  echo "Kobo processing completed with failures" >&2
  exit 1
fi
```

`--dry-run` en reconciliación no escribe datos: sin errores → `0`; con errores
operativos (p. ej. fallo remoto) → distinto de `0`.

Estos comandos CLI **no adquieren** el lease de sincronización incremental del
hub; ese locking pertenece a la sincronización remota del hub y permanece
inalterado.

### Recuperación e incidencias

Use **Sincronizar KoboToolbox** sólo tras una caída, webhook perdido o para
buscar cambios remotos. La operación es idempotente: una submission ya
importada no se materializa de nuevo. Configure la zona desde el panel para
reintentar incidencias de esa zona; crear o reconciliar un núcleo reintenta las
Fichas 10 y 11 relacionadas. Los errores permanentes conservan su causa y no
entran en un bucle automático.

La pantalla de incidencias muestra causa, explicación, posible acción y enlace
funcional. Consulte el historial para auditoría. Para diagnosticar sin exponer
secretos, confirme `KOBO_ENABLED`, que el activo y la zona estén activos, el
estado agregado de sincronización y los códigos de incidencia; nunca copie
tokens, cabeceras de webhook ni payloads privados en tickets o logs.

## 6. Verificación diaria o previa a una entrega

Ejecutar:

```bash
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test
git diff --check
```

### Resultado esperado

* `check` sin incidencias;
* ninguna migración pendiente;
* suite automatizada en verde;
* ningún error de espacios, conflictos o formato detectado por Git.

Cuando se utilicen pruebas dependientes de PostgreSQL, deben ejecutarse contra ese motor y no asumirse como validadas únicamente con SQLite.

La integración continua del repositorio (GitHub Actions) ejecuta gates estáticos, migración desde cero, suite crítica y suite completa contra PostgreSQL 16. No sustituye backups, restore-drills ni `verify_postgres_security` con rol runtime en staging. Ver [TESTING.md §18](TESTING.md#18-integración-continua-github-actions).

## 7. Gestión de archivos

Directorios locales habituales:

```text
staticfiles/
media/
```

Estos directorios no deben versionarse.

### Reglas de operación

* `staticfiles/` contiene archivos estáticos recopilados (`collectstatic`).
  En producción los sirve **WhiteNoise** desde `STATIC_ROOT` (manifest
  hashed + gzip). No se requiere disco persistente para estáticos en el
  runtime Python nativo de Render.
* `MEDIA_ROOT` (`media/` por defecto en desarrollo; en producción
  `SIGEDON_MEDIA_ROOT` absoluto a volumen persistente) almacena archivos
  operativos privados (documentos, evidencias, soportes, adjuntos Kobo).
* WhiteNoise **no** sirve media privada. Los archivos privados **nunca** se
  montan con `static(MEDIA_URL, document_root=MEDIA_ROOT)`, ni siquiera con
  `DEBUG=True`.
* El acceso en desarrollo y producción ocurre solo mediante endpoints protegidos
  de preview/download autenticados (`apps/operations/file_access.py`).
* Producción exige `SIGEDON_MEDIA_ROOT` fuera del repositorio; el despliegue
  monta el volumen antes del arranque y `check --deploy` verifica
  existencia/permisos. Django no crea el directorio de producción.
  Cloudflare R2 queda diferido a RENDER-2.
* Tras `collectstatic` en el build/release, `./deploy/preflight.sh` (vía
  `verify_deployment_assets`) exige sentinelas lógicos bajo el storage de
  staticfiles, incluidos Bootstrap / Bootstrap Icons / SweetAlert2
  vendorizados. El proceso web (Gunicorn) no ejecuta `collectstatic`.
* Un CDN/proxy de borde no debe cachear HTML autenticado ni media privada;
  solo los assets hashed públicos son candidatos a caché larga.
* Los respaldos de archivos deben tratarse como información sensible y usar el
  mismo `SIGEDON_MEDIA_ROOT` que Django.
* Debe verificarse el espacio disponible y la política de retención.
* La documentación histórica que menciona `private_media/` como ruta montada
  separada describe la intención de aislamiento; el código actual usa `MEDIA_ROOT`
  no público + endpoints autorizados.

## 8. Base de datos

Producción requiere PostgreSQL.

### Antes de ejecutar migraciones

1. crear una copia de seguridad;
2. verificar credenciales;
3. validar la conexión;
4. revisar las migraciones pendientes;
5. comprobar dependencias entre migraciones;
6. ejecutar el cambio en una ventana controlada;
7. verificar el sistema después de migrar.

Comandos recomendados:

```bash
python manage.py showmigrations
python manage.py migrate --plan
python manage.py migrate
python manage.py check
```

### Reglas

* No deben editarse migraciones ya aplicadas en producción sin una justificación técnica controlada.
* No deben crearse códigos operativos manualmente para corregir secuencias.
* Las operaciones destructivas requieren respaldo y validación previa.
* Debe comprobarse que `OperationalCodeSequence` permanezca consistente.

## 9. Copias de seguridad y restauración

Antes de despliegues, migraciones relevantes o cambios de infraestructura, debe existir una copia de seguridad verificable.

Los scripts viven en `deploy/backups/` (ver `deploy/backups/README.md`). El
formato del artefacto permanece estable. La automatización operativa
(`run_scheduled_backup.sh`, retención, lock, markers, drill) está en el
repositorio; el **scheduling y la entrega de alertas** son de la plataforma
(ejemplos en `deploy/backups/examples/`).

### Ventana de mantenimiento

La consistencia del backup exige una **ventana de mantenimiento obligatoria**:

* detener web, workers, comandos de procesamiento Kobo y uploads;
* exportar `SIGEDON_MAINTENANCE_CONFIRMED=YES`;
* exportar `SIGEDON_BACKUP_ROOT` (se crea automáticamente con permisos `0700` si no existe) y `SIGEDON_MEDIA_ROOT` (debe existir; no se crea);
* ejecutar `deploy/backups/backup_sigedon.sh` o el pipeline `run_scheduled_backup.sh`.

El script no puede comprobar por sí solo que esos procesos estén detenidos.

### Contenido del respaldo

Cada backup publicado es un directorio:

```text
<backup_id>/
  database.dump    # pg_dump --format=custom
  media.tar.gz
  manifest.json
```

Incluye PostgreSQL + `MEDIA_ROOT` como un solo set operativo. Temps
`.sigedon-backup.*` son incompletos; solo sets que pasan `verify_backup.sh`
cuentan como verificados. La configuración crítica y los secretos deben
respaldarse fuera de estos scripts, en un sistema seguro.

### Pipeline programado, lock, retención y markers

* Lock exclusivo: un global `<SIGEDON_BACKUP_ROOT>/.sigedon-ops.lock` en FD 9
  (código 8 si ocupado); el runner lo mantiene en el pipeline y los hijos
  reafirman el FD heredado (sin reapertura).
* Pipeline: `run_scheduled_backup.sh` → backup → verify → retención opcional →
  `.sigedon-backup-status.json`.
* Retención: `SIGEDON_BACKUP_KEEP_COUNT` / `SIGEDON_BACKUP_KEEP_DAYS`; solo borra
  sets verificados bajo el backup root configurado.
* Alertas: exit codes fiables + hook local opcional `SIGEDON_BACKUP_ALERT_HOOK`
  (la plataforma implementa la entrega).
* Un fallo nunca se publica como `status=success`.

### Verificación y restore aislado

```bash
./deploy/backups/verify_backup.sh /ruta/al/<backup_id>
```

La restauración solo está permitida hacia bases con prefijo seguro (`test_restore_` / `staging_restore_`), distintas de `POSTGRES_DB`, con `SIGEDON_RESTORE_CONFIRM=YES` y un directorio de media nuevo/vacío. Nunca sobreescribir el `MEDIA_ROOT` activo ni modificar `.env`.

Drill periódico: `run_restore_drill.sh` con `SIGEDON_RESTORE_DRILL_ENABLED=YES`
contra destinos desechables. **Prohibido** automatizar restore de producción
con un timer.

### Post-restore

Con el entorno apuntando a la base y media restauradas, y con las
**credenciales del rol runtime** (no el propietario de migraciones):

```bash
python manage.py migrate --check
python manage.py check
python manage.py verify_postgres_security
python manage.py reconcile_operational_code_sequences
python manage.py verify_restored_data
python manage.py sync_sigedon_roles
```

`verify_postgres_security` exige PostgreSQL, comprueba append-only de
`AuditLog` y `ExpenseRequestEvent`, constraints críticos y postura del rol
runtime; las sondas hacen rollback y no reparan. Fallar el restore si este
comando no sale 0: restaurar filas no basta si faltan triggers o grants.
`verify_restored_data` es complementario (conteos, archivos, secuencias
observables), no un reemplazo.

`reconcile_operational_code_sequences` funciona en modo detect-only y es de
solo lectura: no crea ni ajusta secuencias. El comando falla ante una secuencia
ausente (`MISSING_SEQUENCE`), atrasada (`LAGGING_SEQUENCE`) o inválida
(`INVALID_SEQUENCE`). No existe reparación automática; cualquier corrección
debe revisarse y ejecutarse manualmente. Una secuencia adelantada es válida.

### Reglas

* Los respaldos no deben versionarse.
* Deben almacenarse fuera del repositorio.
* Deben protegerse mediante controles de acceso.
* Preferir `~/.pgpass` frente a `PGPASSWORD` en los scripts.
* Las restauraciones deben probarse periódicamente (drill aislado; al menos trimestral).
* Un respaldo no se considera válido hasta comprobar que puede restaurarse.
* Cifrado y copia off-site son requisitos de infraestructura (aún no implementados en scripts).
* RPO/RTO: propuestas no aprobadas / placeholders de despliegue
  (p. ej. RPO ≤ 24 h, RTO ≤ 4 h). **No son un SLA.** El RTO debe validarse con
  un restore drill representativo medido. Detalle en `deploy/backups/README.md`.

## 10. Auditoría

`AuditLog` no debe limpiarse, editarse ni eliminarse mediante scripts ordinarios.

Cualquier política futura de:

* retención;
* exportación;
* archivado;
* anonimización;
* traslado a almacenamiento externo;

requiere una decisión institucional explícita y una implementación controlada.

Los eventos técnicos de Kobo no sustituyen el registro de auditoría funcional.

## 11. Monitoreo operativo

Durante la operación deben revisarse:

* errores de aplicación;
* respuestas `500`;
* accesos `403` inesperados;
* fallos del webhook;
* submissions detenidas;
* errores de procesamiento Kobo;
* espacio disponible (incluye `SIGEDON_BACKUP_ROOT`);
* conexiones a PostgreSQL;
* vencimiento o rotación de credenciales;
* crecimiento de archivos y logs;
* fallos sostenidos de readiness (`/readyz/` → `503`) frente a ventanas
  esperadas de migración en el release;
* jobs de backup: `.sigedon-backup-status.json` con `status!=success`, exit
  code distinto de cero, o `finished_at_utc` obsoleto respecto a la frecuencia
  programada;
* drills de restore: `.sigedon-restore-drill-status.json` y frescura del último
  drill exitoso (no confundir con `/healthz/`/`/readyz/`).

### 11.1. Sondas HTTP vs preflight

* `./deploy/preflight.sh` valida el **release** (checks estáticos, migraciones
  pendientes vía CLI, assets). No arranca el servidor web.
* `GET /healthz/` — liveness del proceso (sin BD ni dependencias externas).
* `GET /readyz/` — readiness runtime: BD `default` alcanzable y migraciones
  aplicadas. No consulta Kobo, caché ni media.

Detalle de contrato, cabeceras y ejemplos de plataforma:
[DEPLOYMENT.md §6.3](DEPLOYMENT.md#63-sondas-http-healthz-y-readyz).

Los logs no deben contener secretos, tokens ni payloads sensibles completos.
Los fallos de readiness se registran de forma segura en `sigedon.health` (sin
detalles internos en la respuesta HTTP).

## 12. Errores comunes

### 12.1. Falta una secuencia operativa

#### Síntomas

* no puede generarse un código;
* aparece un error indicando que la secuencia no está inicializada;
* falla la creación de proyectos, donaciones, asignaciones o gastos.

#### Verificar

* migraciones aplicadas;
* existencia de `OperationalCodeSequence`;
* namespace correcto;
* consistencia de la secuencia.

#### Acción

* ejecutar las migraciones pendientes;
* revisar la inicialización de secuencias;
* no crear códigos manualmente;
* no corregir el problema contando filas.

---

### 12.2. Kobo no procesa submissions

#### Verificar

* `KOBO_ENABLED`;
* `KOBO_BASE_URL`;
* `KOBO_API_TOKEN`;
* credenciales del webhook;
* activo configurado;
* activo habilitado;
* definición asociada;
* zona pastoral configurada;
* estado de la submission;
* eventos de procesamiento.

Comandos útiles:

```bash
python manage.py process_kobo_submissions
python manage.py reconcile_kobo_submissions --dry-run
```

No deben registrarse tokens ni secretos al diagnosticar.

### 12.2.1. PostgreSQL no disponible para pruebas concurrentes Kobo

Primero confirme `DATABASE_ENGINE=postgresql` y las variables de conexión sin
imprimir sus valores. Si Django informa `connection is bad`, clasifíquelo como
conexión cerrada o dañada; un error de host/puerto indica servidor inaccesible y
un rechazo de autenticación indica credenciales inválidas. No use SQLite como
sustituto de los tests de locking. Una vez restaurado el servidor, ejecute las
suites focales concurrentes Kobo con `venv/bin/python manage.py test`.

---

### 12.3. Respuesta `403 Forbidden`

#### Verificar

* autenticación;
* grupo asignado;
* permisos individuales;
* matriz vigente;
* sincronización de roles;
* permiso requerido por la vista;
* pertenencia del recurso solicitado.

Acción recomendada:

```bash
python manage.py sync_sigedon_roles
```

La ausencia de un botón en la interfaz no demuestra que el permiso esté correctamente configurado en el servidor.

---

### 12.4. Métricas vacías

#### Verificar

* permisos del usuario;
* existencia de registros;
* moneda operativa USD;
* estados anulados;
* filtros de publicación: un proyecto público de métricas debe estar
  `ACTIVE` e `is_public=True`;
* fechas y estados operativos;
* selectores utilizados por el dashboard o portal.

Los datos anulados quedan excluidos deliberadamente; toda operación monetaria válida está expresada en USD.

---

### 12.5. Archivos que no descargan

#### Verificar

* existencia física del archivo;
* ruta configurada;
* permisos del usuario;
* relación con la entidad;
* clasificación pública o privada;
* configuración de almacenamiento;
* tamaño y metadatos del adjunto Kobo.

Los archivos privados no deben corregirse exponiendo directamente `FileField.url`.

---

### 12.6. Migraciones pendientes

Ejecutar:

```bash
python manage.py makemigrations --check --dry-run
python manage.py showmigrations
```

Si aparecen cambios inesperados:

* revisar modelos modificados;
* comprobar migraciones no versionadas;
* no generar una migración automática sin entender su causa;
* verificar que la rama de trabajo esté actualizada.

## 13. Cierre de una versión

Antes de etiquetar o desplegar una versión, ejecutar:

```bash
git status --short
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test
git diff --check
```

También debe verificarse:

* documentación actualizada;
* variables nuevas reflejadas en `.env.example`;
* migraciones versionadas;
* dependencias registradas;
* roles sincronizados;
* ausencia de secretos;
* estado de la integración Kobo;
* respaldo disponible cuando corresponda.

### Resultado esperado

* repositorio limpio;
* suite automatizada en verde;
* ninguna migración pendiente;
* documentación coherente con el código;
* configuración de despliegue validada.

## 14. Lista mínima posterior al despliegue

Después de desplegar:

1. ejecutar migraciones;
2. sincronizar roles;
3. ejecutar `python manage.py check` y `./deploy/preflight.sh`;
4. verificar `/healthz/` (`200`) y `/readyz/` (`200`);
5. verificar acceso al panel interno;
6. verificar acceso al portal público;
7. comprobar descargas protegidas;
8. confirmar conexión con PostgreSQL;
9. revisar logs;
10. verificar webhook y procesamiento Kobo, cuando esté habilitado;
11. realizar una comprobación funcional básica sin alterar datos reales.

El despliegue no debe considerarse completo hasta validar el comportamiento básico del sistema.

## Panel operativo Kobo

Con Kobo habilitado, el panel `/integrations/kobo/` permite revisar y operar
asignación de zonas, núcleos registrados, incidencias y reconciliación. No es
necesario pulsar sincronizar durante la operación normal: las Fichas 1, 10 y 11
llegan por webhook, el panel se actualiza por polling cada 15 segundos y la
intervención humana ocurre sólo ante incidencias.
Compruebe que los permisos Kobo se asignaron antes de habilitar acceso operativo.
Operador de campo queda fuera de esa audiencia territorial; el webhook y el
procesamiento de importación en backend no dependen de su acceso al panel.

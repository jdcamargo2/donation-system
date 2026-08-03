# Seguridad de SIGEDON

Este documento describe los controles de seguridad aplicados en SIGEDON y las responsabilidades que deben cumplirse durante el desarrollo, despliegue y operación del sistema.

## 1. Autenticación

El panel interno utiliza el sistema de autenticación de Django.

Las rutas privadas requieren un usuario autenticado.

El portal de transparencia:

* es público;
* no requiere autenticación;
* se encuentra separado del panel interno;
* expone únicamente información autorizada.

La autenticación confirma la identidad del usuario, pero no concede por sí sola permisos operativos.

## 2. Autorización

SIGEDON utiliza permisos de Django por modelo y acción.

Las vistas privadas:

* verifican permisos en el servidor;
* limitan consultas y acciones según el usuario;
* responden con estado HTTP `403 Forbidden` cuando un usuario autenticado carece de autorización.

El ocultamiento de botones, enlaces o componentes visuales no sustituye los controles de autorización del backend.

Toda acción restringida debe validar los permisos correspondientes antes de consultar, modificar o exponer información.

## 3. Seguridad del dashboard

El dashboard permanece disponible como punto de entrada para usuarios autenticados.

Según los permisos del usuario:

* no consulta métricas restringidas;
* no entrega datos sensibles al contexto del template;
* no muestra navegación hacia módulos no autorizados;
* no ejecuta consultas innecesarias sobre información protegida.

La seguridad del dashboard debe aplicarse tanto en la presentación como en la obtención de los datos.

## 4. Auditoría

`AuditLog` funciona como un registro append-only dentro de Django.

Se bloquean las siguientes operaciones:

* actualización de registros existentes mediante `save()`;
* eliminación;
* mutación desde el panel de administración;
* asignación de permisos de creación, edición o eliminación a roles operativos.

### Defensa a nivel de base de datos

En PostgreSQL, `operations_auditlog` está protegida por una segunda capa,
independiente de Django:

* un trigger append-only (migración `0018_auditlog_append_only_trigger`)
  rechaza `UPDATE`, `DELETE` y `TRUNCATE` a nivel de base de datos;
* el rol runtime `sigedon_app` no recibe esos privilegios sobre la tabla
  (ver `deploy/postgresql/harden_runtime_role.sql`);
* `INSERT` y `SELECT` permanecen disponibles para que la aplicación siga
  registrando eventos y consultando el historial;
* el mensaje de error del trigger es genérico y nunca incluye el contenido
  del registro afectado.

Esta defensa complementa, no sustituye, la inmutabilidad ya aplicada en el
modelo, el queryset, el admin y la UI. Un superusuario de PostgreSQL puede
administrar o desactivar el trigger; esa capacidad debe reservarse para
administración técnica explícita, nunca para el uso cotidiano de la
aplicación. `python manage.py verify_postgres_security` verifica el contrato
PostgreSQL antes de aceptar tráfico o un entorno restaurado:

* exige motor PostgreSQL (SQLite u otro backend → salida distinta de 0);
* debe ejecutarse con el **rol runtime final** (un éxito bajo el propietario
  o un superusuario no valida la separación de roles);
* comprueba función/trigger habilitado y sondas UPDATE/DELETE (con rollback)
  para `AuditLog` y `ExpenseRequestEvent`;
* comprueba constraints financieros críticos (USD, montos, reservas, unicidad
  de códigos operativos) y la postura de privilegios del rol runtime sobre
  `operations_auditlog` según `deploy/postgresql/harden_runtime_role.sql`;
* no repara grants, triggers ni datos; la remediación corresponde al
  propietario autorizado de la base.

Detalle operativo: `deploy/postgresql/README.md`, `docs/DEPLOYMENT.md` y
`docs/OPERATIONS.md`.

### Eventos de solicitud de gasto

`ExpenseRequestEvent` es append-only en ORM (QuerySet/manager + `save`/`delete`)
y, en PostgreSQL, mediante el trigger
`operations_expenserequestevent_append_only` (migración
`0029_expense_request_event_append_only`). Ningún rol canónico de aplicación
recibe permisos Django de mutación de eventos. Complementa `AuditLog`; no lo
sustituye. La verificación operativa cubre ambas familias append-only.

Los adjuntos de solicitud (`ExpenseRequestAttachment`) son evidencia privada del
flujo de solicitud de gasto. Solo el solicitante original puede mutarlos mientras
la solicitud está en `PENDING_DECISION`. Tras cualquier decisión o cierre quedan
congelados. La lectura autorizada (Admin, Comité, Auditor, Operador dueño) usa
rutas protegidas anidadas (`expense_request_attachment_preview` /
`expense_request_attachment_download`); no se exponen URLs directas de media ni
acceso público.

Los servicios de ciclo de vida (ER2B–ER2E) escriben `ExpenseRequestEvent` y
`AuditLog` en la misma transacción que la mutación. El fallo de evento o
auditoría revierte la solicitud/reserva/cumplimiento. No se exponen filas de
`ExpenseRequest` en el portal público.

Las acciones críticas deben registrar, como mínimo:

* actor;
* acción;
* entidad afectada;
* identificador;
* resumen;
* fecha y hora.

La mutación del dominio y la creación del registro de auditoría deben ejecutarse dentro de la misma transacción cuando formen parte de una única operación.

## 5. Integridad financiera

La protección de las operaciones financieras incluye:

* `transaction.atomic()`;
* `select_for_update()`;
* constraints de base de datos;
* validación de saldos con reservas activas (`APPROVED_RESERVED`);
* orden de bloqueo canónico `Donation → FundAllocation → ExpenseRequest` en
  aprobación;
* exclusión de registros anulados de los totales;
* validación de montos;
* exclusión de registros anulados;
* generación transaccional de códigos;
* saldos derivados;
* pruebas concurrentes sobre PostgreSQL.

### Reglas obligatorias

* Los saldos no deben almacenarse como valores editables.
* Las validaciones no deben depender únicamente de JavaScript.
* Las asignaciones no anuladas no pueden superar el monto disponible de una donación.
* Los gastos no anulados no pueden superar el monto disponible de una asignación.
* Las operaciones concurrentes deben protegerse mediante bloqueos de filas.
* Los registros anulados no participan en métricas ni saldos.
* `FundAllocation` es solo inspección en Django Admin (incluido para
  superusuarios). Las mutaciones de asignación deben pasar por los flujos
  operativos con servicios de dominio, transacciones, bloqueos de fila y
  `AuditLog` de aplicación.

## 6. Archivos privados

Los documentos operativos privados:

* no se sirven mediante URLs directas del campo de archivo;
* se previsualizan y descargan mediante endpoints autorizados separados;
* requieren autenticación cuando corresponda;
* verifican permisos canónicos del modelo (no nombres de rol);
* validan la pertenencia del archivo a la entidad padre solicitada (rutas anidadas);
* separan explícitamente la información pública de la privada;
* no se montan vía `static(MEDIA_URL, …)` ni siquiera en `DEBUG`.

En producción (`DJANGO_DEBUG=False`) el directorio de media privada debe ser un
volumen persistente explícito (`SIGEDON_MEDIA_ROOT` absoluto). Un `MEDIA_ROOT`
implícito bajo el repositorio no es aceptable: los blobs privados sobrevivirían
solo al ciclo de vida del contenedor/instancia. Los respaldos de base de datos
no sustituyen la copia de esos archivos. El proxy no debe servir ese path;
solo los endpoints autorizados de preview/download deben leer el storage.

No debe utilizarse directamente:

```python
file_field.url
```

para exponer archivos privados a usuarios finales.

La publicación de una entidad no implica automáticamente que todos sus archivos asociados sean públicos.

### 6.0. Vista previa persistida versus vista previa de carga

| Concepto | Alcance |
|----------|---------|
| Vista previa de carga (upload preview) | Solo cliente, antes del submit; no sustituye autorización del servidor |
| Vista previa persistida (persisted preview) | Respuesta autenticada del servidor para un archivo ya almacenado |

Autorización:

* preview y download comparten la misma autorización;
* el alcance por objeto padre es obligatorio;
* se gobierna por permisos Django, no por comprobaciones de nombre de rol;
* el portal público no recibe adjuntos privados.

Lista blanca de vista previa en línea (extensión → MIME controlado por el servidor):

* `.png` → `image/png`
* `.jpg` / `.jpeg` → `image/jpeg`
* `.webp` → `image/webp`
* `.gif` → `image/gif`
* `.pdf` → `application/pdf`
* `.txt` → `text/plain; charset=utf-8`

No se renderizan en línea: SVG, HTML/HTM, XML, JavaScript, Office, ZIP/RAR/7Z,
ejecutables ni binarios desconocidos. Esos tipos conservan descarga protegida.

Cabeceras obligatorias en preview y download:

* `X-Content-Type-Options: nosniff`
* `Cache-Control: private, no-store`

Implementación compartida: `apps/operations/file_access.py`
(`get_safe_persisted_file_preview_type`, `protected_file_response`).

### 6.1. Vista previa local de selección de archivos

La vista previa de selección en formularios de adjuntos de avance, en la carga pendiente
de `ProjectDocument`, en la carga pendiente de soportes de gasto
(`ExpenseForm.support_file`, `SupportingDocumentForm.document`), en la carga pendiente
del documento legal de institución (`InstitutionForm.legal_document`) y en la carga
pendiente de adjuntos de remediación (`ProjectUpdateRemediationAttachmentForm.file`):

* opera solo en el cliente y no envía archivos hasta el submit del formulario;
* no almacena nombres ni contenido en persistencia del navegador;
* no sustituye la validación ni los controles de acceso del servidor;
* no gestiona ni elimina adjuntos o documentos ya persistidos;
* mantiene el input nativo como autoridad de la selección enviada;
* no altera el perímetro de descarga privada ni la validación autoritativa del servidor;
* en `InstitutionForm.legal_document`, el enlace al archivo actual y la casilla de limpiar
  siguen siendo el widget `ClearableFileInput` de Django; la vista previa solo cubre la
  selección pendiente de reemplazo; limpiar/reemplazar el archivo persistido sigue
  validándose en el servidor; la vista previa no altera permisos;
* en `ProjectUpdateRemediationAttachmentForm.file`, la vista previa no altera permisos ni
  las reglas de estado de remediación (borrador) validadas en dominio/servicio.

### 6.2. Estáticos públicos versus media privada

Los CSS/JS/fuentes del panel (incluidos Bootstrap, Bootstrap Icons y
SweetAlert2 vendorizados) son activos públicos bajo `STATIC_ROOT` tras
`collectstatic`: no contienen secretos. La media operativa privada permanece
en `MEDIA_ROOT` / `SIGEDON_MEDIA_ROOT` y solo se expone por endpoints
autorizados. Vendorizar elimina la dependencia de disponibilidad/DNS de CDN
públicos para esas librerías; no sustituye el monitoreo de avisos de seguridad
ni implica integridad automática frente a compromiso de la cadena de suministro
en el momento de obtener el artefacto. Actualizar vendor debe ser deliberado,
versionado, probado y documentado en
[`static/vendor/THIRD_PARTY_ASSETS.md`](../static/vendor/THIRD_PARTY_ASSETS.md).
Para assets same-origin del repositorio, la integridad del despliegue y del
control de cambios sustituye SRI de CDN; CSP debe permitir CSS/JS/fuentes
same-origin según la política vigente (los scripts inline existentes ya
condicionan CSP).

## 7. Seguridad de KoboToolbox

La integración con KoboToolbox protege:

* token de API;
* credenciales del webhook;
* payload original;
* adjuntos;
* configuración técnica;
* datos sensibles;
* información de procesamiento.

El webhook:

* acepta únicamente las credenciales configuradas;
* valida la solicitud antes de procesarla;
* no debe registrar secretos en logs;
* no debe exponer mensajes internos sensibles;
* debe conservar el payload original únicamente en almacenamiento autorizado.

Las firmas y otros archivos sensibles no pueden marcarse como candidatos a publicación pública.

Los permisos técnicos `kobo.*` deben mantenerse separados de los permisos operativos.

## 8. Variables de entorno

Las siguientes variables nunca deben versionarse:

```text
DJANGO_SECRET_KEY
POSTGRES_PASSWORD
KOBO_API_TOKEN
KOBO_WEBHOOK_SECRET
```

El archivo `.env` debe estar ignorado por Git.

También deben mantenerse fuera del repositorio:

* credenciales de base de datos;
* tokens de servicios externos;
* claves privadas;
* secretos de despliegue;
* credenciales de correo;
* respaldos con información real.

El archivo `.env.example` puede incluir nombres de variables, pero no valores secretos reales.

## 9. Seguridad en producción

Cuando:

```env
DJANGO_DEBUG=False
```

deben cumplirse las siguientes condiciones:

* `DJANGO_SECRET_KEY` es obligatorio;
* `ALLOWED_HOSTS` debe estar configurado;
* PostgreSQL es obligatorio;
* SQLite está prohibido;
* los secretos deben obtenerse desde variables de entorno;
* los errores internos no deben mostrarse al usuario;
* el proceso web de producción es Gunicorn sirviendo `core.wsgi:application`
  ([DEPLOYMENT.md §6](DEPLOYMENT.md#6-servidor-de-aplicación-gunicorn));
  `runserver` no es aceptable en producción; los workers no ejecutan
  migraciones ni `collectstatic`.
* los logs de runtime van a stdout/stderr con correlación `X-Request-ID`; no
  deben contener secretos, cabeceras sensibles, cookies, cuerpos de petición ni
  payloads Kobo; la retención y el acceso a logs son controlados por la
  plataforma, no por la aplicación.
* las sondas `/healthz/` y `/readyz/` son intencionalmente anónimas y exponen
  solo un estado grueso (`ok` / `ready` / `not_ready`); no incluyen versión,
  entorno, dependencias, BD, host ni migraciones; no son un panel de estado
  público ni deben indexarse; el proxy puede restringir orígenes de red, pero
  la aplicación permanece segura aunque sean alcanzables; `X-Request-ID`
  permite correlación; no sirven para diagnosticar dependencias externas
  (Kobo, caché, object storage). No hay allowlist de IP dentro de Django.
* `seed_sigedon_demo` está **deshabilitado** cuando `DEBUG=False` y se rechaza
  antes de cualquier mutación; no existe bypass operativo. Es exclusivo de
  desarrollo local/efímero y no forma parte de release, preflight ni arranque
  web. `SIGEDON_DEMO_PASSWORD` es solo desarrollo y nunca se imprime.
* Los comandos `process_kobo_submissions` y `reconcile_kobo_submissions` deben
  devolver exit distinto de cero cuando hay fallos de lote; no imprimen
  tokens, payloads ni datos personales. No deben invocarse desde el arranque
  web ni enmascararse con `|| true`.

Según el entorno de despliegue, deben configurarse:

* redirección a HTTPS;
* cookies seguras;
* HSTS;
* `CSRF_TRUSTED_ORIGINS`;
* cabecera de proxy SSL;
* protección de cookies de sesión;
* protección de cookies CSRF.

Configuraciones representativas:

```python
SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
```

Estas opciones deben ajustarse según la infraestructura real y no activarse sin verificar el comportamiento del proxy y del servidor.

## 10. CSRF y métodos HTTP

Las mutaciones utilizan solicitudes `POST`.

Django protege los formularios mediante CSRF.

Las acciones terminales, como cerrar, anular, publicar, eliminar o restaurar
(según la entidad):

* no deben ejecutarse mediante `GET`;
* requieren permiso;
* requieren validación del dominio;
* deben usar protección CSRF;
* deben generar auditoría cuando corresponda.

Para `Project`, terminar, publicar y retirar del portal usan `POST` con CSRF.
Un proyecto no se anula ni se elimina como acción terminal operativa.

Las solicitudes `GET` deben limitarse a operaciones de consulta.

## 10.1. Publicación y no eliminación de proyectos

* Publicar y retirar del portal requieren
  `operations.manage_project_publication`.
* Esos endpoints son solo `POST` y están protegidos por CSRF.
* Los selectores públicos usan `status=ACTIVE` e `is_public=True`.
* La eliminación de proyectos está bloqueada en UI operativa, Django Admin,
  modelo (`Project.delete()`) y queryset (`QuerySet.delete()`).
* La administración directa de base de datos, `flush`, teardown de pruebas y
  destrucción de la base quedan fuera de las garantías de aplicación; no se
  describe un trigger PostgreSQL que impida borrar proyectos.
* La invalidación de caché del portal ocurre solo después de cambios exitosos
  del ciclo de publicación (publicar, retirar, o terminar un proyecto que
  estaba público), mediante limpieza amplia del cache por defecto.

## 10.2. Exportaciones CSV operativas

Las exportaciones CSV del panel interno (`proyectos.csv`, `donaciones.csv`,
`asignaciones.csv`, `gastos.csv`) neutralizan prefijos de fórmula de hoja de
cálculo (`=`, `+`, `-`, `@`) en texto controlado por el usuario antes de
escribir cada celda con `csv.writer`.

* Solo se afecta la representación exportada de texto potencialmente peligroso
  (prefijo `'`); no se modifican filas en base de datos.
* Valores numéricos (`Decimal`, enteros), fechas y códigos operativos generados
  conservan su representación habitual de exportación.
* Permisos, querysets y columnas exportadas no cambian por este control.

El helper canónico es `apps.operations.csv_export.escape_csv_cell`, aplicado en
`FilteredCsvExportView`.

## 11. Datos públicos

El portal público no debe exponer:

* correos electrónicos;
* usuarios;
* auditoría;
* payloads crudos;
* documentos privados;
* notas internas;
* firmas;
* submissions rechazadas;
* entidades anuladas;
* información Kobo no aprobada;
* donaciones individuales;
* gastos individuales;
* identificadores técnicos innecesarios.

La información pública debe obtenerse mediante selectores y consultas explícitamente diseñadas para publicación.

Los selectores de proyecto del portal requieren `ACTIVE` e `is_public=True`;
ninguna de las dos condiciones basta por sí sola.

No debe reutilizarse directamente el contexto del panel interno.

## 12. Manejo de errores

Los usuarios no deben recibir:

* tracebacks;
* errores SQL;
* nombres internos de modelos;
* rutas internas del servidor;
* secretos;
* mensajes falsos de éxito;
* detalles técnicos innecesarios.

Los errores deben:

* mostrarse con mensajes comprensibles;
* conservar información técnica en logs autorizados;
* diferenciar fallos de validación de fallos internos;
* evitar revelar la estructura interna del sistema.

## 13. Logs

Los logs no deben almacenar:

* contraseñas;
* tokens;
* secretos del webhook;
* claves de API;
* contenido sensible completo;
* payloads crudos sin sanitización;
* datos personales innecesarios.

Los eventos técnicos deben registrar únicamente la información necesaria para diagnóstico y trazabilidad.

Los logs deben almacenarse con permisos restringidos y aplicar políticas de rotación.

## 14. Copias de seguridad

Las bases SQLite, los dumps y los respaldos:

* no deben versionarse;
* deben almacenarse fuera del repositorio;
* deben tratarse como información potencialmente sensible;
* deben protegerse con controles de acceso;
* deben conservarse según una política definida;
* deben probarse periódicamente mediante restauraciones controladas (trimestral como mínimo).

Los scripts en `deploy/backups/` no incluyen secretos, no leen `.env`, no imprimen contraseñas y rechazan restaurar sobre la base activa. Preferir `~/.pgpass` (permisos `0600`) frente a `PGPASSWORD`. La retención automatizada solo elimina sets verificados bajo `SIGEDON_BACKUP_ROOT` (nunca rutas arbitrarias). Markers y el hook de alerta usan identificadores operativos acotados (sin dumps, rutas productivas ni secretos). Los timers de ejemplo no restauran producción.

También deben excluirse del repositorio archivos como:

```text
*.sqlite3
*.sql
*.dump
*.bak
*.backup
```

El cifrado de artefactos y la copia off-site son requisitos de infraestructura; esta fase aún no los implementa en los scripts. RPO/RTO propuestos (no SLA; requieren decisión de despliegue y drill de RTO): `deploy/backups/README.md`.

## 15. Dependencias

Las dependencias deben mantenerse actualizadas y revisarse periódicamente.

Se recomienda verificar:

* vulnerabilidades conocidas;
* versiones sin soporte;
* compatibilidad con la versión de Python;
* compatibilidad con Django;
* cambios de seguridad relevantes.

Las actualizaciones deben validarse mediante pruebas automatizadas antes de desplegarse.

## 16. Principio de mínimo privilegio

Cada usuario debe recibir únicamente los permisos necesarios para cumplir su función.

Esto implica:

* separar roles operativos y técnicos;
* evitar el uso cotidiano de superusuarios;
* retirar permisos heredados incompatibles;
* revisar periódicamente grupos y usuarios;
* no conceder permisos de modificación cuando basta con consulta;
* limitar el acceso a payloads y archivos sensibles.

A nivel de base de datos, esto se traduce en separar el rol propietario de
migraciones (`sigedon_owner`) del rol runtime (`sigedon_app`), como describe
`deploy/postgresql/harden_runtime_role.sql` y `docs/DEPLOYMENT.md`. El rol
runtime nunca debe ser superusuario, propietario de las tablas, ni recibir
`ALL PRIVILEGES`.

## 17. Limitaciones del MVP

La auditoría del MVP no incluye:

* hash encadenado;
* almacenamiento WORM;
* almacenamiento externo inmutable;
* snapshots completos de estado anterior y posterior;
* integración con SIEM;
* firma criptográfica de eventos;
* detección automática de anomalías.

Estas capacidades quedan fuera del alcance actual y no deben asumirse como implementadas.

## 18. Responsabilidad de la infraestructura

La protección final también depende de:

* configuración del servidor;
* permisos del sistema operativo;
* HTTPS;
* firewall;
* copias de seguridad;
* rotación de secretos;
* actualización de dependencias;
* configuración de PostgreSQL;
* protección del almacenamiento de archivos;
* monitoreo (incluidas rutas de sonda `/healthz/` y `/readyz/` y alertas ante
  fallos sostenidos de readiness);
* gestión segura de cuentas administrativas.

La seguridad de la aplicación no sustituye la seguridad de la infraestructura donde se despliega.

## Hub territorial Kobo

Las rutas mutables del Hub exigen autenticación, permiso en servidor, CSRF y
POST. Las vistas muestran datos territoriales resumidos; no exponen payloads,
tokens, teléfonos o coordenadas por defecto.

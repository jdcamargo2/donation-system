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
aplicación. `python manage.py verify_postgres_security` verifica, de forma
solo de lectura, que el rol runtime conectado no tenga esos privilegios y
que el trigger esté instalado.

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

## 6. Archivos privados

Los documentos operativos privados:

* no se sirven mediante URLs directas del campo de archivo;
* se descargan mediante endpoints autorizados;
* requieren autenticación cuando corresponda;
* verifican permisos;
* validan la pertenencia del archivo a la entidad solicitada;
* separan explícitamente la información pública de la privada.

No debe utilizarse directamente:

```python
file_field.url
```

para exponer archivos privados a usuarios finales.

La publicación de una entidad no implica automáticamente que todos sus archivos asociados sean públicos.

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
* los errores internos no deben mostrarse al usuario.

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

Los scripts en `deploy/backups/` no incluyen secretos, no leen `.env`, no imprimen contraseñas y rechazan restaurar sobre la base activa. Preferir `~/.pgpass` (permisos `0600`) frente a `PGPASSWORD`.

También deben excluirse del repositorio archivos como:

```text
*.sqlite3
*.sql
*.dump
*.bak
*.backup
```

El cifrado de artefactos y la copia off-site son requisitos de infraestructura; esta fase aún no los implementa en los scripts. La frecuencia/retención automatizadas y los objetivos RPO/RTO permanecen pendientes hasta medir restauraciones reales.

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
* monitoreo;
* gestión segura de cuentas administrativas.

La seguridad de la aplicación no sustituye la seguridad de la infraestructura donde se despliega.

## Hub territorial Kobo

Las rutas mutables del Hub exigen autenticación, permiso en servidor, CSRF y
POST. Las vistas muestran datos territoriales resumidos; no exponen payloads,
tokens, teléfonos o coordenadas por defecto.

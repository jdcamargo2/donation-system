# Arquitectura de SIGEDON

## 1. Visión general

SIGEDON utiliza una arquitectura modular basada en Django, con separación entre los siguientes componentes:

```text
Panel operativo interno
Portal público
Integración con KoboToolbox
Servicios de dominio
Persistencia en PostgreSQL
Gestión de archivos privados
```

La arquitectura busca mantener separadas las responsabilidades de presentación, dominio, persistencia, publicación pública e integración externa.

## 1.1. Arquitectura de `Project`

`Project` se modela con dos ejes independientes:

```text
Ciclo operativo:     ACTIVE → CLOSED
Visibilidad pública: is_public = False | True
```

* Los servicios de dominio controlan publicación (`publish_project` /
  `unpublish_project`) y el cierre terminal (`finish_project`), que fuerza
  `is_public=False`.
* Los formularios ordinarios no cambian `status` ni `is_public`.
* Los selectores públicos exigen ambas condiciones (`ACTIVE` + `is_public=True`).
* El modelo y el queryset bloquean la eliminación; Admin y capas de URL
  aportan defensa en profundidad. La administración directa de base de datos
  queda fuera de esa garantía.

## 2. Componentes

### 2.1. `core/`

Contiene la configuración central del proyecto:

* settings;
* rutas raíz;
* configuración de base de datos;
* seguridad de producción;
* archivos estáticos;
* archivos multimedia;
* carga de variables de entorno;
* contrato de arranque WSGI de producción (`core.wsgi:application` vía Gunicorn;
  detalle en [DEPLOYMENT.md](DEPLOYMENT.md#6-servidor-de-aplicación-gunicorn));
* logging de runtime a stdout/stderr con middleware de correlación
  `X-Request-ID` (`core.request_ids.RequestIdMiddleware`) y filtros de
  redacción defensiva; distinto de `AuditLog` / `KoboProcessingEvent`
  (trazabilidad de negocio vs diagnóstico operativo);
* sondas HTTP de runtime en `core.health` (`/healthz/` liveness,
  `/readyz/` readiness de BD + migraciones); independientes de Kobo, caché,
  media, R2 y autenticación (detalle en
  [DEPLOYMENT.md §6.3](DEPLOYMENT.md#63-sondas-http-healthz-y-readyz)).
* contrato de storage privado en `core.private_storage`
  (`SIGEDON_PRIVATE_STORAGE=filesystem|r2`); default filesystem. R2 usa
  `django-storages` S3Storage con bucket privado; staticfiles siguen en
  WhiteNoise.

### 2.2. `apps.operations`

Es el núcleo funcional de SIGEDON.

Contiene:

* modelos operativos;
* reglas de negocio;
* servicios transaccionales;
* formularios;
* vistas;
* rutas;
* roles;
* permisos;
* comandos administrativos;
* auditoría;
* pruebas automatizadas.

Toda mutación financiera crítica debe ejecutarse mediante servicios de dominio.

`FundAllocation` es solo inspección en Django Admin. Toda mutación de
asignaciones debe usar los flujos operativos de la aplicación respaldados por
servicios de dominio, transacciones, bloqueos de fila y `AuditLog` de
aplicación. No hay bypass por superusuario en Admin. El ciclo de vida terminal
de asignaciones usa solo Finalizar y Anular; no hay endpoint genérico de
cambio de estado. Donation sí conserva transición de estado no terminal.

### 2.3. `apps.public_portal`

Publica información previamente autorizada y sanitizada.

Este componente:

* no debe depender de formularios internos;
* no debe reutilizar vistas operativas;
* no debe exponer información privada;
* utiliza selectores y consultas específicas para determinar qué información puede publicarse.

La selección de datos públicos debe permanecer separada de la lógica del panel operativo.

### 2.4. `apps.integrations.kobo`

Gestiona la integración con KoboToolbox.

Incluye:

* comunicación con KoboToolbox;
* descubrimiento de activos;
* definiciones de formularios;
* registro de activos;
* bindings;
* staging del payload original;
* normalización;
* routing hacia proyectos;
* gestión de adjuntos;
* procesamiento;
* inspección técnica de incidencias;
* importación automática;
* reconciliación;
* recepción mediante webhook.

La integración Kobo transforma y relaciona información, pero no modifica directamente los saldos financieros.

### 2.5. `web`

`web` es un paquete histórico que actualmente no contiene modelos ni vistas productivas activas.

Se conserva porque:

* contiene pruebas de regresión;
* la mayoría de los templates operativos continúan bajo `templates/web`;
* las etiquetas de formularios operativos centralizan la marca de obligatoriedad en
  `templates/web/includes/ops_form_field_label.html` (según `field.field.required`);
* mantiene compatibilidad estructural durante la consolidación modular del proyecto.

## 3. Capas arquitectónicas

### 3.1. Presentación

```text
URLs
→ Views
→ Forms
→ Templates
```

Responsabilidades:

* autenticación;
* autorización;
* recepción de entradas del usuario;
* validación de formularios;
* mensajes de interfaz;
* navegación;
* presentación de información.

La capa de presentación no debe contener reglas financieras críticas ni lógica transaccional compleja.

Los activos UI del panel interno (Bootstrap 5.3.3, Bootstrap Icons 1.11.3,
SweetAlert2 11.26.25) se sirven desde `static/vendor/` mediante el contrato
estático de Django (`{% static %}` + `collectstatic`). En producción,
**WhiteNoise** sirve `STATIC_ROOT` con storage de manifest comprimido; Gunicorn
sigue siendo el proceso WSGI canónico (`./deploy/start_web.sh`). El portal
público usa su propio CSS y no depende de ese stack vendor. Media privada
permanece fuera de WhiteNoise. En el destino Render final, la media privada
aceptada es R2 tras probe real; filesystem no es modo final en Render (ver
[CLOUDFLARE_R2.md](runbooks/CLOUDFLARE_R2.md) y
[RENDER_FIRST_DEPLOY.md](runbooks/RENDER_FIRST_DEPLOY.md)). Inventario
y licencias:
[`static/vendor/THIRD_PARTY_ASSETS.md`](../static/vendor/THIRD_PARTY_ASSETS.md).

Despliegue inicial previsto: runtime Python nativo en Render (sin Docker).
Contrato RENDER-3 preparado (`./deploy/render/build.sh`,
`./deploy/render/pre_deploy.sh`, `./deploy/start_web.sh`, health `/readyz/`);
**ningún** servicio provisionado aún. Build = collectstatic + asset verify;
migraciones = pre-deploy/one-off owner; start = Gunicorn existente. Activar R2
es configuración + verificación, no otro rewrite.

### 3.2. Dominio

Componentes principales:

```text
services.py
role_services.py
selectores públicos
normalizadores Kobo
```

Responsabilidades:

* transacciones;
* invariantes de negocio;
* transiciones de estado;
* auditoría;
* bloqueos;
* cálculos derivados;
* procesamiento de integraciones;
* coordinación entre entidades.

Las operaciones críticas deben estar encapsuladas en servicios reutilizables y cubiertas por pruebas.

### 3.3. Persistencia

```text
Django ORM
→ PostgreSQL
```

Incluye:

* constraints;
* claves foráneas;
* relaciones protegidas;
* secuencias;
* índices;
* restricciones de unicidad;
* validaciones de integridad monetaria.

La base de datos actúa como una capa adicional de protección y no depende únicamente de validaciones en formularios o JavaScript.

## 4. Dependencias entre componentes

```text
apps.public_portal
→ consulta datos autorizados de apps.operations

apps.integrations.kobo
→ asocia información normalizada con entidades de apps.operations

core
→ registra y configura todas las aplicaciones

web
→ conserva templates y pruebas históricas
```

Reglas de dependencia:

* `apps.public_portal` puede consultar datos operativos, pero no debe modificarlos.
* `apps.integrations.kobo` puede asociar información normalizada con proyectos y otras entidades autorizadas.
* Kobo no debe alterar directamente donaciones, asignaciones, gastos ni saldos.
* `apps.operations` no debe depender de vistas o formularios del portal público.
* `core` debe limitarse a configuración y composición del proyecto.

## 5. Gestión de archivos

Tipos principales de archivos:

* documentos del proyecto;
* adjuntos de avances;
* adjuntos de remediación;
* documento legal de institución;
* soportes de gastos;
* adjuntos Kobo.

Los archivos privados:

* no se publican mediante `FileField.url` (esa URL no es autorización);
* se previsualizan (`disposition=inline`, lista blanca estricta) y se descargan
  (`disposition=attachment`) mediante vistas autorizadas y acotadas al padre;
* requieren autenticación y permisos cuando corresponda;
* conservan trazabilidad;
* no deben exponerse directamente desde el almacenamiento;
* en desarrollo local tampoco se monta `MEDIA_ROOT` vía `static()`; el acceso
  ocurre solo por los endpoints protegidos.

Almacenamiento privado (`core.private_storage`):

* **Default actual:** `SIGEDON_PRIVATE_STORAGE=filesystem` →
  `FileSystemStorage` + `MEDIA_ROOT` (`SIGEDON_MEDIA_ROOT` obligatorio en
  producción).
* **Opcional (código listo):** `SIGEDON_PRIVATE_STORAGE=r2` →
  `storages.backends.s3.S3Storage` hacia Cloudflare R2. Bucket privado:
  `default_acl=None`, `querystring_auth=True`, `file_overwrite=False`.
  Entrega: `SIGEDON_PRIVATE_FILE_DELIVERY=stream|signed_redirect`.
* WhiteNoise / staticfiles **nunca** usan R2.
* Cambiar de filesystem a R2 (o al revés) no debilita la autorización de
  preview/download; sí cambia dónde viven los blobs. Con objetos productivos
  en R2, volver a un filesystem vacío hace que los documentos parezcan
  ausentes.
* **Estado de preparación:** no hay cuenta Cloudflare, bucket ni
  credenciales provisionadas; no hay sonda real de conectividad completada.
  Provisionar es configuración + secretos +
  `verify_private_storage` / `--probe` + pruebas de acceso; no un rewrite.
  Runbook: [CLOUDFLARE_R2.md](runbooks/CLOUDFLARE_R2.md).

Settings valida la forma de la configuración al importar;
`python manage.py check --deploy` verifica el contrato según el modo.
Comandos: `verify_private_storage`, `migrate_private_media`,
`export_private_objects`. La automatización operativa de backup (lock,
retención, markers, drills) vive en `deploy/backups/`; el scheduling
permanece en la plataforma.

La visibilidad de cada archivo debe definirse explícitamente según su naturaleza y contexto.
El helper compartido vive en `apps/operations/file_access.py`. El contrato de UI
reutilizable está en `templates/web/includes/protected_file_item.html`.

### 5.1. Vista previa local de selección (adjuntos de avance, documento de proyecto, soportes de gasto, documento legal de institución y adjuntos de remediación)

Los formularios de carga de archivos operativos pueden activar una vista previa
client-side mediante el atributo explícito `data-file-upload-preview`.

En la fase actual, el opt-in aplica a:

* `ProjectUpdateForm.attachments`;
* `ProjectUpdateForProjectForm.attachments`;
* `ProjectUpdateAttachmentForm.files`;
* `ProjectDocumentForm.file` (entrada de un solo archivo);
* `ExpenseForm.support_file` (entrada de un solo archivo, campo solo de formulario);
* `SupportingDocumentForm.document` (entrada de un solo archivo);
* `InstitutionForm.legal_document` (entrada de un solo archivo; primer superficie
  `ClearableFileInput`);
* `ProjectUpdateRemediationAttachmentForm.file` (entrada de un solo archivo;
  superficie solo de alta).

Comportamiento:

* la vista previa es local al navegador y no sube archivos hasta el envío del formulario;
* el `input[type=file]` nativo permanece visible y es la fuente autoritativa de la selección;
* en `ProjectDocumentForm.file`, `ExpenseForm.support_file`, `SupportingDocumentForm.document`
  y `ProjectUpdateRemediationAttachmentForm.file`
  solo se previsualiza la selección pendiente local; al elegir otro archivo se reemplaza esa
  selección; los documentos ya persistidos siguen gestionándose solo con las acciones de
  servidor (detalle, descarga, eliminación);
* en `ProjectUpdateRemediationAttachmentForm.file`, los adjuntos de remediación ya persistidos
  siguen gestionados en el detalle de la remediación; las reglas de borrador las imponen las
  capas de dominio/servicio;
* en `InstitutionForm.legal_document`, el enlace al archivo persistido y la casilla de limpiar
  siguen gestionados por Django (`ClearableFileInput`); la vista previa cubre únicamente la
  selección pendiente de reemplazo; quitar esa selección pendiente no limpia el archivo
  persistido ni altera `legal_document-clear`;
* en `ExpenseForm.support_file`, un archivo enviado en edición crea un `SupportingDocument`
  adicional y no sustituye soportes existentes;
* en entradas múltiples, la selección pendiente puede construirse de forma incremental y
  permiten quitar archivos individuales antes del submit cuando el navegador soporta
  `DataTransfer`;
* las imágenes raster (JPEG, PNG, WebP, GIF) pueden mostrar una miniatura local con
  `URL.createObjectURL`; SVG, PDF y documentos no se incrustan;
* no se persisten nombres ni contenido en `localStorage`/`sessionStorage`;
* la vista previa no sustituye la validación ni el almacenamiento del backend;
* los adjuntos ya persistidos siguen gestionándose solo con las acciones de servidor
  existentes (detalle, descarga, eliminación en borrador).

## 6. Base de datos

### 6.1. Desarrollo

SQLite puede utilizarse únicamente con una configuración explícita de desarrollo:

```env
DJANGO_DEBUG=True
DATABASE_ENGINE=sqlite
```

SQLite se utiliza para desarrollo local y pruebas que no dependan de comportamiento específico de PostgreSQL.

### 6.2. Producción

PostgreSQL es obligatorio en producción.

SIGEDON depende de PostgreSQL para:

* bloqueos reales de filas;
* pruebas y operaciones concurrentes;
* integridad transaccional;
* reserva segura de códigos;
* protección de saldos;
* ejecución confiable de `select_for_update()`.

El comportamiento concurrente crítico no debe considerarse validado únicamente con SQLite.

## 7. Seguridad arquitectónica

SIGEDON aplica las siguientes medidas:

* autenticación de Django;
* permisos por modelo y acción;
* separación entre permisos operativos y permisos `kobo.*`;
* auditoría append-only (`AuditLog` y `ExpenseRequestEvent`) con defensa
  PostgreSQL (triggers) verificable mediante
  `python manage.py verify_postgres_security` bajo el rol runtime;
* archivos protegidos;
* acciones terminales mediante solicitudes `POST`;
* protección CSRF;
* HTTPS en producción;
* secretos mediante variables de entorno;
* constraints monetarios;
* `transaction.atomic()`;
* `select_for_update()`.

La validación continua del repositorio vive en GitHub Actions (Python 3.12 + PostgreSQL 16): gates estáticos, migración desde cero, artefactos de `collectstatic`, suite crítica y suite completa. El despliegue permanece fuera de CI. Ver [TESTING.md §18](TESTING.md#18-integración-continua-github-actions).

Además:

* las vistas no deben confiar únicamente en elementos ocultos de la interfaz;
* los permisos deben validarse en el servidor;
* las acciones críticas deben generar auditoría;
* los errores internos no deben exponerse al usuario;
* el portal público debe usar consultas explícitamente sanitizadas.

## 8. Principios arquitectónicos

La arquitectura de SIGEDON sigue estos principios:

* separación de responsabilidades;
* servicios de dominio para mutaciones críticas;
* validación en múltiples capas;
* mínima exposición de datos;
* trazabilidad de acciones;
* inmutabilidad de registros publicados o auditados;
* protección transaccional;
* dependencia explícita entre módulos;
* compatibilidad controlada con componentes históricos.

## 9. Deuda técnica conocida

Actualmente se mantienen las siguientes condiciones:

* los templates operativos continúan bajo `templates/web`;
* `web` se conserva como cascarón histórico;
* existe un flujo legado de sincronización de la Ficha 1;
* algunos estados históricos de Kobo permanecen declarados por compatibilidad;
* las dependencias de producción y desarrollo comparten `requirements.txt`
  (incluye Gunicorn pinneado para el runtime WSGI de producción; el arranque
  canónico está en [DEPLOYMENT.md](DEPLOYMENT.md#6-servidor-de-aplicación-gunicorn)).

Estas condiciones no impiden la operación actual del MVP, pero deben tratarse como deuda técnica controlada y no como patrón para nuevas implementaciones.

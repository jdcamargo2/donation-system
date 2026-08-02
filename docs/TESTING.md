# Pruebas de SIGEDON

Este documento describe el estado de referencia de la suite automatizada, las áreas cubiertas y las verificaciones mínimas requeridas antes de integrar o desplegar cambios.

## 1. Estado de referencia

```text
880 tests
880 aprobados
```

Este número representa el estado de referencia actual de esta rama.

Debe revisarse cuando la cantidad total de pruebas cambie de forma estable en
la rama principal.

## 2. Ejecución completa

Para ejecutar toda la suite:

```bash
python manage.py test
```

### Resultado esperado

* todas las pruebas aprobadas;
* ningún error;
* ningún fallo;
* ninguna migración inesperada;
* omisiones únicamente cuando estén justificadas por el motor de base de datos o el entorno.

## 3. Áreas cubiertas

### 3.1. `apps.operations`

La suite cubre:

* modelos;
* servicios de dominio;
* constraints;
* generación de códigos;
* estados y transiciones de proyecto (`ACTIVE`/`CLOSED`, sin estados obsoletos);
* defaults `ACTIVE` + privado (`is_public=False`);
* publicar/retirar con permiso `manage_project_publication`;
* selectores públicos de doble condición;
* terminar proyecto y retiro automático de visibilidad pública;
* rutas eliminadas de cambio genérico de estado, anulación o eliminación de
  proyecto;
* bloqueos de eliminación en instancia, queryset y Admin (incluido superusuario);
* acciones terminales de otras entidades (anular/eliminar donde aplique);
* auditoría;
* archivos protegidos;
* permisos;
* interfaz según rol;
* avances;
* revisiones;
* decisiones institucionales;
* escape de fórmulas en exportaciones CSV operativas;
* flujos end-to-end.

### 3.2. `apps.integrations.kobo`

La suite cubre:

* cliente de API;
* contratos de integración;
* Fichas 1, 10 y 11;
* staging;
* routing;
* bindings;
* adjuntos;
* descubrimiento de activos;
* configuración;
* webhook;
* reconciliación;
* revisión;
* importación;
* rechazo;
* restauración.

### 3.3. `apps.public_portal`

La suite cubre:

* proyectos públicos (`ACTIVE` + `is_public=True`);
* exclusión de proyectos privados o cerrados;
* avances publicados;
* métricas agregadas;
* privacidad;
* respuestas JSON;
* navegación;
* exclusión de entidades anuladas.

### 3.4. `core`

La suite cubre:

* settings;
* configuración de producción;
* uso controlado de SQLite;
* secretos obligatorios;
* `ALLOWED_HOSTS`;
* PostgreSQL.

### 3.5. `web`

La suite cubre:

* dashboard;
* formularios;
* vistas;
* auditoría;
* búsquedas;
* exportaciones;
* seguridad de escape CSV frente a inyección de fórmulas de hoja de cálculo
  (`apps.operations.tests.test_csv_export_security`);
* pruebas de regresión.

## 4. Pruebas sobre PostgreSQL

Las pruebas concurrentes requieren PostgreSQL.

SIGEDON opera exclusivamente en USD. `Donation.currency` y `Expense.currency`
solo admiten `USD`, y las pruebas de constraints comprueban que PostgreSQL
impone esta restricción. No existen conversiones ni tasas de cambio. `EUR`, `VES`
y `COP` solo deben aparecer en migraciones históricas o en pruebas negativas
que verifican su rechazo.

Validan:

* prevención de sobreasignación;
* prevención de sobre-ejecución;
* generación simultánea de códigos;
* anulaciones concurrentes;
* actualizaciones concurrentes;
* bloqueos reales de filas.

En SQLite estas pruebas pueden omitirse porque ese motor no reproduce el comportamiento de bloqueo requerido por SIGEDON.

### Regla

Una prueba concurrente omitida en SQLite no debe considerarse validada hasta ejecutarse correctamente sobre PostgreSQL.

## 5. Pruebas focalizadas

### Dashboard

```bash
python manage.py test web.tests.test_dashboard
```

### Roles y permisos

```bash
python manage.py test apps.operations.tests.test_roles
```

### Solicitudes de gasto (ER1)

```bash
python manage.py test \
  apps.operations.tests.test_expense_request_models \
  apps.operations.tests.test_expense_request_roles \
  apps.operations.tests.test_expense_request_operational_codes \
  apps.operations.tests.test_expense_request_events \
  apps.operations.tests.test_expense_request_attachments \
  apps.operations.tests.test_roles \
  apps.operations.tests.test_operational_codes
```

Los tests de trigger PostgreSQL y concurrencia de códigos `SGS` se omiten de
forma limpia bajo SQLite (`skipUnless`); deben ejecutarse contra una base
PostgreSQL desechable para validar la defensa append-only.

### Solicitudes de gasto — reservas, cumplimiento y anulación (ER2A–ER2E)

```bash
python manage.py test \
  apps.operations.tests.test_expense_request_fulfillment \
  apps.operations.tests.test_expense_request_annulment \
  apps.operations.tests.test_expense_request_concurrency \
  apps.operations.tests.test_expense_request_services \
  apps.operations.tests.test_expense_request_balances \
  apps.operations.tests.test_expense_lifecycle \
  apps.operations.tests.test_services \
  apps.operations.tests.test_concurrency \
  apps.operations.tests.test_roles \
  --noinput
```

Cobertura focalizada:

* saldos reservation-aware y selectores sin multiplicación por join;
* create / update / withdraw / deny / approve;
* cumplimiento exacto/parcial, eventos y rollbacks;
* anulación administrativa pendiente/reservada;
* anulación de gasto enlazado sin recrear reserva;
* rechazo de `create_expense()` público;
* `ExpenseForm` solo edición con choices de asignación alineados a elegibilidad
  operativa canónica (`test_expense_form_allocation_choices`);
* concurrencia PostgreSQL (fulfill vs fulfill/annul/bypass/approval);
* regresión de ciclo de vida de `Expense` y roles.

`test_expense_request_concurrency` se omite bajo SQLite (`skipUnless`).

### Solicitudes de gasto — UI de listado/detalle (ER3A), solicitante (ER3B), decisión del Comité (ER4A), anulación administrativa (ER4B) y cumplimiento (ER5)

```bash
python manage.py test \
  apps.operations.tests.test_expense_request_fulfillment_ui \
  apps.operations.tests.test_expense_request_admin_annul_ui \
  apps.operations.tests.test_expense_request_committee_ui \
  apps.operations.tests.test_expense_request_requester_ui \
  apps.operations.tests.test_expense_request_forms \
  apps.operations.tests.test_expense_request_views \
  apps.operations.tests.test_expense_request_ui \
  apps.operations.tests.test_expense_request_permissions \
  apps.operations.tests.test_expense_request_fulfillment \
  apps.operations.tests.test_expense_request_services \
  apps.operations.tests.test_expense_request_balances \
  apps.operations.tests.test_expense_lifecycle \
  apps.operations.tests.test_role_based_ui \
  apps.operations.tests.test_permissions \
  web.tests.test_dashboard \
  --noinput
```

Cobertura focalizada:

* selector de visibilidad por permisos (no por nombre de rol);
* listado compartido con filtros/paginación y default pendiente del Comité;
* detalle con resumen financiero, timeline y acciones del solicitante;
* creación global (Admin) y desde proyecto (Admin/Operador);
* edición/retiro solo del solicitante original en `PENDING_DECISION`;
* defensa de POST forzado (asignación ajena, requester/status/código);
* aprobación/denegación del Comité con reserva atómica y motivo obligatorio;
* exclusión de Admin/Operador/Auditor de las rutas de decisión;
* anulación administrativa (Admin) de pendientes y aprobadas-reservadas;
* motivo obligatorio; liberación de reserva; preservación del historial de decisión;
* saldo/estado obsoleto y fallos de evento/auditoría sin escrituras parciales;
* cumplimiento UI (Admin): exacto/parcial, soporte obligatorio, rollbacks;
* retiro de CTAs de creación directa de `Expense` y redirección de `expense_create`;
* ítem de sidebar entre Asignaciones y Gastos;
* regresión de navegación, permisos y panel.

ER5 no cubre contadores nuevos en el dashboard.

### Solicitudes de gasto — adjuntos protegidos (ER6)

```bash
python manage.py test \
  apps.operations.tests.test_expense_request_attachments_ui \
  apps.operations.tests.test_expense_request_attachment_files \
  apps.operations.tests.test_protected_file_preview \
  apps.operations.tests.test_expense_request_ui \
  apps.operations.tests.test_expense_request_permissions \
  apps.operations.tests.test_expense_request_views \
  apps.operations.tests.test_expense_request_requester_ui \
  apps.operations.tests.test_expense_request_committee_ui \
  apps.operations.tests.test_expense_request_admin_annul_ui \
  apps.operations.tests.test_expense_request_fulfillment_ui \
  apps.operations.tests.test_role_based_ui \
  apps.operations.tests.test_permissions \
  --noinput
```

Cubre:

* upload/delete solo del solicitante original en `PENDING_DECISION`;
* congelación tras aprobación/denegación/retiro/anulación/cumplimiento;
* preview/download protegidos con alcance del padre visible;
* Operador ajeno → 404; sin permiso de vista → 403; anónimo → login;
* sin URLs `/media/` directas; MIME whitelist; compensación de huérfanos;
* regresión ER3–ER5 (Editar/Retirar, Aprobar/Denegar, Anular, Registrar gasto).

### Solicitudes de gasto — cierre de módulo y dashboard (ER7)

```bash
python manage.py test \
  web.tests.test_dashboard \
  apps.operations.tests.test_role_based_ui \
  apps.operations.tests.test_expense_request_fulfillment_ui \
  apps.operations.tests.test_expense_request_ui \
  apps.operations.tests.test_expense_request_views \
  apps.operations.tests.test_expense_request_permissions \
  apps.operations.tests.test_expense_request_attachments_ui \
  apps.operations.tests.test_internal_experience \
  --noinput
```

Cubre:

* atajos de dashboard por permisos efectivos (Admin / Operador / Comité);
* Auditor sin bloque de accesos rápidos; sidebar de solicitudes intacto;
* ausencia de CTAs `Crear gasto` / `Nuevo gasto` / `expense_create` activos;
* filtros `status=approved_reserved` y `status=pending_decision` en atajos;
* etiquetas canónicas: «Solicitudes de gasto», «Solicitar gasto», «Registrar gasto»;
* regresión de navegación y experiencia interna.

Suite completa (PostgreSQL, sin sustituir por SQLite):

```bash
python manage.py test --noinput
```

Resultado de validación ER7 (PostgreSQL `test_db_sigedon`):

* 1562 tests;
* OK;
* 0 skips reportados por el runner en la corrida de cierre;
* duración registrada ~796 s (no normativa);
* teardown: Destroying test database OK;
* exit code 0.

Migración de cierre descubierta en la suite: `operations.0030_expense_request_event_expense_protect`
(`ExpenseRequestEvent.expense` `SET_NULL` → `PROTECT`). Aplicar solo en bases
autorizadas; no se ejecuta automáticamente contra `db_sigedon` en este
checkpoint.

ER7 no añade contadores financieros de solicitudes, descarga ZIP, antivirus ni
versionado de adjuntos.

### Concurrencia

```bash
python manage.py test apps.operations.tests.test_concurrency
```

### Portal público

```bash
python manage.py test apps.public_portal.tests
```

### Integración KoboToolbox

```bash
python manage.py test apps.integrations.kobo.tests
```

Las rutas de módulos de prueba deben ajustarse si la organización interna cambia.

## 6. Verificación de migraciones

Ejecutar:

```bash
python manage.py makemigrations --check --dry-run
```

### Resultado esperado

```text
No changes detected
```

Si se detectan cambios:

* revisar los modelos modificados;
* verificar migraciones no creadas o no versionadas;
* confirmar que no existan diferencias accidentales;
* no generar una migración sin comprender primero su causa.

## 7. Verificación del sistema

Ejecutar:

```bash
python manage.py check
```

### Resultado esperado

```text
System check identified no issues
```

En producción pueden aplicarse verificaciones adicionales según la configuración activa.

## 8. Calidad del diff

Ejecutar:

```bash
git diff --check
```

No debe reportar:

* espacios finales;
* errores de indentación detectables por Git;
* marcadores de conflicto;
* líneas mal formadas.

Esta verificación no sustituye al formateo, al análisis estático ni a la revisión del código.

## 9. Indicadores de campos obligatorios (formularios operativos)

Contrato de la marca `*` en formularios operativos de `templates/web/`:

* la fuente de verdad es `field.field.required` de Django;
* la marca se renderiza únicamente mediante
  `templates/web/includes/ops_form_field_label.html`;
* los campos opcionales y los campos ocultos no reciben marca;
* el marcador usa `aria-hidden="true"` (decorativo para lectores de pantalla);
* las pruebas Django verifican el contrato HTML (etiqueta, clase y `aria-hidden`),
  no el espaciado visual;
* los campos con obligatoriedad condicional solo en `clean()` (por ejemplo
  `ExpenseForm.support_file`) son una preocupación aparte: no exponen
  `required=True` y por tanto no muestran `*` hasta que el formulario Python lo declare.

Módulo focalizado: `web.tests.test_required_field_indicators`.

## 10. Regla para nuevos cambios

Todo cambio funcional debe incluir pruebas sobre los casos relevantes.

Como mínimo, deben considerarse:

* caso válido;
* permiso insuficiente;
* estado inválido;
* validación de datos;
* inmutabilidad;
* auditoría;
* regresión relacionada;
* concurrencia cuando el cambio afecta saldos, reservas o códigos.

No todos los cambios requieren exactamente la misma combinación, pero cualquier exclusión debe ser coherente con el riesgo del cambio.

## 11. Pruebas de permisos

Las funcionalidades restringidas deben comprobar:

* acceso permitido con el permiso correcto;
* respuesta `403` sin autorización;
* ausencia de datos sensibles en el contexto;
* ocultamiento de navegación restringida;
* protección directa de la URL;
* separación entre permisos operativos y permisos `kobo.*`.

Ocultar un botón no constituye una prueba suficiente de autorización.

## 12. Pruebas de acciones terminales

Las acciones de publicación, cierre, anulación, eliminación, rechazo o restauración deben validar:

* uso de `POST`;
* protección CSRF;
* permiso requerido;
* estado inicial válido;
* transición permitida;
* motivo cuando corresponda;
* auditoría;
* bloqueo posterior;
* ausencia de mensajes falsos de éxito.

También debe probarse que la acción no pueda ejecutarse mediante `GET`.

## 13. Pruebas de archivos

Las pruebas de archivos privados deben comprobar:

* autenticación;
* autorización (permisos canónicos, sin nombres de rol hardcodeados);
* pertenencia del archivo al padre (rutas anidadas / querysets acotados);
* preview en línea solo para la lista blanca de extensiones;
* download con `Content-Disposition: attachment` para tipos autorizados;
* cabeceras `X-Content-Type-Options: nosniff` y `Cache-Control: private, no-store`;
* respuesta ante un archivo inexistente (fila o almacenamiento);
* protección frente a URLs directas y ausencia de montaje DEBUG de `MEDIA_ROOT`;
* separación entre archivos públicos y privados;
* privacidad de adjuntos Kobo (preview + download separados);
* comportamiento de archivos asociados a entidades anuladas o no publicadas;
* cobertura de roles (Admin, Operador, Auditor, Comité, anónimo, permiso directo);
* carga múltiple de adjuntos de avance en registro/edición y en la ruta independiente del detalle;
* contrato de widget/plantilla del opt-in `data-file-upload-preview` en los campos de
  adjuntos de `ProjectUpdate` (atributo, contenedores de lista/resumen, help text y exclusión
  de formularios no habilitados);
* contrato de opt-in de un solo archivo en `ProjectDocumentForm.file` (atributo de widget,
  wrapper/list/summary renderizados, ausencia de `multiple`, y remount tras redisplay por
  validación);
* contrato de opt-in de un solo archivo en `ExpenseForm.support_file` y
  `SupportingDocumentForm.document` (atributo de widget, wrapper/list/summary en create/edit
  de gasto y alta standalone de soporte, ausencia de `multiple`, y remount tras redisplay por
  validación);
* contrato de opt-in `ClearableFileInput` en `InstitutionForm.legal_document` (preservación
  del widget, atributo de preview, wrapper/list/summary en create/edit, ausencia de
  `multiple`, controles Django de archivo actual/limpiar en edición con documento, matriz
  servidor preserve/replace/clear/contradiction, y remount tras redisplay por validación);
* contrato de opt-in de un solo archivo en `ProjectUpdateRemediationAttachmentForm.file`
  (atributo de widget, wrapper/list/summary en el alta, ausencia de `multiple`, remount tras
  redisplay por archivo requerido, alta exitosa con auditoría, rechazo en remediaciones no
  borrador y regresión de descarga privada);
* confirmación manual de que quitar la selección pendiente no marca ni desmarca
  `legal_document-clear` y no limpia el archivo persistido;
* inclusión única de `file_upload_preview.js` en `templates/base.html`;
* un evento de auditoría `CREATED` por cada adjunto persistido;
* rechazo de altas o bajas de adjuntos cuando el avance ya está `PUBLISHED`.

Módulo dedicado de preview/download: `apps.operations.tests.test_protected_file_preview`.

La vista previa client-side de selección (fusión incremental o reemplazo de un solo archivo,
miniaturas, `DataTransfer`, enfoque tras quitar y limpieza de object URLs) no está cubierta
por el cliente HTTP de Django; debe validarse manualmente en navegador según la checklist
del cambio correspondiente, incluyendo humo de reemplazo/remoción en `ProjectDocument`,
`ExpenseForm.support_file`, `SupportingDocumentForm.document`,
`InstitutionForm.legal_document` y `ProjectUpdateRemediationAttachmentForm.file`.

## 14. Pruebas de auditoría

Las pruebas sobre `AuditLog` deben comprobar:

* creación durante acciones críticas;
* conservación del actor;
* conservación de la entidad y el resumen;
* atomicidad con la mutación;
* prohibición de edición;
* prohibición de eliminación;
* protección desde el panel de administración;
* ausencia de permisos incompatibles en los roles operativos.

## 15. Pruebas del portal público

El portal debe comprobar:

* publicación exclusiva de proyectos con `ACTIVE` e `is_public=True`;
* inclusión únicamente de avances publicados de esos proyectos;
* exclusión de entidades anuladas;
* exclusión de datos privados;
* persistencia exclusiva de operaciones monetarias en USD;
* ausencia de payloads Kobo;
* ausencia de firmas y documentos privados;
* consistencia entre páginas y respuestas JSON.

## 16. Pruebas de KoboToolbox

La integración debe comprobar:

* autenticación del webhook;
* rechazo de JSON inválido;
* idempotencia;
* conservación del payload original;
* selección del normalizador correcto;
* resolución del proyecto;
* validación de bindings;
* límites de adjuntos;
* privacidad;
* procesamiento;
* reconciliación sin duplicados;
* revisión humana;
* importación;
* rechazo;
* restauración;
* diferenciación entre `KoboProcessingEvent` y `AuditLog`.

Para transporte remoto, use `FakeResponse`, `SequenceTransport` y
`RecordingSleeper` en `apps.integrations.kobo.tests.helpers`: el adapter espera
`status_code`, `body`, metadatos opcionales y `headers`; no `response.json()` ni
`raise_for_status()`. Las secuencias deben representar cada intento, por ejemplo
`[500, 500, 200]`, y comprobar número exacto de llamadas y delays.

Las pruebas de locking real de Kobo están marcadas explícitamente con
`Requires PostgreSQL row-level locking` en `test_concurrency.py` y las clases
concurrentes de importación, priorización y routing. No se validan con SQLite.

## 17. Flujo recomendado antes de integrar cambios

Ejecutar:

```bash
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test
git diff --check
```

Cuando el cambio afecte concurrencia o comportamiento específico de PostgreSQL, la suite correspondiente debe ejecutarse contra PostgreSQL.

## 18. Criterio de aceptación

Un cambio se considera listo cuando:

* las pruebas nuevas cubren el comportamiento incorporado;
* las pruebas de regresión continúan aprobadas;
* no existen migraciones pendientes;
* `python manage.py check` no reporta incidencias;
* `git diff --check` no reporta errores;
* las pruebas concurrentes relevantes pasan en PostgreSQL;
* la documentación refleja el comportamiento real.

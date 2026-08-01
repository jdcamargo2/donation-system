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

## 9. Regla para nuevos cambios

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

## 10. Pruebas de permisos

Las funcionalidades restringidas deben comprobar:

* acceso permitido con el permiso correcto;
* respuesta `403` sin autorización;
* ausencia de datos sensibles en el contexto;
* ocultamiento de navegación restringida;
* protección directa de la URL;
* separación entre permisos operativos y permisos `kobo.*`.

Ocultar un botón no constituye una prueba suficiente de autorización.

## 11. Pruebas de acciones terminales

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

## 12. Pruebas de archivos

Las pruebas de archivos privados deben comprobar:

* autenticación;
* autorización;
* pertenencia del archivo;
* respuesta ante un archivo inexistente;
* protección frente a URLs directas;
* separación entre archivos públicos y privados;
* privacidad de adjuntos Kobo;
* comportamiento de archivos asociados a entidades anuladas o no publicadas;
* carga múltiple de adjuntos de avance en registro/edición y en la ruta independiente del detalle;
* contrato de widget/plantilla del opt-in `data-file-upload-preview` en los campos de
  adjuntos de `ProjectUpdate` (atributo, contenedores de lista/resumen, help text y exclusión
  de formularios no habilitados);
* contrato de opt-in de un solo archivo en `ProjectDocumentForm.file` (atributo de widget,
  wrapper/list/summary renderizados, ausencia de `multiple`, y remount tras redisplay por
  validación);
* inclusión única de `file_upload_preview.js` en `templates/base.html`;
* un evento de auditoría `CREATED` por cada adjunto persistido;
* rechazo de altas o bajas de adjuntos cuando el avance ya está `PUBLISHED`.

La vista previa client-side de selección (fusión incremental o reemplazo de un solo archivo,
miniaturas, `DataTransfer`, enfoque tras quitar y limpieza de object URLs) no está cubierta
por el cliente HTTP de Django; debe validarse manualmente en navegador según la checklist
del cambio correspondiente, incluyendo humo de reemplazo/remoción en `ProjectDocument`.

## 13. Pruebas de auditoría

Las pruebas sobre `AuditLog` deben comprobar:

* creación durante acciones críticas;
* conservación del actor;
* conservación de la entidad y el resumen;
* atomicidad con la mutación;
* prohibición de edición;
* prohibición de eliminación;
* protección desde el panel de administración;
* ausencia de permisos incompatibles en los roles operativos.

## 14. Pruebas del portal público

El portal debe comprobar:

* publicación exclusiva de proyectos con `ACTIVE` e `is_public=True`;
* inclusión únicamente de avances publicados de esos proyectos;
* exclusión de entidades anuladas;
* exclusión de datos privados;
* persistencia exclusiva de operaciones monetarias en USD;
* ausencia de payloads Kobo;
* ausencia de firmas y documentos privados;
* consistencia entre páginas y respuestas JSON.

## 15. Pruebas de KoboToolbox

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

## 16. Flujo recomendado antes de integrar cambios

Ejecutar:

```bash
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test
git diff --check
```

Cuando el cambio afecte concurrencia o comportamiento específico de PostgreSQL, la suite correspondiente debe ejecutarse contra PostgreSQL.

## 17. Criterio de aceptación

Un cambio se considera listo cuando:

* las pruebas nuevas cubren el comportamiento incorporado;
* las pruebas de regresión continúan aprobadas;
* no existen migraciones pendientes;
* `python manage.py check` no reporta incidencias;
* `git diff --check` no reporta errores;
* las pruebas concurrentes relevantes pasan en PostgreSQL;
* la documentación refleja el comportamiento real.

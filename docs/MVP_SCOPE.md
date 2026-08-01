# Alcance del MVP de SIGEDON

## 1. Propósito

El MVP de SIGEDON permite registrar, controlar, consultar, auditar y publicar información autorizada sobre donaciones monetarias y su ejecución mediante proyectos institucionales.

El MVP no busca cubrir toda la gestión humanitaria, pastoral o administrativa futura. Su alcance se concentra en:

* trazabilidad financiera;
* evidencia documental;
* seguimiento de proyectos;
* auditoría;
* transparencia pública básica.

## 2. Cadena central

```text
Institución donante
→ Donación
→ Asignación de fondos
→ Proyecto
→ Gasto
→ Documento de soporte
→ Avance
→ Evidencia
→ Revisión institucional
→ Auditoría
→ Publicación autorizada
```

## 3. Módulos incluidos

### 3.1. Instituciones

Permite registrar organizaciones con:

* nombre;
* tipo;
* rol institucional;
* país;
* contacto;
* responsable;
* información legal;
* estado operativo.

Roles institucionales soportados:

* donante;
* receptora;
* ejecutora;
* aliada;
* supervisora.

### 3.2. Proyectos

Permite registrar:

* código inmutable;
* nombre;
* descripción;
* objetivo;
* ubicación;
* presupuesto estimado;
* fechas;
* estado operativo;
* visibilidad pública (`is_public`);
* documentos;
* avances;
* resumen financiero;
* levantamientos Kobo asociados.

Estados operativos:

```text
ACTIVE
CLOSED
```

Todo proyecto nuevo se crea automáticamente como `ACTIVE` y privado
(`is_public=False`). El estado no es editable manualmente. No existen estados
`PLANNED`, `SUSPENDED` ni `ANNULLED` para proyecto, ni flujos de activación,
suspensión, reactivación, anulación o reapertura.

La única transición es `ACTIVE` → `CLOSED`, mediante «Terminar proyecto»
(`finish_project()`). Terminar es irreversible, fuerza `is_public=False` y
registra metadatos terminales y auditoría de cierre.

Publicación:

* `is_public` es independiente del estado operativo;
* publicar y retirar del portal son acciones explícitas y auditadas;
* requieren el permiso `operations.manage_project_publication`;
* la visibilidad pública exige `status=ACTIVE` e `is_public=True`.

Los proyectos no pueden eliminarse por la UI operativa, URLs, Django Admin ni
ORM de aplicación. La anulación sigue aplicada a donaciones, asignaciones y
gastos; no a proyectos.

### 3.3a. Solicitudes de gasto

Una solicitud de gasto (`ExpenseRequest`) es el paso gobernado entre asignación
y gasto. La cadena operativa del MVP es:

```text
Donation
→ FundAllocation
→ ExpenseRequest
→ Expense
```

Todo gasto futuro debe originarse en una solicitud aprobada con fondos
reservados. ER2A–ER2E completan reserva, cumplimiento, anulación administrativa
e integración de anulación del gasto enlazado. La UI ordinaria de solicitudes
queda para checkpoints posteriores; ER1 estableció modelos, permisos, evidencias
y eventos inmutables.

Estados:

```text
PENDING_DECISION
APPROVED_RESERVED
DENIED
WITHDRAWN
FULFILLED
ANNULLED
```

No existe estado `DRAFT`. Los adjuntos de solicitud son opcionales, distintos de
`SupportingDocument`, y se congelan al salir de `PENDING_DECISION`.

### 3.3. Donaciones

Incluye:

* código inmutable;
* institución donante;
* tipo;
* monto;
* moneda;
* objetivo;
* restricciones;
* fecha de compromiso;
* fecha de recepción;
* referencia documental;
* estado;
* saldo y progreso derivados.

Estados:

```text
REGISTERED
RECEIVED
ANNULLED
```

El nivel de asignación no es un estado editable. Se calcula a partir de las asignaciones no anuladas.

### 3.4. Asignaciones de fondos

Una asignación distribuye fondos desde una donación hacia un proyecto.

Incluye:

* código inmutable;
* donación;
* proyecto;
* categoría presupuestaria;
* monto;
* responsable;
* fecha;
* notas;
* gastos;
* saldo;
* progreso de ejecución.

Estados:

```text
ACTIVE
FINISHED
ANNULLED
```

La ejecución parcial o completa se calcula automáticamente.

### 3.5. Gastos

Un gasto representa una ejecución monetaria previamente autorizada fuera del sistema.
En la cadena gobernada, todo gasto futuro debe proceder de una `ExpenseRequest`
aprobada y reservada. `create_expense()` público rechaza la creación directa;
el camino canónico es `fulfill_expense_request`. `Expense` conserva únicamente:

```text
REGISTERED
ANNULLED
```

No se introducen estados de aprobación o pendiente en `Expense`.

Incluye:

* código inmutable;
* asignación;
* fecha;
* categoría;
* monto;
* proveedor o receptor;
* motivo;
* método de pago;
* referencia;
* observaciones;
* soporte obligatorio;
* estado.

Estados:

```text
REGISTERED
ANNULLED
```

El registro de gasto permanece como ejecución contable; la decisión de aprobación
vive en `ExpenseRequest`, no en `Expense`.

### 3.6. Avances

Flujo del avance:

```text
UNPUBLISHED
→ PUBLISHED
```

Incluye:

* proyecto;
* título;
* descripción;
* fecha real;
* creador técnico;
* persona responsable del contenido del avance;
* evidencias;
* publicación.

Un avance se registra inicialmente como **No publicado** (`UNPUBLISHED`): visible
internamente, editable por usuarios autorizados, admite adjuntos y no aparece en
el portal público. La publicación es una transición explícita; un avance
publicado es inmutable, elegible para revisión y visible públicamente solo si el
proyecto está activo y marcado como público.

El progreso operativo del proyecto no se captura en el avance; se deriva de hitos verificables.

### 3.7. Revisión institucional

La revisión no forma parte del estado del avance.

Se registra mediante entidades separadas:

```text
ProjectUpdate
→ ProjectUpdateReview
→ ProjectUpdateReviewDecision
```

Un avance debe estar publicado para ser revisado.

Resultados posibles:

```text
CONFORMING
OBSERVED
```

### 3.8. Documentos

El MVP separa:

* documentos propios del proyecto;
* adjuntos de avances;
* soportes financieros;
* adjuntos Kobo.

Los archivos privados se previsualizan y descargan mediante endpoints autorizados
(parent-scoped). No se exponen vía `FileField.url` ni montaje público de `MEDIA_ROOT`.

### 3.9. Auditoría

Incluye:

* actor;
* acción;
* entidad;
* identificador;
* resumen;
* fecha y hora.

`AuditLog` es append-only dentro de Django.

No se permite:

* edición;
* eliminación;
* mutación mediante el panel de administración;
* modificación mediante servicios ordinarios.

### 3.10. Portal público

Incluye:

* proyectos con `status=ACTIVE` e `is_public=True`;
* avances publicados de esos proyectos;
* métricas agregadas;
* JSON público autorizado;
* navegación pública.

No expone:

* usuarios;
* notas internas;
* payloads Kobo;
* auditoría;
* documentos privados;
* donaciones individuales;
* gastos individuales;
* firmas;
* datos anulados.

### 3.11. KoboToolbox

El MVP soporta directamente:

* Ficha 1;
* Ficha 10;
* Ficha 11.

Incluye:

* descubrimiento;
* configuración;
* recepción;
* normalización;
* asociación;
* revisión;
* importación;
* rechazo;
* restauración;
* reconciliación;
* gestión de adjuntos.

Las fichas 2 a 9 no se importan directamente dentro del MVP.

## 4. Roles incluidos

### 4.1. Administrador SIGEDON

Gestiona el dominio operativo completo, excepto la mutación del registro de auditoría.

### 4.2. Operador de campo

Puede:

* consultar proyectos;
* consultar avances;
* registrar avances;
* cargar adjuntos durante el registro;
* consultar y registrar soportes autorizados;
* gestionar remediaciones propias según el flujo.

No puede:

* publicar avances;
* editar avances después del registro;
* gestionar finanzas;
* revisar, decidir ni resolver en nombre del Comité.

### 4.3. Auditor externo

Puede consultar:

* instituciones;
* proyectos;
* donaciones;
* asignaciones;
* gastos;
* soportes;
* avances;
* auditoría.

No puede modificar información.

### 4.4. Comité de proyectos

Un único rol funcional. El mismo rol puede revisar, decidir y resolver
remediaciones según el estado del flujo; no son roles separados.

Puede:

* consultar proyectos y avances;
* consultar documentos y evidencias;
* registrar una revisión;
* registrar una decisión institucional;
* resolver remediaciones cuando el flujo lo permita.

No puede modificar el contenido original del avance.

Los permisos exactos están en [Roles y permisos](ROLES_AND_PERMISSIONS.md).

### 4.5. Administración técnica de Kobo

Utiliza permisos `kobo.*` separados de los roles operativos.

## 5. Reglas financieras

### 5.1. Saldo de donación

```text
Saldo disponible
=
Monto recibido
-
Asignaciones no anuladas
```

### 5.2. Saldo de asignación

```text
Saldo disponible
=
Monto asignado
-
Gastos no anulados
```

### 5.3. Reglas obligatorias

* La asignación no es un gasto.
* Los saldos no se guardan como campos editables.
* Los saldos no se calculan únicamente en JavaScript.
* Las operaciones críticas utilizan transacciones.
* Las reservas concurrentes utilizan bloqueos de filas en PostgreSQL.
* Los registros anulados no cuentan en métricas ni saldos.
* USD es la moneda operativa del MVP.
* SIGEDON admite exclusivamente USD; PostgreSQL rechaza cualquier otra moneda.

## 6. Códigos operativos

Formatos:

```text
PRJ-000001
DON-000001
ASG-000001
SGS-000001
GAS-000001
```

Los códigos:

* son únicos;
* son inmutables;
* se reservan transaccionalmente;
* no dependen del conteo de filas;
* requieren secuencias inicializadas.

## 7. Acciones terminales

Cerrar, anular o eliminar (según la entidad) requiere:

* solicitud `POST`;
* permiso;
* confirmación;
* validación de dominio;
* motivo, cuando corresponda;
* auditoría;
* bloqueo posterior, cuando aplique.

Para `Project`, la única acción terminal operativa es terminar
(`ACTIVE` → `CLOSED`). Un proyecto no se anula ni se elimina. Anular y eliminar
siguen aplicando a otras entidades operativas cuando el dominio lo permite.

## 8. Eliminaciones protegidas

Las relaciones protegidas deben producir mensajes comprensibles.

Nunca deben mostrarse al usuario:

* tracebacks;
* nombres internos de modelos;
* errores SQL;
* mensajes falsos de éxito.

## 9. Exclusiones

Quedan fuera del MVP:

* inteligencia artificial;
* chat;
* tareas y cronogramas avanzados;
* gestión completa de beneficiarios;
* donaciones en especie completas;
* distribución física;
* firma digital;
* pagos electrónicos;
* API pública sofisticada;
* autenticación externa;
* mapas territoriales generales;
* aprobación multinivel de gastos;
* hash encadenado de auditoría;
* almacenamiento WORM;
* importación directa de las fichas Kobo 2 a 9.

## 10. Definición de terminado

El MVP se considera cerrado cuando:

* los formularios guardan y validan correctamente;
* las fechas persisten;
* los códigos son seguros e inmutables;
* los saldos se protegen transaccionalmente;
* las acciones terminales están protegidas;
* los archivos privados requieren autorización;
* la auditoría es append-only;
* los avances se registran y publican;
* el Comité de proyectos (un rol) puede revisar, decidir y resolver remediaciones;
* el portal publica únicamente datos autorizados;
* las fichas 1, 10 y 11 funcionan;
* PostgreSQL está soportado;
* no existen migraciones pendientes;
* la suite automatizada está verde;
* la documentación corresponde al comportamiento real.

## 11. Control de alcance

Toda nueva funcionalidad debe clasificarse como una de las siguientes categorías:

### `MVP-BLOCKER`

Impide una tarea esencial o amenaza la integridad de los datos.

### `MVP-REQUIRED`

Pertenece al contrato aprobado del MVP.

### `POST-MVP`

Es una mejora futura que no impide operar el sistema actual.

No se incorpora una funcionalidad únicamente porque resulte interesante o conveniente.

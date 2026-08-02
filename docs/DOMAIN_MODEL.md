# Modelo de dominio de SIGEDON

Este documento describe las principales entidades del dominio de SIGEDON, sus responsabilidades, estados y reglas de negocio.

## 1. `Institution`

Representa una organización participante dentro del sistema.

### Roles institucionales

```text
DONOR
RECEIVER
EXECUTOR
ALLY
SUPERVISOR
```

### Estados

```text
ACTIVE
INACTIVE
```

### Reglas y relaciones

* Puede relacionarse con donaciones.
* Puede asumir responsabilidades institucionales sobre proyectos y avances.
* Su rol define la función que cumple dentro de la operación.
* Una institución inactiva se conserva como registro histórico.

## 2. `Project`

Las relaciones históricas `KoboProjectBinding` no determinan proyectos en el
runtime. La asociación Kobo vigente se resuelve por zona pastoral e identidad
territorial de cada submission.

Representa un proyecto, programa o línea de acción.

### Estados

```text
ACTIVE
CLOSED
```

Estado por defecto: `ACTIVE`.

No existen estados `PLANNED`, `SUSPENDED` ni `ANNULLED` para `Project`.

### Publicación

Campo independiente del estado operativo:

```text
is_public = False | True
```

Valor por defecto: `False` (privado).

Estado operativo y visibilidad pública son dimensiones separadas. Solo un
proyecto `ACTIVE` puede publicarse. Un proyecto `CLOSED` nunca es público:
`finish_project()` / «Terminar proyecto» fuerza `is_public=False`.

Los selectores del portal público exigen ambas condiciones:

```text
status = ACTIVE
AND
is_public = True
```

### Ciclo de vida

```text
ACTIVE → CLOSED
```

| Transición | Cómo ocurre | Reversible |
| --- | --- | --- |
| `ACTIVE` → `CLOSED` | Solo mediante `finish_project()` / «Terminar proyecto» | No |

Reglas del ciclo:

* Todo proyecto nuevo nace `ACTIVE` y privado (`is_public=False`).
* El estado no es editable manualmente (formularios ordinarios, dropdown genérico o Admin).
* No hay flujos de activación, suspensión, reactivación, anulación ni reapertura.
* Terminar es terminal e irreversible: fija `status=CLOSED`, `is_public=False`, metadatos terminales (`terminal_at`, `terminal_by`, `terminal_reason`) y una entrada de auditoría de cierre.
* Un proyecto solo puede cerrarse cuando no tiene asignaciones `ACTIVE` y no existen solicitudes de gasto abiertas (`PENDING_DECISION` o `APPROVED_RESERVED`) en su alcance. El cierre nunca finaliza ni anula automáticamente asignaciones ni solicitudes: el usuario debe resolverlas de forma explícita. La guarda se aplica de forma transaccional en `finish_project`.
* Un `Project` no puede anularse.
* Un `Project` no puede eliminarse por la UI operativa, URLs, Django Admin, `Project.delete()` ni `Project.objects...delete()`. La administración directa de base de datos queda fuera de la garantía de aplicación.

### Reglas operativas

* Solo los proyectos activos pueden recibir nuevas asignaciones, avances y mutaciones de hitos.
* Los gastos y avances de proyecto requieren un proyecto `ACTIVE`.
* Un proyecto cerrado es inmutable para cambios operativos ordinarios.
* La selección operativa Kobo usa proyectos `ACTIVE`; la visibilidad pública no afecta elegibilidad financiera ni Kobo interna.
* El presupuesto estimado no puede ser negativo.
* La fecha final no puede ser anterior a la fecha inicial.
* El código operativo es único e inmutable.
* El monto financiado se deriva únicamente de las asignaciones no anuladas financiadas en USD.
* El monto ejecutado se deriva únicamente de gastos efectivos en USD sobre donaciones USD.
* PostgreSQL impide monedas distintas de USD en donaciones y gastos.
* Los proyectos cerrados no deben recibir nuevas operaciones incompatibles con su estado.

### 2.1. `ProjectMilestone`

Representa un resultado verificable ordenado dentro de un proyecto.

Reglas:

* Sus posiciones son consecutivas desde 1 y únicas dentro del proyecto.
* El progreso se deriva de hitos completados sobre el total; no se persiste en `Project`.
* Un proyecto sin hitos tiene progreso indefinido.
* Completar o reabrir hitos no modifica `Project.status`.
* Los proyectos cerrados conservan sus hitos visibles, pero no admiten mutaciones.
* Completar exige un actor autenticado y conserva fecha y actor mientras este exista.
* Si el actor se elimina posteriormente, `completed_by` puede quedar en `NULL` sin perder la fecha ni la completitud histórica.
* Crear, editar, completar, reabrir, eliminar y reordenar se ejecutan mediante servicios atómicos y auditados.

## 3. `Donation`

Representa una donación monetaria.

### Estados

```text
REGISTERED
RECEIVED
ANNULLED
```

### Reglas

* El monto debe ser positivo.
* La moneda es estrictamente USD; no existe conversión monetaria.
* El código operativo es único e inmutable.
* PostgreSQL impide monedas distintas de USD.
* El monto de la donación no puede ser inferior a la suma de sus asignaciones no anuladas (`ACTIVE` y `FINISHED` cuentan; `ANNULLED` no cuenta). La invariante se aplica de forma transaccional en el servicio (`update_donation` / `create_donation`), con validación defensiva en formulario y modelo; no existe un `CheckConstraint` cruzado.
* Solo instituciones `ACTIVE` pueden figurar como donante en una donación nueva. Un donante histórico `INACTIVE` puede permanecer en ediciones existentes, pero no puede sustituirse por otra institución inactiva.
* Las asignaciones no anuladas no pueden exceder el monto disponible.
* Una donación anulada queda excluida de métricas y saldos operativos.
* El KPI global de dashboard **Fondos recibidos** cuenta solo `RECEIVED`
  (no `REGISTERED`).
* El nivel de asignación se calcula a partir de sus asignaciones y no se almacena como estado editable.
* `DonationAdmin` es de solo lectura (sin alta, cambio ni borrado), para impedir bypass del servicio.

## 4. `FundAllocation`

Distribuye fondos desde una donación hacia un proyecto.

### Estados

```text
ACTIVE
FINISHED
ANNULLED
```

### Reglas

* El monto asignado debe ser positivo.
* No puede superar el saldo disponible de la donación.
* No puede utilizar una donación anulada.
* Los gastos no anulados no pueden superar el monto asignado.
* El saldo disponible resta gastos efectivos **y** reservas activas
  (`ExpenseRequest.APPROVED_RESERVED`); la ejecución (`executed_amount`) y la
  reserva permanecen conceptos distintos.
* Una asignación anulada no participa en métricas ni saldos.
* La ejecución parcial o completa se deriva de los gastos asociados.
* Una asignación no debe interpretarse como un gasto.
* Finalizar (`FINISHED`) exige que no existan solicitudes `PENDING_DECISION` ni
  `APPROVED_RESERVED`, ni reservas activas. El histórico terminal (`FULFILLED`,
  `DENIED`, `WITHDRAWN`, `ANNULLED`) y los gastos ya ejecutados no bloquean el
  cierre. La finalización no anula ni modifica solicitudes; la guarda vive en
  `finish_fund_allocation` bajo bloqueo transaccional.
* Las transiciones terminales de `FundAllocation` usan acciones dedicadas
  (`finish_fund_allocation` / `annul_fund_allocation`). No hay cambio genérico
  de estado para asignaciones.

## 5a. `ExpenseRequest`

Representa una solicitud de gasto gobernada sobre una asignación de fondos.

### Estados

```text
PENDING_DECISION
  → APPROVED_RESERVED
  → FULFILLED

PENDING_DECISION
  → DENIED

PENDING_DECISION
  → WITHDRAWN

PENDING_DECISION / APPROVED_RESERVED
  → ANNULLED
```

### Reglas

* El monto solicitado debe ser positivo.
* El código operativo `SGS-######` es único e inmutable.
* La moneda se deriva de la asignación/donación (USD); no hay columna `currency`.
* Los adjuntos (`ExpenseRequestAttachment`) son opcionales y mutables solo en
  `PENDING_DECISION`.
* `ExpenseRequestEvent` es append-only y complementa `AuditLog`.
* Servicios ER2A–ER2E (`apps/operations/expense_request_services.py`):
  * creación → `PENDING_DECISION` (sin reserva);
  * edición / retiro → solo solicitante original y solo pendiente;
  * denegación / aprobación → solo `decide_expenserequest` (Comité);
  * aprobación reserva atómicamente `requested_amount`;
  * cumplimiento (`fulfill_expense_request`) → `FULFILLED` + `Expense` enlazado;
  * anulación administrativa (`annul_expense_request`) → `ANNULLED` con liberación
    de reserva si estaba aprobada.
* La anulación del `Expense` enlazado deja la solicitud en `FULFILLED` y emite
  `LINKED_EXPENSE_ANNULLED` sin recrear reserva.
* Agregación autoritativa de reservas: `get_allocation_reserved_amount()` en
  `apps/operations/financials.py` (solo `APPROVED_RESERVED`).
* Resumen financiero interno de proyecto (detalle autenticado y bloque
  DASH-FIN3 del dashboard): Fondos asignados, Gastos registrados, Reservado,
  Disponible operativo = `max(asignados − gastos − reservado, 0)`. Visible solo
  con `view_fundallocation` **y** `view_expense` (misma regla en detalle y
  dashboard); sin ambos permisos el resumen no se calcula ni se añade al
  contexto. El portal público conserva su propio resumen sin restar reservas.

## 5. `Expense`

Representa un gasto registrado contra una asignación de fondos.

### Estados

```text
REGISTERED
ANNULLED
```

### Reglas

* El monto debe ser positivo.
* Debe pertenecer a una asignación operativa.
* No puede superar el saldo disponible de la asignación (ejecutado + reservas
  activas).
* Debe contar con soporte documental obligatorio.
* La anulación requiere una justificación.
* El código operativo es único e inmutable.
* Los gastos anulados no participan en saldos ni métricas.
* Todo gasto futuro debe originarse en una `ExpenseRequest` aprobada y reservada.
* `create_expense()` público rechaza la creación directa ordinaria; el camino
  canónico es `fulfill_expense_request` (primitiva `_create_expense_locked`).
* `create_expense_legacy()` queda solo para tests/importaciones controladas.
* `ExpenseForm` edita gastos existentes: las opciones de asignación siguen
  elegibilidad operativa canónica y conservan la asignación histórica actual;
  la validación de escritura la impone `update_expense`.
* Reasignación (`update_expense` cuando cambia el pk de asignación): el destino
  debe cumplir la misma regla estructural que `operational_fund_allocation_choices`
  (`validate_fund_allocation_for_new_operational_use`) y capacidad de saldo;
  la asignación histórica sin cambio puede permanecer en ediciones de otros
  campos. Gastos materializados desde `ExpenseRequest` no se reasignan a otra
  asignación.

## 6. `SupportingDocument`

Representa evidencia documental asociada principalmente a una operación financiera.

### Información conservada

* archivo;
* tipo;
* referencia;
* descripción;
* actor responsable;
* fecha de registro.

### Reglas

* Se vincula principalmente con gastos.
* La descarga requiere autorización.
* El archivo no debe exponerse mediante una URL pública directa.
* Debe conservarse la trazabilidad de su carga y consulta.

## 7. `ProjectUpdate`

Representa un avance de proyecto.

### Estados

```text
UNPUBLISHED
PUBLISHED
```

### Actores

* `created_by`: usuario autenticado que registró técnicamente el avance.
* `reported_by`: persona responsable del contenido al que se atribuye el avance.

### Reglas

* Solo puede crearse para proyectos activos.
* Un avance nuevo queda en `UNPUBLISHED` (No publicado): registrado internamente,
  editable por usuarios autorizados, admite adjuntos y no aparece en el portal
  público.
* La publicación constituye una transición explícita a `PUBLISHED`.
* Un avance publicado es inmutable, elegible para revisión y visible
  públicamente solo si el proyecto está activo y marcado como público.
* El creador técnico y la persona responsable del avance representan responsabilidades diferentes.
* Para el Operador de campo, al crear un avance ambos coinciden con el actor autenticado; Administrador/superusuario pueden seleccionar otro `reported_by` elegible.
* La revisión institucional no altera el estado del avance.
* `ProjectUpdate` no almacena porcentaje de progreso; el progreso operativo del proyecto se deriva de hitos (`ProjectMilestone`).

## 8. `ProjectDocument`

Representa documentos generales asociados a un proyecto.

### Tipos mínimos

```text
PROPOSAL
WORK_PLAN
ACTION_PLAN
REPORT
OTHER
```

### Reglas

* Debe vincularse a un proyecto.
* No debe confundirse con una evidencia de avance.
* Puede contener documentación institucional, técnica o de planificación.
* Su visibilidad debe definirse de manera explícita.

## 9. `ProjectUpdateAttachment`

Representa una evidencia o archivo adjunto asociado a un avance de proyecto.

### Reglas

* Debe pertenecer a un avance.
* Su descarga requiere autorización.
* No debe exponerse directamente mediante `FileField.url`.
* Debe conservar la trazabilidad del archivo y de su carga.
* La publicación del avance no implica automáticamente que todos sus adjuntos sean públicos.

## 10. `ProjectUpdateReview`

Representa la revisión institucional de un avance publicado.

### Reglas

* Mantiene una relación uno a uno con `ProjectUpdate`.
* Solo puede crearse sobre avances publicados.
* Conserva las observaciones del revisor.
* Conserva el usuario responsable de la revisión.
* Conserva la fecha de revisión.
* No cambia el estado del avance.
* No modifica el contenido original del avance.

## 11. `ProjectUpdateReviewDecision`

Representa el resultado institucional asociado a una revisión.

### Resultados

```text
CONFORMING
OBSERVED
```

### Reglas

* Mantiene una relación uno a uno con `ProjectUpdateReview`.
* Conserva el fundamento de la decisión.
* Conserva el actor responsable.
* Conserva la fecha de la decisión.
* No modifica el contenido original del avance.
* No sustituye ni elimina la revisión que le sirve de base.

## 12. `AuditLog`

Representa el registro append-only de acciones críticas del sistema.

### Información conservada

* usuario;
* acción;
* modelo o tipo de entidad;
* identificador de la entidad;
* etiqueta;
* resumen;
* fecha y hora.

### Restricciones

Está prohibido:

* editar registros;
* eliminar registros;
* mutarlos desde el panel de administración;
* alterarlos mediante servicios ordinarios;
* reutilizarlos como registros operativos editables.

`AuditLog` registra acciones funcionales relevantes y no debe confundirse con los eventos técnicos de procesamiento de integraciones.

## 13. `OperationalCodeSequence`

Mantiene secuencias transaccionales para códigos operativos.

### Namespaces

```text
project
donation
fund_allocation
expense
```

### Prefijos

```text
PRJ
DON
ASG
GAS
```

### Reglas

* Cada namespace mantiene su propia secuencia.
* Los valores se reservan transaccionalmente.
* La generación no depende del conteo de filas.
* Los códigos resultantes son únicos e inmutables.
* Las secuencias deben encontrarse correctamente inicializadas.
* Un rollback reutiliza el número reservado; una eliminación posterior al commit
  puede dejar huecos.
* El padding de seis dígitos es un mínimo, no un límite máximo.
* Los códigos manuales se reservan para seeds o migraciones controladas;
  `QuerySet.update()` y SQL directo omiten la inmutabilidad del modelo.
* Tras restaurar un backup, `reconcile_operational_code_sequences` verifica sin
  reparar que cada `next_value` sea mayor al máximo canónico persistido.
  Una secuencia adelantada es válida; una ausente, igual o menor es insegura.

## 14. Modelos de integración con KoboToolbox

### 14.1. `KoboFormDefinition`

Define un formulario Kobo y la versión soportada por SIGEDON.

Responsabilidades:

* identificar el tipo de ficha;
* declarar la versión compatible;
* definir reglas de normalización;
* asociar el formulario con el procesamiento correspondiente.

### 14.2. `KoboAsset`

Representa un activo remoto de Kobo configurado dentro del sistema.

Reglas:

* puede activarse o desactivarse;
* debe relacionarse con una definición compatible;
* conserva información necesaria para sincronización y recepción;
* determina qué activos participan en el pipeline ordinario.

### 14.3. `KoboDiscoveredAsset`

Representa un activo encontrado durante el proceso de descubrimiento.

Responsabilidades:

* inventariar activos remotos;
* conservar información técnica básica;
* permitir su revisión antes de configurarlos;
* evitar que el descubrimiento implique una activación automática.

### 14.4. `KoboProjectBinding`

Registro histórico de configuraciones asset-proyecto. No participa en el
runtime ni recibe escrituras nuevas: Fichas 1, 10 y 11 resuelven su proyecto
por zona pastoral e identidad territorial de la submission. La tabla se
conserva hasta auditar los datos persistentes.

### 14.5. `KoboSubmission`

Almacena el payload recibido desde Kobo y su estado de procesamiento.

Responsabilidades:

* conservar el payload original;
* mantener el estado del pipeline;
* registrar intentos y resultados;
* permitir revisión humana;
* soportar rechazo, restauración e importación;
* evitar la pérdida de información original.

### 14.6. `KoboTerritorialIdentity`

Representa el código territorial canónico, su zona pastoral y su proyecto. El
routing de Ficha 1 crea o confirma esta identidad; la importación no crea una
segunda identidad ni cambia su asignación territorial.

Estados administrativos: `PENDING_REVIEW`, `ACTIVE`, `OBSERVED` e `INACTIVE`.
La inactivación no libera `nucleo_code_normalized` ni elimina historia.

### 14.6.1. `KoboPastoralZoneProjectMapping`

Configura explícitamente una de las cinco zonas pastorales hacia un `Project`.
La unicidad parcial permite un solo mapping activo por zona. Las desactivaciones
conservan actor, fecha y razón; el servicio impide cambiar o desactivar mappings
usados por identidades.

### 14.6.2. `KoboTerritorialIdentityConflict`

Conserva identidad, submission entrante, zona/proyecto existentes y propuestos.
Una decisión humana motivada puede conservar lo existente, aceptar una propuesta
sin historia incompatible o descartar un conflicto técnico. No elimina evidencia
ni reasigna materializaciones importadas.

### 14.6.3. `KoboTerritorialAdministrationEvent`

Evento append-only y libre de payload para las mutaciones territoriales. Conserva
actor, acción, entidad, estado anterior, estado posterior, motivo y timestamp; no
sustituye al `AuditLog` creado en la misma transacción.

### 14.7. `KoboTerritorialProfile`

Representa el diagnóstico aprobado e inmutable materializado desde una Ficha 1.
Cada submission puede originar un solo perfil, mientras una identidad puede
tener varios perfiles históricos. El perfil vigente se deriva por fecha y no se
guarda como puntero redundante.

```text
KoboTerritorialIdentity 1 ──< KoboTerritorialProfile
Project 1 ──< KoboTerritorialProfile
KoboSubmission 1 ── 1 KoboTerritorialProfile
KoboImportRecord 1 ── referencia lógica ──> KoboTerritorialProfile
```

El perfil conserva la ubicación canónica como JSON validado y los campos
revisados del payload normalizado, pero no duplica código ni zona pastoral.

### 14.8. `KoboPrioritizedMicroproject`

Representa una propuesta priorizada, aprobada e inmutable materializada desde
una Ficha 10. No es otro `Project`: pertenece a la identidad territorial y al
proyecto Núcleo Vital ya resuelto por routing.

```text
KoboTerritorialIdentity 1 ──< KoboPrioritizedMicroproject
Project 1 ──< KoboPrioritizedMicroproject
KoboSubmission 1 ── 1 KoboPrioritizedMicroproject
KoboImportRecord 1 ── referencia lógica ──> KoboPrioritizedMicroproject
```

Cada submission produce como máximo un microproyecto. Dos submissions con el
mismo nombre conservan dos propuestas históricas distintas: el nombre no es una
clave de deduplicación. `component`, `estimated_cost_range`,
`implementation_urgency` y `technical_viability` conservan códigos canónicos de
catálogo; `beneficiary_group` conserva el `select_multiple` como lista JSON
ordenada y `main_activities` permanece texto libre.

La propuesta no crea presupuesto ejecutable, donaciones, asignaciones, gastos ni
movimientos financieros, y tampoco activa ni modifica la identidad territorial.

### 14.9. `KoboPrioritizationAssessment`

Representa una evaluación territorial histórica, aprobada e inmutable,
materializada desde una Ficha 11. Cada submission produce como máximo una
evaluación, mientras una identidad conserva todas sus evaluaciones históricas.

```text
KoboTerritorialIdentity 1 ──< KoboPrioritizationAssessment
Project 1 ──< KoboPrioritizationAssessment
KoboSubmission 1 ── 1 KoboPrioritizationAssessment
KoboImportRecord 1 ── referencia lógica ──> KoboPrioritizationAssessment
```

La evaluación conserva individualmente los diez scores de 1 a 5, el total y
semáforo sugerido recibidos, el total y semáforo recalculados por SIGEDON, y las
decisiones humanas `final_semaphore` y `final_priority`. Las discrepancias se
guardan como warnings estructurados y no sustituyen la decisión humana.

`linked_microprojects_snapshot` conserva el texto libre normalizado recibido.
No crea relaciones por coincidencia de nombre ni modifica microproyectos. La
evaluación tampoco cambia el estado o la prioridad institucional del proyecto,
la identidad territorial, presupuestos ni movimientos financieros.

### 14.10. `KoboImportRecord`

Registra el resultado único de una importación completada y apunta lógicamente
a la entidad materializada sin introducir una relación genérica en la submission.

### 14.11. `KoboAttachment`

Representa un archivo adjunto descargado desde Kobo.

Reglas:

* debe vincularse con una submission;
* conserva metadatos técnicos;
* incluye una política explícita de privacidad;
* su descarga debe estar protegida;
* no se publica automáticamente en el portal público.

### 14.12. `KoboProcessingEvent`

Representa un evento técnico ocurrido durante el procesamiento de una submission.

Responsabilidades:

* registrar etapas del pipeline;
* conservar errores y resultados técnicos;
* apoyar la trazabilidad de la integración;
* permitir el diagnóstico de fallos;
* conservar metadata estructurada limitada a identificadores no sensibles.

`KoboProcessingEvent` no sustituye a `AuditLog`.

La diferencia principal es:

* `AuditLog` registra acciones funcionales y de negocio;
* `KoboProcessingEvent` registra eventos técnicos del pipeline Kobo.
* `KoboTerritorialAdministrationEvent` registra decisiones administrativas
  territoriales sin depender de una submission concreta.

## 15. Modelos heredados de Ficha 1

### `Ficha01Territorio`

Representa información territorial normalizada por el flujo legado inicial de la Ficha 1.

### `Ficha01CoveredCommunity`

Representa comunidades cubiertas asociadas al flujo legado de la Ficha 1.

### Condición actual

Ambos modelos:

* forman parte del schema legado del primer flujo de Ficha 1;
* se conservan temporalmente por compatibilidad histórica;
* no tienen escritores activos conocidos y no son utilizados por el pipeline
  vigente;
* no son la fuente de verdad activa: el staging vigente reside en
  `KoboSubmission`;
* no deben recibir nuevas integraciones sin una decisión arquitectónica
  explícita;
* solo podrán eliminarse tras una decisión de producto y una migración
  específica.

# Decisiones técnicas de SIGEDON

## 2026-07-20 — Retiro del binding Kobo en runtime

`KoboProjectBinding` queda deprecado y conservado como dato histórico hasta una
auditoría persistente. Fichas 1, 10 y 11 se enrutan exclusivamente por
submission mediante `route_normalized_submission()`; no existe fallback a
binding ni se eliminan datos históricos en esta fase.

Este documento registra las principales decisiones arquitectónicas, funcionales y operativas adoptadas durante el desarrollo de SIGEDON.

## 2026-07-08 — Django y Bootstrap

### Decisión

SIGEDON utiliza Django con templates del servidor y Bootstrap para la interfaz.

### Motivo

Esta combinación reduce la complejidad técnica, evita introducir un frontend desacoplado innecesario y facilita la entrega de un MVP mantenible.

### Consecuencias

* La interfaz se renderiza principalmente desde Django.
* No se requiere un proceso de compilación con Node.js.
* La lógica crítica permanece en el backend.
* La arquitectura prioriza simplicidad y mantenibilidad.

---

## 2026-07-08 — Ciudad no obligatoria en `Institution`

### Decisión

El modelo `Institution` conserva el país, pero no exige una ciudad como campo estructural obligatorio.

### Motivo

La ciudad no es necesaria para todas las instituciones y su obligatoriedad introduciría datos incompletos o artificiales.

### Consecuencias

* El país permanece como información geográfica principal.
* La ubicación más específica puede mantenerse en otros campos cuando sea necesaria.
* No se obliga a registrar una ciudad inexistente o desconocida.

---

## 2026-07-10 — USD como moneda operativa

### Decisión

El MVP opera financieramente en USD.

### Motivo

El uso de una única moneda operativa evita introducir conversiones, tasas de cambio y reglas contables que están fuera del alcance del MVP.

### Consecuencias

* Las métricas financieras operativas utilizan USD.
* Los registros históricos en otras monedas no se convierten automáticamente.
* Los registros no expresados en USD se excluyen de métricas agregadas cuando corresponda.
* La conversión multimoneda queda fuera del MVP.

> Sustituida por la decisión de 2026-07-15: SIGEDON ya no conserva monedas históricas distintas de USD.

---

## 2026-07-11 — PostgreSQL obligatorio en producción

### Decisión

PostgreSQL es el motor de base de datos obligatorio en producción.

SQLite queda limitado al desarrollo local con `DEBUG=True`.

### Motivo

La integridad concurrente de SIGEDON depende de transacciones, bloqueos reales de filas y comportamiento consistente bajo operaciones simultáneas.

### Consecuencias

* Las operaciones críticas utilizan `select_for_update()`.
* Las pruebas concurrentes relevantes deben ejecutarse sobre PostgreSQL.
* SQLite no se considera suficiente para validar sobreasignaciones, sobre-ejecuciones ni reservas simultáneas de códigos.
* La configuración de producción debe rechazar SQLite.

---

## 2026-08-01 — Solicitudes de gasto (Expense Request) como paso gobernado

### Decisión

`ExpenseRequest` es una entidad separada de `Expense`. No existe estado `DRAFT`.
El prefijo operativo es `SGS` (`namespace=expense_request`). Los adjuntos de
solicitud se congelan al salir de `PENDING_DECISION`. La trazabilidad combina
eventos estructurados e inmutables (`ExpenseRequestEvent`) con `AuditLog`.
Administrador SIGEDON puede crear solicitudes pero no decidirlas
(`decide_expenserequest` exclusividad del Comité). La edición y el retiro quedan
limitados al creador original mientras la solicitud esté `PENDING_DECISION`
(enforcement de ownership en servicios ER2B). La entrada directa ordinaria de
gastos se retira progresivamente como parte del flujo gobernado.

### Motivo

Separar la decisión/reserva de la ejecución contable evita contaminar `Expense`
con estados de aprobación y permite trazabilidad financiera explícita.

### Consecuencias

* Cadena: Donation → FundAllocation → ExpenseRequest → Expense.
* `Expense` conserva solo `REGISTERED` / `ANNULLED`.
* ER1 entrega modelos, constraints, permisos, secuencia `SGS` y trigger
  append-only.
* ER2A–ER2C implementan agregación de reservas, saldo disponible
  reservation-aware, y servicios de creación/edición/retiro/denegación/aprobación
  con reserva atómica.
* ER2D–ER2E implementan cumplimiento (`FULFILLED` → `Expense`), anulación
  administrativa de solicitudes, integración de anulación del gasto enlazado y
  retiro del `create_expense()` público como camino ordinario.
* ER5 completa la UI de cumplimiento y retira los puntos de entrada ordinarios
  de creación directa de `Expense` (la ruta legacy redirige a solicitudes
  aprobadas-reservadas).
* ER6 añade adjuntos protegidos (mutación solo en pendiente; preview/download
  autorizados; sin `/media/` directo).
* ER7 cierra el módulo: atajos de dashboard por permisos efectivos, auditoría de
  navegación/acciones por rol, reconciliación documental y suite completa
  PostgreSQL. Sin contadores agregados nuevos, ZIP, antivirus ni versionado.
* Defecto de cierre ER7: el FK `ExpenseRequestEvent.expense` pasa de `SET_NULL`
  a `PROTECT`. El trigger append-only es `FOR EACH STATEMENT` y rechazaba
  incluso el `UPDATE` vacío que Django emite al borrar un `Expense` no
  enlazado; `PROTECT` evita esa mutación y bloquea borrados duros de gastos
  referenciados por historial de solicitud.

---

## 2026-08-01 — Reserva financiera en aprobación de solicitud

### Decisión

La creación de una `ExpenseRequest` no reserva fondos ni altera saldos. Solo la
aprobación del Comité (`decide_expenserequest`) transiciona
`PENDING_DECISION → APPROVED_RESERVED`, fija `reserved_amount = requested_amount`
y reduce el saldo disponible de la asignación.

El saldo disponible de `FundAllocation` es:

```text
amount − executed_amount − active_reservations
```

donde `active_reservations` suma `reserved_amount` de solicitudes en
`APPROVED_RESERVED`. No se almacena un total de reserva en `FundAllocation`.

### Motivo

Separar la solicitud informativa de la reserva financiera permite al Comité
actuar como única puerta de capacidad, sin bloquear fondos en borradores
pendientes, y evita doble conteo entre ejecución y reserva.

### Consecuencias

* Pendiente, denegada, retirada, anulada o cumplida no cuentan como reserva.
* La creación directa pública de `Expense` está retirada; el legado
  `create_expense_legacy` tampoco puede consumir fondos ya reservados.
* Aprobación, eventos (`APPROVED`, `RESERVATION_CREATED`) y `AuditLog` son
  atómicos bajo bloqueo `Donation → FundAllocation → Project → ExpenseRequest`.
* ER2D–ER2E: cumplimiento exacto/parcial, anulación administrativa de
  solicitudes, anulación de gasto enlazado sin recrear reserva, y gobernanza de
  `create_expense()`.

## 2026-07-11 — Códigos operativos transaccionales

### Decisión

Los proyectos, donaciones, asignaciones, solicitudes de gasto y gastos utilizan secuencias operativas bloqueadas transaccionalmente.

### Formatos

```text
PRJ-000001
DON-000001
ASG-000001
SGS-000001
GAS-000001
```

### Motivo

La generación basada en el conteo de filas produce colisiones y resultados inseguros bajo concurrencia.

### Consecuencias

* Los códigos son únicos.
* Los códigos son inmutables.
* La generación no depende del número de registros existentes.
* Las secuencias deben inicializarse correctamente.
* La reserva del código forma parte de la transacción de creación.

---

## 2026-07-11 — Progreso financiero derivado

### Decisión

El nivel de asignación de una donación y el nivel de ejecución de una asignación se calculan a partir de montos persistidos.

No se almacenan como estados editables.

### Motivo

Mantener estados manuales de progreso duplicaría información y permitiría inconsistencias con los montos reales.

### Consecuencias

* El progreso de una donación se deriva de sus asignaciones no anuladas.
* El progreso de una asignación se deriva de sus gastos no anulados.
* El saldo disponible de una asignación resta además las reservas activas
  (`ExpenseRequest` en `APPROVED_RESERVED`).
* Los saldos se calculan dinámicamente.
* Los registros anulados no participan en los cálculos.
* La interfaz no puede modificar manualmente el nivel de progreso financiero.

---

## 2026-07-11 — Gasto simplificado

### Decisión

Un gasto representa una operación monetaria ya autorizada fuera de SIGEDON.

Su ciclo de vida es:

```text
REGISTERED
→ ANNULLED
```

### Motivo

La aprobación multinivel de gastos está fuera del alcance del MVP.

### Consecuencias

* SIGEDON registra y controla la ejecución, pero no sustituye el proceso externo de autorización.
* No existen estados internos de aprobación, rechazo o revisión financiera.
* La anulación requiere una justificación.
* Los gastos anulados dejan de participar en saldos y métricas.

---

## 2026-07-11 — Avance simplificado

### Decisión

El ciclo de vida de un avance es:

```text
DRAFT
→ PUBLISHED
```

> **Superseded 2026-08-01:** the preliminary state label is now
> `UNPUBLISHED` / No publicado. The lifecycle and publication approval remain
> unchanged; see the decision dated 2026-08-01.

### Motivo

El flujo anterior incorporaba estados de revisión y aprobación que aumentaban la complejidad y mezclaban responsabilidades distintas.

### Consecuencias

* Los avances se registran inicialmente como borradores.
* La publicación es una transición explícita.
* Un avance publicado es inmutable.
* La revisión institucional se registra mediante entidades separadas.
* No existen transiciones de aprobación o rechazo sobre el propio avance.

---

## 2026-07-12 — Revisión separada del avance

### Decisión

La revisión del Comité no modifica el estado ni el contenido del avance.

Se representa mediante:

```text
ProjectUpdate
→ ProjectUpdateReview
→ ProjectUpdateReviewDecision
```

### Motivo

El avance constituye un registro institucional original que debe permanecer inmutable después de su publicación.

### Consecuencias

* `ProjectUpdateReview` conserva observaciones, revisor y fecha.
* `ProjectUpdateReviewDecision` conserva resultado, fundamento, actor y fecha.
* La decisión puede ser `CONFORMING` u `OBSERVED`.
* La revisión no altera el estado `PUBLISHED`.
* El contenido original del avance permanece intacto.

---

## 2026-07-12 — Creador técnico y persona responsable del avance separados

### Decisión

Los avances diferencian entre:

```text
created_by
→ usuario autenticado que registra el avance

reported_by
→ persona responsable del contenido del avance
```

### Motivo

La persona que introduce la información en el sistema no necesariamente es la responsable del contenido del avance reportado.

### Consecuencias

* `created_by` se asigna automáticamente desde `request.user`.
* `reported_by` se selecciona explícitamente.
* La auditoría puede identificar al operador técnico.
* La atribución de la persona responsable se conserva de forma independiente.
* No deben utilizarse ambos campos como si representaran la misma responsabilidad.

---

## 2026-08-01 — Operador de campo: created_by y reported_by coinciden al crear

### Decisión

Para el rol funcional **Operador de campo**, la creación de un avance de
proyecto exige:

```text
created_by == reported_by == actor autenticado
```

El campo `Persona responsable del avance` permanece visible en el formulario,
pero en modo no editable, con el operador autenticado preasignado.

La delegación de `reported_by` a otro usuario elegible permanece disponible
para Administrador SIGEDON y superusuario.

### Motivo

El Operador de campo registra avances sobre trabajo propio; no debe poder
atribuir la responsabilidad del contenido a otro usuario mediante el formulario
ni mediante un POST manipulado.

### Consecuencias

* El servicio `register_advance` resuelve el reporter de forma autoritativa.
* Un POST forjado con otro `reported_by` no altera la atribución del Operador.
* `created_by` y `reported_by` siguen siendo conceptos distintos en el modelo;
  coinciden por regla de dominio solo en la creación por Operador de campo.
* La edición de avances no publicados por Administrador (cambio de responsable) no cambia.

---

## 2026-07-12 — Auditoría append-only

### Decisión

Los registros de `AuditLog` no pueden editarse ni eliminarse mediante Django.

### Motivo

La auditoría debe conservar evidencia estable de las acciones críticas realizadas en el sistema.

### Consecuencias

* Se bloquea la actualización de registros existentes.
* Se bloquea la eliminación.
* El panel de administración no permite mutaciones.
* Los roles operativos no reciben permisos de creación, edición ni eliminación.
* Las acciones críticas crean auditoría dentro de la misma transacción cuando corresponde.

### Limitación

El MVP no incluye hash encadenado, almacenamiento WORM ni firma criptográfica de eventos.

---

## 2026-07-12 — Permisos Kobo separados

### Decisión

Los permisos técnicos de KoboToolbox no se asignan automáticamente al Administrador SIGEDON.

### Motivo

La operación institucional y la administración técnica de la integración representan responsabilidades diferentes.

### Consecuencias

* Los permisos `kobo.*` deben asignarse explícitamente.
* El Administrador SIGEDON puede gestionar el dominio operativo sin acceder al payload crudo.
* El personal técnico puede gestionar activos y submissions sin recibir automáticamente control financiero.
* Se aplica separación de responsabilidades y mínimo privilegio.

---

## 2026-07-13 — Dashboard autorizado por bloque

### Decisión

El dashboard permanece disponible para usuarios autenticados, pero consulta y muestra únicamente la información autorizada para cada usuario.

### Motivo

Ocultar elementos visuales sin filtrar las consultas permitiría enviar información sensible al contexto del template.

### Consecuencias

* Cada bloque verifica el permiso correspondiente.
* Las métricas no autorizadas no se consultan.
* Los datos restringidos no se envían al template.
* La navegación se adapta a los permisos.
* Un usuario autenticado sin permisos puede acceder únicamente a un dashboard básico.

---

## 2026-07-13 — Retiro de aplicaciones vacías

### Decisión

Se retiraron las siguientes aplicaciones:

```text
apps.users
apps.integrations.payments
```

### Motivo

No contenían implementación funcional y mantenían una estructura que podía sugerir capacidades inexistentes.

### Consecuencias

* Se reduce ruido arquitectónico.
* La documentación refleja únicamente módulos activos.
* Las funcionalidades futuras no se representan mediante aplicaciones vacías.
* Una nueva aplicación deberá incorporarse cuando exista una responsabilidad funcional real.

---

## 2026-07-15 — USD estricto con integridad en PostgreSQL

### Decisión

SIGEDON admite exclusivamente USD. Las columnas `Donation.currency` y
`Expense.currency` se conservan como unidad explícita, pero PostgreSQL impide
cualquier valor distinto de `USD`.

### Motivo

Una única unidad monetaria elimina reglas de conversión y evita que datos
financieros incompatibles alcancen los agregados, servicios o exportaciones.

### Consecuencias

* No existe multimoneda, tasas de cambio ni conversión de importes.
* Los formularios y el admin no permiten elegir moneda.
* Los servicios rechazan moneda distinta de USD antes de persistir.
* Los constraints de PostgreSQL son la garantía final de integridad.

---

## 2026-08-01 — Renombre del estado preliminar de avance

### Decisión

The ProjectUpdate preliminary state is renamed from DRAFT/Borrador to
UNPUBLISHED/No publicado. The lifecycle and publication approval remain
unchanged.

```text
UNPUBLISHED
→ PUBLISHED
```

### Motivo

This is a semantic clarification, not immediate publication. The preliminary
state remains internally visible and editable before an explicit Admin
publication step. Existing documentation that said publication required
`change_projectupdate` is corrected: publication requires
`publish_projectupdate`.

### Consecuencias

* Newly created advances remain `UNPUBLISHED` / No publicado.
* No publicado: registrado internamente; editable por usuarios autorizados;
  admite adjuntos; no aparece en el portal público.
* Publicado: inmutable; elegible para revisión; visible públicamente solo si el
  proyecto está activo y marcado como público.
* Admin retains publication responsibility; Operator cannot publish.
* `created_by` / `reported_by` semantics and the Operator self-report rule are
  unchanged.
* Remediation `DRAFT` / Borrador semantics are unchanged.
* Historical decision text describing the prior DRAFT label remains as
  superseded context under 2026-07-11.

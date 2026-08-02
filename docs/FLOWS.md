# Flujos funcionales de SIGEDON

Este documento describe los principales flujos operativos de SIGEDON, incluyendo sus precondiciones, pasos, resultados y restricciones.

## 1. Flujo de proyecto

```text
Create Project
    ↓
ACTIVE + Private
    ├── Edit
    ├── Publish
    │      ↓
    │   ACTIVE + Public
    │      └── Unpublish → ACTIVE + Private
    └── Finish
           ↓
       CLOSED + Private
```

### PRE

* El usuario posee permisos para gestionar proyectos.
* Los datos obligatorios del proyecto están disponibles.
* Las fechas y el presupuesto cumplen las reglas del dominio.

### Pasos

1. El usuario registra el proyecto.
2. El sistema reserva un código operativo con prefijo `PRJ`.
3. El proyecto se crea automáticamente como `ACTIVE` y privado (`is_public=False`).
   No hay selector genérico de estado ni de publicación en el formulario ordinario.
4. Mientras permanece `ACTIVE` puede editarse, recibir documentos, asignaciones,
   avances e hitos.
5. Publicar en portal / retirar del portal (`publish_project` /
   `unpublish_project`) son acciones `POST` con CSRF, permiso
   `operations.manage_project_publication` y auditoría. Solo un proyecto
   `ACTIVE` puede publicarse.
6. Terminar proyecto (`finish_project`) es `POST` confirmado, irreversible y
   terminal: pasa a `CLOSED`, fuerza `is_public=False`, escribe metadatos
   terminales y audita el cierre. Solo procede cuando todas las asignaciones del
   proyecto están `FINISHED` o `ANNULLED` y no queden solicitudes de gasto
   abiertas (`PENDING_DECISION` / `APPROVED_RESERVED`). No finaliza ni anula
   automáticamente registros hijos; el usuario debe resolver el trabajo
   financiero pendiente antes. La validación corre bajo
   `Donation → FundAllocation → Project → ExpenseRequest`.
7. No existen flujos de suspensión, reactivación, anulación ni eliminación de
   proyecto. Las acciones críticas generan auditoría.
8. La exportación CSV del listado (`project_export_csv` y equivalentes de
   donación, asignación y gasto) reutiliza el mismo queryset filtrado del
   listado y neutraliza prefijos de fórmula de hoja de cálculo en texto
   controlado por el usuario; montos y códigos operativos conservan su
   representación numérica o generada habitual.

### POST

* El proyecto conserva un código único e inmutable.
* Un proyecto activo queda disponible para las operaciones permitidas.
* Un proyecto cerrado queda inmutable para cambios operativos ordinarios y
  privado respecto al portal.
* Las acciones realizadas conservan trazabilidad.

### 1.1. Flujo de hitos verificables

1. El servicio bloquea el proyecto y sus hitos mediante `select_for_update()`.
2. Solo un proyecto `ACTIVE` admite mutaciones de hitos; un proyecto `CLOSED`
   rechaza toda mutación.
3. Crear añade un hito pendiente al final; eliminar compacta las posiciones.
4. Completar y reabrir actualizan únicamente el estado y metadatos del hito.
5. Reordenar intercambia hitos adyacentes mediante una posición temporal libre.
6. La mutación y sus eventos de auditoría se confirman en la misma transacción.
7. Solo se audita el proyecto cuando el progreso derivado cruza hacia o desde el 100 %.

El progreso por hitos nunca modifica `Project.status` ni se almacena como porcentaje persistido.

### Hub territorial Kobo

Las Fichas 1, 10 y 11 nunca usan `KoboProjectBinding`: cada submission pasa
por `route_normalized_submission()` y un formulario no soportado queda con
`UNSUPPORTED_FORM`, sin fallback genérico. El Hub territorial es la superficie
operativa para mappings e identidades.

El Hub exige `kobo.view_territorial_administration`. Operador de campo queda
excluido de ese acceso. Los cambios de mappings, estado y conflictos exigen
POST con motivo; la reconciliación opera un lote y nunca aprueba ni importa
submissions.

La interfaz HTTP mantiene las mutaciones en la capa de servicios y exige el
permiso específico del hito junto con `operations.view_project`:

| Operación | Métodos HTTP | Permiso específico |
| --- | --- | --- |
| Crear | `GET`, `POST` | `add_projectmilestone` |
| Editar | `GET`, `POST` | `change_projectmilestone` |
| Completar | `POST` | `complete_projectmilestone` |
| Reabrir | `GET` de confirmación, `POST` | `complete_projectmilestone` |
| Eliminar | `GET` de confirmación, `POST` | `delete_projectmilestone` |
| Subir o bajar | `POST` | `reorder_projectmilestone` |

Los formularios no exponen proyecto, posición ni metadatos de finalización. Un
`GET` nunca completa, reabre, elimina ni reordena hitos, y todo `POST` requiere
protección CSRF.

El detalle interno presenta una checklist ordenada y calcula su progreso con
`get_milestone_progress()` sobre hitos precargados. Los proyectos cerrados
conservan esa información como registro histórico, pero no muestran acciones
de mutación.

En navegadores con JavaScript, HTMX usa un contrato de fragmentos según la
operación: completar y reabrir sustituyen solo la fila afectada y actualizan el
resumen y la barra mediante OOB; reordenar sustituye solo la lista; eliminar
sustituye la lista compactada y actualiza el progreso mediante OOB. Ninguna de
estas acciones reconstruye la sección completa. SweetAlert2 confirma reapertura
y eliminación, y `HX-Trigger` transporta el toast sin almacenar un mensaje
duplicado. Sin JavaScript se conservan los mismos `POST`, los redirects con
ancla y las páginas de confirmación.

El piloto usa HTMX 2.0.10 vendorizado en `static/vendor/htmx/htmx.min.js` y se
carga solo desde el detalle interno del proyecto. El archivo fue verificado
contra el SHA-384 oficial
`H5SrcfygHmAuTDZphMHqBJLc3FhssKjG7w/CeCpFReSfwBWDTKpkzPP8c+cLsK+V`.

---

## 2. Flujo de donación

### PRE

* Existe una institución donante en estado `ACTIVE`.
* El monto es positivo.
* La moneda es USD y las fechas son válidas.

### Pasos

1. El usuario registra la donación mediante la UI operativa (no el admin).
2. El servicio `create_donation` valida el donante activo, reserva un código `DON` y persiste la fila.
3. La donación queda inicialmente en estado `REGISTERED`.
4. Cuando se confirma la recepción, puede pasar a `RECEIVED`.
5. Sus fondos pueden distribuirse mediante asignaciones.
6. El progreso de asignación se calcula automáticamente.
7. Una edición ordinaria (`update_donation`) bloquea la fila, exige que el monto no sea inferior al total de asignaciones no anuladas (`ACTIVE`/`FINISHED`) y aplica la regla de donante histórico/inactivo.
8. La donación puede anularse cuando las reglas del dominio lo permitan.
9. Las acciones críticas generan exactamente una auditoría por mutación exitosa.

### POST

* La donación conserva un código único e inmutable.
* El saldo disponible se deriva de las asignaciones no anuladas.
* Una donación anulada queda excluida de las métricas operativas.
* En el dashboard global, el KPI **Fondos recibidos** suma solo donaciones en
  estado `RECEIVED` (moneda operativa). `REGISTERED` no cuenta.
* **Fondos sin asignar** = recibidos − asignaciones no anuladas (mínimo 0);
  las reservas de solicitudes no entran en este KPI.
* Instituciones inactivas siguen visibles en donaciones históricas, pero no financian actividad nueva.

### Regla monetaria

SIGEDON opera exclusivamente en USD. `Donation.currency` y `Expense.currency`
solo admiten `USD`, y PostgreSQL impone esta restricción. El sistema no realiza
conversiones ni utiliza tasas de cambio. `EUR`, `VES` y `COP` solo pueden
aparecer en migraciones históricas o en pruebas negativas que verifican su
rechazo.

---

## 3. Flujo de asignación de fondos

### PRE

* La donación está operativa.
* La donación posee saldo disponible.
* Existe un proyecto de destino válido.
* El monto solicitado es positivo.
* El usuario posee permisos para registrar asignaciones.

### Pasos

1. El usuario selecciona la donación y el proyecto de destino.
2. El sistema inicia una transacción.
3. La fila de la donación se bloquea mediante `select_for_update()`.
4. Se recalcula el saldo disponible.
5. Se valida que el monto solicitado no supere el saldo.
6. El sistema reserva un código operativo con prefijo `ASG`.
7. Se registra la asignación.
8. Se crea el registro de auditoría.
9. La transacción se confirma de forma atómica.
10. Finalizar asignación (`finish_fund_allocation`) es `POST` confirmado y
    terminal hacia `FINISHED`. Bloquea si existen solicitudes
    `PENDING_DECISION` o `APPROVED_RESERVED` (reservas activas). No cancela ni
    anula solicitudes automáticamente. El histórico terminal y los gastos
    ejecutados sí pueden permanecer. Orden de bloqueo:
    `Donation → FundAllocation → Project → ExpenseRequest`.
11. Anular asignación (`annul_fund_allocation`) sigue siendo el flujo terminal
    con motivo hacia `ANNULLED` y no sustituye a la finalización.

### POST

* La suma de las asignaciones no anuladas no supera el monto disponible de la donación.
* La asignación conserva un código único e inmutable.
* El saldo de la donación queda actualizado de forma derivada.
* Una asignación finalizada o anulada deja de admitir nuevas solicitudes de gasto.

---

## 4. Flujo de gasto

### PRE

* La asignación está operativa.
* La asignación posee saldo disponible.
* El monto del gasto es positivo.
* Existe soporte documental obligatorio.
* El usuario posee permisos para registrar gastos.

### Pasos

1. El usuario registra los datos del gasto.
2. El sistema inicia una transacción.
3. La asignación correspondiente se bloquea.
4. Se recalcula el saldo disponible.
5. Se valida que el gasto no supere el saldo.
6. El sistema reserva un código operativo con prefijo `GAS`.
7. Se registra el gasto.
8. Se guarda el soporte documental.
   En los formularios de gasto y de soporte standalone con opt-in de vista previa, la
   selección pendiente es local al cliente y no se sube hasta el submit; los soportes ya
   persistidos no se gestionan desde esa vista previa.
9. Se crea el registro de auditoría.
10. El progreso de ejecución se recalcula de manera derivada.
11. La transacción se confirma de forma atómica.

### POST

* El gasto queda registrado en estado `REGISTERED`.
* El gasto conserva un código único e inmutable.
* El saldo de la asignación refleja gastos no anulados **y** reservas activas
  (`APPROVED_RESERVED`); un gasto directo no puede consumir fondos reservados.
* El gasto puede anularse posteriormente cuando el dominio lo permita.
* Django Admin (`ExpenseAdmin`) es solo inspección: sin alta, cambio, borrado ni
  mutación de inlines de soporte (incluido para superusuarios). La creación
  ocurre solo al cumplir una `ExpenseRequest` aprobada; anulación y demás
  mutaciones pasan por los servicios/UI de SIGEDON. Los documentos soporte
  permanecen protegidos y gestionados por el flujo operativo.
* Django Admin (`SupportingDocumentAdmin` y su inline en `ExpenseAdmin`) es solo
  inspección: sin alta, cambio, borrado, reemplazo ni reasignación (incluido
  para superusuarios). Vista previa y descarga usan rutas protegidas de
  SIGEDON; el Admin no expone URLs directas de media ni altera el ciclo de vida
  del archivo.

---

## 4a. Flujo de solicitud de gasto (ER2A–ER2E)

### Actores

* Creación / edición / retiro: Administrador SIGEDON u Operador de campo (sobre
  solicitudes propias).
* Decisión (aprobar / denegar): Comité de proyectos (`decide_expenserequest`).
* Cumplimiento y anulación administrativa: Administrador SIGEDON
  (`fulfill_expenserequest`, `annul_expenserequest`).

### PRE (creación)

* Asignación, donación y proyecto operativos.
* Monto solicitado positivo; propósito no vacío.
* El actor posee `add_expenserequest`.
* No se exige saldo suficiente en la creación.

### Pasos (creación)

1. El actor registra asignación, monto, propósito y fecha.
2. El sistema bloquea `Donation → FundAllocation → Project`.
3. Valida entidades operativas y monto positivo.
4. Reserva código `SGS-######` y crea la solicitud en `PENDING_DECISION`.
5. Escribe `ExpenseRequestEvent.CREATED` y `AuditLog`.
6. Confirma la transacción. **No hay reserva financiera.**

### PRE (aprobación)

* Solicitud en `PENDING_DECISION`.
* Actor con `decide_expenserequest`.
* Saldo disponible de la asignación ≥ `requested_amount`
  (`amount − ejecutado − otras reservas activas`).

### Pasos (aprobación)

1. Bloqueo canónico: `Donation → FundAllocation → Project → ExpenseRequest`.
2. Recalcula ejecutado y reservas (excluyendo la solicitud actual).
3. Si el saldo es insuficiente: falla sin mutar, sin eventos ni auditoría.
4. Si alcanza: fija `APPROVED_RESERVED`, `reserved_amount = requested_amount`,
   metadatos de decisión/reserva.
5. Escribe `ExpenseRequestEvent.APPROVED` y `RESERVATION_CREATED`.
6. Escribe un `AuditLog` de aprobación/reserva.
7. Todo o nada en una sola transacción.

### Retiro y denegación

* Retiro: solo solicitante original, solo pendiente, motivo terminal obligatorio;
  `PENDING_DECISION → WITHDRAWN`; sin efecto financiero.
* Denegación: solo Comité, motivo obligatorio; `PENDING_DECISION → DENIED`;
  sin efecto financiero.

### Cumplimiento (ER2D)

* Solo `APPROVED_RESERVED` sin gasto enlazado; `0 < amount <= reserved_amount`.
* Bloqueo: `Donation → FundAllocation → Project → ExpenseRequest`.
* Crea `Expense` + `SupportingDocument` vía `_create_expense_locked` con
  `reservation_credit = reserved_amount`.
* Transiciona a `FULFILLED` y enlaza OneToOne; preserva metadatos de reserva.
* Eventos: `EXPENSE_REGISTERED`, `RESERVATION_CONSUMED`, y
  `UNUSED_RESERVATION_RELEASED` solo si hay diferencia positiva.
* Identidad: `available_after = available_before + (reserved − executed)`.

### Anulación administrativa de solicitud (ER2E)

* Admite `PENDING_DECISION` o `APPROVED_RESERVED`; motivo terminal obligatorio.
* Pendiente: sin efecto financiero; solo evento `ANNULLED`.
* Aprobada: libera la reserva completa (`ANNULLED` + `RESERVATION_RELEASED`) y
  preserva historial de decisión/reserva.
* Cumplida / denegada / retirada / anulada: rechazada.

### Anulación del gasto enlazado

* `annul_expense` restaura el saldo del gasto.
* La solicitud permanece `FULFILLED`; no se recrea reserva.
* Emite `LINKED_EXPENSE_ANNULLED` + auditoría de solicitud, además de
  `EXPENSE_CANCELLED` del gasto.

### Gobernanza de creación de gastos

* `create_expense()` público rechaza creación directa ordinaria.
* Camino normal: `fulfill_expense_request`.
* `create_expense_legacy()` solo para tests/importaciones controladas.
* `ExpenseForm` es solo edición de gastos existentes; la ruta `expense_create`
  redirige a la cola de cumplimiento de solicitudes aprobadas.
* Las opciones de reasignación en edición usan elegibilidad operativa canónica
  (`operational_fund_allocation_choices`) y conservan la asignación histórica
  actual del gasto; `update_expense` permanece como autoridad de escritura.
* Reasignación a otra asignación: el destino debe ser estructuralmente elegible
  (asignación `ACTIVE`, proyecto `ACTIVE`, donación `RECEIVED` en USD) y tener
  capacidad financiera; la comprobación es transaccional con bloqueos. Si la
  asignación no cambia, se conserva el vínculo histórico aunque padres pasen a
  estado terminal. Un gasto enlazado a una `ExpenseRequest` cumplida no puede
  moverse a otra asignación. Las restricciones del formulario son UX; el servicio
  rechaza también llamadas directas.

### POST

* Solo `APPROVED_RESERVED` reduce el saldo disponible por reserva.
* `FULFILLED` convierte reserva en ejecución (exacta o parcial).
* ER3A expone listado y detalle (`expense_request_list` /
  `expense_request_detail`) con selector de visibilidad por permisos efectivos:
  Operador ve solo las propias; Admin/Comité/Auditor ven todas. El Comité entra
  por defecto en `pending_decision`.
* ER3B añade el flujo del solicitante:
  * Operador crea desde el detalle del proyecto (`expense_request_create_for_project`)
    solo cuando `expense_request_allocation_choices(project=...)` tiene al menos una
    asignación elegible; si no, el detalle muestra guía neutra sin CTA ejecutable;
  * desde el detalle de una asignación elegible, «Solicitar gasto» abre la misma
    ruta con `?allocation=<pk>`; la preselección se resuelve solo dentro del
    queryset autoritativo del formulario (parámetros manipulados no amplían acceso);
  * el detalle de asignación lista hasta cinco solicitudes visibles para el
    usuario (`visible_expense_requests_for_allocation`), sin mutaciones en esa
    sección; Operador solo ve las propias;
  * si el formulario de creación no tiene asignaciones elegibles, muestra guía y
    omite el botón de envío (la validación POST del backend sigue vigente);
  * Administrador crea desde proyecto o desde el listado global
    (`expense_request_create`);
  * solo el solicitante original edita o retira su solicitud en
    `PENDING_DECISION` (`expense_request_update` / `expense_request_withdraw`);
  * la creación no reserva fondos; el Comité sigue siendo la puerta de reserva;
  * adjuntos permanecen de solo lectura.
* ER4A añade la UI de decisión del Comité:
  * solo `decide_expenserequest` aprueba o deniega (`expense_request_approve` /
    `expense_request_deny`); el Administrador no decide aunque haya creado la
    solicitud;
  * la aprobación reserva atómicamente `requested_amount` y deja la solicitud en
    `APPROVED_RESERVED` sin registrar un `Expense`;
  * la denegación es terminal, exige motivo y no crea reserva;
  * estado no pendiente en GET de acción → 404; errores de servicio (saldo,
    estado obsoleto) se muestran en el formulario sin escrituras parciales.
* ER4B añade la UI de anulación administrativa:
  * solo `annul_expenserequest` anula (`expense_request_annul`); bajo roles
    canónicos es exclusivo del Administrador;
  * admite `PENDING_DECISION` (sin efecto financiero) y `APPROVED_RESERVED`
    (libera la reserva completa; el historial de decisión/reserva se preserva);
  * exige motivo obligatorio; el mensaje de éxito distingue pendiente vs
    reserva liberada según el estado previo a la mutación;
  * estado no anulable en GET → 404; fallos de evento/auditoría se muestran en
    el formulario con rollback completo.
* ER5 añade la UI de cumplimiento (Administrador):
  * solo `fulfill_expenserequest` registra el gasto final (`expense_request_fulfill`);
  * admite únicamente `APPROVED_RESERVED` sin `Expense` enlazado;
  * exige documento soporte obligatorio; monto `0 < amount <= reserved_amount`;
  * cumplimiento exacto no altera el saldo disponible; parcial libera la diferencia;
  * la UI ordinaria de creación directa de `Expense` queda retirada (listado,
    dashboard, detalle de asignación y ruta `expense_create` redirigen o apuntan
    a solicitudes); listado/detalle de gastos históricos permanecen;
  * no hay contadores financieros nuevos de solicitudes en el dashboard.
* ER6 añade adjuntos protegidos de solicitud:
  * el solicitante original puede agregar o eliminar adjuntos solo mientras la
    solicitud está en `PENDING_DECISION`;
  * tras cualquier decisión o cierre (`APPROVED_RESERVED`, `DENIED`,
    `WITHDRAWN`, `FULFILLED`, `ANNULLED`) los adjuntos quedan congelados
    (sin upload ni delete) pero siguen legibles;
  * preview/download van por rutas anidadas autorizadas
    (`expense_request_attachment_preview` / `expense_request_attachment_download`);
  * no hay URLs directas de media ni exposición pública;
  * Admin/Comité/Auditor leen adjuntos de solicitudes visibles; el Operador
    solo las propias.
* ER7 cierra el módulo con navegación de solicitudes por permisos efectivos
  (sidebar y listados; el dashboard ya no expone Accesos rápidos):
  * Admin (`fulfill_expenserequest`): ver solicitudes y aprobadas pendientes de
    registrar gasto;
  * Operador (`view_expenserequest` sin decide/fulfill): mis solicitudes, sin
    atajo global de creación;
  * Comité (`decide_expenserequest`): pendientes de decisión;
  * Auditor: sin bloque de accesos rápidos (sigue el sidebar);
  * sin contadores agregados nuevos ni CTAs de creación directa de `Expense`.
* DASH-FIN2 añade colas de solicitudes en el panel financiero (entre ratios y
  actividad reciente), siempre con el mismo alcance de los selectores
  autorizados:
  * `fulfill_expenserequest` → aprobadas pendientes de registrar gasto;
  * `decide_expenserequest` → pendientes de decisión;
  * superusuario → ambas colas accionables;
  * Operador → solo solicitudes propias activas;
  * Auditor con `view_expenserequest` → seguimiento de solo lectura;
  * los conteos no revelan solicitudes fuera del queryset accesible.
* DASH-FIN3 añade «Estado financiero por proyecto» entre las colas de
  solicitudes y la actividad reciente:
  * lista acotada (máx. 10) en orden estable no ranking (actividad financiera,
    ACTIVE antes que CLOSED, código, pk);
  * métricas reservation-aware: Fondos asignados, Gastos registrados,
    Reservado (`APPROVED_RESERVED`), Disponible operativo
    (`max(asignados − gastos − reservado, 0)`), Ejecución
    (`gastos / asignados`; `None` si asignados = 0);
  * «Ver todos los proyectos» solo cuando hay más de 10;
  * requiere `view_fundallocation` + `view_expense` (Operador/Comité no lo ven);
  * el detalle interno del proyecto exige la misma pareja de permisos antes de
    calcular o exponer el resumen; sin ambos, no hay montos en contexto ni HTML;
  * el portal público no cambia.

---

## 5. Flujo de avance de proyecto

### Actor inicial

Operador de campo o usuario con el permiso `add_projectupdate`.

### PRE

* El proyecto se encuentra en estado `ACTIVE`.
* La fecha del avance es válida.
* Existe una persona responsable del avance: el Operador de campo autenticado se
  asigna automáticamente; Administrador SIGEDON o superusuario seleccionan un
  responsable elegible (activo y con permisos operativos sobre avances).
* El usuario posee permisos para registrar el avance.

### Pasos

1. El usuario registra:

   * título;
   * descripción;
   * fecha real;
   * persona responsable del avance.
2. En el formulario de creación:

   * el Operador de campo ve el campo `Persona responsable del avance` en modo
     no editable (deshabilitado), con su propio usuario asignado automáticamente;
     no puede delegar la responsabilidad a otro usuario;
   * el Administrador SIGEDON (o superusuario) conserva el selector de usuarios
     elegibles y puede atribuir el avance a otra persona responsable.
3. El usuario puede adjuntar una o varias evidencias durante el registro o la edición del avance no publicado.
   En los formularios de avance con opt-in de vista previa, la selección múltiple puede armarse
   de forma incremental en el cliente antes del envío; los archivos no se suben hasta el submit
   y los adjuntos ya persistidos no se gestionan desde esa vista previa.
4. Desde el detalle de un avance en `UNPUBLISHED`, un usuario con `add_projectupdateattachment` puede agregar varios adjuntos en una sola carga.
   El mismo componente de vista previa local aplica en ese formulario independiente.
5. Cada archivo persistido genera su propio evento de auditoría de creación.
6. `created_by` se asigna automáticamente desde el usuario autenticado.
7. `reported_by` conserva la persona responsable del contenido del avance.
   Para el Operador de campo, `created_by` y `reported_by` coinciden con el
   actor autenticado; para Administrador/superusuario pueden diferir cuando se
   delega la atribución.
8. El avance se guarda en estado `UNPUBLISHED`.
9. Un usuario con el permiso `publish_projectupdate` puede iniciar la publicación.
10. El sistema valida nuevamente las condiciones de publicación.
11. El avance pasa a estado `PUBLISHED`.
12. Se registra la auditoría correspondiente.
13. El avance queda bloqueado contra edición y eliminación.
14. Los adjuntos permanecen privados y solo pueden agregarse o eliminarse mientras el avance esté en `UNPUBLISHED`; el avance publicado y sus adjuntos son inmutables.
15. Usuarios autorizados pueden previsualizar (lista blanca) o descargar evidencias
    persistidas mediante endpoints anidados al proyecto/avance; `UNPUBLISHED` vs
    `PUBLISHED` no bloquea la lectura interna autorizada.
16. La vista previa de carga (cliente) no sustituye la vista previa persistida (servidor).

### POST

* El avance publicado es inmutable.
* El creador técnico y la persona responsable del avance quedan diferenciados
  como conceptos; en el registro del Operador de campo coinciden por regla de
  dominio.
* El avance puede ser revisado institucionalmente.
* El avance puede aparecer en el portal público cuando cumpla las reglas de publicación.
* El progreso operativo del proyecto no se captura en el avance; permanece derivado de hitos.
* Los adjuntos no se exponen en el portal público por el solo hecho de publicar el avance.

---

## 6. Flujo de revisión institucional

### PRE

* El avance se encuentra en estado `PUBLISHED`.
* El usuario posee permisos para registrar revisiones.
* El avance todavía no posee una revisión incompatible con la relación uno a uno.

### Pasos

Los actores «revisor» y «usuario autorizado» son acciones dentro del único rol
funcional **Comité de proyectos**; no son roles separados. El estado del flujo
impide secuencias inválidas.

1. Un miembro del Comité de proyectos consulta el avance y sus evidencias.
2. Un actor con permiso de revisión registra sus observaciones.
3. Se crea una instancia de `ProjectUpdateReview`.
4. Se conserva:

   * el revisor;
   * la fecha;
   * las observaciones.
5. Posteriormente, un actor con permiso de decisión (mismo rol funcional)
   registra una decisión.
6. Se crea `ProjectUpdateReviewDecision`.
7. La decisión puede ser:

   * `CONFORMING`;
   * `OBSERVED`.
8. Se conserva:

   * el actor;
   * la fecha;
   * el resultado;
   * el fundamento.
9. La acción genera auditoría.

### POST

* La revisión queda separada del contenido original del avance.
* La decisión no modifica el estado `PUBLISHED`.
* El avance original permanece inmutable.
* La revisión y la decisión conservan trazabilidad institucional.

### Colas de gobernanza en el panel (FLOW-COMMITTEE-QUEUES)

El panel financiero expone una sección **Gobernanza de avances** solo cuando el
usuario tiene al menos uno de los permisos de acción:

* `review_projectupdate` → pendientes de revisión;
* `decide_projectupdate` → pendientes de decisión;
* `resolve_projectupdateremediation` → remediaciones por resolver.

Significado exacto (selectores canónicos, no filtros de plantilla):

* **Pendiente de revisión:** `ProjectUpdate` en `PUBLISHED` sin
  `ProjectUpdateReview` (`committee_review`);
* **Pendiente de decisión:** `ProjectUpdateReview` sin
  `ProjectUpdateReviewDecision`, con avance padre publicado;
* **Remediación por resolver:** `ProjectUpdateRemediation` en `SUBMITTED`
  (DRAFT / ACCEPTED / REJECTED no entran).

Las colas son superficies de descubrimiento/navegación hacia el detalle
autorizado; las vistas de acción y los servicios de dominio siguen siendo la
fuente autoritativa de mutación. Permisos parciales habilitan solo la cola
correspondiente. No hay «Ver todos» porque el listado de avances no expone aún
filtros `workflow` acotados.

---

## 7. Flujo de auditoría

### PRE

* Se ejecuta una acción crítica o relevante para el dominio.

### Pasos

1. La acción pasa por un servicio de dominio.
2. La mutación se ejecuta dentro de una transacción.
3. Se crea un registro `AuditLog`.
4. La mutación y la auditoría se confirman de forma atómica.
5. El registro queda disponible para usuarios autorizados.

### POST

* El registro de auditoría no puede editarse.
* El registro de auditoría no puede eliminarse.
* El panel de administración no permite su mutación.
* La acción conserva actor, entidad, resumen y fecha.

---

## 8. Flujo de publicación pública

### PRE

* La información cumple las reglas de visibilidad pública.
* El proyecto cumple `status=ACTIVE` e `is_public=True`.
* Los avances publicados siguen sujetos a la visibilidad del proyecto padre.
* La entidad financiera no se encuentra anulada cuando aplique.
* Los datos son consultados mediante selectores públicos.

### Pasos

1. El portal consulta exclusivamente selectores y consultas públicas.
2. Listado, detalle, avances y métricas de proyecto exigen
   `ACTIVE` + `is_public=True`.
3. Solo se incluyen avances en estado `PUBLISHED` cuyo proyecto padre sigue
   activo y público.
4. Se excluyen entidades anuladas (donaciones, asignaciones, gastos y demás
   entidades que admiten anulación).
5. Todas las operaciones monetarias publicables ya están expresadas en USD.
6. Se eliminan campos privados o internos.
7. Las respuestas autorizadas pueden almacenarse temporalmente en caché.
8. El portal presenta páginas o respuestas JSON públicas.

### POST

* El portal publica únicamente información autorizada.
* No se exponen datos privados, financieros individuales ni técnicos.
* La capa pública no modifica información operativa.
* Tras publicar, retirar del portal o terminar un proyecto previamente público,
  la aplicación invalida la caché del portal (invalidación amplia del cache
  por defecto; no se garantiza invalidación por clave individual).

---

## 9. Flujo ordinario de KoboToolbox por proyecto

### PRE

* Existe una definición de formulario compatible.
* El activo Kobo ha sido descubierto y configurado.
* El activo se encuentra habilitado.
* Para Ficha 1 existe un mapping de zona pastoral hacia proyecto.
* Para Ficha 10/11 puede existir ya una identidad territorial; su ausencia no
  impide conservar la submission en staging.

### Pasos

1. Un activo remoto de Kobo se descubre.
2. Se registra o asocia su definición compatible.
3. Se configura el activo y, para Ficha 1, el mapping territorial de zona.
4. El activo se activa.
5. Kobo envía un webhook o se ejecuta una sincronización.
6. El sistema crea o actualiza una `KoboSubmission` en staging.
7. Se conserva el payload original.
8. El payload se valida.
9. Se normaliza según la ficha correspondiente:

   * Ficha 1;
   * Ficha 10;
   * Ficha 11.
10. El dispatcher resuelve el proyecto: Ficha 1 crea o confirma la identidad;
    Ficha 10/11 consulta exclusivamente esa identidad por `nucleo_code`.
11. Si una Ficha 10/11 aún no tiene identidad, queda `PENDING_IDENTITY`, sin
    proyecto; no se usa binding como fallback y la incidencia aparece en el hub
    global de incidencias Kobo.
12. Una submission con routing resuelto entra al pipeline automático: se
    auto-aprueba e importa sin cola humana por proyecto.
13. `READY_FOR_REVIEW` permanece como estado interno transitorio del pipeline;
    no representa una bandeja sostenida de revisión en el detalle del proyecto.
14. Las rutas HTTP de revisión/importación/rechazo manual por proyecto están
    deshabilitadas (`Http404`) donde aún existen por compatibilidad.
15. El servicio común bloquea la submission, revalida routing, proyecto,
    normalización, payload preservado, revisión automática y permisos, y
    selecciona el handler cerrado de Ficha 1, 10 u 11.
16. Solo una materialización específica exitosa crea `KoboImportRecord`, cambia
    la submission a `IMPORTED`, completa `processed_at` e `imported_at` y registra
    evento y auditoría.
17. El handler de Ficha 1 localiza la identidad ya creada por routing, valida
    código, zona, proyecto, conflictos y datos normalizados, y crea un
    `KoboTerritorialProfile` inmutable por submission.
18. Si la identidad estaba `PENDING_REVIEW`, pasa a `ACTIVE`; `OBSERVED`
    permanece observada e `INACTIVE` bloquea la importación.
19. El handler de Ficha 10 localiza la identidad sin crearla, valida código,
    proyecto, estado, conflictos, campos requeridos y catálogos, y crea un
    `KoboPrioritizedMicroproject` inmutable por submission.
20. La Ficha 10 conserva `beneficiary_group` como lista canónica y
    `main_activities` como texto libre; no interpreta de nuevo `raw_payload`.
21. El handler de Ficha 11 localiza la identidad sin crearla, valida código,
    proyecto, estado, conflictos, diez scores, cálculos, catálogos, decisiones
    humanas y warnings, y crea un `KoboPrioritizationAssessment` inmutable por
    submission.
22. La Ficha 11 conserva por separado total y semáforo originales, total y
    semáforo recalculados, semáforo final humano y prioridad final. Las
    discrepancias son warnings y `linked_microprojects` permanece como snapshot
    textual, sin relaciones automáticas por nombre.
23. El detalle del proyecto muestra datos importados de Ficha 1/10/11 y el
    historial Kobo del proyecto. El detalle importado usa el contrato de
    presentación compartido: resumen y secciones por dominio, valores de
    catálogo traducidos al español, IDs técnicos en Registro Kobo y datos
    sensibles/técnicos colapsados y condicionados por permiso. Solo Ficha 1
    aporta ubicación normalizada y el enlace opt-in a OpenStreetMap. Fallos
    operativos e identidades sin resolver se gestionan en el hub global de
    incidencias.

### POST

* El payload original permanece conservado.
* Routing resuelto dispara importación automática cuando la submission es válida.
* Importación significa materialización exitosa y resultado persistido.
* No existe cola humana de revisión pendiente asociada a un Project.
* Una Ficha 1 importada produce exactamente un perfil territorial y un import
  record; otra Ficha 1 válida del mismo núcleo conserva un perfil histórico nuevo.
* Una Ficha 10 importada produce exactamente un microproyecto priorizado y un
  import record; otra submission con el mismo nombre conserva otra propuesta.
* El routing de Ficha 10 identifica el proyecto Núcleo Vital; su importación crea
  la propuesta subordinada y no crea otro `Project`.
* Una Ficha 11 importada produce exactamente una evaluación histórica y un import
  record; otra Ficha 11 válida del mismo núcleo crea otra evaluación.
* El routing de Ficha 11 identifica el proyecto Núcleo Vital; su importación no
  cambia el estado ni la prioridad institucional del proyecto, la identidad o
  los microproyectos.
* La integración no modifica directamente saldos financieros.
* Las Fichas 10 y 11 no crean presupuesto, donación, asignación de fondos ni gasto.
* `KoboProjectBinding` permanece histórico y no se usa en runtime.

---

## 9.1. Administración territorial Kobo

La configuración zona pastoral → proyecto se realiza únicamente mediante el
servicio transaccional. Solo admite las cinco zonas canónicas y proyectos no
terminales. Cambiar o desactivar un mapping queda bloqueado cuando existe
cualquier identidad de esa zona; nunca reasigna submissions ni materializaciones.

Los conflictos admiten tres decisiones tipadas y motivadas:

* `KEEP_EXISTING` conserva identidad y marca la submission entrante como error
  `territorial_conflict_rejected`, sin importarla;
* `ACCEPT_PROPOSED` solo cambia una identidad sin perfiles, microproyectos,
  evaluaciones, import records, importaciones ni otras submissions resueltas;
* `DISMISS` conserva intactas identidad y submission cuando el conflicto no
  representa una decisión territorial.

`PENDING_REVIEW` o `ACTIVE` pueden pasar a `OBSERVED`; solo `OBSERVED` puede
volver a `ACTIVE`. Una identidad puede pasar a `INACTIVE`, conserva código e
historia y no se reactiva por recibir otra Ficha 1. `OBSERVED` continúa
permitiendo routing e importación según el contrato vigente; `INACTIVE` permite
conservar routing pero bloquea nuevas materializaciones.

La reconciliación administrativa procesa como máximo 100 Fichas 10/11
`PENDING_IDENTITY` por llamada, bloquea el lote y solo resuelve proyecto/routing.
No usa bindings, no aprueba, no importa y no modifica submissions importadas.
Cada mutación produce un evento administrativo territorial y un `AuditLog` en
la misma transacción.

---

## 10. Flujo histórico de rechazo y restauración de Kobo

Las rutas HTTP de rechazo/restauración por proyecto están deshabilitadas. Los
servicios de dominio pueden conservar capacidad programática para trazabilidad y
compatibilidad; no restauran una cola humana de revisión por Project.

### 10.1. Rechazo

#### PRE

* La submission se encuentra en un estado rechazable del servicio de dominio.
* El actor posee permisos para rechazarla.

#### Pasos

1. Se registra el motivo del rechazo.
2. La submission pasa a estado `REJECTED`.
3. Se registra el evento técnico correspondiente.
4. No se importa información al dominio operativo.

#### POST

* La submission permanece conservada en el historial del proyecto.
* El payload original no se elimina.
* El rechazo no reabre una bandeja humana por Project.

### 10.2. Restauración

#### PRE

* La submission se encuentra en estado `REJECTED`.
* El actor posee permisos para restaurarla.

#### Pasos

1. El sistema valida que la transición sea permitida.
2. La submission vuelve a `READY_FOR_REVIEW` como estado interno transitorio.
3. Se registra el evento técnico correspondiente.
4. El pipeline automático puede reintentar routing/importación; no implica
   aprobación humana por Project.

#### POST

* El historial conserva tanto el rechazo como la restauración.
* La restauración no reabre una bandeja humana por Project.
* `READY_FOR_REVIEW` permanece transitorio interno del pipeline.

---

## 11. Flujo de reconciliación de Kobo

### PRE

* Existe acceso autorizado a KoboToolbox.
* El activo se encuentra configurado.
* El usuario o proceso posee permisos técnicos.

### Pasos

1. El comando consulta las submissions remotas.
2. Compara sus identificadores con el staging local.
3. Detecta submissions ausentes localmente.
4. Registra las submissions faltantes.
5. Evita duplicar submissions conocidas.
6. Puede ejecutarse en modo `--dry-run`.
7. Las submissions incorporadas continúan mediante el pipeline ordinario.

### POST

* El staging local refleja las submissions remotas detectadas.
* No se duplican registros ya conocidos.
* La reconciliación no sustituye la normalización, revisión ni importación.

---

* La eliminación futura de los modelos específicos requiere una decisión de
  producto y una migración dedicada.

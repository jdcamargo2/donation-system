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
   terminales y audita el cierre.
7. No existen flujos de suspensión, reactivación, anulación ni eliminación de
   proyecto. Las acciones críticas generan auditoría.

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

El Hub exige `kobo.view_territorial_administration`. Los cambios de mappings,
estado y conflictos exigen POST con motivo; la reconciliación opera un lote y
nunca aprueba ni importa submissions.

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

* Existe una institución con rol de donante.
* El monto es positivo.
* La moneda es USD y las fechas son válidas.

### Pasos

1. El usuario registra la donación.
2. El sistema reserva un código operativo con prefijo `DON`.
3. La donación queda inicialmente en estado `REGISTERED`.
4. Cuando se confirma la recepción, puede pasar a `RECEIVED`.
5. Sus fondos pueden distribuirse mediante asignaciones.
6. El progreso de asignación se calcula automáticamente.
7. La donación puede anularse cuando las reglas del dominio lo permitan.
8. Las acciones críticas generan auditoría.

### POST

* La donación conserva un código único e inmutable.
* El saldo disponible se deriva de las asignaciones no anuladas.
* Una donación anulada queda excluida de las métricas operativas.

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

### POST

* La suma de las asignaciones no anuladas no supera el monto disponible de la donación.
* La asignación conserva un código único e inmutable.
* El saldo de la donación queda actualizado de forma derivada.

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
9. Se crea el registro de auditoría.
10. El progreso de ejecución se recalcula de manera derivada.
11. La transacción se confirma de forma atómica.

### POST

* El gasto queda registrado en estado `REGISTERED`.
* El gasto conserva un código único e inmutable.
* El saldo de la asignación refleja únicamente gastos no anulados.
* El gasto puede anularse posteriormente cuando el dominio lo permita.

---

## 5. Flujo de avance de proyecto

### Actor inicial

Operador de campo o usuario con el permiso `add_projectupdate`.

### PRE

* El proyecto se encuentra en estado `ACTIVE`.
* La fecha del avance es válida.
* Se ha seleccionado una persona responsable del avance, activa y con permisos operativos sobre avances.
* El usuario posee permisos para registrar el avance.

### Pasos

1. El usuario registra:

   * título;
   * descripción;
   * fecha real;
   * persona responsable del avance.
2. El usuario puede adjuntar una o varias evidencias durante el registro o la edición en borrador.
   En los formularios de avance con opt-in de vista previa, la selección múltiple puede armarse
   de forma incremental en el cliente antes del envío; los archivos no se suben hasta el submit
   y los adjuntos ya persistidos no se gestionan desde esa vista previa.
3. Desde el detalle de un avance en `DRAFT`, un usuario con `add_projectupdateattachment` puede agregar varios adjuntos en una sola carga.
   El mismo componente de vista previa local aplica en ese formulario independiente.
4. Cada archivo persistido genera su propio evento de auditoría de creación.
5. `created_by` se asigna automáticamente desde el usuario autenticado.
6. `reported_by` conserva la persona responsable del contenido del avance.
7. El avance se guarda en estado `DRAFT`.
8. Un usuario con el permiso `change_projectupdate` puede iniciar la publicación.
9. El sistema valida nuevamente las condiciones de publicación.
10. El avance pasa a estado `PUBLISHED`.
11. Se registra la auditoría correspondiente.
12. El avance queda bloqueado contra edición y eliminación.
13. Los adjuntos permanecen privados y solo pueden agregarse o eliminarse mientras el avance esté en `DRAFT`; el avance publicado y sus adjuntos son inmutables.

### POST

* El avance publicado es inmutable.
* El creador técnico y la persona responsable del avance quedan diferenciados.
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

1. El Comité consulta el avance y sus evidencias.
2. El revisor registra sus observaciones.
3. Se crea una instancia de `ProjectUpdateReview`.
4. Se conserva:

   * el revisor;
   * la fecha;
   * las observaciones.
5. Posteriormente, un usuario autorizado registra una decisión.
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
    proyecto y fuera de bandejas de proyecto; no se usa binding como fallback.
12. Una submission con routing resuelto queda disponible para revisión humana.
13. Un usuario autorizado consulta la información normalizada.
14. El usuario puede:

    * aprobar para importación;
    * rechazar.

15. La aprobación cambia `READY_FOR_REVIEW` a `APPROVED_FOR_IMPORT`; todavía no
    constituye una importación.
16. El servicio común bloquea la submission, revalida routing, proyecto,
    normalización, payload preservado, revisión y permisos, y selecciona el
    handler cerrado de Ficha 1, 10 u 11.
17. Solo una materialización específica exitosa crea `KoboImportRecord`, cambia
    la submission a `IMPORTED`, completa `processed_at` e `imported_at` y registra
    evento y auditoría.
18. El handler de Ficha 1 localiza la identidad ya creada por routing, valida
    código, zona, proyecto, conflictos y datos normalizados, y crea un
    `KoboTerritorialProfile` inmutable por submission.
19. Si la identidad estaba `PENDING_REVIEW`, pasa a `ACTIVE`; `OBSERVED`
    permanece observada e `INACTIVE` bloquea la importación.
20. El handler de Ficha 10 localiza la identidad sin crearla, valida código,
    proyecto, estado, conflictos, campos requeridos y catálogos, y crea un
    `KoboPrioritizedMicroproject` inmutable por submission.
21. La Ficha 10 conserva `beneficiary_group` como lista canónica y
    `main_activities` como texto libre; no interpreta de nuevo `raw_payload`.
22. El handler de Ficha 11 localiza la identidad sin crearla, valida código,
    proyecto, estado, conflictos, diez scores, cálculos, catálogos, decisiones
    humanas y warnings, y crea un `KoboPrioritizationAssessment` inmutable por
    submission.
23. La Ficha 11 conserva por separado total y semáforo originales, total y
    semáforo recalculados, semáforo final humano y prioridad final. Las
    discrepancias son warnings y `linked_microprojects` permanece como snapshot
    textual, sin relaciones automáticas por nombre.

### POST

* El payload original permanece conservado.
* Routing resuelto no implica revisión aprobada.
* Revisión aprobada no implica importación.
* Importación significa materialización exitosa y resultado persistido.
* Una Ficha 1 importada produce exactamente un perfil territorial y un import
  record; otra Ficha 1 válida del mismo núcleo conserva un perfil histórico nuevo.
* Una Ficha 10 importada produce exactamente un microproyecto priorizado y un
  import record; otra submission con el mismo nombre conserva otra propuesta.
* El routing de Ficha 10 identifica el proyecto Núcleo Vital; su importación crea
  la propuesta subordinada y no crea otro `Project`.
* Una Ficha 11 importada produce exactamente una evaluación histórica y un
  import record; otra Ficha 11 válida del mismo núcleo crea otra evaluación.
* El routing de Ficha 11 identifica el proyecto Núcleo Vital; su importación no
  cambia el estado ni la prioridad institucional del proyecto, la identidad o
  los microproyectos.
* La integración no modifica directamente saldos financieros.
* Las Fichas 10 y 11 no crean presupuesto, donación, asignación de fondos ni gasto.

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

## 10. Flujo de rechazo y restauración de Kobo

### 10.1. Rechazo

#### PRE

* La submission se encuentra disponible para revisión.
* El usuario posee permisos para rechazarla.

#### Pasos

1. El usuario revisa la submission.
2. Registra el motivo del rechazo.
3. La submission pasa a estado `REJECTED`.
4. Se registra el evento técnico correspondiente.
5. No se importa información al dominio operativo.

#### POST

* La submission permanece conservada en el historial.
* El payload original no se elimina.
* La información rechazada no modifica entidades operativas.

### 10.2. Restauración

#### PRE

* La submission se encuentra en estado `REJECTED`.
* El usuario posee permisos para restaurarla.

#### Pasos

1. El usuario abre la submission desde el historial.
2. Solicita su restauración.
3. El sistema valida que la transición sea permitida.
4. La submission vuelve a un estado revisable.
5. Se registra el evento técnico correspondiente.

#### POST

* La submission puede revisarse nuevamente.
* La restauración no implica una importación automática.
* El historial conserva tanto el rechazo como la restauración.

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

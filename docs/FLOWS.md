# Flujos funcionales de SIGEDON

Este documento describe los principales flujos operativos de SIGEDON, incluyendo sus precondiciones, pasos, resultados y restricciones.

## 1. Flujo de proyecto

### PRE

* El usuario posee permisos para gestionar proyectos.
* Los datos obligatorios del proyecto están disponibles.
* Las fechas y el presupuesto cumplen las reglas del dominio.

### Pasos

1. El usuario registra el proyecto.
2. El sistema reserva un código operativo con prefijo `PRJ`.
3. El proyecto se crea con el estado seleccionado.
4. Se pueden asociar documentos, asignaciones de fondos y avances.
5. Según las reglas del dominio, el proyecto puede:

   * activarse;
   * suspenderse;
   * cerrarse;
   * anularse.
6. Las acciones críticas generan auditoría.

### POST

* El proyecto conserva un código único e inmutable.
* El proyecto queda disponible para las operaciones permitidas por su estado.
* Las acciones realizadas conservan trazabilidad.

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
* El porcentaje de progreso se encuentra entre 0 y 100.
* Se ha seleccionado una persona responsable del avance, activa y con permisos operativos sobre avances.
* El usuario posee permisos para registrar el avance.

### Pasos

1. El usuario registra:

   * título;
   * descripción;
   * fecha real;
   * porcentaje de progreso;
   * persona responsable del avance.
2. El usuario puede adjuntar evidencias durante el registro.
3. `created_by` se asigna automáticamente desde el usuario autenticado.
4. `reported_by` conserva la persona responsable del contenido del avance.
5. El avance se guarda en estado `DRAFT`.
6. Un usuario con el permiso `change_projectupdate` puede iniciar la publicación.
7. El sistema valida nuevamente las condiciones de publicación.
8. El avance pasa a estado `PUBLISHED`.
9. Se registra la auditoría correspondiente.
10. El avance queda bloqueado contra edición y eliminación.

### POST

* El avance publicado es inmutable.
* El creador técnico y la persona responsable del avance quedan diferenciados.
* El avance puede ser revisado institucionalmente.
* El avance puede aparecer en el portal público cuando cumpla las reglas de publicación.

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
* La entidad no se encuentra anulada.
* Los datos son consultados mediante selectores públicos.

### Pasos

1. El portal consulta exclusivamente selectores y consultas públicas.
2. Se seleccionan proyectos autorizados para publicación.
3. Solo se incluyen avances en estado `PUBLISHED`.
4. Se excluyen entidades anuladas.
5. Todas las operaciones monetarias publicables ya están expresadas en USD.
6. Se eliminan campos privados o internos.
7. Las respuestas autorizadas pueden almacenarse temporalmente en caché.
8. El portal presenta páginas o respuestas JSON públicas.

### POST

* El portal publica únicamente información autorizada.
* No se exponen datos privados, financieros individuales ni técnicos.
* La capa pública no modifica información operativa.

---

## 9. Flujo ordinario de KoboToolbox por proyecto

### PRE

* Existe una definición de formulario compatible.
* El activo Kobo ha sido descubierto y configurado.
* Existe un binding válido hacia un proyecto.
* El activo se encuentra habilitado.

### Pasos

1. Un activo remoto de Kobo se descubre.
2. Se registra o asocia su definición compatible.
3. Se configura un `KoboProjectBinding`.
4. El activo se activa.
5. Kobo envía un webhook o se ejecuta una sincronización.
6. El sistema crea o actualiza una `KoboSubmission` en staging.
7. Se conserva el payload original.
8. El payload se valida.
9. Se normaliza según la ficha correspondiente:

   * Ficha 1;
   * Ficha 10;
   * Ficha 11.
10. El sistema resuelve el proyecto asociado.
11. La submission queda disponible para revisión humana.
12. Un usuario autorizado consulta la información normalizada.
13. El usuario puede:

* importar;
* rechazar.

14. Si se importa, la información normalizada se vincula al proyecto.
15. La acción queda registrada en el historial técnico y, cuando corresponda, en la auditoría funcional.

### POST

* El payload original permanece conservado.
* La importación se realiza únicamente después de la revisión autorizada.
* Los datos normalizados quedan asociados al proyecto correspondiente.
* La integración no modifica directamente saldos financieros.

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

## 12. Entrada de compatibilidad de Ficha 1

El mapping `ficha_01` y el comando legado se conservan como compatibilidad de
entrada al staging genérico. El orden vigente es:

```text
sync_kobo_ficha_01
→ obtención y validación del payload Ficha 1
→ receive_api_submission
→ KoboSubmission (received, payload crudo)
→ procesamiento y normalización ficha_01
→ routing, revisión e importación mediante el pipeline genérico
```

### Reglas

* No representa el flujo recomendado para nuevas integraciones.
* No sustituye al pipeline ordinario basado en activos configurados.
* `Ficha01Territorio` y `Ficha01CoveredCommunity` pertenecen al schema legado,
  no tienen escritores activos conocidos y no son utilizados por el pipeline
  vigente.
* `KoboSubmission` es la fuente de staging activa; recibido, procesado e
  importado representan etapas diferentes.
* Su conservación no implica que nuevas fichas deban implementar modelos específicos equivalentes.
* La eliminación futura de los modelos específicos requiere una decisión de
  producto y una migración dedicada.

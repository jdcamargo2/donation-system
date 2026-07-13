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

Representa un proyecto, programa o línea de acción.

### Estados

```text
PLANNED
ACTIVE
SUSPENDED
CLOSED
ANNULLED
```

### Reglas

* Solo los proyectos activos pueden recibir nuevos avances.
* El presupuesto estimado no puede ser negativo.
* La fecha final no puede ser anterior a la fecha inicial.
* El código operativo es único e inmutable.
* El monto financiado se deriva de las asignaciones no anuladas.
* El monto ejecutado se deriva de los gastos no anulados.
* Los proyectos cerrados o anulados no deben recibir nuevas operaciones incompatibles con su estado.

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
* La moneda operativa del MVP es USD.
* El código operativo es único e inmutable.
* Las asignaciones no anuladas no pueden exceder el monto disponible.
* Una donación anulada queda excluida de métricas y saldos operativos.
* El nivel de asignación se calcula a partir de sus asignaciones y no se almacena como estado editable.

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
* Una asignación anulada no participa en métricas ni saldos.
* La ejecución parcial o completa se deriva de los gastos asociados.
* Una asignación no debe interpretarse como un gasto.

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
* No puede superar el saldo disponible de la asignación.
* Debe contar con soporte documental obligatorio.
* La anulación requiere una justificación.
* El código operativo es único e inmutable.
* Los gastos anulados no participan en saldos ni métricas.

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
DRAFT
PUBLISHED
```

### Actores

* `created_by`: usuario autenticado que registró técnicamente el avance.
* `reported_by`: responsable institucional al que se atribuye el avance.

### Reglas

* Solo puede crearse para proyectos activos.
* El porcentaje de progreso debe estar entre 0 y 100.
* La publicación constituye una transición explícita.
* Un avance publicado es inmutable.
* El creador técnico y el responsable institucional representan responsabilidades diferentes.
* La revisión institucional no altera el estado del avance.

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

Define cómo una submission de Kobo se resuelve hacia un proyecto.

### Modos de resolución

```text
DIRECT
FIELD_VALUE
```

Donde:

* `DIRECT`: todas las submissions se asocian con un proyecto predefinido.
* `FIELD_VALUE`: el proyecto se determina a partir del valor de un campo del formulario.

### 14.5. `KoboSubmission`

Almacena el payload recibido desde Kobo y su estado de procesamiento.

Responsabilidades:

* conservar el payload original;
* mantener el estado del pipeline;
* registrar intentos y resultados;
* permitir revisión humana;
* soportar rechazo, restauración e importación;
* evitar la pérdida de información original.

### 14.6. `KoboAttachment`

Representa un archivo adjunto descargado desde Kobo.

Reglas:

* debe vincularse con una submission;
* conserva metadatos técnicos;
* incluye una política explícita de privacidad;
* su descarga debe estar protegida;
* no se publica automáticamente en el portal público.

### 14.7. `KoboProcessingEvent`

Representa un evento técnico ocurrido durante el procesamiento de una submission.

Responsabilidades:

* registrar etapas del pipeline;
* conservar errores y resultados técnicos;
* apoyar la trazabilidad de la integración;
* permitir el diagnóstico de fallos.

`KoboProcessingEvent` no sustituye a `AuditLog`.

La diferencia principal es:

* `AuditLog` registra acciones funcionales y de negocio;
* `KoboProcessingEvent` registra eventos técnicos del pipeline Kobo.

## 15. Modelos heredados de Ficha 1

### `Ficha01Territorio`

Representa información territorial normalizada por el flujo legado inicial de la Ficha 1.

### `Ficha01CoveredCommunity`

Representa comunidades cubiertas asociadas al flujo legado de la Ficha 1.

### Condición actual

Ambos modelos:

* pertenecen al primer flujo de integración de la Ficha 1;
* se conservan por compatibilidad;
* permanecen cubiertos por pruebas;
* no representan el patrón recomendado para nuevas fichas;
* podrán revisarse durante una futura consolidación del modelo Kobo.

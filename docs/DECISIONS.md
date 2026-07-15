# Decisiones técnicas de SIGEDON

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

## 2026-07-11 — Códigos operativos transaccionales

### Decisión

Los proyectos, donaciones, asignaciones y gastos utilizan secuencias operativas bloqueadas transaccionalmente.

### Formatos

```text
PRJ-000001
DON-000001
ASG-000001
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

## 2026-07-13 — Conservación del flujo legado de Ficha 1

### Decisión

La sincronización histórica de la Ficha 1 permanece disponible por compatibilidad, pero no constituye el camino recomendado para nuevos activos.

### Flujo legado

```text
sync_kobo_ficha_01
→ normalización heredada
→ Ficha01Territorio
→ Ficha01CoveredCommunity
```

### Motivo

El flujo histórico continúa siendo necesario para compatibilidad y pruebas existentes.

### Consecuencias

* El comando `sync_kobo_ficha_01` se mantiene.
* Los modelos heredados permanecen disponibles.
* Los nuevos activos deben utilizar el pipeline general de KoboToolbox.
* Las nuevas fichas no deben replicar automáticamente el patrón de modelos específicos del flujo legado.
* La deuda técnica queda documentada y controlada.

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

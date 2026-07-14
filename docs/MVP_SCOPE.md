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
* estado;
* documentos;
* avances;
* resumen financiero;
* levantamientos Kobo asociados.

Estados:

```text
PLANNED
ACTIVE
SUSPENDED
CLOSED
ANNULLED
```

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

El MVP no incluye aprobación multinivel de gastos.

### 3.6. Avances

Flujo del avance:

```text
DRAFT
→ PUBLISHED
```

Incluye:

* proyecto;
* título;
* descripción;
* fecha real;
* porcentaje de progreso;
* creador técnico;
* responsable institucional;
* evidencias;
* publicación.

Un avance publicado es inmutable.

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

Los archivos privados se descargan mediante endpoints autorizados.

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

* proyectos activos;
* avances publicados;
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
* consultar y registrar soportes autorizados.

No puede:

* publicar avances;
* editar avances después del registro;
* gestionar finanzas;
* revisar en nombre del Comité.

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

Puede:

* consultar proyectos y avances;
* consultar documentos y evidencias;
* registrar una revisión;
* registrar una decisión institucional.

No puede modificar el contenido original del avance.

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
* Los registros históricos en monedas distintas de USD se excluyen de las métricas.

## 6. Códigos operativos

Formatos:

```text
PRJ-000001
DON-000001
ASG-000001
GAS-000001
```

Los códigos:

* son únicos;
* son inmutables;
* se reservan transaccionalmente;
* no dependen del conteo de filas;
* requieren secuencias inicializadas.

## 7. Acciones terminales

Cerrar, anular o eliminar requiere:

* solicitud `POST`;
* permiso;
* confirmación;
* validación de dominio;
* motivo, cuando corresponda;
* auditoría;
* bloqueo posterior, cuando aplique.

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
* el Comité puede revisar y decidir;
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

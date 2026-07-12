Objetivo:
Cerrar la Fase 0 del MVP de SIGEDON creando un documento formal de alcance,
reglas de negocio, exclusiones y criterio de cierre.

Rama actual:
feat/core-module-completion

Importante:
- No cambiar de rama.
- No modificar código productivo.
- No crear modelos, migraciones, vistas, formularios ni tests.
- No tocar Kobo.
- No hacer commit todavía.
- Crear únicamente documentación.

Archivo a crear:
docs/MVP_SCOPE.md

Contenido obligatorio:

# Alcance del MVP de SIGEDON

## 1. Objetivo del MVP

Definir que SIGEDON busca registrar, consultar y transparentar:

- instituciones;
- proyectos;
- donaciones;
- asignaciones;
- gastos;
- avances;
- documentos;
- auditoría;
- información pública;
- integración Kobo limitada.

Aclarar que el MVP no pretende cubrir toda la gestión institucional futura.

## 2. Roles del sistema

### Administrador SIGEDON

Puede gestionar:

- instituciones;
- proyectos;
- donaciones;
- asignaciones;
- gastos;
- documentos;
- avances;
- auditoría.

### Operador de campo

Puede:

- consultar proyectos;
- crear avances;
- guardar borradores;
- adjuntar evidencias;
- publicar avances.

### Auditor externo

Puede:

- consultar información financiera;
- consultar documentos autorizados;
- recorrer donación → asignación → gasto;
- consultar auditoría;
- no modificar información.

### Usuario público

Puede:

- consultar proyectos publicados;
- consultar avances publicados;
- consultar métricas agregadas;
- descargar datos públicos autorizados.

## 3. Alcance funcional aprobado

### Proyectos

El MVP incluye:

- creación y edición;
- estado activo, terminado y anulado;
- información general;
- avances;
- documentos propios del proyecto;
- resumen financiero;
- métricas e indicadores básicos;
- sección Kobo solo cuando exista integración.

No incluye:

- planificación avanzada;
- tareas;
- cronogramas complejos;
- gestión completa de equipos;
- IA;
- resúmenes automáticos;
- analítica avanzada.

### Donaciones

Incluye:

- registrada;
- recibida;
- anulada;
- soporte documental opcional;
- objetivo;
- restricciones;
- monto;
- total asignado;
- saldo disponible.

Los estados asignada parcialmente y asignada totalmente son derivados
automáticamente y no los controla el usuario.

### Asignaciones

Incluye:

- creación;
- relación donación → proyecto;
- monto;
- ejecutado;
- disponible;
- gastos asociados;
- estado activo, terminado y anulado;
- anulación con motivo;
- destino del saldo;
- comprobante opcional de anulación.

La ejecución parcial o total se calcula automáticamente.

### Gastos

El gasto representa un pago ya autorizado fuera del sistema.

Incluye:

- fecha;
- categoría;
- monto;
- proveedor o receptor;
- método de pago;
- factura o referencia bancaria;
- documento soporte obligatorio;
- observaciones;
- anulación excepcional.

No incluye:

- aprobación multinivel;
- revisión interna;
- rechazo como workflow ordinario.

### Avances

Flujo:

Borrador → Publicado

Incluye:

- guardado de borrador;
- edición del borrador;
- fecha real del avance;
- porcentaje de avance;
- múltiples archivos;
- publicación directa;
- visibilidad pública al publicar.

No incluye:

- aprobación;
- rechazo;
- revisión previa obligatoria.

### Documentos del proyecto

Deben existir separados de los avances.

Tipos mínimos:

- propuesta;
- plan de trabajo;
- plan de acción;
- informe;
- otro.

### Auditoría

Incluye:

- append-only dentro de Django;
- consulta por admin y auditor;
- filtros y búsqueda;
- sin edición ni eliminación desde la aplicación.

No incluye:

- hash encadenado;
- WORM;
- snapshots before/after;
- almacenamiento externo inmutable.

### Datos abiertos

Incluye:

- CSV;
- JSON;
- proyectos públicos;
- avances publicados;
- métricas agregadas.

No incluye:

- API pública avanzada;
- autenticación externa;
- rate limiting sofisticado;
- portal para desarrolladores.

### Kobo

El MVP integra únicamente:

- Ficha 1;
- Ficha 10;
- Ficha 11.

Las fichas 2–9 no se importan directamente.

## 4. Reglas financieras

### Saldo de donación

Saldo disponible =
monto de la donación
- suma de asignaciones no anuladas

### Saldo de asignación

Saldo disponible =
monto asignado
- suma de gastos no anulados

Reglas técnicas:

- no calcular saldos únicamente en JavaScript;
- no usar signals para proteger concurrencia;
- no guardar saldos como campos editables;
- usar servicios transaccionales;
- usar transaction.atomic();
- usar select_for_update() en PostgreSQL;
- mantener pruebas concurrentes;
- ignorar entidades anuladas.

## 5. Estados derivados

No pueden ser seleccionados por el usuario:

- donación asignada parcialmente;
- donación asignada totalmente;
- asignación ejecutada parcialmente;
- asignación ejecutada totalmente.

Se calculan desde los montos persistidos.

## 6. Restricciones de las donaciones

Las restricciones deben:

- mostrarse destacadas en el detalle de la donación;
- mostrarse al crear una asignación;
- ser visibles antes de distribuir fondos;
- no aparecer como texto secundario sin énfasis.

## 7. Acciones terminales

Eliminar, anular o terminar requiere:

- solicitud POST;
- confirmación;
- motivo cuando corresponda;
- auditoría;
- explicación de consecuencias;
- bloqueo de edición posterior cuando aplique.

Nunca debe ejecutarse una acción terminal con un solo clic sin confirmación.

## 8. Eliminaciones protegidas

ProtectedError debe capturarse.

El sistema debe mostrar:

- qué entidad bloquea la eliminación;
- cuántos registros relacionados existen;
- nombre humano del tipo relacionado;
- acción recomendada.

Ejemplo:

“No se puede eliminar esta asignación porque tiene 3 gastos asociados.
Conserve la asignación como registro histórico o gestione primero los gastos.”

Nunca mostrar:

- traceback;
- nombres técnicos internos;
- mensaje falso de éxito.

## 9. Archivos

Todo archivo operativo debe:

- descargarse mediante endpoint autorizado;
- evitar enlaces directos FileField.url;
- distinguir visibilidad pública y privada;
- conservar trazabilidad;
- tener validación de formato y tamaño según el tipo.

## 10. Clasificación de nuevos hallazgos

### MVP-BLOCKER

Impide una tarea esencial o genera datos incorrectos.

### MVP-REQUIRED

Forma parte del alcance aprobado, aunque no impida usar el sistema hoy.

### POST-MVP

Mejora útil fuera del alcance actual.

Solo MVP-BLOCKER y MVP-REQUIRED entran antes del cierre.

## 11. Exclusiones explícitas

No forman parte del MVP:

- IA;
- resúmenes automáticos;
- planificación avanzada;
- gestión de tareas;
- chat;
- aprobación multinivel;
- dashboard analítico complejo;
- mapas genéricos;
- API pública sofisticada;
- integración directa de fichas Kobo 2–9;
- hash encadenado de auditoría;
- arquitectura WORM.

## 12. Definición de terminado

El MVP se considera terminado cuando:

- todos los formularios guardan correctamente;
- las fechas persisten;
- los enlaces llevan a la entidad correcta;
- las acciones terminales requieren confirmación;
- los estados derivados se calculan automáticamente;
- los gastos tienen soporte trazable;
- las asignaciones anuladas explican el destino del dinero;
- los proyectos separan avances y documentos;
- los avances pueden guardarse como borrador y publicarse directamente;
- existen filtros y búsquedas esenciales;
- el portal ofrece CSV y JSON públicos;
- Kobo 1, 10 y 11 funcionan;
- PostgreSQL es la base soportada;
- la documentación funcional y técnica está completa.

## 13. Regla de control del alcance

Toda nueva funcionalidad requiere aprobación explícita.

No se introduce una tarea solo porque sería útil.
Debe demostrar que:

- bloquea una tarea real;
- pertenece al alcance aprobado;
- o corrige un riesgo crítico.

Entrega:
- crear docs/MVP_SCOPE.md;
- revisar ortografía y coherencia;
- ejecutar git diff --check;
- mostrar únicamente resumen y ruta del archivo;
- no hacer commit.
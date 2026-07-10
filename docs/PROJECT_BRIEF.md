# SIGEDON

## Documento de alcance del MVP

**Sistema Integral de Gestión, Seguimiento y Trazabilidad de Donaciones**  
**Primera versión funcional para la gestión inicial de donaciones de una Arquidiócesis**

---

## Enfoque

Registro de instituciones, donaciones, proyectos, asignación de fondos, ejecución financiera y auditoría básica.

---

## 1. Propósito del documento

Este documento define el alcance de la primera versión de SIGEDON que será diseñada y desarrollada por el equipo del proyecto.

Aunque SIGEDON puede evolucionar hacia una plataforma integral con transparencia pública, gestión de beneficiarios, donaciones en especie, reportes avanzados e indicadores territoriales, esta primera etapa se concentrará en construir un MVP funcional y verificable.

El objetivo del MVP es permitir que una Arquidiócesis pueda registrar donaciones, asociarlas a proyectos, distribuir fondos por categorías, registrar gastos, adjuntar soportes y mantener trazabilidad básica mediante auditoría.

---

## 2. Alcance general del MVP

La primera versión de SIGEDON se centrará en la trazabilidad financiera básica de las donaciones.

El sistema deberá permitir responder preguntas como:

```text
¿Quién donó?
¿Cuánto donó?
¿A qué proyecto se asignó?
¿En qué categoría se distribuyó?
¿Cuánto se gastó?
¿Quién recibió el pago o recurso?
¿Qué soporte documental respalda el gasto?
¿Cuánto saldo queda disponible?
¿Qué usuario registró o modificó la información?
```

SIGEDON se entiende como una plataforma para registrar, monitorear, auditar y reportar donaciones institucionales. Este MVP toma esa visión general y la reduce a una primera versión alcanzable, centrada en control financiero, soporte documental y auditoría básica.

---

## 3. Módulos incluidos en la primera versión

### 3.1. Módulo de instituciones

Permitirá registrar las organizaciones involucradas en el proceso.

Incluye:

```text
- Nombre de la institución.
- Tipo de institución.
- Rol dentro del sistema.
- País y ciudad.
- Datos de contacto.
- Responsable institucional.
- Documentación legal básica.
- Estado operativo.
```

Este módulo permitirá identificar donantes, receptores, ejecutores, aliados y supervisores.

---

### 3.2. Módulo de donaciones

Permitirá registrar cada donación recibida.

Incluye:

```text
- Código único de donación.
- Donante.
- Tipo de donación.
- Monto.
- Moneda.
- Objetivo.
- Restricciones o condiciones.
- Fecha de compromiso.
- Fecha de recepción.
- Estado.
- Documento soporte.
- Proyecto asociado, cuando aplique.
```

Este será el punto de entrada financiero del sistema.

---

### 3.3. Módulo de proyectos

Permitirá registrar programas, planes o líneas de acción.

Incluye:

```text
- Código del proyecto.
- Nombre.
- Descripción.
- Objetivo.
- Núcleo responsable.
- Ubicación.
- Presupuesto estimado.
- Monto financiado.
- Fecha de inicio.
- Fecha de cierre.
- Estado.
```

Ejemplos de proyectos:

```text
- Comida para damnificados.
- Apoyo en salud.
- Atención a comunidades afectadas.
- Reconstrucción de espacios pastorales.
- Asistencia logística.
```

---

### 3.4. Módulo de asignación de fondos

Permitirá distribuir una donación entre proyectos, actividades, categorías o territorios.

Incluye:

```text
- Donación de origen.
- Proyecto destino.
- Categoría presupuestaria.
- Monto asignado.
- Monto ejecutado.
- Saldo disponible.
- Responsable de la asignación.
- Fecha de asignación.
- Estado.
```

Decisión clave:

> La asignación no es un gasto.  
> La asignación indica cómo se distribuye el dinero.  
> El gasto indica cómo se ejecuta realmente.

Ejemplo:

```text
Proyecto: Comida para damnificados
Total recibido: 100.000

Asignaciones:
- Alimentos: 10.000
- Medicinas: 40.000
- Saldo disponible: 50.000
```

---

### 3.5. Módulo de ejecución financiera

Permitirá registrar gastos realizados con los fondos asignados.

Incluye:

```text
- Fecha del gasto.
- Categoría.
- Monto.
- Moneda.
- Motivo del gasto.
- Proveedor o destinatario.
- Método de pago.
- Factura o documento soporte.
- Descripción.
- Observaciones.
- Estado de validación.
```

Este módulo permitirá saber en qué se usó el dinero y qué evidencia respalda cada operación.

---

### 3.6. Módulo de auditoría

Permitirá registrar acciones importantes dentro del sistema.

Incluye:

```text
- Usuario que realizó la acción.
- Acción ejecutada.
- Módulo afectado.
- Entidad modificada.
- Fecha y hora.
- Cambios relevantes.
- Aprobaciones.
- Rechazos.
- Observaciones.
- Responsable de validación, cuando aplique.
```

Este módulo no será una auditoría avanzada todavía, pero sí permitirá mantener historial básico de cambios, validaciones y acciones críticas.

---

## 4. Módulos excluidos de esta primera versión

Para mantener el proyecto realista, esta primera versión no incluirá:

```text
- Donaciones en especie completas.
- Beneficiarios detallados.
- Entregas y distribución física.
- Portal público de transparencia.
- Mapa territorial.
- Indicadores avanzados de impacto.
- Integración con WhatsApp.
- Códigos QR.
- Firma digital.
- Inteligencia de datos.
- API pública.
- Automatización avanzada de reportes.
```

Estos módulos quedan como posibles fases futuras.

---

## 5. Cadena central del MVP

La trazabilidad del MVP será:

```text
Institución / Donante
        ↓
Donación
        ↓
Proyecto
        ↓
Asignación de fondos
        ↓
Gasto
        ↓
Documento soporte
        ↓
Auditoría interna
        ↓
Reporte básico
```

La propuesta general de SIGEDON plantea una cadena de trazabilidad entre donante, donación, proyecto, ejecución, evidencia y reporte de impacto. Nuestro MVP toma esa lógica y la reduce a una versión alcanzable.

---

## 6. Resultado esperado

Al finalizar el MVP, el sistema deberá permitir:

```text
- Registrar instituciones.
- Registrar donantes.
- Registrar donaciones monetarias.
- Asociar donaciones a proyectos.
- Crear categorías presupuestarias.
- Asignar fondos por categoría.
- Registrar gastos asociados a fondos asignados.
- Adjuntar documentos soporte.
- Consultar montos recibidos.
- Consultar montos asignados.
- Consultar montos ejecutados.
- Consultar saldos disponibles.
- Registrar acciones críticas en auditoría.
- Generar una vista básica de seguimiento financiero.
```

---

## 7. Caso mínimo de validación

El MVP se considerará funcional si permite registrar y consultar un caso como el siguiente:

```text
Proyecto:
Comida para damnificados

Donaciones:
20 donaciones recibidas

Total recibido:
100.000

Asignaciones:
- Alimentos: 10.000
- Medicinas: 40.000
- Saldo disponible: 50.000

Gastos:
- Registrar gasto.
- Asociarlo a una categoría.
- Indicar motivo.
- Indicar destinatario o proveedor.
- Adjuntar soporte.
- Actualizar saldo.
- Dejar registro de auditoría.
```

Si el sistema puede hacer eso de forma clara, ya cumple su primera versión.

---

## 8. Cierre ejecutivo

La primera versión de SIGEDON no busca cubrir toda la operación humanitaria ni todos los módulos posibles de una plataforma institucional completa.

Su objetivo es construir una base sólida de trazabilidad financiera que permita registrar donaciones, asignarlas a proyectos, controlar gastos, conservar soportes y auditar acciones críticas.

Desde esta base, el sistema podrá crecer posteriormente hacia módulos de beneficiarios, entregas, donaciones en especie, transparencia pública, indicadores de impacto, mapas territoriales e inteligencia de datos.

## 9. Reglas de negocio principales

- Una donación puede financiar uno o varios proyectos.
- Una asignación reserva fondos, pero no ejecuta gasto.
- Un gasto siempre debe estar asociado a una asignación.
- Un gasto no puede superar el saldo disponible de su asignación.
- Todo gasto validado debe tener soporte documental.
- Toda acción crítica debe generar registro de auditoría.

## 10. Estados principales

### Donación
- Registrada
- Recibida
- Asignada parcialmente
- Asignada totalmente
- Cerrada
- Anulada

### Proyecto
- Planificado
- Activo
- Suspendido
- Cerrado

### Asignación
- Creada
- En ejecución
- Ejecutada parcialmente
- Ejecutada totalmente
- Cerrada

### Gasto
- Registrado
- En revisión
- Validado
- Rechazado
- Anulado

## 11. Invariantes del sistema

- El saldo disponible de una donación nunca debe ser negativo.
- El saldo disponible de una asignación nunca debe ser negativo.
- La suma asignada de una donación no debe superar el monto recibido.
- La suma ejecutada de una asignación no debe superar el monto asignado.
- Ningún gasto validado debe existir sin soporte documental.
- Toda modificación crítica debe quedar registrada en auditoría.
  
## Stack tecnológico del MVP

La primera versión de SIGEDON será construida como una aplicación web server-rendered usando:

- Django para backend, modelos, vistas, formularios, validaciones, autenticación, permisos y administración.
- Django Templates para renderizado HTML del lado del servidor.
- Bootstrap para interfaz visual, diseño responsivo y componentes base.
- SQLite para desarrollo local.
- PostgreSQL como base de datos recomendada para producción.

El MVP no será una SPA y no usará frameworks frontend como React, Vue, Angular o Svelte en esta etapa.

La prioridad técnica del MVP será:

- Simplicidad.
- Mantenibilidad.
- Trazabilidad financiera clara.
- Formularios confiables.
- Validaciones explícitas.
- Auditoría básica.
- Interfaz institucional limpia.
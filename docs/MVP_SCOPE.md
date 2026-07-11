Fase 0 — Congelación formal del MVP
Objetivo
Evitar que el desarrollo vuelva a expandirse sin control.
A partir de ahora, solo entra código que pertenezca a uno de estos bloques:
1. Corregir errores funcionales observados.
2. Simplificar workflows confirmados por negocio.
3. Añadir trazabilidad documental mínima.
4. Completar Kobo para fichas 1, 10 y 11.
5. Añadir búsqueda, exportación y documentación acordadas.
Cualquier otra propuesta pasa al backlog posterior.
Alcance aprobado del MVP
Proyectos
El usuario podrá:
crear y editar proyectos;
consultar información general;
ver resumen financiero;
ver avances;
ver documentos propios del proyecto;
consultar métricas e indicadores básicos;
terminar o anular mediante acciones explícitas;
consultar Kobo únicamente cuando el proyecto tenga integración.
No entra ahora:
planificación completa;
cronogramas avanzados;
tareas;
gestión de equipos sofisticada;
análisis con IA;
resúmenes automáticos de documentos.
Donaciones
El usuario podrá:
registrar;
marcar como recibida;
anular con motivo;
adjuntar soporte opcional;
consultar monto, asignado y disponible;
consultar restricciones claramente destacadas.
Los estados parcial/total serán derivados automáticamente.
Asignaciones
El usuario podrá:
asignar fondos a un proyecto;
consultar monto, ejecutado y disponible;
ver gastos asociados;
terminar;
anular justificadamente;
indicar destino del dinero;
adjuntar comprobante de anulación.
La ejecución parcial/total será derivada automáticamente.
Gastos
El gasto será un registro de un pago ya autorizado.
Debe incluir:
fecha;
monto;
categoría;
proveedor o receptor;
método de pago;
factura o referencia;
documento soporte obligatorio;
observaciones.
No habrá workflow de revisión/aprobación interno para el MVP.
Avances
El flujo será:
Borrador → Publicado
El operador podrá guardar y editar borradores.
Un avance publicado:
aparece en el proyecto;
aparece en el portal público;
puede tener fecha propia;
puede indicar porcentaje;
puede incluir varios adjuntos.
No habrá aprobación ni rechazo.
Auditoría
Se mantiene:
append-only dentro de Django;
visible para admin y auditor;
sin edición ni eliminación desde aplicación;
con filtros y búsqueda.
No entra:
hash encadenado;
WORM;
snapshots before/after;
SIEM externo.
Datos abiertos
Se incluirá:
CSV;
JSON;
datos públicos agregados;
documentación breve del dataset.
No entra:
API con autenticación;
rate limits avanzados;
versionado formal;
portal para desarrolladores.
Kobo
Solo:
Ficha 1
Ficha 10
Ficha 11
Nada más forma parte del MVP.
Reglas técnicas obligatorias
Dinero
Saldo de donación
= monto de donación
- suma de asignaciones no anuladas

Saldo de asignación
= monto asignado
- suma de gastos no anulados
Estas reglas deben:
ejecutarse desde servicios;
estar protegidas por transacciones;
usar locks PostgreSQL;
ignorar entidades anuladas;
tener pruebas concurrentes;
no depender de JS, templates o signals.
Estados derivados
No se almacenarán como decisiones manuales:
Asignada parcialmente
Asignada totalmente
Ejecutada parcialmente
Ejecutada totalmente
Se presentarán como propiedades calculadas o indicadores visuales.
Acciones terminales
Toda acción terminal exige:
POST;
confirmación;
motivo cuando corresponda;
auditoría;
bloqueo de edición posterior cuando aplique.
Archivos
Todo archivo operativo debe:
descargarse mediante endpoint autorizado;
tener validación de tamaño y formato en una fase posterior del plan;
no enlazarse directamente mediante .url;
distinguir si es público o privado.
ProtectedError
Toda eliminación bloqueada debe responder con:
entidad bloqueada
cantidad aproximada o exacta
tipo de relación
acción recomendada
Ejemplo:
No se puede eliminar esta donación porque tiene 5 asignaciones asociadas. Anule primero las asignaciones o conserve la donación como registro histórico.

Sin traceback y sin mensaje falso de éxito.
Regla para nuevos hallazgos
Todo problema nuevo se etiqueta así:
MVP-BLOCKER
Impide completar una tarea esencial.
Ejemplos:
una fecha no se guarda;
un enlace lleva a otra entidad;
un gasto no puede crearse;
un usuario accede a información prohibida.
MVP-REQUIRED
No rompe el sistema, pero forma parte del alcance acordado.
Ejemplos:
múltiples adjuntos;
documentos de proyecto;
filtros de auditoría;
datos abiertos.
POST-MVP
Mejora útil que no impide entregar.
Ejemplos:
IA;
paneles analíticos avanzados;
planificación completa;
API sofisticada;
mapas dinámicos genéricos.
Solo los dos primeros grupos entran en esta rama.
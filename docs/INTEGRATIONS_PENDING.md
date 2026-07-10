# Integraciones Pendientes

Las integraciones estan reservadas estructuralmente, pero no implementadas en el MVP.

## KoboToolbox

Preguntas pendientes:

- Que formularios Kobo se importaran?
- Cual sera el identificador estable de cada formulario?
- Que campos Kobo corresponden a proyectos, gastos, evidencias o avances?
- Se importaran archivos adjuntos?
- Cual sera la politica de reintentos y deduplicacion?
- Quien puede ejecutar o configurar la sincronizacion?

## Pagos

Preguntas pendientes:

- Que proveedor de pago se usara?
- Se registraran pagos como donaciones, gastos o conciliaciones separadas?
- Como se verificara el estado final de una transaccion?
- Que datos sensibles deben excluirse de logs y auditoria?
- Cual sera la politica de reversos, anulaciones y conciliacion?

## Criterios de seguridad

- No guardar secretos en el repositorio.
- Usar variables de entorno o gestor de secretos.
- Registrar eventos criticos en auditoria.
- Validar idempotencia para webhooks o sincronizaciones.
- No abrir una API publica sin autenticacion, permisos, rate limiting y pruebas especificas.

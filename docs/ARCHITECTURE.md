# Arquitectura SIGEDON

SIGEDON es un proyecto Django modular para trazabilidad de donaciones.

## Componentes

- `core/`: configuracion global de Django, rutas raiz, settings, media/static local.
- `apps.operations`: nucleo operativo interno. Contiene instituciones, proyectos, donaciones, asignaciones, gastos, documentos soporte, avances, auditoria, formularios, vistas y servicios del MVP.
- `apps.public_portal`: portal publico separado del panel interno. Publica solo informacion aprobada y sanitizada.
- `apps.users`: paquete reservado para futuras mejoras de usuarios. No define custom user.
- `apps.integrations`: contenedor reservado para integraciones futuras.
- `apps.integrations.kobo`: reservado para KoboToolbox.
- `apps.integrations.payments`: reservado para pagos.
- `web/`: app historica/deprecated. Se mantiene registrada temporalmente por compatibilidad con rutas, imports o pruebas antiguas durante la migracion modular.

## Principios

- La logica financiera vive en `apps.operations`.
- El portal publico no importa vistas ni formularios internos.
- Las integraciones estan presentes estructuralmente, pero no implementadas.
- No existe API publica en el MVP.

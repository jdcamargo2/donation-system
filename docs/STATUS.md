# Estado de la integración Kobo

## Implementado

- Conexión API autenticada.
- Descubrimiento de activos remotos.
- Sincronización idempotente de Ficha 1.
- Staging y payload bruto.
- Normalización de Ficha 1.
- Descarga protegida de adjuntos.
- Revisión humana.
- Asociación mediante bindings.
- Visualización dentro del proyecto.
- Feature flag KOBO_ENABLED.
- Routing direct y field_value.
- Configuración manual de activos descubiertos.

## Integrado actualmente

- Ficha 1 — Identificación territorial.

## Pendiente

- Normalizador e integración de Ficha 10.
- Normalizador e integración de Ficha 11.
- Sincronización genérica de activos configurados.
- Automatización programada.
- Mejoras visuales del hub.

## Regla operativa

Los activos nuevos pueden descubrirse y configurarse, pero no deben
activarse para procesamiento hasta que exista una definición y un
normalizador soportados.
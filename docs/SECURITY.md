# Seguridad SIGEDON

## Panel interno

- El panel operativo requiere login.
- Las vistas internas usan permisos de Django por modelo.
- La matriz operativa se implementa con Django Groups + Permissions.
- Roles actuales: Administrador SIGEDON, Operador de campo y Auditor externo.
- El comando demo crea un superusuario solo para desarrollo local.

## Portal publico

- El portal publico no requiere login.
- El portal esta separado visual y arquitectonicamente del panel interno.
- Solo publica proyectos activos y avances aprobados.
- No debe exponer usuarios, emails internos, telefonos privados, notas de revision ni rutas administrativas.

## Datos y archivos

- `MEDIA_ROOT` y `MEDIA_URL` se usan para desarrollo local.
- No hay almacenamiento cloud en el MVP.
- Documentos soporte son evidencia operativa y deben manejarse como informacion interna.

## Integraciones futuras

- Toda integracion debe tener validacion de autenticacion, autorizacion, auditoria y manejo seguro de secretos.
- No almacenar tokens o credenciales en el repositorio.

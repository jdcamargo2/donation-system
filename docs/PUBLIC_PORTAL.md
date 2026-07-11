# Portal Publico SIGEDON

El portal publico muestra informacion de transparencia sin abrir el sistema operativo interno.

## Criterio de publicacion

- Proyectos: solo `active`.
- Avances: solo `approved` cuyo proyecto continue `active`.
- Proyectos planificados, suspendidos, cerrados o anulados no se listan publicamente.
- Si un proyecto deja de estar `active`, sus avances dejan de aparecer tambien en el feed publico.

## Datos publicados

- Nombre, codigo, estado publico y descripcion del proyecto.
- Resumen financiero derivado: asignado, ejecutado y disponible.
- Avances aprobados y evidencia asociada si existe.

## Metricas publicas

- Las metricas se calculan solo sobre proyectos `active`.
- `Total recibido` suma una sola vez cada donacion no anulada vinculada a una asignacion publica.
- `Asignado` suma asignaciones no anuladas de proyectos activos y con donaciones no anuladas.
- `Ejecutado` suma gastos no anulados de esas asignaciones.
- `Disponible` representa fondos asignados a proyectos activos que todavia no se han ejecutado: asignado menos ejecutado.
- Estas metricas son una vista publica del portafolio activo; no representan el total historico interno de SIGEDON.

## Datos no publicados

- Usuarios internos.
- Contactos privados de instituciones.
- Notas de revision.
- Avances pendientes, rechazados o borradores.
- Rutas del panel interno o del admin.

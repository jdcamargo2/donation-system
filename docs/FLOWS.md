# Flujos SIGEDON

## Flujo financiero

1. Se registra una institucion donante.
2. Se crea un proyecto operativo.
3. Se registra una donacion recibida.
4. Se asigna parte o todo el monto de la donacion a un proyecto.
5. Se registran gastos contra una asignacion.
6. El dashboard muestra donaciones recibidas, monto asignado, ejecutado y disponible.

Reglas centrales:

- Una asignacion distribuye fondos disponibles.
- Una asignacion no es un gasto.
- Un gasto registra ejecucion real de fondos.
- Un gasto pertenece a una asignacion.
- Los saldos disponibles no deben ser negativos.
- USD es la unica moneda operativa del MVP.
- Formularios, servicios, dashboard y portal publico no mezclan monedas ni realizan conversiones.
- Registros historicos no USD se excluyen de metricas hasta que exista una decision explicita de saneamiento de datos.

## Flujo de gasto

1. El operador crea un gasto en estado registrado o revision.
2. Adjunta uno o mas documentos soporte.
3. Si el gasto pasa a validado, debe tener al menos un documento soporte.
4. El gasto validado queda auditado.

## Flujo de avance

1. El operador registra un avance para un proyecto activo.
2. El avance queda pendiente de revision.
3. Un usuario con permiso revisa y aprueba o rechaza.
4. Un avance aprobado o rechazado no se revisa una segunda vez.

## Flujo publico

1. El portal publico lista proyectos activos.
2. El detalle publico muestra avances aprobados.
3. Avances pendientes, rechazados o borradores no se publican.
4. No se publican datos personales ni notas internas de revision.

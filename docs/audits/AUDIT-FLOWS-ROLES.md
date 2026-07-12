Revisado por: Juan Diego Guerrero Camargo

Primera ruta: admin_demo

A. Inicio y navegación.

Se ve bien.

B. Instituciones.

Se ve bien.

¿Se entiende el rol de la institución? Si se entiende.
¿Falta dirección, ciudad, responsable o documentación? No me parece que haga falta agregar algo de eso.
¿Puede distinguirse donante, ejecutora y receptora? Si se distingue.
¿La eliminación explica por qué está bloqueada? No, no explica nada. Directamente vamos del botón eliminar a una confirmación final y su eliminación posterior.
C. Proyectos.
Cuando creo/edito un nuevo proyecto, no se guardan las fechas.
Los estados se ven bien, pero no les veo utilidad para un usuario. Si creo un proyecto es porque va a estar activo, si esta planificado en el sistema no hay forma de llevar control de esa planificación.
- Los botones están abiertos a la nada, literalmente anule un proyecto sin querer y no puedo volverlo a poner activo. 
- Pienso que esos estados solo sirven para el backend, se deberian manejar solo 2 estados, activo y terminado. 
- Si un administrador sube un avance a un proyecto. La aprobación la hace el mismo en ese momento, es un paso extra innecesario. 
- Si aprueba un avance supongo que sale en el portal público, pero ese es el único uso de esa función, y ni siquiera válida si hay mensajes sensibles. No solo eso, solo permite subir una evidencia, lo cuál no tiene sentido si el avance son 10 fotos y 1 documento. Hay decidir una regla de negocio, en los avances solo se puede subir un documento del avance en word o pdf. Eso pienso yo.
- En la parte inferior esta la tabla de Levantamientos de campo. Solo funciona si hay un kobo enlazado, si no hay kobo, la tabla no sirve para nada. Además, pueden existir proyectos donde no hayan levantamientos de campo. Hacer una pregunta al inicio de "marque si tiene levantamientos de campo" no es lo mejor, genera ruido visual y al no poder enlazar una ficha con Kobo, pues no tiene sentido poner esa opción. 

¿Qué necesita hacer la coordinadora desde un proyecto? Ver el proyecto y su información, los avances que tiene, las evidencias de los mismos. 
¿Qué documentos deberían vivir aquí? Evidencias y avances. 
¿Se necesita responsable institucional? Depende, si es responsable que lleve esto como un administrador, si, es necesario. Si es alguien que lo audite, también, pero no es un requisito para funcionar. 
¿Se necesita equipo, territorio, beneficiarios o indicadores? Efectivamente, se necesita saber que equipos estan trabajando, en donde, con que personas, empresas o beneficiarios y unos indicadores de avance o de información del proyecto serían muy útiles. Un pequeño panel de información con KPIs por proyecto vendría genial, además de unos resumenes de los documentos que suben a la plataforma suena bien también. 
¿Hace falta una vista resumen más completa? La verdad que sí, no diría un nuevo HTML, sino debajo de lo que ya existe mostrar cosas más útiles, hasta ahora es solo un consultorio de cosas, pero no puedes sacar conclusiones de cosas o inferir en que fase o en que parte esta el proyecto.

D. Donaciones
- Las fechas no se están guardando ni cuando creo/edito. 
- Como no guarda fechas, no se puede cambiar al estado de recibida. 
- En las donaciones si tiene más sentido los estados, ya que se pueden registrar y recibirlo de a poco o todo de golper o ser cancelados.
- Hace falta un modal de confirmación, anule una donacion en un solo click, y no puedo volverla a poner como recibida. 
- Hay que generar una lógica de los botones.
- Un estado es asignado parcialmente, pero puedo poner el estado de asignada totalmente, cuando en los datos que tengo abajo no la he asignado completamente. En esos dos estados es mejor llevar la lógica en backend y que no lo cambie el usuario. Podriamos hacer algo como monto total - monto asignado == 0, entonces asignado totalmente, else, asignado parcialmente. Esta lógica hay que mejorarla y hacerla más segura.
- No encuentro el punto del estado cerrada. No puedes cerrar las donaciones de dinero o los bienes, etc. Puedes anular el envió de una donación, pero no existe que yo conozca forma de cerrar, en cambio podría ser mejor recibida, eso tiene más sentido.
- Los montos se reflejan bien y están bien calculados.
- Si una donación esta anulada no debería poder seguir editandose no? Eso genera una falta de trazabilidad, porque lo anulo, y me puedo poner otro monto. Eso debe quedar en la historia
- La sección de asignaciones funciona bien y lleva a las rutas que son.

¿La donación representa dinero, bienes o ambas cosas? No, la donación solo representa una cosa, lo cuál esta bien. 
¿Hace falta soporte documental como archivo? Si, una sección para poner un archivo o imagen sería idea, este debe ser opcional.
¿Las restricciones se ven claramente? Si se ven claramente, pero no resaltan, están justo debajo de objetivo y son un texto más. 
¿El estado parcial/total se entiende? No, es mi crítica a que podríamos manejarlo en backend, es solo un paso extra que debe hacer el usuario admin.

E. Asignaciones

- La tabla de asignaciones lleva a los links de las donaciones, no a las asignaciones.
- Solo puedo entrar a el detalle de una asignación mediante la tabla de asignaciones que esta dentro de una donación.
- Misma crítica, los estados de ejecutado parcialmente o ejecutada totalmente deberían ser manejados por backend, no por el usuario.
- Pienso que deberíamos pasar del estado cerrada a terminado en todo el sistema.
- Cuando anulo uno asignación que sucede? a donde va el dinero? eso es algo que hay que justificar, "Se anulo la asignación de fondos porque el parroco no permitió la entrada de los bienes, todos los fondos han sido devueltos a los donantes" O "Se anulo la asignación de fondos porque el parroco no permitió la entrada de los fondos, se han redirigido los fondos a el proyecto X" Esto es importante, porque cualquiera anula eso, y dice que se perdió el dinero, eso no es transparente, necesitamos en asignaciones un sistema de anulación que explique el porque y en donde termino el dinero, con comprobantes y lo necesario. Para este caso si es sumamente importante una imagen, documento o validación de que el dinero si fue asignado y se está usando para el fin que tenía la donación en un principio.
- Todo se proyecta bien, los montos son los que son, y los cálculos son correctos.
- Al momento de crear una asignación de fondos, no se sabe cuanto es el monto de la donación. Entonces, al seleccionar la donación es importante mostrar en el formulario, de cuanto dinero es la donación. Sino, lleva a que de el error de "El monto de la asignación excede el saldo disponible de la donación.", pero uno para saberlo tiene que salirse del formulario, buscar por código la donación, ver cuanto es, acordarse y volver a crear la asignación, son puros pasos innecesarios. 
- No se guarda la fecha, y al ser obligatoria no puedo crear la asignación de fondos.
- Al querer eliminar una asignación me tiro este error: "ProtectedError at /allocations/3/delete/
("Cannot delete some instances of model 'FundAllocation' because they are referenced through protected foreign keys: 'Expense.allocation'.", {<Expense: Jornada de diagnóstico territorial - 2200.00>})"
- Y aunque me lanzo el error y no la pude eliminar, igual salto la notificación de Asignación eliminada con exito.

¿La categoría presupuestaria es suficiente? Sí, se ve bien y se entiende para que es. 
¿Falta actividad, territorio o componente? Si vale la pena dar más información. Pero creo relevante que si o si necesitamos un sistema de trazabilidad y transparencia de todas las asignaciones de donaciones, tanto como se hayan ejecutado o no. Siempre hay que saber a donde se fue el dinero. 
¿Hace falta dividir una asignación en partidas? No parece. 
¿Se entiende la relación donación → proyecto? Si se entiende, pero se esta manejando por códigos en esta sección: <p>DON-DEMO-001 → PRJ-DEMO-001 · <span class="badge ops-status-badge">En ejecución</span></p>. Lo cuál no da a entender nada, es mejor manejar los nombres en esa parte. En la zona de detalle de asignación ya están los códigos, nombres y el link que te redirige a la donación o proyecto correspondiente, entonces ya se entiende mejor.

F. Gastos

- Los links de gastos llevan a las asignaciones, no a los gastos, y tiene el código de las donaciones, no de los gastos.
- Esta lógica es importante, una asignación puede tener muchos gastos. 
- En la tabla de los gastos sale "Con soporte", eso no se entiende. 
- En los estados que se ven dicen Validado o Registrado. ¿Dónde se valida un gasto? No veo por donde hacerlo. Es posible que este dentro del detalle de cada gasto, pero no puedo entrar porque los links redirigen a las donaciones.
- La asignación con código DON-DEMO-001 que no sé de donde salió, devuelve 403 forbidden. Lo cuál esta bien, porque los códigos que mantenemos son DON_000001. Pero dentro del panel de donaciones si lo permite editar y se refleja y lo mismo en asignaciones. El error solo existe en Gastos. Los demás si se pueden editar. 
- En este caso es igual a asignaciones, si no sé el monto de la asignación, no sé cuanto poner en monto. 
- No se guardan las fechas. Como es obligatorio, no puedo crear el gasto. 
- Los documentos en gastos deben ser obligatorios.
- Los gastos deben aparecer en su asignación correspondiente dentro del detalle del panel de asignaciones.
- Los estados de los gastos no tienen sentido, ya que, al ser manejados por un humano y no una maquina, el estado de registrado debe ser el inicial, pero una persona no va a entrar a mirar el gasto, pero justo antes va a cambiar el estado a "En revisión" para 2 minutos después ponerlo en "Validado", eso son los estados de un sistema automatizado, esto es manual. Y si se ha hecho un gasto, no lo puedes cancelar, a menos que pidas un reembolso al proveedor o surge algún problema, pero eso es una lógica extra que vamos a manejar con observaciones dentro del formulario.

¿Hace falta rechazo explícito? Como explique, no puedes como tal rechazar un gasto. 
¿Hace falta aprobación en varios niveles? Esto es como una libreta de gastos, las aprobaciones vienen de más arriba fuera del sistema. 
¿Se necesita número de factura o referencia bancaria? Si, es necesario que haya un número de factura, referencia bancaría o documento que avale y de trazabilidad a ese gasto. Eso es transparencia. 
¿Se entiende la diferencia entre anulado y rechazado? No, como digo, no tienen mucho sentido.

G. Avances.

- Si se crea, lo cree dentro de un proyecto. 
- Si adjunta evidencia.
- No puedo editarlo desde admin_demo.
- No le veo mucha lógica a validar un avance, es un avance, no van a subir a un mono bailando como avance, son personas profesionales. Pienso que debemos quitar la lógica de revisar y validar un avance, es mejor subir un avance y que entre directo. En tal caso es mejor tener una opción de borrar solo en el rol de admini en el caso de que se suba algo raro, y más que eso una sección para editar el avance.
- Como digo, no tengo forma de editar desde admin. 
- No puedo eliminar desde admin.
- Si descarga la evidencia. 

¿Falta porcentaje de avance? Sí, hace falta un porcentaje de avance dentro de cada proyecto. 
¿Faltan indicadores? Sí, los faltan. 
¿Se necesita fecha del avance distinta a created_at? Sí, un avance pudo ser hecho en otro momento y subirlo en otro distinto a la plataforma. 
¿Se necesitan varios archivos? Sí, sería ideal. 
¿Hace falta comentarios entre operador y revisor?

H. Auditoría

- Genera los eventos correctamente.
- No tiene filtro.
- No tiene buscador.
- No tiene botones. Pero esto hay que blindarlo desde código, una vez en producción nadie debería poder meter la mano en los logs.
- Los eventos son congruentes.

¿El resumen permite entender qué pasó? Sí, da información, creo que es la suficiente. 
¿Hace falta detalle del cambio anterior/nuevo? No, es mejor tener las acciones realizadas y porque, tener los cambios del antes y después es complicarnos. 
¿Se identifica correctamente al actor? Sí.

Ruta 2 - Operador de campo

- Puedo crear, montar todo en el avance, pero después de que lo guardo no puedo editarlo. Es correcto el flujo.
- Si puedo entrar a ver el avance y descargar el documento que subí.
- El operador solo puede ver el Panel financiero y los proyectos.
- El operador obtiene 403 forbidden al intentar entra a urls que a las que no tiene acceso.

¿Puede encontrar rápidamente su proyecto? Sí.
¿Puede saber qué información debe subir? Sí, pero tiene que validarlo alguien que no conozca la plataforma.
¿Falta guardar avances como borrador? Sí, es importante mantener la persistencia de un avance, es posible una ida de luz, o un problema donde no se haya podido enviar el avance. Si persiste, el usuario puede volver a ingresar cuando tenga acceso y seguir donde se quedo. 
¿Puede ver la razón de rechazo? No parece, pero esa lógica de validación hay que eliminarla.
¿Puede crear una corrección después de un rechazo? No, no puede. 
¿Necesita adjuntar más de una evidencia? Sí, debe poder juntar varios archivos. 
¿Puede distinguir lo pendiente, aprobado y rechazado? Sí, si puede.

- Hay que eliminar la lógica de rechazo o validación, es un muro que no aporta nada.

Ruta 3 - Auditor externo

¿Tiene suficiente información para auditar? Sí. 
¿Puede rastrear donación → asignación → gasto? Sí. 
¿Puede ver documentos soporte sin modificar? Sí. 
¿La auditoría le permite reconstruir lo ocurrido? Sí. Eso parece. 
¿Faltan filtros por fecha, proyecto o institución? Sí, faltan filtros. 
¿Necesita exportar información? Sí. Podemos crear una API para extraer datos.

Ruta 4 - Usuario anónimo y portal público

- La página es correcta. 
- Los avances se ven bien.
- Los proyectos son coherentes.
- Los montos son los mismos.
- Las evidencias se pueden ver.
- Falta agregar en la sección de datos abiertos una API o sección para descargar todo lo que este en el portal público.
- Hay que arreglar UX y UI. Hay montos que se salen de sus espacios o se aplastan. 

General

- Noto que no se están guardando las fechas ni cuando creo/edito formularios.
- Podemos agregar buscadores en las diferentes secciones y filtradores por estado.
- Los números no son legibles, hacen falta los puntos entre medio (10.000.000), ya hay una integración para eso, pero no esta funcionando.
- Considero que dentro de los proyectos es necesario subir los planes de trabajo o documentos importante del mismo. Como la propuesta, los planes de acción, etc.
- En varios de los módulos hay links movidos. En asignaciones salen las donaciones. En gastos salen las donaciones. No tienen sus links ni códigos propios.
# Roles y permisos de SIGEDON

Este documento es la guía canónica de roles y permisos de SIGEDON. Define exactamente cuatro roles funcionales, su construcción, la administración de usuarios y grupos, y el contrato de sincronización.

## 0. Visión general

SIGEDON tiene exactamente cuatro roles funcionales canónicos:

```text
Administrador SIGEDON
Operador de campo
Auditor externo
Comité de proyectos
```

### Invariante administrativa

```text
usuario ordinario
→ cero o un rol funcional SIGEDON
→ grupos técnicos opcionales
→ permisos directos opcionales
```

Reglas:

* Un usuario ordinario puede tener **cero o un** rol funcional SIGEDON.
* Los grupos técnicos son independientes del rol funcional.
* Los permisos directos (`auth_user_user_permissions`) son independientes.
* Superusuarios y cuentas de servicio pueden no tener rol funcional.
* La tabla M2M `auth_user_groups` permite estructuralmente varios grupos; la regla administrativa de SIGEDON admite **como máximo un** grupo de rol funcional canónico por usuario.
* Los grupos legacy `Revisor del Comité` y `Decisor del Comité` ya no existen.
* Las constantes `ROLE_PROJECT_UPDATE_REVIEWER` y `ROLE_PROJECT_UPDATE_DECIDER` ya no existen.

## 1. Administrador SIGEDON

### Construcción del rol

El Administrador SIGEDON recibe:

1. todos los permisos de la aplicación `operations`, **excepto** los listados en `ADMIN_EXCLUDED_PERMISSION_CODENAMES`;
2. más los cinco permisos de administración territorial Kobo.

### Exclusiones explícitas (`ADMIN_EXCLUDED_PERMISSION_CODENAMES`)

Admin **no** recibe:

```text
add_auditlog
change_auditlog
delete_auditlog
add_projectupdatereview
change_projectupdatereview
delete_projectupdatereview
add_projectupdatereviewdecision
change_projectupdatereviewdecision
delete_projectupdatereviewdecision
review_projectupdate
decide_projectupdate
resolve_projectupdateremediation
delete_project
decide_expenserequest
delete_expenserequest
add_expenserequestevent
change_expenserequestevent
delete_expenserequestevent
```

### Permisos territoriales Kobo (automáticos)

Admin recibe automáticamente al sincronizar:

```text
kobo.view_territorial_administration
kobo.manage_pastoral_zone_mappings
kobo.resolve_territorial_conflicts
kobo.change_territorial_identity_status
kobo.run_territorial_reconciliation
```

Clarificación:

* los permisos de administración territorial son automáticos vía `sync_sigedon_roles`;
* los demás permisos generales de administración Kobo (`view_koboasset`, `change_kobosubmission`, etc.) permanecen separados y se asignan manualmente cuando corresponde.

### Puede gestionar

* instituciones;
* proyectos (crear, editar mientras estén `ACTIVE`, publicar/retirar del portal,
  terminar);
* hitos verificables de proyectos, incluidos completar, reabrir y reordenar;
* donaciones;
* asignaciones de fondos;
* gastos;
* solicitudes de gasto (crear, editar propias pendientes, cumplir, retirar,
  anular; **sin** decidir);
* documentos;
* avances;
* publicación de avances;
* acciones terminales de entidades que las admiten (por ejemplo anular
  donaciones, asignaciones o gastos; finalizar asignaciones; terminar proyectos);
* consulta de auditoría;
* administración territorial Kobo (mappings, conflictos, identidad, reconciliación).

### Permisos específicos de proyecto

Recibe, entre otros:

```text
operations.add_project
operations.change_project
operations.view_project
operations.manage_project_publication
```

* `change_project` habilita editar un proyecto `ACTIVE` y terminar el proyecto
  (`finish_project` / «Terminar proyecto») solo cuando el alcance financiero
  está resuelto (sin asignaciones `ACTIVE` ni solicitudes de gasto abiertas).
  El servicio rechaza el cierre aunque la UI oculte la acción.
* `manage_project_publication` habilita publicar y retirar del portal. Solo
  Administrador SIGEDON recibe este permiso.

No recibe de forma efectiva `operations.delete_project`: el permiso técnico
generado por Django puede existir, pero no se asigna a roles operativos y no
habilita eliminación. Los proyectos no pueden eliminarse ni siquiera por
superusuarios a través de Django Admin; el modelo y el queryset rechazan
`delete()`.

### Restricciones

* No puede crear, modificar ni eliminar registros de `AuditLog`.
* No puede anular un proyecto: `Project` no admite estado anulado.
* No puede eliminar un proyecto.
* No puede gestionar identidades: crear, editar, activar, desactivar ni
  restablecer la contraseña de ninguna cuenta institucional. El rol
  funcional «Administrador SIGEDON» no otorga autoridad de identidad; esa
  gestión es exclusiva del superusuario en `/panel/usuarios/` (ver §5.1).
* No recibe permisos de revisión, decisión ni resolución de remediaciones del Comité.
* No recibe `decide_expenserequest` (solo el Comité decide solicitudes de gasto).
* Puede crear, editar y retirar solicitudes de gasto **propias** en
  `PENDING_DECISION`; la aprobación/reserva es exclusiva del Comité.
* Puede cumplir solicitudes aprobadas (`fulfill_expenserequest`) y anular
  administrativamente pendientes o reservadas (`annul_expenserequest`).
* En ER3A consulta todas las solicitudes en listado/detalle.
* En ER3B puede crear solicitudes (global y desde proyecto), y editar/retirar
  solo las propias en `PENDING_DECISION`.
* En ER4A no ve acciones de aprobación/denegación; esas rutas responden 403.
* En ER4B anula administrativamente solicitudes `PENDING_DECISION` o
  `APPROVED_RESERVED` desde `expense_request_annul` (motivo obligatorio; sin
  efecto financiero si pendiente; libera la reserva completa si estaba
  aprobada). No ve «Aprobar»/«Denegar».
* En ER5 registra el gasto final desde `APPROVED_RESERVED`
  (`expense_request_fulfill`; soporte obligatorio; exacto o parcial). Los CTAs
  ordinarios de creación directa de gasto quedan retirados.
* En ER6 puede agregar/eliminar adjuntos solo en solicitudes propias pendientes
  y leer adjuntos de cualquier solicitud visible mediante rutas protegidas.
* No recibe `delete_expenserequest` ni permisos de mutación de `ExpenseRequestEvent`.
* No recibe automáticamente permisos técnicos generales de Kobo distintos de los cinco territoriales.

## 2. Operador de campo

### Conjunto exacto de permisos

```text
operations.view_project
operations.view_projectupdate
operations.add_projectupdate
operations.view_projectupdateattachment
operations.view_supportingdocument
operations.add_supportingdocument
operations.view_projectupdateremediation
operations.view_projectupdateremediationattachment
operations.add_projectupdateremediation
operations.change_projectupdateremediation
operations.add_projectupdateremediationattachment
operations.delete_projectupdateremediationattachment
operations.submit_projectupdateremediation
operations.view_expenserequest
operations.add_expenserequest
operations.change_expenserequest
operations.withdraw_expenserequest
operations.view_expenserequestattachment
operations.add_expenserequestattachment
operations.delete_expenserequestattachment
operations.view_expenserequestevent
```

### Puede

* consultar proyectos;
* consultar avances;
* registrar avances;
* cargar uno o varios adjuntos durante el registro;
* consultar y descargar evidencias de avance asociadas a proyectos que puede ver
  (`view_projectupdateattachment`), sin obtener edición/eliminación/publicación
  de adjuntos salvo permisos adicionales;
* consultar soportes autorizados (incluido un listado de proyecto sin montos ni
  datos financieros del gasto);
* registrar soportes permitidos;
* gestionar remediaciones propias (crear, editar, adjuntar, enviar);
* crear solicitudes de gasto desde el detalle de un proyecto (no desde el
  listado global);
* editar y retirar **solo** solicitudes propias en `PENDING_DECISION`;
* agregar o eliminar adjuntos **solo** en solicitudes propias pendientes
  (`add_expenserequestattachment` / `delete_expenserequestattachment`);
* previsualizar o descargar adjuntos de sus propias solicitudes visibles
  (`view_expenserequestattachment`) mediante rutas protegidas.

En la UI (ER3A+ER3B), el listado/detalle de solicitudes se limita a
las creadas por el mismo usuario (`requested_by`); no ve solicitudes de otros
Operadores. El listado no muestra «Nueva solicitud» global; el CTA es
«Solicitar gasto» en el detalle del proyecto **solo** cuando existe al menos una
asignación elegible según `expense_request_allocation_choices` (misma regla del
formulario). Sin asignaciones elegibles no hay CTA ejecutable; se muestra guía
neutra. Desde el detalle de una asignación elegible, el CTA preserva
`?allocation=<pk>` sin ampliar el queryset. El detalle de asignación muestra
solicitudes vinculadas visibles al usuario (alcance de propiedad del Operador).
En ER4A no ve «Aprobar» ni
«Denegar»; esas rutas responden 403. En ER4B no ve «Anular solicitud»; la ruta
de anulación responde 403. En ER6 ve «Agregar adjunto» / «Eliminar adjunto»
solo en propias `PENDING_DECISION`; tras la decisión los adjuntos permanecen
legibles pero congelados.

Al registrar un avance, el usuario autenticado se asigna automáticamente como
persona responsable. El campo se muestra en modo no editable.

Los datos de Kobo ya integrados en un proyecto pueden seguir siendo visibles
cuando el acceso se gobierna por `operations.view_project`; eso no implica
acceso al panel de administración territorial.

`view_project` otorga identidad, estado, datos descriptivos, hitos, avances y
otra información no financiera del detalle. Las métricas financieras
operativas del detalle (Fondos asignados, Gastos registrados, Reservado,
Disponible operativo, Ejecución) requieren **ambos**
`operations.view_fundallocation` y `operations.view_expense` — la misma regla
que DASH-FIN3. Sin ambos permisos no se calculan ni se añaden al contexto.

### No puede

* crear proyectos;
* publicar ni retirar proyectos del portal;
* gestionar finanzas (donaciones, asignaciones, gastos), incluidos los montos
  del resumen financiero en el detalle de proyecto;
* consultar la auditoría global;
* revisar ni decidir avances en nombre del Comité;
* resolver remediaciones;
* publicar avances;
* acceder al panel KoboToolBox ni a la administración territorial;
* configurar mappings territoriales, resolver conflictos, cambiar estados de
  identidad ni ejecutar reconciliación.

Operador de campo no puede acceder al panel KoboToolBox ni a la administración
territorial.

## 3. Auditor externo

### Conjunto exacto de permisos

```text
operations.view_institution
operations.view_project
operations.view_donation
operations.view_fundallocation
operations.view_expense
operations.view_supportingdocument
operations.view_projectupdate
operations.view_auditlog
operations.view_expenserequest
operations.view_expenserequestattachment
operations.view_expenserequestevent
kobo.view_territorial_administration
```

### Alcance

El Auditor externo es un rol de **solo lectura**.

Puede consultar:

* instituciones;
* proyectos;
* donaciones;
* asignaciones;
* gastos;
* solicitudes de gasto y su evidencia/historial (solo lectura);
* soportes;
* avances;
* auditoría;
* hub territorial (lectura).

Las exportaciones CSV financieras (donaciones, asignaciones, gastos) y de
proyectos usan los mismos permisos `view_*` y el mismo alcance de queryset que
el listado correspondiente; el escape de fórmulas de hoja de cálculo no amplía
ni reduce ese alcance.

En ER3A/ER3B el Auditor ve **todas** las solicitudes visibles en listado/detalle
(read-only global), con el ítem de sidebar «Solicitudes de gasto»; el ocultamiento
de accesos rápidos del panel no afecta esa navegación. No ve «Solicitar gasto»,
«Editar», «Retirar», «Aprobar» ni «Denegar». En ER4A las rutas de decisión
responden 403. En ER4B no ve «Anular solicitud»; la ruta de anulación responde
403. En ER6 puede previsualizar/descargar adjuntos protegidos de solicitudes
visibles, sin CTAs de mutación.

### Restricciones

No puede:

* crear información;
* modificar información;
* anular registros;
* eliminar registros;
* publicar ni retirar proyectos del portal;
* ejecutar acciones terminales;
* revisar, decidir ni resolver remediaciones.

### Presentación del Panel financiero

El Auditor externo mantiene acceso a las consultas financieras y de auditoría
mediante el menú lateral y las vistas autorizadas. El bloque de accesos rápidos
del Panel financiero no se muestra para este rol. Esto no revoca permisos ni
cambia rutas ni la navegación lateral.

## 4. Comité de proyectos

Comité de proyectos es **un único rol funcional**. La revisión, la decisión y la
resolución de remediaciones son permisos y acciones de flujo distintos dentro
de ese rol; no son tres roles separados. El estado del flujo impide secuencias
inválidas.

### Conjunto exacto de permisos

```text
operations.view_project
operations.view_projectupdate
operations.view_projectdocument
operations.view_projectupdateattachment
operations.view_projectupdatereview
operations.view_projectupdatereviewdecision
operations.view_projectupdateremediation
operations.view_projectupdateremediationattachment
operations.review_projectupdate
operations.decide_projectupdate
operations.resolve_projectupdateremediation
operations.view_expenserequest
operations.decide_expenserequest
operations.view_expenserequestattachment
operations.view_expenserequestevent
kobo.view_territorial_administration
```

### Puede

* consultar proyectos;
* consultar avances;
* consultar documentos de proyecto;
* consultar evidencias de avances;
* registrar una revisión institucional (`review_projectupdate`);
* registrar una decisión institucional (`decide_projectupdate`);
* resolver remediaciones (`resolve_projectupdateremediation`);
* consultar solicitudes de gasto y su evidencia;
* aprobar o denegar solicitudes de gasto (`decide_expenserequest`);
  la aprobación reserva atómicamente el monto solicitado sobre la asignación;
* consultar el hub territorial en modo lectura.

En ER3A/ER3B el Comité ve todas las solicitudes; la primera visita al listado aplica
por defecto el filtro `pending_decision` (sobreescribible). En ER4A el Comité aprueba
o deniega solicitudes pendientes desde páginas dedicadas (`expense_request_approve` /
`expense_request_deny`); la aprobación reserva fondos de forma atómica y la denegación
exige motivo. No ve CTAs de creación/edición/retiro del solicitante; tampoco ve
«Anular solicitud» (ER4B; la ruta responde 403). No ve «Registrar gasto» (ER5;
la ruta de cumplimiento responde 403). En ER6 puede previsualizar/descargar
adjuntos de solicitudes visibles, pero no ve CTAs de mutación de adjuntos
(upload/delete responden 403/404 según alcance).

### No recibe

Comité **no** recibe permisos CRUD de mutación sobre las entidades de revisión o
decisión:

```text
add_projectupdatereview
change_projectupdatereview
delete_projectupdatereview
add_projectupdatereviewdecision
change_projectupdatereviewdecision
delete_projectupdatereviewdecision
```

Esas codenames existen en Django y se excluyen de Admin; el Comité opera mediante
las acciones de flujo `review_projectupdate`, `decide_projectupdate` y
`resolve_projectupdateremediation`.

### No puede

* modificar el contenido original del avance;
* publicar avances;
* publicar ni retirar proyectos del portal;
* eliminar avances;
* modificar una revisión o decisión ya registrada vía CRUD genérico;
* gestionar finanzas, incluidos los montos del resumen financiero en el detalle
  de proyecto.

La revisión, la decisión y la remediación se registran en entidades separadas y
no alteran el contenido inmutable del avance publicado.

## 5. Administración de usuarios (UserAdmin)

SIGEDON reemplaza el UserAdmin estándar con `SigedonUserAdmin`.

Campos relevantes en el panel:

| Concepto | Campo / etiqueta |
| --- | --- |
| Rol funcional SIGEDON | `functional_role` — opcional, selección única |
| Grupos técnicos adicionales | `groups` — selector filtrado |
| Permisos directos | `user_permissions` |

Comportamiento:

* el rol funcional es opcional y de selección única (incluye «Ninguno»);
* al cambiar el rol se eliminan únicamente membresías de roles funcionales previos;
* los grupos técnicos se conservan salvo deselección explícita;
* los permisos directos son independientes del rol y de los grupos técnicos;
* los grupos canónicos se excluyen del selector de grupos técnicos;
* un usuario con cero rol funcional es válido;
* el bypass de superusuario permanece;
* las cuentas de servicio pueden operar solo con permisos directos.

### 5.1. Panel institucional de usuarios (`/panel/usuarios/`)

Además de `SigedonUserAdmin` en Django Admin (recuperación técnica, ver §10),
SIGEDON expone un panel superuser-only bajo `/panel/usuarios/` para la gestión
ordinaria de cuentas institucionales:

```text
/panel/usuarios/                          listado
/panel/usuarios/nuevo/                    alta
/panel/usuarios/<pk>/                     detalle
/panel/usuarios/<pk>/editar/              edición (rol, datos, estado)
/panel/usuarios/<pk>/activar/             activación
/panel/usuarios/<pk>/desactivar/          desactivación
/panel/usuarios/<pk>/restablecer-clave/   restablecer contraseña temporal
```

### Contrato de autoridad

* Toda operación mutadora exige `request.user.is_superuser`; ningún rol
  funcional (incluido «Administrador SIGEDON») la sustituye.
* El panel no puede crear, editar ni gestionar cuentas superusuario; esas
  cuentas se administran exclusivamente desde `/admin/` (recuperación técnica).
* Un superusuario no puede desactivar su propia cuenta desde este panel.
* El alta fija exactamente un rol funcional canónico, `is_staff=False`,
  `is_superuser=False` y `UserAccessProfile.must_change_password=True` con
  una contraseña temporal; la contraseña nunca se registra en logs ni en
  `AuditLog`.
* Desactivar una cuenta o restablecer su contraseña invalida de inmediato
  sus sesiones activas (`django.contrib.sessions` en base de datos),
  conservando la sesión del actor cuando corresponde.
* El enlace de sidebar «Gestión de usuarios» solo se renderiza cuando
  `request.user.is_superuser` es verdadero; no depende del rol funcional.
* No existe autorregistro público: ninguna ruta anónima crea cuentas.

Detalle del flujo completo: [Flujos §0](FLOWS.md#0-flujo-de-acceso-institucional).
Procedimiento de despliegue: [Despliegue §7](DEPLOYMENT.md#7-usuarios-y-roles).

## 6. Administración de grupos (GroupAdmin)

SIGEDON reemplaza el GroupAdmin estándar con `SigedonGroupAdmin`.

* Los cuatro grupos canónicos son visibles pero de solo lectura en Django admin.
* Sus nombres no pueden cambiarse.
* Sus matrices de permisos no pueden editarse desde el admin.
* Los grupos canónicos no pueden eliminarse.
* Una eliminación masiva mixta que incluya un grupo canónico se bloquea por completo.
* Los grupos técnicos / no canónicos permanecen editables.
* Shell, ORM y SQL directo quedan fuera de esta protección de capa admin; la
  fuente autoritativa de las matrices canónicas es `sync_sigedon_roles`.

## 7. Sincronización de roles

Comando:

```bash
python manage.py sync_sigedon_roles
```

### Contrato

* asegura que existen los cuatro grupos canónicos;
* reemplaza la matriz de permisos de cada grupo canónico desde el código;
* preserva grupos no canónicos;
* preserva membresías de usuarios;
* no elimina grupos técnicos;
* es idempotente;
* debe ejecutarse después de un despliegue, una restauración o un cambio en la
  matriz de roles.

### Salida esperada

```text
Roles operativos de SIGEDON sincronizados.
- Administrador SIGEDON: <n> permisos
- Operador de campo: <n> permisos
- Auditor externo: <n> permisos
- Comité de proyectos: <n> permisos
```

Advertencia: las ediciones manuales a permisos de grupos canónicos no son
autoritativas y serán sobrescritas por la sincronización.

Instrucciones operativas detalladas: [Operaciones](OPERATIONS.md).

## 8. Administración técnica de KoboToolbox

La administración técnica general de Kobo utiliza permisos de la aplicación
`kobo` distintos de los territoriales automáticos.

### Permisos representativos (manuales)

```text
view_koboasset
change_koboasset
view_kobosubmission
change_kobosubmission
```

La matriz real puede incluir permisos adicionales según los modelos y acciones
técnicas disponibles.

Los antiguos permisos de binding no conceden administración territorial. La
operación vigente se centra en ver la integración, gestionar mappings, resolver
conflictos, cambiar estados de identidad, reconciliar, revisar e importar.

### Permisos de administración territorial

```text
view_territorial_administration
manage_pastoral_zone_mappings
resolve_territorial_conflicts
change_territorial_identity_status
run_territorial_reconciliation
```

Asignación automática vía sincronización:

* `Administrador SIGEDON` → los cinco permisos;
* Auditor externo y Comité de proyectos → únicamente
  `view_territorial_administration`;
* Operador de campo → ninguno de los permisos territoriales.

Ninguna acción territorial se concede por poseer solamente `change_kobosubmission`.

### Permite (con permisos técnicos generales asignados)

* descubrir activos;
* configurar activos;
* activar o desactivar activos;
* revisar payloads técnicos;
* utilizar la consola global;
* reintentar submissions;
* asociar submissions;
* realizar operaciones técnicas de procesamiento.

### Restricciones

* Los permisos técnicos generales deben asignarse únicamente a personal técnico autorizado.
* Los permisos técnicos generales no forman parte automática de ningún rol operativo.
* El acceso técnico a Kobo no implica permiso para modificar información financiera.
* Los payloads crudos y los adjuntos sensibles permanecen protegidos.

## 9. Flujo Kobo asociado a proyectos

La consulta ordinaria de fichas importadas e historial Kobo asociados a un
proyecto requiere:

```text
operations.view_project
```

No existen endpoints HTTP de aprobación, rechazo o restauración humana de
submissions Kobo. La importación ordinaria es automática; el reintento técnico
de importación e inspección de incidencias viven en el hub territorial y requieren
permisos `kobo.*` (lectura/cambio según la acción).

### Reglas

* El permiso sobre el proyecto habilita la consulta funcional de resultados ya
  importados y del historial del proyecto.
* La información cruda y sensible continúa protegida por permisos técnicos `kobo.*`.
* Un usuario puede consultar resultados normalizados sin acceder necesariamente al payload original.
* La integración Kobo no debe utilizar permisos operativos para exponer información técnica sensible.
* Distinguir: (1) revisión de gobernanza de `ProjectUpdate`; (2) inspección
  técnica de incidencias Kobo en el hub; (3) la retirada revisión humana de
  submissions Kobo (sin endpoints).

## 10. Superusuario de Django

El superusuario:

* ignora la matriz ordinaria de permisos;
* puede acceder al panel de administración (`/admin/`);
* es el único actor autorizado para gestionar identidades institucionales en
  `/panel/usuarios/` (ver §5.1);
* puede utilizar herramientas técnicas;
* puede consultar y operar sobre todos los módulos habilitados;
* puede no tener ningún rol funcional SIGEDON.

### Restricciones de uso

* Debe reservarse para la administración técnica del sistema y para el
  bootstrap inicial de acceso (`python manage.py createsuperuser` en el
  procedimiento de despliegue).
* `/admin/` queda reservado para recuperación técnica; la gestión
  operativa diaria de cuentas institucionales pasa por `/panel/usuarios/`.
* No debe utilizarse como cuenta operativa diaria.
* Su uso debe limitarse a tareas excepcionales de configuración, soporte o recuperación.
* La existencia del rol no sustituye la asignación correcta de permisos a usuarios ordinarios.
* Incluso como superusuario, Django Admin no permite eliminar proyectos: el Admin
  deniega la eliminación y el modelo/queryset la rechazan. La garantía es de
  aplicación, no de base de datos.
* Incluso como superusuario, `DonationAdmin` es de solo lectura (sin alta, cambio
  ni borrado): las mutaciones de donación deben pasar por `create_donation` /
  `update_donation` en la UI operativa, que aplican la invariante
  monto ≥ asignaciones no anuladas y la elegibilidad de instituciones activas.
* Incluso como superusuario, `ExpenseAdmin` es solo inspección (sin alta, cambio
  ni borrado, ni mutación de inlines de soporte): la creación ocurre únicamente
  al cumplir una `ExpenseRequest` aprobada; anulación y demás mutaciones pasan
  por los servicios/UI de SIGEDON. `ExpenseForm` en la UI operativa es solo
  edición (no crea gastos). La reasignación de asignación la valida
  `update_expense` (elegibilidad estructural + saldo); el formulario solo
  estrecha opciones. Los documentos soporte siguen el ciclo
  protegido de la aplicación, no el Admin.
* Incluso como superusuario, `SupportingDocumentAdmin` es solo inspección (sin
  alta, cambio, borrado, reemplazo de archivo ni reasignación de gasto): la
  carga, vista previa y descarga pasan por rutas protegidas de SIGEDON; el Admin
  no expone URLs directas de almacenamiento ni muta el ciclo de vida del
  documento.

## 11. Usuario autenticado sin permisos

Un usuario autenticado sin permisos específicos puede iniciar sesión y abrir el dashboard básico.

No recibe:

* métricas financieras;
* actividad reciente;
* auditoría;
* navegación hacia módulos restringidos;
* consultas sensibles;
* acciones operativas.

La autenticación por sí sola no concede acceso al dominio funcional. Cero rol
funcional es un estado válido.

## 12. Usuario público

El usuario público no requiere autenticación.

Puede consultar únicamente:

* el portal público;
* proyectos autorizados;
* avances publicados;
* métricas agregadas;
* respuestas JSON autorizadas.

No puede acceder a:

* panel interno;
* información financiera individual;
* documentos privados;
* auditoría;
* payloads Kobo;
* usuarios;
* notas internas;
* datos anulados.

## 13. Control de acceso en el dashboard

El dashboard filtra tanto la presentación visual como la consulta de datos.
Los accesos rápidos se eliminaron del dashboard: la navegación operativa
permanece en el sidebar.

```text
view_donation
→ KPI Fondos recibidos (solo Donation.Status.RECEIVED) y actividad de ingresos

view_fundallocation
→ KPI Fondos asignados

view_expense
→ KPI Gastos registrados y actividad de gastos

view_auditlog
→ actividad reciente de auditoría

view_donation + view_fundallocation
→ KPI Fondos sin asignar (recibidos − asignados, mínimo 0)
→ ratio Asignación de fondos

view_fundallocation + view_expense
→ ratio Ejecución financiera
→ bloque «Estado financiero por proyecto» (DASH-FIN3): lista acotada (10),
  reservation-aware (Reservado / Disponible operativo), sin ranking;
  «Ver todos los proyectos» cuando hay más de 10
```

Las reservas de solicitudes de gasto no intervienen en los cuatro KPI ni en
las dos ratios del panel global (DASH-FIN1). Sí restan en el Disponible
operativo del listado por proyecto y del detalle interno del proyecto.

### Colas de solicitudes de gasto (DASH-FIN2)

El panel muestra colas de acción/seguimiento **solo** con el alcance autorizado
del usuario. Los contadores nunca superan el queryset accesible.

```text
fulfill_expenserequest
→ cola «Aprobadas pendientes de registrar gasto»
→ acción: Registrar gasto (expense_request_fulfill)
→ Ver todas: ?status=approved_reserved

decide_expenserequest
→ cola «Solicitudes pendientes de decisión»
→ acción: Revisar solicitud (detalle)
→ Ver todas: ?status=pending_decision

view_expenserequest sin fulfill ni decide
→ Operador (ownership): «Mis solicitudes activas» · Ver solicitud
→ Auditor/lectura global: «Solicitudes de gasto en seguimiento» · Ver solicitud
```

El superusuario ve ambas colas accionables cuando aplican. La sección se omite
si el usuario carece de visibilidad de solicitudes. Con colas autorizadas vacías
se muestra un estado positivo consolidado (sin tarjetas vacías repetidas).

### Colas de gobernanza de avances (FLOW-COMMITTEE-QUEUES)

Visible solo con al menos uno de `review_projectupdate`, `decide_projectupdate`
o `resolve_projectupdateremediation` (Comité canónico y superusuario; Admin no
las recibe por defecto porque esos permisos están excluidos del rol). Cada cola
usa el selector canónico correspondiente y solo se renderiza si el usuario tiene
el permiso de esa cola:

```text
review_projectupdate
→ «Pendientes de revisión»
→ PUBLISHED sin ProjectUpdateReview
→ CTA: Revisar avance (detalle del avance)

decide_projectupdate
→ «Pendientes de decisión»
→ revisión sin ProjectUpdateReviewDecision
→ CTA: Emitir decisión (detalle de la revisión)

resolve_projectupdateremediation
→ «Remediaciones por resolver»
→ remediación SUBMITTED
→ CTA: Resolver remediación (detalle de remediación)
```

Vista previa acotada (5). Los conteos coinciden con el mismo queryset. Sin
permiso de acción no hay filas, conteos ni etiquetas en el contexto. Permisos
parciales (p. ej. solo `decide_projectupdate`) muestran únicamente esa cola.
Las colas no sustituyen las validaciones de detalle/servicio.

### Estado financiero por proyecto (DASH-FIN3)

Visible solo con `view_fundallocation` **y** `view_expense` (Administrador,
Auditor, superusuario). Operador y Comité no ven el bloque ni reciben montos
en el contexto. El detalle interno del proyecto aplica **exactamente** la misma
regla de permisos (`user_can_view_project_financials`): sin ambos permisos no se
calcula el resumen ni se expone en contexto/HTML; con ambos, las etiquetas y
fórmulas reservation-aware coinciden con DASH-FIN3. El portal público permanece
sin cambios.

Atajos de navegación de solicitudes (ER7) viven en el sidebar y en los listados;
el dashboard no restaura Accesos rápidos:

```text
fulfill_expenserequest
→ Ver solicitudes de gasto (sidebar / listados)
→ filtro aprobadas pendientes de registrar gasto (?status=approved_reserved)

decide_expenserequest (sin fulfill)
→ filtro solicitudes pendientes de decisión (?status=pending_decision)

view_expenserequest (sin fulfill ni decide)
→ Mis solicitudes de gasto (listado)
→ guía: se crean desde el detalle de un proyecto
```

No se muestra un CTA de creación directa de `Expense` (`Crear gasto` /
`expense_create`). La creación global de solicitudes permanece en el encabezado
del listado para quien tenga `add_expenserequest` con alcance global; el Operador
no recibe atajo de creación desde el dashboard.

### Regla de seguridad

Cuando el usuario carece de un permiso:

* el componente visual correspondiente no se muestra;
* la consulta sensible no debe ejecutarse;
* la información no debe enviarse al contexto del template;
* el control no debe depender únicamente de ocultar elementos en la interfaz.

## 14. Principios de autorización

La matriz de permisos sigue estas reglas:

* autenticación y autorización son controles diferentes;
* los permisos se validan en el servidor;
* los roles operativos y técnicos se mantienen separados;
* los usuarios reciben únicamente los permisos necesarios;
* los permisos de consulta no implican permisos de modificación;
* las acciones críticas requieren permisos explícitos;
* la interfaz no sustituye las validaciones de backend;
* el superusuario no representa un rol operativo ordinario;
* un usuario ordinario tiene como máximo un rol funcional canónico.

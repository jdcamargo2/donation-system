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
  donaciones, asignaciones o gastos; terminar proyectos);
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
  (`finish_project` / «Terminar proyecto»).
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
* editar y retirar **solo** solicitudes propias en `PENDING_DECISION`.

En la UI (ER3A+ER3B), el listado/detalle de solicitudes se limita a
las creadas por el mismo usuario (`requested_by`); no ve solicitudes de otros
Operadores. El listado no muestra «Nueva solicitud» global; el CTA es
«Solicitar gasto» en el detalle del proyecto. En ER4A no ve «Aprobar» ni
«Denegar»; esas rutas responden 403. En ER4B no ve «Anular solicitud»; la ruta
de anulación responde 403.

Al registrar un avance, el usuario autenticado se asigna automáticamente como
persona responsable. El campo se muestra en modo no editable.

Los datos de Kobo ya integrados en un proyecto pueden seguir siendo visibles
cuando el acceso se gobierna por `operations.view_project`; eso no implica
acceso al panel de administración territorial.

### No puede

* crear proyectos;
* publicar ni retirar proyectos del portal;
* gestionar finanzas (donaciones, asignaciones, gastos);
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

En ER3A/ER3B el Auditor ve **todas** las solicitudes visibles en listado/detalle
(read-only global), con el ítem de sidebar «Solicitudes de gasto»; el ocultamiento
de accesos rápidos del panel no afecta esa navegación. No ve «Solicitar gasto»,
«Editar», «Retirar», «Aprobar» ni «Denegar». En ER4A las rutas de decisión
responden 403. En ER4B no ve «Anular solicitud»; la ruta de anulación responde
403.

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
«Anular solicitud» (ER4B; la ruta responde 403). Aún no hay UI de cumplimiento.

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
* gestionar finanzas.

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

La consulta ordinaria de fichas asociadas a un proyecto requiere:

```text
operations.view_project
```

La importación, el rechazo o la restauración de submissions asociadas a un proyecto requieren:

```text
operations.change_project
```

### Reglas

* El permiso sobre el proyecto habilita las acciones funcionales asociadas a ese proyecto.
* La información cruda y sensible continúa protegida por permisos técnicos `kobo.*`.
* Un usuario puede consultar resultados normalizados sin acceder necesariamente al payload original.
* La integración Kobo no debe utilizar permisos operativos para exponer información técnica sensible.

## 10. Superusuario de Django

El superusuario:

* ignora la matriz ordinaria de permisos;
* puede acceder al panel de administración;
* puede utilizar herramientas técnicas;
* puede consultar y operar sobre todos los módulos habilitados;
* puede no tener ningún rol funcional SIGEDON.

### Restricciones de uso

* Debe reservarse para la administración técnica del sistema.
* No debe utilizarse como cuenta operativa diaria.
* Su uso debe limitarse a tareas excepcionales de configuración, soporte o recuperación.
* La existencia del rol no sustituye la asignación correcta de permisos a usuarios ordinarios.
* Incluso como superusuario, Django Admin no permite eliminar proyectos: el Admin
  deniega la eliminación y el modelo/queryset la rechazan. La garantía es de
  aplicación, no de base de datos.

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

```text
view_donation
→ total y actividad de donaciones

view_fundallocation
→ total asignado

view_expense
→ total ejecutado y gastos recientes

view_auditlog
→ actividad reciente de auditoría

view_donation + view_fundallocation
→ saldo global disponible
```

Además, el bloque de accesos rápidos (`show_financial_quick_actions`) se oculta
únicamente para el rol funcional `Auditor externo`. Los demás roles y usuarios
sin rol canónico siguen viendo el bloque; los botones internos siguen filtrados
por permisos efectivos. Ocultar el bloque no revoca permisos.

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

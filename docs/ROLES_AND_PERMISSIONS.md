# Roles y permisos de SIGEDON

Este documento define los roles operativos de SIGEDON, sus permisos, capacidades y restricciones.

## 1. Administrador SIGEDON

El Administrador SIGEDON recibe todos los permisos de la aplicación `operations`, excepto los relacionados con la mutación del registro de auditoría:

```text
add_auditlog
change_auditlog
delete_auditlog
```

### Puede gestionar

* instituciones;
* proyectos;
* hitos verificables de proyectos, incluidos completar, reabrir y reordenar;
* donaciones;
* asignaciones de fondos;
* gastos;
* documentos;
* avances;
* publicación de avances;
* acciones terminales;
* consulta de auditoría.

### Restricciones

* No puede crear, modificar ni eliminar registros de `AuditLog`.
* No recibe automáticamente permisos técnicos de KoboToolbox.
* Los permisos `kobo.*` deben asignarse por separado.

El Hub territorial requiere `kobo.view_territorial_administration`. Las
operaciones requieren además permisos explícitos de mappings, estado de
identidad, resolución de conflictos o reconciliación.

## 2. Operador de campo

### Permisos

```text
view_project
view_projectupdate
add_projectupdate
view_supportingdocument
add_supportingdocument
```

### Puede

* consultar proyectos;
* consultar avances;
* registrar avances;
* cargar adjuntos durante el registro;
* consultar soportes autorizados;
* registrar soportes permitidos.

### No puede

* crear proyectos;
* gestionar donaciones;
* gestionar asignaciones;
* registrar gastos;
* editar avances después del registro;
* publicar avances;
* revisar avances en nombre del Comité;
* consultar la auditoría global.

## 3. Auditor externo

### Permisos

```text
view_institution
view_project
view_donation
view_fundallocation
view_expense
view_supportingdocument
view_projectupdate
view_auditlog
```

### Alcance

El Auditor externo es un rol de solo lectura.

Puede consultar:

* instituciones;
* proyectos;
* donaciones;
* asignaciones;
* gastos;
* soportes;
* avances;
* auditoría.

### Restricciones

No puede:

* crear información;
* modificar información;
* anular registros;
* eliminar registros;
* ejecutar acciones terminales.

## 4. Comité de proyectos

### Permisos

```text
view_project
view_projectupdate
view_projectdocument
view_projectupdateattachment
view_projectupdatereview
add_projectupdatereview
view_projectupdatereviewdecision
add_projectupdatereviewdecision
```

### Puede

* consultar proyectos;
* consultar avances;
* consultar documentos de proyecto;
* consultar evidencias de avances;
* registrar una revisión institucional;
* registrar una decisión institucional.

### No puede

* modificar el contenido original del avance;
* publicar avances;
* eliminar avances;
* modificar una revisión ya registrada;
* modificar una decisión ya registrada.

La revisión y la decisión institucional se registran en entidades separadas y no alteran el estado ni el contenido del avance.

## 5. Administración técnica de KoboToolbox

La administración técnica de Kobo utiliza permisos de la aplicación `kobo`.

### Permisos representativos

```text
view_koboasset
change_koboasset
view_kobosubmission
change_kobosubmission
```

La matriz real puede incluir permisos adicionales según los modelos y acciones técnicas disponibles.

### Permisos de administración territorial

```text
view_territorial_administration
manage_pastoral_zone_mappings
resolve_territorial_conflicts
change_territorial_identity_status
run_territorial_reconciliation
```

`Administrador SIGEDON` recibe los cinco permisos al sincronizar roles. Operador
de campo, Auditor externo y Comité reciben únicamente
`view_territorial_administration`; no pueden configurar mappings, resolver
conflictos, cambiar estados ni ejecutar reconciliación. Ninguna acción se concede
por poseer solamente `change_kobosubmission`.

### Permite

* descubrir activos;
* configurar activos;
* activar o desactivar activos;
* revisar payloads técnicos;
* utilizar la consola global;
* reintentar submissions;
* asociar submissions;
* realizar operaciones técnicas de procesamiento.

### Restricciones

* Estos permisos deben asignarse únicamente a personal técnico autorizado.
* Los permisos técnicos generales no forman parte automática de ningún rol
  operativo; la excepción explícita son los permisos territoriales descritos arriba.
* El acceso técnico a Kobo no implica permiso para modificar información financiera.
* Los payloads crudos y los adjuntos sensibles permanecen protegidos.

## 6. Flujo Kobo asociado a proyectos

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

## 7. Superusuario de Django

El superusuario:

* ignora la matriz ordinaria de permisos;
* puede acceder al panel de administración;
* puede utilizar herramientas técnicas;
* puede consultar y operar sobre todos los módulos habilitados.

### Restricciones de uso

* Debe reservarse para la administración técnica del sistema.
* No debe utilizarse como cuenta operativa diaria.
* Su uso debe limitarse a tareas excepcionales de configuración, soporte o recuperación.
* La existencia del rol no sustituye la asignación correcta de permisos a usuarios ordinarios.

## 8. Usuario autenticado sin permisos

Un usuario autenticado sin permisos específicos puede iniciar sesión y abrir el dashboard básico.

No recibe:

* métricas financieras;
* actividad reciente;
* auditoría;
* navegación hacia módulos restringidos;
* consultas sensibles;
* acciones operativas.

La autenticación por sí sola no concede acceso al dominio funcional.

## 9. Usuario público

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

## 10. Control de acceso en el dashboard

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

### Regla de seguridad

Cuando el usuario carece de un permiso:

* el componente visual correspondiente no se muestra;
* la consulta sensible no debe ejecutarse;
* la información no debe enviarse al contexto del template;
* el control no debe depender únicamente de ocultar elementos en la interfaz.

## 11. Principios de autorización

La matriz de permisos sigue estas reglas:

* autenticación y autorización son controles diferentes;
* los permisos se validan en el servidor;
* los roles operativos y técnicos se mantienen separados;
* los usuarios reciben únicamente los permisos necesarios;
* los permisos de consulta no implican permisos de modificación;
* las acciones críticas requieren permisos explícitos;
* la interfaz no sustituye las validaciones de backend;
* el superusuario no representa un rol operativo ordinario.

## 12. Sincronización de roles

Los grupos y permisos se crean o actualizan mediante:

```bash
python manage.py sync_sigedon_roles
```

La operación debe ser:

* idempotente;
* segura frente a ejecuciones repetidas;
* capaz de añadir permisos faltantes;
* capaz de eliminar permisos incompatibles heredados;
* consistente con la matriz definida en el código.

Después de modificar la definición de roles, debe ejecutarse nuevamente el comando y verificarse el resultado mediante pruebas automatizadas.

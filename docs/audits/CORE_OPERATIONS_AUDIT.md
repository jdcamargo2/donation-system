# Auditoría del core operativo de SIGEDON

## Resumen ejecutivo

El MVP operativo es **funcional pero no está listo para operar como sistema financiero/auditable sin una fase de endurecimiento**. El flujo principal puede recorrerse desde institución hasta revisión de avances, las vistas aplican permisos de modelo, y los servicios de asignaciones/gastos usan transacciones y bloqueos. Las 104 pruebas de `apps.operations` y las 65 pruebas heredadas de `web` pasan.

La evaluación encontró **3 hallazgos P0, 9 P1, 7 P2 y 3 P3**. Los bloqueadores son: el registro de auditoría es editable/eliminable; documentos legales y evidencias se enlazan mediante URLs de media sin autorización de objeto; y las invariantes monetarias no existen en la base de datos ni tienen una prueba concurrente válida para el motor objetivo. Los estados también pueden cambiar sin una máquina de transición y un gasto validado puede volver a un estado ordinario conservando metadatos de validación.

Conclusión: completar primero una fase de integridad, archivos privados y auditoría inmutable; después consolidar transiciones, separación de funciones y legado. No se implementó ninguna corrección durante esta auditoría.

### Alcance y método

- Inspección estática: `core/`, `apps/operations/`, `templates/operations/`, `templates/web/`, `web/`, migraciones y tests relacionados.
- Dependencias externas revisadas solo cuando afectan `Project`: consulta Kobo desde `ProjectDetailView` y selectores públicos sobre modelos operations.
- Verificación ejecutada: `manage.py check`, detección de migraciones, suite operations y suite web.
- Limitación: SQLite es el motor configurado; sus bloqueos no demuestran el comportamiento esperado de `select_for_update()` en producción.

## Inventario actual

| Área | Inventario | Estado |
|---|---|---|
| Modelos | `Institution`, `Project`, `Donation`, `FundAllocation`, `Expense`, `SupportingDocument`, `ProjectUpdate`, `AuditLog` | Completo para MVP; invariantes parciales |
| Choices/estados | Roles/estados internos en modelos; moneda y categorías en `choices.py` | Completo como catálogo; sin transiciones |
| Servicios | Auditoría, validación de gasto, CRUD monetario transaccado, métricas, registro/revisión de avances | Parcial; no todo cambio pasa por servicio |
| Formularios | CRUD de todas las entidades y revisión; normalización monetaria/fecha | Completo en UI; filtros de dominio parciales |
| Vistas/URLs | Dashboard, CRUD, soporte, descarga, revisión y auditoría | Completo para MVP; varias rutas de riesgo |
| Permisos/roles | Permisos Django + tres grupos sincronizables | Implementado; administrador sin separación de funciones |
| Admin | Todos los modelos registrados | Funcional; bypass y auditoría insuficientes |
| Templates | UI operativa vive principalmente en `templates/web`; detalle Kobo opcional en `templates/operations` | Funcional; nomenclatura heredada |
| Comandos | `sync_sigedon_roles`, `seed_sigedon_demo` | Probados e idempotentes en entidades clave |
| Tests | 104 operations + 65 web; E2E MVP y roles | Amplios en happy path; huecos críticos |
| Migraciones | `0001_initial`, `0002_projectupdate`, `0003` choices | Sin constraints de negocio |
| Core | Django 6, auth estándar, SQLite, media local, rutas operations antes de `web` | Desarrollo funcional; configuración no endurecida |

### Modelos y relaciones

```text
Institution --PROTECT--> Donation --PROTECT--> FundAllocation --PROTECT--> Expense
                                      |                 |                       |
                                      +--> Project <----+                       +--CASCADE--> SupportingDocument
                                             |
                                             +--CASCADE--> ProjectUpdate

AuditLog --SET_NULL--> User; referencia objetos por model_name/entity_id, sin FK
```

`Project` no está relacionado con una institución ejecutora/receptora. Por tanto, el primer enlace del flujo solicitado es conceptual: `Institution → Donation`; `Project` entra después mediante `FundAllocation`, no como responsabilidad institucional.

## Flujo operativo real

| Paso | Implementación real | Clasificación | Evidencia/riesgo principal |
|---|---|---|---|
| Institution | CRUD, admin, permisos, auditoría en vistas | Completo con riesgo | No valida rol donante al crear Donation; documento legal no usa descarga autorizada |
| Donation | CRUD, saldo calculado, USD forzado por form | Parcial | Estados arbitrarios; modelo aún admite otras monedas; sin constraint positivo en DB |
| Project | CRUD, presupuesto y fechas en `clean()` | Parcial | Sin institución responsable; código secuencial sujeto a carrera |
| FundAllocation | CRUD UI llama servicios con `atomic` y locks | Completo en ruta web | Admin/ORM y concurrencia SQLite no garantizan saldo |
| Expense | CRUD UI llama servicios; soporte opcional transaccional | Completo en ruta web | Estados reversibles; validación mezclada con edición |
| SupportingDocument | Adjuntar, listar, descargar con permiso, borrar con guarda | Parcial | Sin validación de archivo; no aplica a evidencia/legal_document |
| Expense validation | Servicio idempotente, actor/fecha y audit log | Completo en backend/UI | Permiso `change_expense` basta para editar y validar; puede revertirse después |
| ProjectUpdate | Registro pendiente y revisión final | Parcial | Crear/editar no audita; final puede editarse/eliminarse; review sin lock |
| Review | Form separado y servicio con estado final | Parcial | GET muestra form correctamente; servicio no es atómico ni exige estado pending |
| Audit | Lista protegida y eventos para acciones seleccionadas | Parcial/riesgo | Registro mutable, eliminable, incompleto y sin snapshot estructurado |

## Matriz de permisos

Matriz efectiva del servidor después de `sync_sigedon_roles`; `Administrador SIGEDON` recibe **todos** los permisos de modelos operations.

| Acción | Administrador SIGEDON | Operador de campo | Auditor externo | Enforcement |
|---|---:|---:|---:|---|
| Ver dashboard y totales financieros | Sí | Sí | Sí | Solo login; no permiso financiero |
| CRUD Institution | Sí | No | Solo ver | Permisos de modelo |
| CRUD Donation | Sí | No | Solo ver | Permisos de modelo |
| CRUD Project | Sí | Solo ver | Solo ver | Permisos de modelo |
| CRUD FundAllocation | Sí | No | Solo ver | Permisos de modelo |
| CRUD Expense / validar | Sí | No | Solo ver | `add/change/delete_expense`; validar no tiene permiso separado |
| Ver/descargar SupportingDocument | Sí | Sí | Sí | `view_supportingdocument` |
| Adjuntar SupportingDocument | Sí | Sí | No | `add_supportingdocument` |
| Borrar SupportingDocument | Sí | No | No | `delete_supportingdocument` |
| Ver ProjectUpdate | Sí | Sí | Sí | `view_projectupdate` |
| Crear ProjectUpdate | Sí | Sí | No | `add_projectupdate` |
| Editar/revisar/borrar ProjectUpdate | Sí | No | No | `change/delete_projectupdate`; revisión no separada |
| Ver AuditLog | Sí | No | Sí | `view_auditlog` |
| Editar/borrar AuditLog vía admin/ORM | Sí | No | No | Administrador recibe `change/delete_auditlog` |

La UI oculta acciones con `perms.operations.*` y las vistas vuelven a exigir permisos. La excepción es `DashboardView`, accesible a cualquier usuario autenticado y con métricas financieras siempre visibles.

## Hallazgos críticos P0

### P0-01 — El registro de auditoría no es inmutable

- **Severidad:** P0.
- **Archivo y símbolo:** `apps/operations/models.py:367-430` (`AuditLog`); `apps/operations/admin.py:146-151` (`AuditLogAdmin`); `apps/operations/role_services.py:13-15`.
- **Evidencia concreta:** `AuditLog` es un modelo ordinario sin protección de `save()`/`delete()`; admin solo marca `created_at` readonly. El grupo administrador recibe todos los permisos, incluidos change/delete. Los propios tests borran logs con `AuditLog.objects.all().delete()`.
- **Impacto:** un administrador o código interno puede alterar/borrar la evidencia que debe demostrar qué ocurrió. La auditoría no es confiable para control externo.
- **Corrección recomendada:** almacenamiento append-only, denegar change/delete en admin y servicios, permisos explícitos solo de lectura, integridad encadenada o exportación WORM y política de retención.
- **Requiere migración:** Sí, si se agregan hash, event UUID, payload estructurado o constraints; no para cerrar permisos admin.
- **Tests necesarios:** intento de update/delete por admin, ORM y endpoints; prueba de integridad/hash; retención y actor eliminado.

### P0-02 — Documentos legales y evidencias eluden autorización de objeto

- **Severidad:** P0.
- **Archivo y símbolo:** `templates/web/institution_detail.html:40-41`; `project_detail.html:70-71`; `project_update_detail.html:40-43`; `project_update_review.html:29-30`; `core/settings.py:151-152`.
- **Evidencia concreta:** los templates enlazan `FileField.url` directamente. Solo `SupportingDocumentDownloadView` abre el archivo tras verificar permiso. En desarrollo, `core.urls` sirve todo `MEDIA_ROOT`; en un despliegue típico, el servidor web podría hacerlo públicamente.
- **Impacto:** conocimiento o filtración de la URL permitiría descargar documentos legales/evidencias sin login ni permiso; afecta información institucional y posiblemente personal.
- **Corrección recomendada:** almacenamiento privado y endpoints autenticados por tipo/objeto, nombres opacos, `Content-Disposition`, autorización coherente y política de expiración.
- **Requiere migración:** No necesariamente; puede requerir migración de archivos/paths operativa, no de esquema.
- **Tests necesarios:** acceso anónimo/sin permiso/con permiso, URL no expuesta, traversal, archivo ausente y backend de almacenamiento remoto.

### P0-03 — La integridad monetaria depende de rutas Python y bloqueos no demostrados

- **Severidad:** P0.
- **Archivo y símbolo:** `models.py:219-232,263-287,328-348`; `services.py:61-94,117-297`; migración `0001_initial.py`.
- **Evidencia concreta:** no hay `CheckConstraint` para montos positivos ni constraints que protejan saldos. `clean()` no se ejecuta en `save()`/`update()` por defecto. Los servicios bloquean padres, pero SQLite configurado en `core/settings.py:105-109` no ofrece la semántica de row-lock esperada; no existe `TransactionTestCase` concurrente.
- **Impacto:** admin, shell, importadores o carreras pueden crear donaciones/asignaciones/gastos inválidos o sobreejecutados; los balances ocultan negativos usando `max(..., 0)`.
- **Corrección recomendada:** constraints positivos en DB, único servicio de escritura, motor transaccional objetivo definido (PostgreSQL), estrategia de serialización y rechazo visible de inconsistencias en vez de truncarlas.
- **Requiere migración:** Sí para checks; posiblemente cambio de infraestructura para motor.
- **Tests necesarios:** escrituras ORM bypass, dos asignaciones/gastos concurrentes, updates cruzando donación/asignación y pruebas sobre el motor objetivo.

## Hallazgos importantes P1

### P1-01 — No existe máquina de transición de estados

- **Severidad:** P1.
- **Archivo y símbolo:** estados en `models.py:68-84,121-136,183-201,236-250,291-318`; formularios exponen `status`.
- **Evidencia concreta:** Project, Donation, FundAllocation y Expense aceptan cualquier choice en create/update. No se valida transición previa→nueva ni coherencia con fechas/saldos.
- **Impacto:** estados imposibles (cerrado sin ejecución, recibido sin fecha, anulado con actividad) y reportes contradictorios.
- **Corrección recomendada:** servicios de transición nominados, tabla de transiciones, guardas de dominio y formularios que muestren solo acciones permitidas.
- **Requiere migración:** Posiblemente, para timestamps/razones/versionado y constraints.
- **Tests necesarios:** matriz completa de transiciones válidas/inválidas, idempotencia y estados terminales.

### P1-02 — Un gasto validado puede revertirse conservando la firma de validación

- **Severidad:** P1.
- **Archivo y símbolo:** `services.py:238-297` (`update_expense`); `models.py:308-316`.
- **Evidencia concreta:** `update_expense` permite pasar de VALIDATED a cualquier status y no limpia ni protege `validated_by/validated_at`. También permite editar monto/asignación después de validar.
- **Impacto:** la aprobación deja de representar el contenido revisado; un gasto puede cambiar después de la validación manteniendo actor y fecha históricos.
- **Corrección recomendada:** hacer el gasto validado inmutable o crear revisión/versionado; separar rechazo/anulación; exigir revalidación tras cambios materiales.
- **Requiere migración:** Recomendable para versión/snapshot/revisión; no para una guarda mínima.
- **Tests necesarios:** edición/reversión de gasto validado, campos materiales, metadatos y revalidación.

### P1-03 — Auditoría incompleta y no atómica con las mutaciones

- **Severidad:** P1.
- **Archivo y símbolo:** `views.py:129-153` (`AuditMixin`, `DeleteAuditMixin`); `views.py:299-370` (ProjectUpdate); `services.py:117-297`.
- **Evidencia concreta:** crear/editar ProjectUpdate no registra evento; admin no audita CRUD general; servicios de allocation/expense mutan y la vista escribe el log después, fuera de la transacción del servicio. Un fallo del log deja cambio sin auditoría.
- **Impacto:** trazabilidad parcial y eventos que no pueden garantizar correspondencia uno-a-uno con commits de dominio.
- **Corrección recomendada:** auditoría dentro de la misma transacción/servicio, catálogo de eventos por mutación, cobertura admin/commands e identificador de correlación.
- **Requiere migración:** Probable para campos estructurados/correlación.
- **Tests necesarios:** fallo al escribir audit, rollback conjunto, cobertura de cada acción y admin.

### P1-04 — La revisión de avances admite carrera y estados de origen inadecuados

- **Severidad:** P1.
- **Archivo y símbolo:** `services.py:393-410` (`review_project_update`).
- **Evidencia concreta:** no usa `transaction.atomic()` ni `select_for_update()`. Rechaza solo estados finales; un DRAFT podría aprobarse invocando el servicio directamente.
- **Impacto:** dos revisores pueden generar decisiones/logs contradictorios y saltar el paso pending_review.
- **Corrección recomendada:** lock, atomicidad con audit, exigir `PENDING_REVIEW`, reviewer autenticado y control optimista/versionado.
- **Requiere migración:** No para lock/guardas; sí si se agrega versión.
- **Tests necesarios:** dos revisiones concurrentes, draft→approved rechazado, actor anónimo y rollback de audit.

### P1-05 — Avances finales siguen editables y eliminables

- **Severidad:** P1.
- **Archivo y símbolo:** `views.py:353-370` (`ProjectUpdateUpdateView`, `ProjectUpdateDeleteView`); `forms.py:173-203`.
- **Evidencia concreta:** las vistas no filtran por estado; un usuario con change/delete puede modificar título, descripción, evidencia o borrar un avance aprobado/rechazado.
- **Impacto:** la evidencia publicada/revisada puede cambiar sin nueva revisión y sin log de edición.
- **Corrección recomendada:** bloquear finalizados, usar revisiones inmutables o crear nueva versión; delete lógico con motivo.
- **Requiere migración:** Recomendable para versionado/soft delete.
- **Tests necesarios:** GET/POST update/delete sobre finalizados y creación de revisión sucesora.

### P1-06 — El dominio no enlaza Project con una institución responsable

- **Severidad:** P1.
- **Archivo y símbolo:** `models.py:34-94` (`Institution`, `Project`); `models.py:182-217` (`Donation`).
- **Evidencia concreta:** Project solo guarda `responsible_unit` textual. Donation acepta cualquier Institution como donor sin validar rol/estado. Formularios incluyen todas las instituciones/proyectos.
- **Impacto:** el flujo Institution→Donation→Project no es trazable de extremo a extremo; se pueden usar entidades inactivas o de rol incorrecto.
- **Corrección recomendada:** definir relaciones/roles de responsabilidad, validar institución donante activa y entidades elegibles para asignación.
- **Requiere migración:** Sí para FK/tabla de roles de proyecto.
- **Tests necesarios:** rol incorrecto, entidad inactiva, responsable de proyecto y cambios históricos.

### P1-07 — Los archivos no tienen política común de seguridad

- **Severidad:** P1.
- **Archivo y símbolo:** FileFields en `models.py:53,131,354`; forms correspondientes.
- **Evidencia concreta:** no hay límites de tamaño, allowlist de tipo/extensión, firma mágica, antivirus, cuarentena ni clasificación de sensibilidad.
- **Impacto:** almacenamiento abusivo, contenido ejecutable/malicioso y tratamiento inconsistente de datos sensibles.
- **Corrección recomendada:** validador/servicio común de uploads, límites nombrados, inspección de contenido, nombres seguros, storage privado y retención.
- **Requiere migración:** No salvo metadatos/estado de escaneo.
- **Tests necesarios:** tamaño, MIME falso, extensión doble, archivo vacío, nombre malicioso y escaneo fallido.

### P1-08 — El dashboard filtra acciones, pero no autoriza métricas financieras

- **Severidad:** P1.
- **Archivo y símbolo:** `views.py:41-47` (`DashboardView`); `templates/web/dashboard.html:64-97`.
- **Evidencia concreta:** cualquier usuario autenticado accede; las cuatro métricas financieras se renderizan sin checks. Los roles solo gobiernan enlaces/listas.
- **Impacto:** usuarios sin permisos Donation/Allocation/Expense reciben totales financieros globales.
- **Corrección recomendada:** permiso explícito de dashboard financiero o métricas filtradas por capacidades/alcance.
- **Requiere migración:** No; sí sincronización de permisos si se crea uno nuevo.
- **Tests necesarios:** usuario sin permisos, operador de campo, auditor y admin verificando contenido, no solo status 200.

### P1-09 — El rol administrador viola separación de funciones

- **Severidad:** P1.
- **Archivo y símbolo:** `role_services.py:8-24` (`sync_operation_roles`).
- **Evidencia concreta:** asigna todos los permisos operations al grupo administrador, combinando registrar, validar, revisar, borrar y modificar auditoría.
- **Impacto:** una sola cuenta puede originar, aprobar y borrar evidencia; eleva riesgo de fraude/error no detectado.
- **Corrección recomendada:** permisos de acciones de dominio (`validate_expense`, `review_projectupdate`), roles de registrador/revisor/auditor y prohibición de autoaprobación.
- **Requiere migración:** Sí si se declaran custom permissions; datos para resincronizar grupos.
- **Tests necesarios:** matriz acción×rol, autoaprobación, admin operativo vs administrador técnico y auditor read-only.

## Deuda técnica P2/P3

### P2-01 — Validaciones críticas están repartidas entre model, form, service y admin

- **Severidad:** P2.
- **Archivo y símbolo:** `models.py:101-108,219-221,263-270,328-348`; `forms.py:350-357`; `admin.py:11-50`; `services.py:54-94`.
- **Evidencia concreta:** moneda, soporte y saldos tienen implementaciones solapadas con semánticas distintas.
- **Impacto:** nuevas rutas pueden omitir una capa o divergir.
- **Corrección recomendada:** invariantes en modelo/DB y casos de uso en servicios; forms/admin solo adaptan errores.
- **Requiere migración:** Parcialmente.
- **Tests necesarios:** contract tests compartidos entre web/admin/service/ORM.

### P2-02 — Generación secuencial de códigos es vulnerable a carreras

- **Severidad:** P2.
- **Archivo y símbolo:** `models.py:23-31,96-99,214-217` (`_next_sequential_code`).
- **Evidencia concreta:** calcula desde último id y consulta existencia sin lock; la unicidad solo produce `IntegrityError` sin retry.
- **Impacto:** altas concurrentes pueden fallar de forma intermitente.
- **Corrección recomendada:** secuencia DB, UUID/código no secuencial o contador bloqueado con retry acotado.
- **Requiere migración:** Probable.
- **Tests necesarios:** creación concurrente y retry.

### P2-03 — Los saldos ocultan inconsistencia mediante truncado a cero

- **Severidad:** P2.
- **Archivo y símbolo:** `models.py:229-232,284-287`; `services.py:328,346`.
- **Evidencia concreta:** `max(balance, ZERO_MONEY)` convierte sobreasignación/sobreejecución en saldo cero.
- **Impacto:** dashboards no distinguen “agotado” de “dato corrupto”.
- **Corrección recomendada:** preservar valor real internamente, levantar diagnóstico/invariante y exponer alerta.
- **Requiere migración:** No.
- **Tests necesarios:** fixtures corruptos y señal de diagnóstico.

### P2-04 — Fechas y estados no tienen coherencia de dominio

- **Severidad:** P2.
- **Archivo y símbolo:** `Donation.clean`, `Project.clean`, campos de fecha en modelos.
- **Evidencia concreta:** solo Project compara inicio/fin. Donation no relaciona commitment/received con estado; allocation/expense aceptan fechas fuera de proyecto/donación.
- **Impacto:** cronologías imposibles y reportes temporales débiles.
- **Corrección recomendada:** reglas explícitas por transición y política de fechas operativas.
- **Requiere migración:** No salvo timestamps de transición.
- **Tests necesarios:** matrices de fechas límite y estados.

### P2-05 — AuditLog usa referencias y texto no estructurados

- **Severidad:** P2.
- **Archivo y símbolo:** `models.py:378-390`; `services.py:21-51`.
- **Evidencia concreta:** `model_name`, `entity_id`, `entity_label`, `summary`; no before/after, request/correlation id, IP/canal ni tipo estable.
- **Impacto:** filtros, reconstrucción y análisis forense limitados; traducciones legacy mezcladas.
- **Corrección recomendada:** tipo estable, snapshot seguro de cambios, correlation id y metadata allowlisted.
- **Requiere migración:** Sí.
- **Tests necesarios:** serialización segura, PII excluida, filtros e historial por entidad eliminada.

### P2-06 — Dependencia inversa de Operations hacia Kobo

- **Severidad:** P2.
- **Archivo y símbolo:** `views.py:222-243` (`ProjectDetailView`).
- **Evidencia concreta:** cuando Kobo está activo, la vista operations importa modelos/servicios Kobo y cambia template.
- **Impacto:** Operations no es completamente independiente; fallos/imports de integración afectan detalle de Project.
- **Corrección recomendada:** adaptador/context provider registrado desde integración o API de consulta neutral.
- **Requiere migración:** No.
- **Tests necesarios:** integración ausente/deshabilitada/error de consulta y carga del detalle core.

### P2-07 — Settings de desarrollo no tienen guardas de despliegue

- **Severidad:** P2.
- **Archivo y símbolo:** `core/settings.py:23-51,105-109`; `templates/base.html:8-9,125-126`.
- **Evidencia concreta:** secret fallback inseguro, DEBUG por defecto True, `ALLOWED_HOSTS=[]`, SQLite y dependencias JS/CSS desde CDN sin SRI.
- **Impacto:** riesgo de configuración accidental y disponibilidad/seguridad dependiente de terceros.
- **Corrección recomendada:** settings por entorno, fail-fast de secrets, deployment check, CSP/SRI o assets locales y motor productivo explícito.
- **Requiere migración:** No, salvo cambio de motor operativo.
- **Tests necesarios:** `check --deploy` con settings productivos y smoke sin red.

### P3-01 — `web/` es un cascarón legado que conserva tests y namespace

- **Severidad:** P3.
- **Archivo y símbolo:** `web/models.py`, `views.py`, `forms.py`, `admin.py`, `urls.py`; `core/urls.py:29-30`.
- **Evidencia concreta:** módulos solo contienen docstrings y URLpatterns vacío; 65 tests en `web/tests` prueban realmente `apps.operations`.
- **Impacto:** navegación conceptual y ownership de tests confusos; app instalada sin responsabilidad real.
- **Corrección recomendada:** mover tests a operations, retirar include/app cuando no haya consumidores y documentar compatibilidad.
- **Requiere migración:** No; comprobar content types históricos antes de retirar app.
- **Tests necesarios:** resolución de URLs, imports y suite reubicada.

### P3-02 — Templates operativos permanecen bajo `templates/web`

- **Severidad:** P3.
- **Archivo y símbolo:** vistas operations y `templates/web/*`; solo `templates/operations/project_detail.html` usa namespace nuevo.
- **Evidencia concreta:** naming mezclado y template alterno condicionado por Kobo.
- **Impacto:** dificulta saber qué es legado y qué es canónico.
- **Corrección recomendada:** migración gradual a `templates/operations`, aliases temporales y eliminación posterior.
- **Requiere migración:** No.
- **Tests necesarios:** template names y render por feature flag.

### P3-03 — PRE/POST es inconsistente en funciones relevantes

- **Severidad:** P3.
- **Archivo y símbolo:** helpers de auditoría `services.py:34-51`, servicios de review y métodos de vistas/admin.
- **Evidencia concreta:** funciones importantes tienen contratos parciales o describen precondiciones no verificadas (por ejemplo reviewer/actor).
- **Impacto:** contratos aparentes pueden dar confianza mayor que las guardas reales.
- **Corrección recomendada:** alinear contrato, tipos, checks y excepciones; documentar side effects y atomicidad.
- **Requiere migración:** No.
- **Tests necesarios:** precondiciones inválidas y mensajes de error.

## Cobertura de tests

| Área | Cobertura observada | Evaluación | Huecos prioritarios |
|---|---|---|---|
| Project | Modelo, form, CRUD, permisos, UI, totales | Buena | código concurrente, institución responsable, estados |
| Donation | Modelo/form/CRUD/saldos/USD/dashboard | Buena | rol/estado donor, transiciones, ORM bypass |
| FundAllocation | servicios create/update, saldos y vistas | Buena secuencial | concurrencia real, admin/ORM bypass, estados |
| Expense | servicio, form, soporte, validación, admin | Buena | post-validación, separación de permiso, concurrencia |
| SupportingDocument | attach/download/delete/permisos | Buena ruta web | tipo/tamaño, storage privado, admin concurrente |
| ProjectUpdate | create/review/UI/permisos | Media-alta | audit create/edit, final inmutable, race, draft review |
| Permisos/roles | grupos, rutas y visibilidad UI | Buena | dashboard totals, permisos de acción, object scope |
| Auditoría | eventos críticos/lista | Parcial | inmutabilidad, admin, atomicidad, cobertura total |
| Concurrencia | locks inspeccionados | Ausente | no hay `TransactionTestCase`/threads/motor objetivo |
| Formularios | dinero/fecha/errores/soporte | Buena | archivos y transiciones |
| Comandos | roles y seed idempotente | Buena | fallo parcial/atomicidad, credenciales demo productivas |
| E2E | flujo MVP completo | Buena happy path | roles separados, fallos y rollback |

Resultados ejecutados el 2026-07-11:

- `./venv/bin/python manage.py check`: 0 issues.
- `./venv/bin/python manage.py makemigrations --check --dry-run`: no changes detected.
- `./venv/bin/python manage.py test apps.operations.tests`: 104/104 OK.
- `./venv/bin/python manage.py test web.tests`: 65/65 OK.

Los tests demuestran funcionamiento secuencial bajo SQLite, no ausencia de los riesgos P0/P1 descritos.

## Código legado y duplicación

- `apps.operations` es el dueño real de modelos, forms, views, URLs, admin y servicios.
- `web` permanece instalado, pero sus módulos productivos son cascarones y `web.urls` está vacío.
- `web/tests` sigue siendo una suite válida, aunque prueba código operations; debe reubicarse antes de retirar la app.
- `templates/web` contiene casi toda la UI canónica de operations. No es duplicación de ejecución, pero sí deuda de namespace.
- `templates/operations/project_detail.html` es una variante Kobo-aware. `ProjectDetailView` decide entre ambas y crea acoplamiento desde core hacia integración.
- `apps.public_portal` depende legítimamente de modelos/selectores operations, pero no de sus vistas/forms; esa dirección es adecuada.

## Roadmap recomendado

1. **Bloquear P0 antes de nuevas features:** auditoría append-only, media privada autorizada, constraints/engine/concurrencia financiera.
2. **Hacer explícitas las transiciones:** estados y revisión/validación como comandos de dominio, no campos editables.
3. **Cerrar trazabilidad:** audit dentro de cada transacción, snapshots seguros, correlation IDs y cobertura admin/commands.
4. **Separar funciones:** permisos específicos de validar/revisar, no autoaprobación y dashboard autorizado.
5. **Completar el dominio institucional:** roles válidos y relación responsable de Project.
6. **Unificar archivos:** storage privado, validación/escaneo y endpoints autorizados.
7. **Consolidar legado:** mover tests/templates, retirar app/include `web` y desacoplar Kobo.

## Fases propuestas

### Fase A — Contención inmediata

- Denegar modificación/eliminación de AuditLog y acceso directo a media sensible.
- Bloquear edición/reversión de gastos validados y avances finalizados.
- Añadir tests negativos de los tres P0.

### Fase B — Integridad transaccional

- Definir PostgreSQL como motor objetivo.
- Añadir constraints y servicios únicos de escritura.
- Ejecutar pruebas concurrentes reales de asignación, gasto, validación y review.

### Fase C — Workflow y autorización

- Implementar máquinas de transición.
- Crear permisos de validar/revisar y separación de funciones.
- Autorizar dashboard y archivos por alcance.

### Fase D — Auditoría y datos

- Evolucionar AuditLog a evento append-only estructurado.
- Completar eventos de ProjectUpdate, admin, comandos y deletes.
- Incorporar alertas para balances inconsistentes.

### Fase E — Dominio y consolidación

- Modelar responsabilidad institucional de Project.
- Retirar legado `web` tras mover tests/templates.
- Extraer integración Kobo del detalle core mediante adaptador.

### Preguntas abiertas antes de diseñar correcciones

- ¿Cuál será el motor productivo y el nivel de aislamiento requerido?
- ¿Los documentos legales/evidencias contienen PII o requieren retención normativa específica?
- ¿Quién puede originar y quién puede aprobar gastos/avances en la organización real?
- ¿Un gasto validado puede corregirse mediante nueva versión o debe anularse y recrearse?
- ¿Project tiene una institución ejecutora única o múltiples instituciones con roles y vigencia?

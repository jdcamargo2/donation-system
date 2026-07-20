# Integración con KoboToolbox

Este documento describe la integración entre KoboToolbox y SIGEDON, incluyendo su configuración, flujo de procesamiento, revisión, privacidad y trazabilidad.

## 1. Objetivo

La integración permite:

* recibir levantamientos de campo;
* conservar el payload original;
* normalizar la información;
* validar los datos;
* asociar submissions con proyectos SIGEDON;
* realizar revisión humana;
* importar o rechazar información;
* conservar trazabilidad técnica e institucional.

La integración Kobo no modifica directamente saldos financieros ni sustituye las reglas de negocio de `apps.operations`.

## 2. Formularios soportados

El MVP soporta directamente:

```text
Ficha 1
Ficha 10
Ficha 11
```

Las fichas 2 a 9 no se importan directamente dentro del MVP.

## 3. Configuración

### Variables de entorno

```env
KOBO_ENABLED=False
KOBO_BASE_URL=
KOBO_API_TOKEN=
KOBO_WEBHOOK_USERNAME=sigedon-kobo
KOBO_WEBHOOK_SECRET=
KOBO_FICHA_01_ASSET_UID=
KOBO_REQUEST_TIMEOUT_SECONDS=15
KOBO_MAX_ATTACHMENT_BYTES=10485760
KOBO_ATTACHMENT_PROCESSING_TIMEOUT_SECONDS=900
KOBO_WEBHOOK_MAX_BYTES=1048576
```

### Consideraciones

* `KOBO_ENABLED` activa o desactiva la integración.
* `KOBO_BASE_URL` define la URL base de la instancia KoboToolbox.
* `KOBO_API_TOKEN` contiene el token utilizado para acceder a la API.
* `KOBO_WEBHOOK_USERNAME` define el usuario esperado por el webhook.
* `KOBO_WEBHOOK_SECRET` contiene el secreto utilizado para autenticar solicitudes entrantes.
* `KOBO_REQUEST_TIMEOUT_SECONDS` define el tiempo máximo de espera para solicitudes externas.
* `KOBO_MAX_ATTACHMENT_BYTES` limita el tamaño permitido para archivos adjuntos.
* `KOBO_ATTACHMENT_PROCESSING_TIMEOUT_SECONDS` define cuánto tiempo una reserva `PROCESSING` permanece vigente antes de poder recuperarse (por defecto 900).
* `KOBO_WEBHOOK_MAX_BYTES` limita el cuerpo JSON aceptado por el webhook antes de staging.
* `KOBO_FICHA_01_ASSET_UID` pertenece únicamente al flujo legado de la Ficha 1.

Los valores secretos no deben versionarse ni registrarse en logs.

## 4. Registro de formularios

Las definiciones versionadas conocidas por el normalizador se registran mediante:

```bash
python manage.py register_kobo_forms
```

Este comando permite:

* registrar formularios soportados;
* mantener versiones compatibles;
* asociar cada definición con su normalizador;
* actualizar definiciones conocidas de forma controlada.

El registro de una definición no activa automáticamente ningún activo remoto.

## 5. Descubrimiento de activos

Los activos disponibles en KoboToolbox se descubren mediante:

```bash
python manage.py discover_kobo_assets
```

### Opciones

```text
--limit
--dry-run
```

### Reglas

* El descubrimiento crea o actualiza el inventario local de activos encontrados.
* Un activo descubierto no queda habilitado automáticamente.
* `--dry-run` permite inspeccionar los cambios sin persistirlos.
* El descubrimiento no sustituye la configuración ni la estrategia de routing.

## 6. Configuración de activos

Para participar en el pipeline ordinario, un activo debe:

1. existir en el inventario de activos descubiertos;
2. asociarse con una definición de formulario soportada;
3. disponer de la configuración técnica exigida por su formulario;
4. activarse explícitamente.

Un activo no configurado o inactivo no debe procesar submissions como parte del flujo ordinario.

## 7. Bindings hacia proyectos (legado)

`KoboProjectBinding` se conserva temporalmente solo como evidencia histórica y
no participa en el runtime. Las Fichas 1, 10 y 11 se enrutan por submission:
Ficha 1 usa zona pastoral y el mapping territorial; Fichas 10 y 11 usan la
identidad territorial de `nucleo_code`. El UID del asset identifica la ficha,
no el proyecto. La tabla se retirará únicamente después de auditar los datos
persistentes; no hay backfill ni borrado automático en esta fase.

Los valores `direct`, `field_value`, `source_field` y `source_value` pertenecen
exclusivamente a la configuración histórica almacenada. No hay formularios,
rutas, comandos ni servicios de runtime que creen, editen o consulten esas
rutas.

### 7.3. Routing territorial de las fichas soportadas

El dispatcher `route_normalized_submission()` aplica estrategias explícitas:

```text
Ficha 1
→ nucleo_code normalizado + zona pastoral
→ identidad territorial maestra
→ proyecto configurado para la zona

Ficha 10 / Ficha 11
→ nucleo_code normalizado
→ identidad territorial existente
→ proyecto de la identidad
```

Ficha 10 y Ficha 11 nunca crean identidades ni usan un binding como fallback.
Cuando la identidad todavía no existe, la submission conserva
`READY_FOR_REVIEW` como estado de procesamiento, queda sin proyecto y usa
`PENDING_IDENTITY` como estado independiente de routing. Por ello no aparece en
una bandeja de proyecto ni puede importarse hasta ser reconciliada.

Todos los estados persistidos de `KoboTerritorialIdentity`, incluidos
`PENDING_REVIEW`, `OBSERVED` e `INACTIVE`, son utilizables para routing. En el
contrato vigente esos estados describen revisión administrativa de la identidad
y no revocan su asociación territorial; bloquearlos requeriría una transición y
una política de negocio nuevas.

La materialización aplica una política más restrictiva que el routing: una
identidad `INACTIVE` no admite nuevos perfiles, `OBSERVED` conserva ese estado y
`PENDING_REVIEW` pasa a `ACTIVE` únicamente después de importar exitosamente una
Ficha 1 aprobada y sin conflictos abiertos.

La recepción ordinaria conserva first-write-wins: un webhook repetido no
reemplaza el payload ni la normalización existentes. Si una modificación técnica
posterior cambia el código de una submission ya resuelta hacia otra identidad o
hacia una identidad inexistente, el servicio conserva el proyecto anterior y
registra un conflicto; nunca mueve silenciosamente la submission.

## 8. Webhook

La ruta de recepción se encuentra bajo:

```text
/integrations/kobo/
```

La URL concreta depende de las rutas configuradas por la aplicación.

### Proceso

1. El webhook autentica la solicitud.
2. Valida que el cuerpo contenga JSON válido.
3. Identifica el activo remoto.
4. Identifica la submission.
5. Crea o actualiza el registro de staging.
6. Conserva el payload original.
7. Registra un evento técnico.
8. Procesa la submission o la deja pendiente según su configuración.

### Reglas de seguridad

* Las credenciales deben compararse con los valores configurados.
* Los secretos no deben incluirse en logs.
* Los errores internos no deben exponerse en la respuesta.
* Una solicitud inválida no debe modificar datos operativos.
* La recepción repetida de una misma submission debe manejarse de forma idempotente.

## 9. Estados de una submission

Estados declarados:

```text
RECEIVED
NORMALIZED
VALIDATION_FAILED
READY_FOR_REVIEW
APPROVED_FOR_IMPORT
IMPORTED
PARTIALLY_IMPORTED
REJECTED
DUPLICATE
PROCESSING_FAILED
```

### Flujo ordinario por proyecto

```text
RECEIVED
→ READY_FOR_REVIEW
→ APPROVED_FOR_IMPORT
→ IMPORTED
```

Las transiciones tienen significados independientes:

```text
routing resuelto ≠ revisión aprobada
revisión aprobada ≠ importación
IMPORTED = materialización exitosa + KoboImportRecord trazable
```

### Ramas de validación

```text
RECEIVED
→ VALIDATION_FAILED
```

```text
RECEIVED
→ PROCESSING_FAILED
```

### Rechazo

```text
READY_FOR_REVIEW
→ REJECTED
```

### Restauración

```text
REJECTED
→ READY_FOR_REVIEW
```

### Estados de compatibilidad

`APPROVED_FOR_IMPORT` es el único estado revisado desde el que el servicio común
puede iniciar materialización. La acción histórica de importación desde la
bandeja de proyecto conserva su URL, pero primero registra explícitamente la
aprobación y después invoca el mismo servicio común que la consola técnica.

`NORMALIZED` y `DUPLICATE` permanecen declarados por compatibilidad histórica.
`PARTIALLY_IMPORTED` no tiene escritores, lectores de negocio ni una semántica
operativa vigente: no se usa para fallos normales y debe evaluarse su retirada
en una migración posterior, sin eliminarlo en esta fase.

## 10. Procesamiento de submissions

Las submissions pendientes se procesan mediante:

```bash
python manage.py process_kobo_submissions
```

### Opciones

```text
--limit
--submission-id
--download-attachments
```

### Comportamiento

El comando puede:

* procesar una cantidad limitada de submissions;
* procesar una submission concreta;
* validar el payload;
* ejecutar el normalizador correspondiente;
* resolver el proyecto;
* descargar adjuntos cuando se solicita;
* registrar eventos técnicos;
* dejar la submission lista para revisión;
* ejecutar el mismo dispatcher territorial utilizado por el webhook;
* registrar errores de validación o procesamiento.

El procesamiento no debe importar automáticamente información operativa cuando el flujo exige revisión humana.

## 11. Reconciliación

Las submissions remotas que no llegaron mediante webhook pueden recuperarse mediante:

```bash
python manage.py reconcile_kobo_submissions
```

### Opciones

```text
--asset-uid
--limit
--dry-run
```

### Objetivo

La reconciliación permite:

* consultar submissions remotas;
* comparar identificadores con el staging local;
* detectar registros faltantes;
* crear las submissions ausentes;
* evitar duplicados;
* continuar posteriormente mediante el pipeline normal.
* reintentar Fichas 10/11 en `PENDING_IDENTITY` sin convertir la ausencia de
  Ficha 1 en un error.

El resumen del comando separa `resolved`, `still_pending`, `errors` y `skipped`.

`--dry-run` permite inspeccionar el resultado sin persistir cambios.

La reconciliación no sustituye la validación, normalización, revisión ni importación.

## 12. Gestión de adjuntos

Los adjuntos Kobo:

* se descargan utilizando autenticación contra KoboToolbox;
* respetan el límite configurado en `KOBO_MAX_ATTACHMENT_BYTES`;
* reservan el trabajo con estado `PROCESSING`, `processing_token` y `processing_started_at`;
* recuperan reservas vencidas según `KOBO_ATTACHMENT_PROCESSING_TIMEOUT_SECONDS` al reintentar el procesamiento;
* descargan y guardan en storage fuera de transacciones abiertas;
* compensan archivos huérfanos si la confirmación BD falla o el token ya no coincide;
* conservan metadatos técnicos;
* se asocian con su submission;
* poseen una clasificación de privacidad;
* requieren autorización para su descarga;
* no se publican automáticamente.

### Información que puede conservarse

* nombre original;
* tipo MIME;
* tamaño;
* identificador remoto;
* ruta o referencia local;
* clasificación de privacidad;
* estado de descarga;
* metadata de reserva de procesamiento (`processing_token`, `processing_started_at`);
* fecha de incorporación.

Las firmas y otros archivos sensibles no pueden marcarse como candidatos públicos.

## 13. Revisión por proyecto

La revisión ordinaria asociada a un proyecto permite:

* consultar información normalizada;
* revisar evidencia y adjuntos autorizados;
* importar;
* rechazar;
* restaurar;
* consultar el historial del procesamiento.

### Permisos

La consulta ordinaria requiere:

```text
operations.view_project
```

La importación, el rechazo o la restauración requieren:

```text
operations.change_project
```

El acceso al payload crudo y a la información técnica sensible continúa protegido mediante permisos `kobo.*`.

### Importación

Cuando una submission se importa:

1. se bloquea exclusivamente la fila de `KoboSubmission`;
2. se valida formulario soportado, normalización, routing `RESOLVED`, proyecto,
   revisión `APPROVED_FOR_IMPORT`, permisos y payload original preservado;
3. se selecciona uno de los handlers cerrados para Ficha 1, 10 u 11;
4. el handler materializa su entidad dentro de la misma transacción;
5. se crea un único `KoboImportRecord` con el tipo de handler y la referencia
   lógica mínima al resultado, sin payload ni datos sensibles;
6. solo entonces se asignan `IMPORTED`, `processed_at` e `imported_at`;
7. se registran una vez el evento técnico y la auditoría funcional.

`KoboImportRecord` usa una relación `OneToOne` con la submission en lugar de una
`GenericForeignKey`. Esta estrategia garantiza una sola importación completada,
mantiene la dependencia polimórfica fuera de `KoboSubmission` y conserva una
referencia auditable mediante `target_app_label`, `target_model` y
`target_object_id`. La base de datos no puede imponer una constraint entre el
estado de una tabla y la existencia de una fila en otra; por ello el servicio
transaccional es la frontera que establece `IMPORTED`.

El handler de Ficha 1 crea un `KoboTerritorialProfile` inmutable por submission.
La relación es histórica:

```text
KoboTerritorialIdentity 1 ──< KoboTerritorialProfile
KoboSubmission 1 ── 1 KoboTerritorialProfile
```

No se copian el código ni la zona al perfil: se consultan en la identidad
canónica. `location` conserva el objeto normalizado con latitud, longitud,
altitud y precisión; `communities_covered` conserva el texto libre confirmado
por el XLSForm, y `access_difficulties` usa el catálogo cerrado
`yes/no/unknown`. El handler nunca reinterpreta `raw_payload`.

El handler de Ficha 10 crea un `KoboPrioritizedMicroproject` inmutable por
submission y conserva la relación histórica:

```text
KoboTerritorialIdentity 1 ──< KoboPrioritizedMicroproject
KoboSubmission 1 ── 1 KoboPrioritizedMicroproject
```

La auditoría del dominio operativo descartó reutilizar `ProjectMilestone` y
`ProjectUpdate`: el primero representa un resultado verificable ordenado y el
segundo un avance histórico, mientras que ninguno conserva el contrato completo
de problema, objetivo, beneficiarios, actividades, costo categórico, urgencia y
viabilidad. Tampoco se reutiliza `Project`, porque representa el Núcleo Vital
completo. Por ello la entidad permanece en la integración Kobo y Operations no
adquiere una dependencia hacia Kobo.

El routing identifica la identidad y su proyecto Núcleo Vital; la importación
crea únicamente la propuesta subordinada. No deduplica por nombre, no vuelve a
normalizar el código y no lee `raw_payload`. Todos los campos de negocio de la
Ficha 10 depurada son requeridos. `component`, `estimated_cost_range`,
`implementation_urgency` y `technical_viability` usan los códigos cerrados del
normalizador; `beneficiary_group` es un `select_multiple` persistido como lista
JSON estable y `main_activities` es texto libre.

La importación no cambia el estado de la identidad y acepta identidades
`PENDING_REVIEW`, `ACTIVE` u `OBSERVED`; `INACTIVE` y los conflictos territoriales
abiertos bloquean. No crea otro `Project`, presupuesto, donación, asignación,
gasto ni movimiento financiero. Si ya existe el microproyecto sin su import
record, responde `FICHA_10_MICROPROJECT_STATE_CONFLICT` y no repara ni duplica.

El handler de Ficha 11 crea un `KoboPrioritizationAssessment` inmutable por
submission y conserva la relación histórica:

```text
KoboTerritorialIdentity 1 ──< KoboPrioritizationAssessment
KoboSubmission 1 ── 1 KoboPrioritizationAssessment
```

Los diez scores canónicos se guardan individualmente y SIGEDON deriva de ellos,
sin confiar en totales enviados, `priority_total_calculated` y
`suggested_semaphore_calculated`. Se conservan aparte
`priority_total_original`, `suggested_semaphore_original`, el semáforo final
humano y la prioridad final. `PRIORITY_TOTAL_MISMATCH` y
`SUGGESTED_SEMAPHORE_MISMATCH` son warnings estructurados, aparecen también en
el resultado de importación y no sobrescriben decisiones humanas. Scores,
cálculos persistidos o catálogos inválidos sí bloquean.

El campo Kobo `linked_microprojects` es texto libre en el contrato depurado y se
persiste como `linked_microprojects_snapshot`. No contiene identificadores
estables verificables, por lo que no crea FK, M2M ni coincidencias automáticas
por nombre con `KoboPrioritizedMicroproject`; esa vinculación estructurada queda
pendiente de un contrato futuro con identificadores estables.

La importación acepta identidades `PENDING_REVIEW`, `ACTIVE` u `OBSERVED`,
bloquea `INACTIVE` y conflictos abiertos, y no cambia identidad, proyecto,
prioridad institucional, microproyectos, presupuesto ni movimientos
financieros. Si ya existe la evaluación sin import record, responde
`FICHA_11_ASSESSMENT_STATE_CONFLICT` y no repara ni duplica silenciosamente.

Un fallo técnico revierte materialización, import record, estado, timestamp,
evento y auditoría de éxito. Después se registra, cuando la base de datos lo
permite, un error seguro fuera de la transacción y la submission queda
reintentable. Un reintento de una importación completada devuelve
`ALREADY_IMPORTED` con la referencia original, sin repetir efectos.

Si existe un perfil para la submission pero falta su import record, el sistema
considera el estado corrupto, responde `FICHA_1_PROFILE_STATE_CONFLICT` y no crea
un segundo perfil ni intenta una reparación implícita.

Las filas históricas que ya estaban en `IMPORTED` antes de esta migración no se
rellenan con referencias ficticias. Un reintento sigue siendo idempotente y
devuelve `ALREADY_IMPORTED`, pero identifica la deuda mediante
`LEGACY_IMPORT_RECORD_MISSING`; su reconciliación exige una fase posterior con
conocimiento de las entidades realmente creadas.

Una Ficha 1, 10 u 11 con routing `PENDING_IDENTITY`, `CONFLICT` o `ERROR` no es
importable, aunque conserve `READY_FOR_REVIEW` como estado de procesamiento.

La importación no debe modificar directamente saldos financieros.

### Auditoría del flujo sustituido

Antes de este contrato existían dos escritores de `IMPORTED`:

* `import_kobo_submission()` aceptaba `READY_FOR_REVIEW`, asignaba estado y
  `imported_at`, y creaba `KoboProcessingEvent` y `AuditLog` sin materializar;
* el retirado `associate_submission_with_project()` aceptaba `APPROVED_FOR_IMPORT`, resolvía
  asset/binding, asignaba proyecto, `processed_at`, `imported_at` y estado, y
  creaba un evento técnico, pero no `AuditLog` ni entidad materializada.

La bandeja por proyecto invocaba la primera ruta; la consola técnica ejecutaba
`review_submission()` y después la segunda. Los reintentos sobre `IMPORTED` no
duplicaban eventos, pero tampoco podían responder qué entidad había producido
la importación. Como el cambio de estado y sus eventos estaban dentro de
`transaction.atomic()`, una excepción de base de datos posterior al `save()`
revertía esas escrituras; la deuda era semántica, no un commit parcial conocido.
Ambas rutas terminan ahora en el servicio materializador común.

## 14. Rechazo y restauración

### Rechazo

Una submission lista para revisión puede rechazarse cuando:

* el usuario posee permisos;
* se registra un motivo;
* la transición está permitida.

La submission pasa a:

```text
REJECTED
```

El rechazo:

* no elimina el payload original;
* no importa información operativa;
* conserva la trazabilidad;
* puede permitir una restauración posterior.

### Restauración

Una submission rechazada puede restaurarse al estado revisable:

```text
REJECTED
→ READY_FOR_REVIEW
```

La restauración:

* requiere autorización;
* no implica importación automática;
* debe registrar un evento técnico;
* conserva el rechazo anterior en el historial.

## 15. Consola global

La consola global es una herramienta de administración técnica.

Requiere permisos `kobo.*`.

### Permite

* revisar submissions;
* consultar payloads técnicos;
* asociar proyectos;
* reintentar procesamiento;
* inspeccionar errores;
* gestionar activos;
* utilizar el flujo histórico de aprobación;
* realizar acciones de soporte y diagnóstico.

### Restricciones

* No debe confundirse con el flujo operativo ordinario desde un proyecto.
* No debe estar disponible para usuarios operativos sin autorización técnica.
* El acceso técnico no implica permiso para modificar información financiera.
* Los datos sensibles deben mantenerse protegidos.

## 16. Entrada de compatibilidad de Ficha 1

El mapping `ficha_01` y el comando histórico continúan activos como entrada al
staging genérico:

```bash
python manage.py sync_kobo_ficha_01
```

### Opciones

```text
--limit
--dry-run
```

### Flujo

```text
sync_kobo_ficha_01
→ obtención y validación del payload Ficha 1
→ receive_api_submission
→ KoboSubmission (received, payload crudo)
→ process_submission y normalización ficha_01
→ routing, revisión e importación mediante el pipeline genérico
```

Este flujo:

* se conserva por compatibilidad;
* persiste en `KoboSubmission` y no escribe en los modelos específicos Ficha01;
* no representa el patrón recomendado para nuevas fichas;
* no sustituye el pipeline ordinario basado en activos configurados.

`Ficha01Territorio` y `Ficha01CoveredCommunity` permanecen en el schema legado,
no tienen escritores activos conocidos y no son utilizados por el pipeline
vigente. No son la fuente de verdad activa. Su eventual eliminación requiere
una decisión de producto y una migración específica; ninguna integración nueva
debe escribir en ellos sin una decisión arquitectónica explícita.

## 17. Privacidad

Solo los usuarios técnicos autorizados deben acceder a:

* payload crudo;
* errores técnicos detallados;
* datos sensibles;
* adjuntos privados;
* configuración de activos;
* credenciales;
* información interna del pipeline.

### Reglas

* La información normalizada no implica acceso automático al payload original.
* Los secretos no deben aparecer en templates, mensajes ni logs.
* Los adjuntos no deben publicarse sin una clasificación explícita.
* Las submissions rechazadas no deben exponerse en el portal público.
* La información no aprobada no debe incorporarse a vistas públicas.

## 18. Trazabilidad

SIGEDON utiliza dos registros con responsabilidades distintas.

### `KoboProcessingEvent`

Conserva el historial técnico del pipeline:

* recepción;
* validación;
* normalización;
* descarga de adjuntos;
* errores;
* reintentos;
* cambios de estado;
* reconciliación.

### `AuditLog`

Conserva acciones institucionales dentro del dominio operativo:

* importación autorizada;
* rechazo;
* restauración;
* asociación funcional;
* otras acciones críticas ejecutadas por usuarios.

### Diferencia

```text
KoboProcessingEvent
→ trazabilidad técnica de la integración

AuditLog
→ trazabilidad funcional e institucional
```

Ninguno de los dos registros sustituye al otro.

### Administración territorial

`KoboTerritorialAdministrationEvent` conserva el evento funcional específico
de mappings, conflictos, estados de identidad y reconciliaciones. Registra
actor, acción, entidad, estados anterior/posterior, motivo y fecha sin copiar
payloads, teléfonos, coordenadas, notas territoriales ni excepciones sensibles.
Cada evento se acompaña de un `AuditLog` atómico.

Los mappings se configuran por zona canónica y FK a `Project`; no usan nombres
ni IDs codificados. Una zona usada por cualquier identidad no puede cambiarse o
quedar sin mapping. `ACCEPT_PROPOSED` tampoco migra historia: se bloquea si la
identidad ya produjo perfiles, microproyectos, evaluaciones, import records,
importaciones u otras asignaciones resueltas.

La reconciliación administrativa es distinta de la sincronización remota:
trabaja en lotes de hasta 100 submissions locales `PENDING_IDENTITY`, no usa
bindings y no cambia revisión, aprobación ni importación. Repetir una llamada
sin pendientes no produce nuevos eventos.

## 19. Comandos principales

```bash
python manage.py register_kobo_forms
python manage.py discover_kobo_assets
python manage.py process_kobo_submissions
python manage.py reconcile_kobo_submissions
python manage.py sync_kobo_ficha_01
```

El comando `sync_kobo_ficha_01` es una entrada de compatibilidad hacia
`KoboSubmission`. La operación ordinaria debe utilizar los activos configurados
y el pipeline general de KoboToolbox.

## 20. Hub territorial

Con `KOBO_ENABLED=true`, `/integrations/kobo/` es el Hub para dashboard,
mappings, identidades, conflictos y routing pendiente. Sus mutaciones usan
POST, CSRF y los servicios administrativos existentes. Cuando Kobo está
deshabilitado, el enlace y las rutas del Hub no están disponibles.

# Integración con KoboToolbox

Este documento describe la integración entre KoboToolbox y SIGEDON, incluyendo su configuración, automatización, privacidad y trazabilidad. Las secciones marcadas **Histórico** se conservan únicamente para interpretar datos y auditorías antiguas; no describen acciones disponibles en la UI.

## 1. Objetivo

La integración permite:

* recibir levantamientos de campo;
* conservar el payload original;
* normalizar la información;
* validar los datos;
* asociar submissions con proyectos SIGEDON;
* importar automáticamente información materializable;
* conservar trazabilidad técnica e institucional.

La integración Kobo no modifica directamente saldos financieros ni sustituye las reglas de negocio de `apps.operations`.

## Flujo automatizado vigente

```text
KoboToolbox → webhook → staging idempotente → normalización
→ asignación territorial → importación automática → auditoría
```

El webhook es el mecanismo principal. El botón único **Sincronizar KoboToolbox**
es respaldo para recuperar formularios tras una caída o buscar cambios remotos;
no se sincroniza cada ficha por separado. El panel usa HTMX y polling cada 15
segundos para métricas agregadas, sin payloads ni eventos técnicos completos.

El flujo de incidencia es:

```text
KoboToolbox → procesamiento → bloqueo seguro → incidencia conservada
→ corrección de configuración → reintento automático o manual
```

Una incidencia se clasifica como zona sin proyecto, núcleo no encontrado,
conflicto territorial, datos inválidos, normalización fallida, materialización
fallida, actualización remota pendiente o error técnico. Las submissions
importadas, sin cambios y estados heredados sin error activo no son incidencias.

Ficha 1 crea o confirma la identidad territorial; Fichas 10 y 11 requieren esa
identidad para materializar. La reconciliación recupera cambios remotos y
mantiene la idempotencia; los bloqueos de fila evitan doble materialización.

Las acciones humanas vigentes son configurar zona, resolver conflictos,
consultar historial y reintentar una incidencia resoluble. La aprobación,
rechazo, restauración e importación manual son flujos retirados.

### Actor técnico `kobo.system`

La importación automática siempre usa `kobo.system`, no al operador que pulse
sincronizar. Se crea idempotentemente, está activo para auditoría, no es
superusuario, no pertenece a grupos y tiene sólo `operations.change_project`.
Su contraseña es inutilizable y no se espera acceso interactivo.

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
KOBO_HTTP_CONNECT_TIMEOUT=5
KOBO_HTTP_READ_TIMEOUT=15
KOBO_HTTP_MAX_ATTEMPTS=3
KOBO_HTTP_RETRY_BASE_DELAY=0.5
KOBO_HTTP_RETRY_MAX_DELAY=8
KOBO_HTTP_RETRY_AFTER_MAX_DELAY=60
KOBO_HTTP_MAX_PAGES=100
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
* `KOBO_HTTP_READ_TIMEOUT` define el timeout de `urllib`; el transporte actual no separa conexión y lectura, por lo que `KOBO_HTTP_CONNECT_TIMEOUT` se reserva para un transporte futuro.
* `KOBO_HTTP_MAX_ATTEMPTS`, `KOBO_HTTP_RETRY_BASE_DELAY`, `KOBO_HTTP_RETRY_MAX_DELAY` y `KOBO_HTTP_RETRY_AFTER_MAX_DELAY` controlan reintentos transitorios con backoff.
* `KOBO_HTTP_MAX_PAGES` limita la paginación remota para evitar recorridos no acotados.
* `KOBO_SYNC_OVERLAP_SECONDS` reserva la ventana de solapamiento para el cursor incremental y `KOBO_SYNC_LEASE_SECONDS` limita una ejecución exclusiva por asset.

Las revisiones remotas se identifican por `_uuid` dentro del asset y hash canónico
del payload. Una revisión de una submission aprobada, importada o rechazada nunca
sobrescribe staging ni materialización: queda privada, marcada como pendiente y
requiere inspección técnica posterior en el hub. Solo una ejecución completa avanza el cursor;
una ejecución parcial conserva el cursor anterior y libera su lease.
* `KOBO_MAX_ATTACHMENT_BYTES` limita el tamaño permitido para archivos adjuntos.
* `KOBO_ATTACHMENT_PROCESSING_TIMEOUT_SECONDS` define cuánto tiempo una reserva `PROCESSING` permanece vigente antes de poder recuperarse (por defecto 900).
* `KOBO_WEBHOOK_MAX_BYTES` limita el cuerpo JSON aceptado por el webhook antes de staging.

## Sincronización remota segura

El cliente usa `urllib` con autenticación `Token`, HTTPS y redirects desactivados.
Todos los requests tienen timeout explícito y solo reintentan 429, 500, 502, 503,
504, timeouts y errores de conexión; los demás 4xx fallan sin retry. Los envelopes
`count`/`next`/`results` se recorren de forma paginada, con host, ruta, ciclos y
límite de páginas validados. `KoboSyncRun` conserva únicamente métricas y errores
seguros: un discovery parcial no marca assets ausentes como no disponibles y un
sync de submissions no se declara completo tras un fallo remoto.

`max_attempts` es el número total de solicitudes, incluida la primera. Al
agotarse, 401, 403 y 404 generan respectivamente `KoboAuthenticationError`,
`KoboAuthorizationError` y `KoboNotFoundError`; 429 genera
`KoboRateLimitError` (transitorio), timeout `KoboTimeoutError`, 500/502/503/504
`KoboTransientRemoteError`, y 400/422/redirect `KoboPermanentRemoteError`.
JSON, envelopes y estructuras remotas inválidas generan `KoboInvalidResponseError`.
Todas derivan de `KoboIntegrationError`; los errores de payload local conservan
`KoboPayloadError`. Un `Retry-After` válido y limitado tiene prioridad sobre el
backoff; los tests inyectan sleeper y jitter, por lo que nunca esperan realmente.

Los valores secretos no deben versionarse ni registrarse en logs.

### Sincronización incremental

La consulta incremental usa el parámetro Kobo `query` con
`{"_last_edited":{"$gte":"..."}}`. El inicio es el watermark remoto
confirmado menos `KOBO_SYNC_OVERLAP_SECONDS`; el overlap solo amplía la
consulta y nunca cambia el valor persistido. Las respuestas repetidas se
absorben por hash canónico. Un sync completo ignora cursor y overlap.

El mayor `_last_edited` válido de la ejecución es candidato a watermark. Solo
una ejecución `SUCCEEDED` actualiza atómicamente cursor, watermark y hora del
último éxito; `PARTIAL` y `FAILED` conservan ambos valores. Cada asset posee
una lease atribuida al `KoboSyncRun`; una lease vencida deja el run anterior
como `ABANDONED` con `SYNC_LEASE_EXPIRED` antes de adquirir la nueva.

Las acciones del panel operativo son POST con CSRF, requieren permiso de cambio del asset y
son síncronas; no existe scheduling automático.

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

### Flujo ordinario automático

```text
RECEIVED
→ READY_FOR_REVIEW (transitorio interno)
→ APPROVED_FOR_IMPORT (transitorio interno)
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

### Estados de compatibilidad

`APPROVED_FOR_IMPORT` sólo existe dentro de la transición automática hacia el
servicio materializador; no es aprobación humana ni estado terminal. Si la
materialización se bloquea o falla, pasa a `PROCESSING_FAILED`, conserva causa
y se presenta como incidencia reintentable. `IMPORTED` sólo se asigna en la
misma transacción que crea `KoboImportRecord` y la entidad materializada.

`READY_FOR_REVIEW` es un estado técnico heredado oculto: puede existir entre
normalización/routing e importación automática, pero no es bandeja humana. Un
reintento nunca interpreta una incidencia como aprobación humana válida.

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
* dejar la submission en estado interno listo para el pipeline automático;
* ejecutar el mismo dispatcher territorial utilizado por el webhook;
* registrar errores de validación o procesamiento.

El procesamiento ordinario de submissions válidas y con routing resuelto continúa
hacia auto-aprobación e importación. Las fallas e incidencias se presentan en el
hub global; no se sostiene una cola humana por Project.

### Código de salida

* `0`: el lote seleccionado terminó sin errores (incluye cero elegibles).
* distinto de `0`: al menos un fallo por registro o un fallo fatal de init;
  el resumen se imprime antes del `CommandError`.
* Los éxitos previos del mismo lote **permanecen comprometidos**; no hay
  rollback del lote completo.
* Orquestación: no usar `|| true`. Reejecutar es seguro según idempotencia.
* Detalle operativo: [OPERATIONS.md §5](OPERATIONS.md#5-procesamiento-y-reconciliación-de-kobo).

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

`--dry-run` permite inspeccionar el resultado sin persistir cambios. Un dry-run
sin errores sale `0`; un dry-run con errores operativos (p. ej. fallo remoto)
sale distinto de `0` y no escribe.

### Código de salida

* `errors == 0` → exit `0`.
* `errors > 0` → resumen final y `CommandError` (exit distinto de `0`).
* “Sin registros remotos” no es error.
* Commits parciales exitosos se conservan; inspeccione hub/eventos/logs.
* Este comando no usa el lease de sync incremental del hub.

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

Las evidencias descargadas se sirven solo mediante endpoints protegidos:

* preview (`kobo:project_submission_evidence`) — inline con la lista blanca compartida;
* download (`kobo:project_submission_evidence_download`) — attachment para todo archivo autorizado.

Se reutiliza `apps.operations.file_access.protected_file_response` sin debilitar
las reglas de `privacy_level`, estado `DOWNLOADED`, submission `IMPORTED` ni
permisos `kobo.view_kobosubmission` / elevación sensible.

## 13. Histórico: revisión manual por proyecto (retirada)

> **Histórico / superseded.** La bandeja humana de approve/reject/restore por
> Project y la consola técnica de decisión humana sobre submissions fueron
> retiradas del producto. No quedan rutas HTTP registradas ni tombstones
> `Http404`: las peticiones a las rutas antiguas reciben 404 de resolución de
> URL y `reverse()` de sus nombres lanza `NoReverseMatch`.

Antes del pipeline automático, la revisión ordinaria asociada a un proyecto
permitía consultar información normalizada, importar, rechazar, restaurar y
consultar el historial. El flujo vigente es automático e incident-driven:

* webhook/sync recibe submissions;
* normalización y routing corren automáticamente;
* submissions elegibles se importan automáticamente;
* fallos e incidencias de routing se presentan en el hub territorial;
* usuarios técnicos autorizados inspeccionan submissions y reintentan
  procesamiento/importación;
* registros importados permanecen visibles en detalle de proyecto e historial.

`READY_FOR_REVIEW` es un estado interno/incidencia de automatización, no una
bandeja de aprobación humana. Los valores históricos del enum y las filas
existentes se conservan.

El detalle importado en proyecto usa un contrato de presentación compartido
(`submission_presentation`) para Ficha 1, 10 y 11: valores y etiquetas en
español, secciones y resumen propios de cada dominio, geolocalización formateada
sin representación cruda del payload (solo Ficha 1 aporta ubicación
normalizada y el enlace opt-in a OpenStreetMap), IDs técnicos agrupados bajo
**Registro Kobo**, y datos de contacto/técnicos colapsados solo con
`kobo.change_kobosubmission`.

En el detalle importado de Ficha 1, la sección **Ubicación** puede mostrar un
enlace opt-in **Ver en mapa** hacia OpenStreetMap. OpenStreetMap solo se
contacta tras una activación explícita del usuario; la URL de destino incluye
únicamente latitud y longitud (más un zoom fijo), con
`rel="noopener noreferrer"` para evitar exposición de opener/referrer. No se
cargan recursos de mapa de terceros embebidos en la página; las coordenadas
textuales siguen visibles dentro de SIGEDON; coordenadas inválidas o ausentes
no producen enlace.

### Permisos vigentes (consulta e inspección)

La consulta ordinaria de historial/detalle importado requiere:

```text
operations.view_project
```

La inspección técnica y el reintento de importación requieren permisos `kobo.*`
según la acción (hub territorial / detalle técnico).

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
el retirado `review_submission()` y después la segunda. Los reintentos sobre `IMPORTED` no
duplicaban eventos, pero tampoco podían responder qué entidad había producido
la importación. Como el cambio de estado y sus eventos estaban dentro de
`transaction.atomic()`, una excepción de base de datos posterior al `save()`
revertía esas escrituras; la deuda era semántica, no un commit parcial conocido.
Ambas rutas terminan ahora en el servicio materializador común.

## 14. Histórico: rechazo y restauración (servicios retirados)

> **Histórico / superseded.** Las rutas HTTP y los servicios de dominio
> `reject_kobo_submission()` / `restore_kobo_submission_to_review()` /
> `review_submission()` fueron eliminados del código productivo. Los estados
> `REJECTED` y `READY_FOR_REVIEW`, los eventos históricos y las filas
> existentes se conservan para trazabilidad; no hay endpoints ni formularios
> para crear nuevos rechazos/restauraciones humanas.

Filas históricas con `REJECTED` siguen visibles en el historial del proyecto.
La recuperación operativa vigente es el reintento técnico de importación /
procesamiento desde el hub o el detalle técnico, no una restauración a una
cola humana.

## 15. Consola global / hub territorial

La consola global (hub territorial) es una herramienta de administración técnica.

Requiere permisos `kobo.*` (lectura territorial y, según la acción, cambio).

### Permite

* inspeccionar incidencias de routing/importación;
* consultar payloads técnicos (con elevación);
* reintentar procesamiento e importación;
* inspeccionar errores;
* gestionar activos y mappings;
* consultar historial de sync;
* realizar acciones de soporte y diagnóstico.

### Restricciones

* No debe confundirse con la revisión de gobernanza de `ProjectUpdate`.
* No debe confundirse con la retirada bandeja humana de submissions Kobo.
* No debe estar disponible para usuarios operativos sin autorización técnica.
* El acceso técnico no implica permiso para modificar información financiera.
* Los datos sensibles deben mantenerse protegidos.

## 16. Privacidad

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

## 17. Trazabilidad

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

## 18. Comandos principales

```bash
python manage.py register_kobo_forms
python manage.py discover_kobo_assets
python manage.py process_kobo_submissions
python manage.py reconcile_kobo_submissions
```

## 19. Panel operativo de KoboToolbox

Con `KOBO_ENABLED=true`, `/integrations/kobo/` es el panel operativo para resumen,
asignación de zonas, núcleos registrados y casos por revisar. Sus mutaciones usan
POST, CSRF y los servicios administrativos existentes. Cuando Kobo está
deshabilitado, el enlace y las rutas del panel no están disponibles.

El panel de administración territorial está disponible solo para roles
autorizados. Operador de campo queda explícitamente excluido. La ingestión por
webhook y las importaciones en backend no dependen del acceso del Operador al
panel.

El lenguaje visible del panel prioriza términos operativos:

* Asignación de zonas (configuración zona pastoral → proyecto)
* Núcleos registrados
* Casos por revisar
* Incidencias de importación automática / formularios importados

Los nombres técnicos internos (`mapping`, `routing`, identidades territoriales)
se conservan en modelos, servicios y documentación de arquitectura.

El listado `/integrations/kobo/submissions/pending/` es el hub global de
incidencias (`incident_queryset`). `pending_review_queryset` es un alias
deprecado de ese mismo queryset; no significa `status=READY_FOR_REVIEW` ni una
cola humana por Project. `READY_FOR_REVIEW` permanece como estado interno
transitorio del pipeline automático.

La asignación de zonas admite `?zone=<codigo>` para preseleccionar la zona en el
formulario de configuración sin mutar por GET. El historial completo de
sincronizaciones vive en `/integrations/kobo/sync/history/`.

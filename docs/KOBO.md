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
```

### Consideraciones

* `KOBO_ENABLED` activa o desactiva la integración.
* `KOBO_BASE_URL` define la URL base de la instancia KoboToolbox.
* `KOBO_API_TOKEN` contiene el token utilizado para acceder a la API.
* `KOBO_WEBHOOK_USERNAME` define el usuario esperado por el webhook.
* `KOBO_WEBHOOK_SECRET` contiene el secreto utilizado para autenticar solicitudes entrantes.
* `KOBO_REQUEST_TIMEOUT_SECONDS` define el tiempo máximo de espera para solicitudes externas.
* `KOBO_MAX_ATTACHMENT_BYTES` limita el tamaño permitido para archivos adjuntos.
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
* El descubrimiento no sustituye la configuración ni el binding hacia un proyecto.

## 6. Configuración de activos

Para participar en el pipeline ordinario, un activo debe:

1. existir en el inventario de activos descubiertos;
2. asociarse con una definición de formulario soportada;
3. configurar su mecanismo de routing;
4. activarse explícitamente.

Un activo no configurado o inactivo no debe procesar submissions como parte del flujo ordinario.

## 7. Bindings hacia proyectos

`KoboProjectBinding` define cómo una submission se asocia con un proyecto SIGEDON.

### 7.1. Binding directo

El activo completo se asigna a un proyecto concreto.

```text
Activo Kobo
→ Proyecto SIGEDON predefinido
```

Este es el flujo ordinario principal del MVP.

### 7.2. Binding por valor de campo

Un valor del payload se utiliza para resolver el proyecto correspondiente.

```text
Campo del payload
→ Valor normalizado
→ Proyecto SIGEDON
```

Este modo requiere:

* un campo de routing configurado;
* valores esperados;
* correspondencias válidas;
* manejo de casos sin coincidencia.

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
→ IMPORTED
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

`APPROVED_FOR_IMPORT` permanece principalmente asociado a la consola administrativa heredada.

`NORMALIZED`, `PARTIALLY_IMPORTED` y `DUPLICATE` pueden utilizarse según el resultado técnico del procesamiento y las condiciones históricas del pipeline.

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

`--dry-run` permite inspeccionar el resultado sin persistir cambios.

La reconciliación no sustituye la validación, normalización, revisión ni importación.

## 12. Gestión de adjuntos

Los adjuntos Kobo:

* se descargan utilizando autenticación contra KoboToolbox;
* respetan el límite configurado en `KOBO_MAX_ATTACHMENT_BYTES`;
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

1. se valida nuevamente su estado;
2. se confirma el proyecto asociado;
3. se crean o vinculan los datos normalizados;
4. se actualiza el estado de la submission;
5. se registra el evento técnico;
6. se registra auditoría funcional cuando corresponda.

La importación no debe modificar directamente saldos financieros.

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

## 16. Flujo legado de Ficha 1

El flujo histórico de compatibilidad se ejecuta mediante:

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
→ Obtención del payload
→ Normalización heredada
→ Ficha01Territorio
→ Ficha01CoveredCommunity
```

Este flujo:

* se conserva por compatibilidad;
* pertenece al primer modelo de integración de la Ficha 1;
* no representa el patrón recomendado para nuevas fichas;
* no sustituye el pipeline ordinario basado en activos configurados.

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

## 19. Comandos principales

```bash
python manage.py register_kobo_forms
python manage.py discover_kobo_assets
python manage.py process_kobo_submissions
python manage.py reconcile_kobo_submissions
python manage.py sync_kobo_ficha_01
```

El comando `sync_kobo_ficha_01` pertenece al flujo legado. La operación ordinaria debe utilizar los activos configurados y el pipeline general de KoboToolbox.

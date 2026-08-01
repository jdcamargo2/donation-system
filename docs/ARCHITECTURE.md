# Arquitectura de SIGEDON

## 1. Visión general

SIGEDON utiliza una arquitectura modular basada en Django, con separación entre los siguientes componentes:

```text
Panel operativo interno
Portal público
Integración con KoboToolbox
Servicios de dominio
Persistencia en PostgreSQL
Gestión de archivos privados
```

La arquitectura busca mantener separadas las responsabilidades de presentación, dominio, persistencia, publicación pública e integración externa.

## 1.1. Arquitectura de `Project`

`Project` se modela con dos ejes independientes:

```text
Ciclo operativo:     ACTIVE → CLOSED
Visibilidad pública: is_public = False | True
```

* Los servicios de dominio controlan publicación (`publish_project` /
  `unpublish_project`) y el cierre terminal (`finish_project`), que fuerza
  `is_public=False`.
* Los formularios ordinarios no cambian `status` ni `is_public`.
* Los selectores públicos exigen ambas condiciones (`ACTIVE` + `is_public=True`).
* El modelo y el queryset bloquean la eliminación; Admin y capas de URL
  aportan defensa en profundidad. La administración directa de base de datos
  queda fuera de esa garantía.

## 2. Componentes

### 2.1. `core/`

Contiene la configuración central del proyecto:

* settings;
* rutas raíz;
* configuración de base de datos;
* seguridad de producción;
* archivos estáticos;
* archivos multimedia;
* carga de variables de entorno.

### 2.2. `apps.operations`

Es el núcleo funcional de SIGEDON.

Contiene:

* modelos operativos;
* reglas de negocio;
* servicios transaccionales;
* formularios;
* vistas;
* rutas;
* roles;
* permisos;
* comandos administrativos;
* auditoría;
* pruebas automatizadas.

Toda mutación financiera crítica debe ejecutarse mediante servicios de dominio.

### 2.3. `apps.public_portal`

Publica información previamente autorizada y sanitizada.

Este componente:

* no debe depender de formularios internos;
* no debe reutilizar vistas operativas;
* no debe exponer información privada;
* utiliza selectores y consultas específicas para determinar qué información puede publicarse.

La selección de datos públicos debe permanecer separada de la lógica del panel operativo.

### 2.4. `apps.integrations.kobo`

Gestiona la integración con KoboToolbox.

Incluye:

* comunicación con KoboToolbox;
* descubrimiento de activos;
* definiciones de formularios;
* registro de activos;
* bindings;
* staging del payload original;
* normalización;
* routing hacia proyectos;
* gestión de adjuntos;
* procesamiento;
* revisión humana;
* importación;
* reconciliación;
* recepción mediante webhook.

La integración Kobo transforma y relaciona información, pero no modifica directamente los saldos financieros.

### 2.5. `web`

`web` es un paquete histórico que actualmente no contiene modelos ni vistas productivas activas.

Se conserva porque:

* contiene pruebas de regresión;
* la mayoría de los templates operativos continúan bajo `templates/web`;
* mantiene compatibilidad estructural durante la consolidación modular del proyecto.

## 3. Capas arquitectónicas

### 3.1. Presentación

```text
URLs
→ Views
→ Forms
→ Templates
```

Responsabilidades:

* autenticación;
* autorización;
* recepción de entradas del usuario;
* validación de formularios;
* mensajes de interfaz;
* navegación;
* presentación de información.

La capa de presentación no debe contener reglas financieras críticas ni lógica transaccional compleja.

### 3.2. Dominio

Componentes principales:

```text
services.py
role_services.py
selectores públicos
normalizadores Kobo
```

Responsabilidades:

* transacciones;
* invariantes de negocio;
* transiciones de estado;
* auditoría;
* bloqueos;
* cálculos derivados;
* procesamiento de integraciones;
* coordinación entre entidades.

Las operaciones críticas deben estar encapsuladas en servicios reutilizables y cubiertas por pruebas.

### 3.3. Persistencia

```text
Django ORM
→ PostgreSQL
```

Incluye:

* constraints;
* claves foráneas;
* relaciones protegidas;
* secuencias;
* índices;
* restricciones de unicidad;
* validaciones de integridad monetaria.

La base de datos actúa como una capa adicional de protección y no depende únicamente de validaciones en formularios o JavaScript.

## 4. Dependencias entre componentes

```text
apps.public_portal
→ consulta datos autorizados de apps.operations

apps.integrations.kobo
→ asocia información normalizada con entidades de apps.operations

core
→ registra y configura todas las aplicaciones

web
→ conserva templates y pruebas históricas
```

Reglas de dependencia:

* `apps.public_portal` puede consultar datos operativos, pero no debe modificarlos.
* `apps.integrations.kobo` puede asociar información normalizada con proyectos y otras entidades autorizadas.
* Kobo no debe alterar directamente donaciones, asignaciones, gastos ni saldos.
* `apps.operations` no debe depender de vistas o formularios del portal público.
* `core` debe limitarse a configuración y composición del proyecto.

## 5. Gestión de archivos

Tipos principales de archivos:

* documentos del proyecto;
* adjuntos de avances;
* soportes de gastos;
* adjuntos Kobo.

Los archivos privados:

* no se publican mediante `FileField.url`;
* se descargan mediante vistas autorizadas;
* requieren autenticación y permisos cuando corresponda;
* conservan trazabilidad;
* no deben exponerse directamente desde el almacenamiento.

La visibilidad de cada archivo debe definirse explícitamente según su naturaleza y contexto.

### 5.1. Vista previa local de selección (adjuntos de avance y documento de proyecto)

Los formularios de carga de archivos operativos pueden activar una vista previa
client-side mediante el atributo explícito `data-file-upload-preview`.

En la fase actual, el opt-in aplica a:

* `ProjectUpdateForm.attachments`;
* `ProjectUpdateForProjectForm.attachments`;
* `ProjectUpdateAttachmentForm.files`;
* `ProjectDocumentForm.file` (entrada de un solo archivo).

Comportamiento:

* la vista previa es local al navegador y no sube archivos hasta el envío del formulario;
* el `input[type=file]` nativo permanece visible y es la fuente autoritativa de la selección;
* en `ProjectDocumentForm.file` solo se previsualiza la selección pendiente local; al elegir
  otro archivo se reemplaza esa selección; los `ProjectDocument` ya persistidos siguen
  gestionándose solo con las acciones de servidor (detalle, descarga, eliminación);
* en entradas múltiples, la selección pendiente puede construirse de forma incremental y
  permiten quitar archivos individuales antes del submit cuando el navegador soporta
  `DataTransfer`;
* las imágenes raster (JPEG, PNG, WebP, GIF) pueden mostrar una miniatura local con
  `URL.createObjectURL`; SVG, PDF y documentos no se incrustan;
* no se persisten nombres ni contenido en `localStorage`/`sessionStorage`;
* la vista previa no sustituye la validación ni el almacenamiento del backend;
* los adjuntos ya persistidos siguen gestionándose solo con las acciones de servidor
  existentes (detalle, descarga, eliminación en borrador).

## 6. Base de datos

### 6.1. Desarrollo

SQLite puede utilizarse únicamente con una configuración explícita de desarrollo:

```env
DJANGO_DEBUG=True
DATABASE_ENGINE=sqlite
```

SQLite se utiliza para desarrollo local y pruebas que no dependan de comportamiento específico de PostgreSQL.

### 6.2. Producción

PostgreSQL es obligatorio en producción.

SIGEDON depende de PostgreSQL para:

* bloqueos reales de filas;
* pruebas y operaciones concurrentes;
* integridad transaccional;
* reserva segura de códigos;
* protección de saldos;
* ejecución confiable de `select_for_update()`.

El comportamiento concurrente crítico no debe considerarse validado únicamente con SQLite.

## 7. Seguridad arquitectónica

SIGEDON aplica las siguientes medidas:

* autenticación de Django;
* permisos por modelo y acción;
* separación entre permisos operativos y permisos `kobo.*`;
* auditoría append-only;
* archivos protegidos;
* acciones terminales mediante solicitudes `POST`;
* protección CSRF;
* HTTPS en producción;
* secretos mediante variables de entorno;
* constraints monetarios;
* `transaction.atomic()`;
* `select_for_update()`.

Además:

* las vistas no deben confiar únicamente en elementos ocultos de la interfaz;
* los permisos deben validarse en el servidor;
* las acciones críticas deben generar auditoría;
* los errores internos no deben exponerse al usuario;
* el portal público debe usar consultas explícitamente sanitizadas.

## 8. Principios arquitectónicos

La arquitectura de SIGEDON sigue estos principios:

* separación de responsabilidades;
* servicios de dominio para mutaciones críticas;
* validación en múltiples capas;
* mínima exposición de datos;
* trazabilidad de acciones;
* inmutabilidad de registros publicados o auditados;
* protección transaccional;
* dependencia explícita entre módulos;
* compatibilidad controlada con componentes históricos.

## 9. Deuda técnica conocida

Actualmente se mantienen las siguientes condiciones:

* los templates operativos continúan bajo `templates/web`;
* `web` se conserva como cascarón histórico;
* existe un flujo legado de sincronización de la Ficha 1;
* algunos estados históricos de Kobo permanecen declarados por compatibilidad;
* las dependencias de producción y desarrollo comparten `requirements.txt`.

Estas condiciones no impiden la operación actual del MVP, pero deben tratarse como deuda técnica controlada y no como patrón para nuevas implementaciones.

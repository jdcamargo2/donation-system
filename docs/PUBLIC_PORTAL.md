# Portal público de transparencia

Este documento define el alcance, las rutas, las reglas de publicación y las restricciones de privacidad del portal público de SIGEDON.

## 1. Objetivo

El portal público permite publicar información institucional autorizada sin exponer datos operativos privados.

Su propósito es ofrecer una vista básica de transparencia sobre:

* proyectos activos y públicos;
* avances publicados;
* métricas agregadas;
* datos JSON autorizados.

La ruta principal es:

```text
/transparency/
```

## 2. Rutas públicas

```text
/transparency/
/transparency/projects/
/transparency/projects/<id>/
/transparency/updates/
/transparency/data/projects.json
/transparency/data/metrics.json
```

### Descripción

* `/transparency/`: página principal del portal.
* `/transparency/projects/`: listado de proyectos publicados.
* `/transparency/projects/<id>/`: detalle público de un proyecto.
* `/transparency/updates/`: feed de avances publicados.
* `/transparency/data/projects.json`: salida JSON autorizada de proyectos.
* `/transparency/data/metrics.json`: salida JSON autorizada de métricas agregadas.

Las rutas JSON no constituyen una API pública avanzada.

## 3. Proyectos publicados

Un proyecto aparece en el portal solo cuando cumple ambas condiciones:

```text
Project.status == ACTIVE
AND
Project.is_public == True
```

### Reglas

* `ACTIVE` solo es insuficiente.
* `is_public=True` solo es insuficiente.
* Un proyecto `ACTIVE` privado no aparece en listado ni detalle públicos.
* Un proyecto `CLOSED` permanece oculto aunque exista un dato inconsistente con
  `is_public=True`; los selectores públicos filtran por ambas condiciones.
* No existen estados `PLANNED`, `SUSPENDED` ni `ANNULLED` para `Project`; la
  elegibilidad pública se define únicamente con `ACTIVE` + `is_public`.

## 4. Avances publicados

Solo se muestran avances en estado:

```text
PUBLISHED
```

Además, el proyecto padre debe cumplir:

```text
status == ACTIVE
AND
is_public == True
```

### Reglas

* Los avances en estado `UNPUBLISHED` no se publican.
* Un avance publicado no aparece si su proyecto deja de estar activo o deja de
  ser público.
* Los datos privados asociados al avance permanecen protegidos.
* La publicación del avance no implica la publicación automática de todos sus adjuntos.
* Asignaciones y métricas públicas respetan el mismo límite de visibilidad del
  proyecto (`ACTIVE` + `is_public=True`).

## 5. Métricas públicas

Las métricas públicas:

* utilizan USD como moneda operativa;
* excluyen donaciones anuladas;
* excluyen asignaciones anuladas;
* excluyen gastos anulados;
* no convierten monedas;
* utilizan únicamente agregados autorizados;
* no exponen registros financieros individuales.

Las métricas públicas se expresan exclusivamente en USD.

## 6. Datos publicados

El portal puede exponer, según el contexto:

* nombre del proyecto;
* código público autorizado;
* descripción;
* objetivo;
* ubicación general;
* estado;
* fechas relevantes;
* progreso del proyecto derivado de hitos (cuando se publique);
* avances publicados;
* métricas agregadas;
* información institucional autorizada.

La selección exacta debe realizarse mediante selectores públicos específicos.

`ProjectUpdate` no expone un porcentaje de progreso propio; cualquier porcentaje de progreso de proyecto es independiente del contenido del avance.

## 7. Datos no publicados

El portal no debe exponer:

* usuarios;
* correos electrónicos;
* donaciones individuales;
* asignaciones individuales;
* gastos individuales;
* auditoría;
* documentos privados;
* soportes financieros;
* payloads Kobo;
* notas internas;
* firmas;
* revisiones internas del Comité;
* decisiones internas no autorizadas;
* submissions rechazadas;
* información Kobo no aprobada;
* entidades anuladas;
* identificadores técnicos innecesarios;
* datos personales sensibles.

## 8. Respuestas JSON

Los endpoints JSON son salidas controladas para transparencia.

No constituyen:

* una API pública completa;
* una API versionada;
* una interfaz garantizada para integraciones externas;
* un mecanismo de acceso a información privada;
* un sustituto de los selectores públicos.

### Reglas

* Deben devolver únicamente campos autorizados.
* No deben reutilizar serializaciones internas sin sanitización.
* Deben mantener las mismas reglas de exclusión que las vistas HTML.
* No deben exponer relaciones operativas completas.
* Los cambios de estructura deben revisarse antes de considerarlos estables.

## 9. Caché

Tiempos de caché de referencia:

| Recurso             |     Duración |
| ------------------- | -----------: |
| Página principal    |  60 segundos |
| Lista de proyectos  | 120 segundos |
| Detalle de proyecto | 120 segundos |
| Feed de avances     |  60 segundos |

### Reglas

* La caché no debe almacenar datos privados.
* Las claves deben diferenciar correctamente cada recurso.
* La caché no sustituye las validaciones de publicación.
* Una respuesta obtenida desde caché debe haber sido generada previamente mediante selectores públicos autorizados.
* Los tiempos pueden ajustarse según la infraestructura y la frecuencia de actualización.
* Tras un cambio exitoso del ciclo de publicación del proyecto (publicar,
  retirar del portal, o terminar un proyecto que estaba público), la aplicación
  invalida la caché del portal. La implementación actual limpia el cache por
  defecto de forma amplia (`cache.clear()`); no se promete invalidación
  confiable por clave individual de vista.

## 10. Separación arquitectónica

El portal público:

* utiliza templates propios;
* no comparte navegación privada;
* no requiere autenticación;
* no importa formularios internos;
* no reutiliza vistas operativas;
* se alimenta mediante selectores públicos;
* no modifica información del dominio;
* no depende de permisos de usuarios autenticados.

La capa pública debe mantenerse separada del panel operativo.

## 11. Seguridad

El portal debe:

* evitar la exposición de archivos privados;
* impedir referencias directas a `FileField.url` para documentos protegidos;
* excluir datos anulados;
* excluir datos no aprobados;
* evitar mensajes de error con detalles internos;
* sanitizar la información antes de publicarla;
* utilizar HTTPS en producción;
* mantener consistencia entre vistas HTML y JSON.

Los errores públicos no deben mostrar:

* tracebacks;
* nombres internos de modelos;
* errores SQL;
* rutas del servidor;
* secretos;
* detalles técnicos del pipeline Kobo.

## 12. Selectores públicos

La selección de información debe realizarse mediante consultas o selectores diseñados específicamente para publicación.

Estos selectores deben:

* filtrar por `status=ACTIVE` e `is_public=True` en proyectos;
* excluir información anulada de entidades que admiten anulación;
* limitar campos;
* aplicar reglas de privacidad;
* calcular agregados autorizados;
* evitar consultas innecesarias;
* mantener coherencia entre páginas y JSON.

No debe enviarse al template un objeto completo si solo se requiere una parte de sus datos.

## 13. Criterios de aceptación

El portal público se considera correctamente protegido cuando:

* solo aparecen proyectos con `ACTIVE` e `is_public=True`;
* solo aparecen avances publicados de esos proyectos;
* no se exponen datos privados;
* no se exponen entidades anuladas;
* las métricas utilizan únicamente USD;
* los endpoints JSON contienen solo campos autorizados;
* los documentos privados no son accesibles directamente;
* las páginas y respuestas JSON son consistentes;
* la caché no permite filtrar información restringida;
* las pruebas automatizadas cubren publicación, exclusión y privacidad.

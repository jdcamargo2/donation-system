# Operación y mantenimiento de SIGEDON

Este documento describe las tareas habituales de preparación, operación, verificación y mantenimiento de SIGEDON.

## 1. Preparación local

### 1.1. Crear y activar el entorno virtual

```bash
python3.12 -m venv venv
source venv/bin/activate
```

### 1.2. Instalar dependencias

```bash
pip install -r requirements.txt
```

### 1.3. Crear el archivo de entorno

```bash
cp .env.example .env
```

Antes de continuar, debe completarse la configuración mínima requerida en `.env`.

### 1.4. Aplicar migraciones

```bash
python manage.py migrate
```

### 1.5. Crear un superusuario

```bash
python manage.py createsuperuser
```

### 1.6. Sincronizar roles y permisos

```bash
python manage.py sync_sigedon_roles
```

### 1.7. Ejecutar el servidor local

```bash
python manage.py runserver
```

## 2. Datos de demostración

`seed_sigedon_demo` genera datos locales para explorar la interfaz, realizar
revisiones manuales y preparar capturas de pantalla. No representa una carga
productiva ni una verificación integral de las reglas operativas.

### Precondición

* Ejecutar únicamente en un entorno local o efímero.
* Utilizar una base de datos no productiva donde se acepten datos de demostración.

Comando:

```bash
python manage.py seed_sigedon_demo
```

### Opciones

```text
--password
--skip-users
```

### Reglas

* **No debe ejecutarse en producción.**
* Usa ORM directo intencionalmente y no pasa por todos los services de dominio.
* No genera trazabilidad completa en `AuditLog`.
* Puede crear gastos sin `SupportingDocument`, aunque el flujo UI operativo lo exige.
* Usa códigos explícitos reservados para demostración.
* Su idempotencia es parcial: actualiza entidades clave, pero no garantiza que
  una base previamente modificada vuelva a un estado canónico.
* Las credenciales demo no deben reutilizarse en entornos reales.

### Postcondición

* Quedan disponibles las entidades mínimas para navegar y revisar la interfaz.
* Los datos resultantes no deben considerarse evidencia de cumplimiento de
  todas las reglas, auditorías o invariantes del flujo operativo.

## 3. Sincronización de roles

Los roles y permisos deben sincronizarse después de:

* un despliegue inicial;
* una restauración de base de datos;
* cambios en la matriz de permisos;
* una actualización del sistema;
* modificaciones en grupos operativos;
* incorporación o eliminación de modelos protegidos.

Comando:

```bash
python manage.py sync_sigedon_roles
```

La operación debe ser idempotente y retirar permisos incompatibles heredados.

## 4. Operación inicial de KoboToolbox

### 4.1. Registrar definiciones soportadas

```bash
python manage.py register_kobo_forms
```

### 4.2. Verificar activos disponibles

```bash
python manage.py discover_kobo_assets --dry-run
```

### 4.3. Registrar activos descubiertos

```bash
python manage.py discover_kobo_assets
```

### 4.4. Configuración posterior

Después del descubrimiento se debe:

1. seleccionar el activo correcto;
2. asociarlo con una definición soportada;
3. configurar el binding hacia un proyecto;
4. activar el activo;
5. verificar las credenciales del webhook;
6. comprobar la recepción de submissions;
7. ejecutar el procesamiento cuando corresponda.

El descubrimiento no activa automáticamente un activo ni configura su routing.

## 5. Procesamiento y reconciliación de Kobo

### Procesar submissions pendientes

```bash
python manage.py process_kobo_submissions
```

Opciones disponibles:

```text
--limit
--submission-id
--download-attachments
```

### Reconciliar submissions remotas

```bash
python manage.py reconcile_kobo_submissions
```

Opciones disponibles:

```text
--asset-uid
--limit
--dry-run
```

La reconciliación recupera submissions ausentes en staging, pero no sustituye la validación, normalización ni revisión humana.

## 6. Verificación diaria o previa a una entrega

Ejecutar:

```bash
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test
git diff --check
```

### Resultado esperado

* `check` sin incidencias;
* ninguna migración pendiente;
* suite automatizada en verde;
* ningún error de espacios, conflictos o formato detectado por Git.

Cuando se utilicen pruebas dependientes de PostgreSQL, deben ejecutarse contra ese motor y no asumirse como validadas únicamente con SQLite.

## 7. Gestión de archivos

Directorios locales habituales:

```text
staticfiles/
media/
private_media/
```

Estos directorios no deben versionarse.

### Reglas de operación

* `staticfiles/` contiene archivos estáticos recopilados.
* `media/` puede contener archivos públicos o de desarrollo, según la configuración.
* `private_media/` contiene archivos que requieren autorización.
* Los archivos privados no deben exponerse mediante URLs directas.
* Producción debe utilizar permisos adecuados sobre el sistema de archivos o el almacenamiento externo.
* Los respaldos de archivos deben tratarse como información sensible.
* Debe verificarse el espacio disponible y la política de retención.

## 8. Base de datos

Producción requiere PostgreSQL.

### Antes de ejecutar migraciones

1. crear una copia de seguridad;
2. verificar credenciales;
3. validar la conexión;
4. revisar las migraciones pendientes;
5. comprobar dependencias entre migraciones;
6. ejecutar el cambio en una ventana controlada;
7. verificar el sistema después de migrar.

Comandos recomendados:

```bash
python manage.py showmigrations
python manage.py migrate --plan
python manage.py migrate
python manage.py check
```

### Reglas

* No deben editarse migraciones ya aplicadas en producción sin una justificación técnica controlada.
* No deben crearse códigos operativos manualmente para corregir secuencias.
* Las operaciones destructivas requieren respaldo y validación previa.
* Debe comprobarse que `OperationalCodeSequence` permanezca consistente.

## 9. Copias de seguridad y restauración

Antes de despliegues, migraciones relevantes o cambios de infraestructura, debe existir una copia de seguridad verificable.

Los scripts manuales viven en `deploy/backups/` (ver `deploy/backups/README.md`). Esta fase **no** automatiza frecuencia ni retención con cron/systemd.

### Ventana de mantenimiento

La consistencia del backup exige una **ventana de mantenimiento obligatoria**:

* detener web, workers, comandos de procesamiento Kobo y uploads;
* exportar `SIGEDON_MAINTENANCE_CONFIRMED=YES`;
* exportar `SIGEDON_BACKUP_ROOT` (se crea automáticamente con permisos `0700` si no existe) y `SIGEDON_MEDIA_ROOT` (debe existir; no se crea);
* ejecutar `deploy/backups/backup_sigedon.sh`.

El script no puede comprobar por sí solo que esos procesos estén detenidos.

### Contenido del respaldo

Cada backup publicado es un directorio:

```text
<backup_id>/
  database.dump    # pg_dump --format=custom
  media.tar.gz
  manifest.json
```

Incluye PostgreSQL + `MEDIA_ROOT`. La configuración crítica y los secretos deben respaldarse fuera de estos scripts, en un sistema seguro.

### Verificación y restore aislado

```bash
./deploy/backups/verify_backup.sh /ruta/al/<backup_id>
```

La restauración solo está permitida hacia bases con prefijo seguro (`test_restore_` / `staging_restore_`), distintas de `POSTGRES_DB`, con `SIGEDON_RESTORE_CONFIRM=YES` y un directorio de media nuevo/vacío. Nunca sobreescribir el `MEDIA_ROOT` activo ni modificar `.env`.

### Post-restore

Con el entorno apuntando a la base y media restauradas:

```bash
python manage.py migrate --check
python manage.py check
python manage.py verify_postgres_security
python manage.py reconcile_operational_code_sequences
python manage.py verify_restored_data
python manage.py sync_sigedon_roles
```

`reconcile_operational_code_sequences` funciona en modo detect-only y es de
solo lectura: no crea ni ajusta secuencias. El comando falla ante una secuencia
ausente (`MISSING_SEQUENCE`), atrasada (`LAGGING_SEQUENCE`) o inválida
(`INVALID_SEQUENCE`). No existe reparación automática; cualquier corrección
debe revisarse y ejecutarse manualmente. Una secuencia adelantada es válida.

### Reglas

* Los respaldos no deben versionarse.
* Deben almacenarse fuera del repositorio.
* Deben protegerse mediante controles de acceso.
* Preferir `~/.pgpass` frente a `PGPASSWORD` en los scripts.
* Las restauraciones deben probarse periódicamente (al menos trimestral).
* Un respaldo no se considera válido hasta comprobar que puede restaurarse.
* Cifrado y copia off-site son requisitos de infraestructura (aún no implementados en scripts).
* **RPO/RTO no están definidos** hasta medir restauraciones reales.

## 10. Auditoría

`AuditLog` no debe limpiarse, editarse ni eliminarse mediante scripts ordinarios.

Cualquier política futura de:

* retención;
* exportación;
* archivado;
* anonimización;
* traslado a almacenamiento externo;

requiere una decisión institucional explícita y una implementación controlada.

Los eventos técnicos de Kobo no sustituyen el registro de auditoría funcional.

## 11. Monitoreo operativo

Durante la operación deben revisarse:

* errores de aplicación;
* respuestas `500`;
* accesos `403` inesperados;
* fallos del webhook;
* submissions detenidas;
* errores de procesamiento Kobo;
* espacio disponible;
* conexiones a PostgreSQL;
* vencimiento o rotación de credenciales;
* crecimiento de archivos y logs.

Los logs no deben contener secretos, tokens ni payloads sensibles completos.

## 12. Errores comunes

### 12.1. Falta una secuencia operativa

#### Síntomas

* no puede generarse un código;
* aparece un error indicando que la secuencia no está inicializada;
* falla la creación de proyectos, donaciones, asignaciones o gastos.

#### Verificar

* migraciones aplicadas;
* existencia de `OperationalCodeSequence`;
* namespace correcto;
* consistencia de la secuencia.

#### Acción

* ejecutar las migraciones pendientes;
* revisar la inicialización de secuencias;
* no crear códigos manualmente;
* no corregir el problema contando filas.

---

### 12.2. Kobo no procesa submissions

#### Verificar

* `KOBO_ENABLED`;
* `KOBO_BASE_URL`;
* `KOBO_API_TOKEN`;
* credenciales del webhook;
* activo configurado;
* activo habilitado;
* definición asociada;
* binding configurado;
* estado de la submission;
* eventos de procesamiento.

Comandos útiles:

```bash
python manage.py process_kobo_submissions
python manage.py reconcile_kobo_submissions --dry-run
```

No deben registrarse tokens ni secretos al diagnosticar.

---

### 12.3. Respuesta `403 Forbidden`

#### Verificar

* autenticación;
* grupo asignado;
* permisos individuales;
* matriz vigente;
* sincronización de roles;
* permiso requerido por la vista;
* pertenencia del recurso solicitado.

Acción recomendada:

```bash
python manage.py sync_sigedon_roles
```

La ausencia de un botón en la interfaz no demuestra que el permiso esté correctamente configurado en el servidor.

---

### 12.4. Métricas vacías

#### Verificar

* permisos del usuario;
* existencia de registros;
* moneda operativa USD;
* estados anulados;
* filtros de publicación;
* fechas y estados operativos;
* selectores utilizados por el dashboard o portal.

Los datos anulados quedan excluidos deliberadamente; toda operación monetaria válida está expresada en USD.

---

### 12.5. Archivos que no descargan

#### Verificar

* existencia física del archivo;
* ruta configurada;
* permisos del usuario;
* relación con la entidad;
* clasificación pública o privada;
* configuración de almacenamiento;
* tamaño y metadatos del adjunto Kobo.

Los archivos privados no deben corregirse exponiendo directamente `FileField.url`.

---

### 12.6. Migraciones pendientes

Ejecutar:

```bash
python manage.py makemigrations --check --dry-run
python manage.py showmigrations
```

Si aparecen cambios inesperados:

* revisar modelos modificados;
* comprobar migraciones no versionadas;
* no generar una migración automática sin entender su causa;
* verificar que la rama de trabajo esté actualizada.

## 13. Cierre de una versión

Antes de etiquetar o desplegar una versión, ejecutar:

```bash
git status --short
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test
git diff --check
```

También debe verificarse:

* documentación actualizada;
* variables nuevas reflejadas en `.env.example`;
* migraciones versionadas;
* dependencias registradas;
* roles sincronizados;
* ausencia de secretos;
* estado de la integración Kobo;
* respaldo disponible cuando corresponda.

### Resultado esperado

* repositorio limpio;
* suite automatizada en verde;
* ninguna migración pendiente;
* documentación coherente con el código;
* configuración de despliegue validada.

## 14. Lista mínima posterior al despliegue

Después de desplegar:

1. ejecutar migraciones;
2. sincronizar roles;
3. ejecutar `python manage.py check`;
4. verificar acceso al panel interno;
5. verificar acceso al portal público;
6. comprobar descargas protegidas;
7. confirmar conexión con PostgreSQL;
8. revisar logs;
9. verificar webhook y procesamiento Kobo, cuando esté habilitado;
10. realizar una comprobación funcional básica sin alterar datos reales.

El despliegue no debe considerarse completo hasta validar el comportamiento básico del sistema.

## Hub territorial Kobo

Con Kobo habilitado, el Hub `/integrations/kobo/` permite revisar y operar
mappings, identidades, conflictos y reconciliación. Compruebe que los permisos
Kobo se asignaron antes de habilitar acceso operativo.

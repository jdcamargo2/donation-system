# Esquema de base de datos de SIGEDON

Este documento describe las tablas físicas utilizadas por SIGEDON, sus relaciones principales, claves, restricciones y responsabilidades.

## 1. Propósito

La fuente de verdad del esquema sigue este orden:

```text
Modelos Django
→ Migraciones aplicadas
→ Esquema PostgreSQL
→ Este documento
```

En caso de contradicción, debe verificarse primero el código productivo y las migraciones realmente aplicadas.

## 2. Resumen

La base de datos aplicada contiene 32 tablas.

| Grupo                              | Cantidad |
| ---------------------------------- | -------: |
| Dominio operativo de SIGEDON       |       13 |
| Integración con KoboToolbox        |        9 |
| Autenticación y permisos de Django |        6 |
| Infraestructura interna de Django  |        4 |
| **Total**                          |   **32** |

Las aplicaciones `apps.public_portal` y `web` no poseen tablas propias.

## 3. Convenciones

### 3.1. Claves primarias

La mayoría de las tablas utiliza:

```text
id BIGINT
```

Este identificador se genera mediante `BigAutoField`.

La excepción es:

```text
operations_operationalcodesequence.namespace
```

Este campo utiliza un identificador textual como clave primaria.

### 3.2. Claves foráneas

Django representa físicamente las claves foráneas con el sufijo `_id`.

Ejemplos:

```text
donor
→ donor_id

project
→ project_id

created_by
→ created_by_id
```

### 3.3. Fechas de trazabilidad

Los campos temporales más frecuentes son:

```text
created_at
updated_at
reviewed_at
decided_at
received_at
processed_at
imported_at
```

### 3.4. Registros terminales

Los proyectos, donaciones, asignaciones y gastos pueden conservar:

```text
terminal_reason
terminal_at
terminal_by_id
```

Estos campos registran:

* la razón de la acción terminal;
* la fecha y hora;
* el usuario responsable.

## 4. Diagrama general de relaciones

```text
operations_institution
        │
        └──< operations_donation
                    │
                    └──< operations_fundallocation >── operations_project
                                      │                       │
                                      └──< operations_expense │
                                                │             │
                                                └──< operations_supportingdocument
                                                              │
operations_project ──< operations_projectdocument             │
        │                                                     │
        └──< operations_projectupdate                         │
                    │                                         │
                    ├──< operations_projectupdateattachment   │
                    │                                         │
                    └──1 operations_projectupdatereview       │
                              │                               │
                              └──1 operations_projectupdatereviewdecision

operations_project
        ├──< kobo_koboprojectbinding >── kobo_koboasset
        ├──< kobo_kobosubmission
        ├──< kobo_koboterritorialidentity
        └──< kobo_koboterritorialprofile

kobo_koboformdefinition
        ├──< kobo_koboasset
        └──< kobo_kobosubmission
                    │
                    ├──< kobo_koboattachment
                    ├──< kobo_koboprocessingevent
                    ├──1 kobo_koboimportrecord
                    └──1 kobo_koboterritorialprofile >── kobo_koboterritorialidentity

kobo_koboterritorialidentity
        └──< kobo_koboterritorialprofile

kobo_ficha01territorio
        └──< kobo_ficha01coveredcommunity
```

Las dos tablas Ficha01 del diagrama describen schema legado. No tienen
escritores activos conocidos, no participan en el pipeline vigente y no son la
fuente de verdad del staging genérico basado en `kobo_kobosubmission`.

Convenciones utilizadas:

```text
──<  Uno a muchos
──1  Uno a uno
>──  Relación hacia otra entidad
```

## 5. Tablas del dominio operativo

### 5.1. `operations_institution`

Representa una institución participante en SIGEDON.

| Columna              | Tipo lógico     | Reglas             |
| -------------------- | --------------- | ------------------ |
| `id`                 | `BigAutoField`  | Clave primaria     |
| `name`               | `CharField`     | Obligatorio        |
| `institution_type`   | `CharField`     | Obligatorio        |
| `role`               | `CharField`     | Obligatorio        |
| `country`            | `CountryField`  | Obligatorio        |
| `contact_email`      | `EmailField`    | Opcional           |
| `contact_phone`      | `CharField`     | Opcional           |
| `responsible_person` | `CharField`     | Opcional           |
| `legal_document`     | `FileField`     | Opcional y privado |
| `status`             | `CharField`     | Obligatorio        |
| `created_at`         | `DateTimeField` | Automático         |
| `updated_at`         | `DateTimeField` | Automático         |

#### Relación

```text
Institution 1 ──< Donation
```

#### Roles soportados

```text
donor
receiver
executor
ally
supervisor
```

#### Estados

```text
active
inactive
```

---

### 5.2. `operations_project`

Representa un proyecto institucional.

| Columna            | Tipo lógico     | Reglas                             |
| ------------------ | --------------- | ---------------------------------- |
| `id`               | `BigAutoField`  | Clave primaria                     |
| `code`             | `CharField`     | Único e inmutable                  |
| `name`             | `CharField`     | Obligatorio                        |
| `description`      | `TextField`     | Opcional                           |
| `objective`        | `TextField`     | Opcional                           |
| `responsible_unit` | `CharField`     | Opcional                           |
| `location`         | `CharField`     | Opcional                           |
| `estimated_budget` | `DecimalField`  | Mayor o igual a cero               |
| `start_date`       | `DateField`     | Opcional                           |
| `end_date`         | `DateField`     | Opcional                           |
| `status`           | `CharField`     | Obligatorio                        |
| `terminal_reason`  | `TextField`     | Opcional                           |
| `terminal_at`      | `DateTimeField` | Opcional                           |
| `terminal_by_id`   | `ForeignKey`    | Opcional, referencia a `auth_user` |
| `created_at`       | `DateTimeField` | Automático                         |
| `updated_at`       | `DateTimeField` | Automático                         |

#### Restricción

```text
operations_project_budget_gte_zero

estimated_budget >= 0
```

#### Estados

```text
planned
active
suspended
closed
annulled
```

#### Relaciones

```text
Project 1 ──< FundAllocation
Project 1 ──< ProjectDocument
Project 1 ──< ProjectUpdate
Project 1 ──< KoboProjectBinding
Project 1 ──< KoboSubmission
```

---

### 5.3. `operations_donation`

Representa una donación monetaria.

| Columna             | Tipo lógico     | Reglas                                |
| ------------------- | --------------- | ------------------------------------- |
| `id`                | `BigAutoField`  | Clave primaria                        |
| `code`              | `CharField`     | Único e inmutable                     |
| `donor_id`          | `ForeignKey`    | Referencia a `operations_institution` |
| `donation_type`     | `CharField`     | Obligatorio                           |
| `amount`            | `DecimalField`  | Mayor que cero                        |
| `currency`          | `CharField`     | USD obligatorio por constraint         |
| `objective`         | `TextField`     | Obligatorio                           |
| `restrictions`      | `TextField`     | Opcional                              |
| `commitment_date`   | `DateField`     | Opcional                              |
| `received_date`     | `DateField`     | Opcional                              |
| `status`            | `CharField`     | Obligatorio                           |
| `terminal_reason`   | `TextField`     | Opcional                              |
| `terminal_at`       | `DateTimeField` | Opcional                              |
| `terminal_by_id`    | `ForeignKey`    | Opcional, referencia a `auth_user`    |
| `support_reference` | `CharField`     | Opcional                              |
| `created_at`        | `DateTimeField` | Automático                            |
| `updated_at`        | `DateTimeField` | Automático                            |

#### Restricción

```text
operations_donation_amount_gt_zero

amount > 0
```

#### Estados

```text
registered
received
annulled
```

#### Relaciones

```text
Institution 1 ──< Donation
Donation 1 ──< FundAllocation
```

El saldo no se almacena en una columna editable. Se calcula a partir de las asignaciones no anuladas.

---

### 5.4. `operations_fundallocation`

Representa la distribución de fondos desde una donación hacia un proyecto.

| Columna              | Tipo lógico     | Reglas                             |
| -------------------- | --------------- | ---------------------------------- |
| `id`                 | `BigAutoField`  | Clave primaria                     |
| `code`               | `CharField`     | Único e inmutable                  |
| `donation_id`        | `ForeignKey`    | Referencia a `operations_donation` |
| `project_id`         | `ForeignKey`    | Referencia a `operations_project`  |
| `budget_category`    | `CharField`     | Obligatorio                        |
| `amount`             | `DecimalField`  | Mayor que cero                     |
| `responsible_person` | `CharField`     | Opcional                           |
| `allocation_date`    | `DateField`     | Obligatorio                        |
| `status`             | `CharField`     | Obligatorio                        |
| `terminal_reason`    | `TextField`     | Opcional                           |
| `terminal_at`        | `DateTimeField` | Opcional                           |
| `terminal_by_id`     | `ForeignKey`    | Opcional, referencia a `auth_user` |
| `notes`              | `TextField`     | Opcional                           |
| `created_at`         | `DateTimeField` | Automático                         |
| `updated_at`         | `DateTimeField` | Automático                         |

#### Restricción

```text
operations_allocation_amount_gt_zero

amount > 0
```

#### Estados

```text
active
finished
annulled
```

#### Relaciones

```text
Donation 1 ──< FundAllocation
Project 1 ──< FundAllocation
FundAllocation 1 ──< Expense
```

El monto ejecutado y el saldo se derivan de los gastos no anulados.

---

### 5.5. `operations_expense`

Representa un gasto registrado contra una asignación.

| Columna                 | Tipo lógico     | Reglas                                   |
| ----------------------- | --------------- | ---------------------------------------- |
| `id`                    | `BigAutoField`  | Clave primaria                           |
| `code`                  | `CharField`     | Único e inmutable                        |
| `allocation_id`         | `ForeignKey`    | Referencia a `operations_fundallocation` |
| `expense_date`          | `DateField`     | Obligatorio                              |
| `category`              | `CharField`     | Obligatorio                              |
| `amount`                | `DecimalField`  | Mayor que cero                           |
| `currency`              | `CharField`     | USD obligatorio por constraint            |
| `reason`                | `CharField`     | Obligatorio                              |
| `provider_or_recipient` | `CharField`     | Obligatorio                              |
| `payment_method`        | `CharField`     | Obligatorio                              |
| `description`           | `TextField`     | Opcional                                 |
| `observations`          | `TextField`     | Opcional                                 |
| `status`                | `CharField`     | Obligatorio                              |
| `terminal_reason`       | `TextField`     | Opcional                                 |
| `terminal_at`           | `DateTimeField` | Opcional                                 |
| `terminal_by_id`        | `ForeignKey`    | Opcional, referencia a `auth_user`       |
| `created_at`            | `DateTimeField` | Automático                               |
| `updated_at`            | `DateTimeField` | Automático                               |

#### Restricción

```text
operations_expense_amount_gt_zero

amount > 0
```

#### Estados

```text
registered
annulled
```

#### Relaciones

```text
FundAllocation 1 ──< Expense
Expense 1 ──< SupportingDocument
```

---

### 5.6. `operations_supportingdocument`

Representa evidencia documental asociada a un gasto.

| Columna       | Tipo lógico     | Reglas                            |
| ------------- | --------------- | --------------------------------- |
| `id`          | `BigAutoField`  | Clave primaria                    |
| `expense_id`  | `ForeignKey`    | Referencia a `operations_expense` |
| `title`       | `CharField`     | Obligatorio                       |
| `document`    | `FileField`     | Obligatorio y privado             |
| `uploaded_at` | `DateTimeField` | Automático                        |
| `notes`       | `TextField`     | Opcional                          |

#### Relación

```text
Expense 1 ──< SupportingDocument
```

Los archivos se descargan mediante endpoints autorizados.

---

### 5.7. `operations_projectdocument`

Representa documentos generales de un proyecto.

| Columna          | Tipo lógico     | Reglas                             |
| ---------------- | --------------- | ---------------------------------- |
| `id`             | `BigAutoField`  | Clave primaria                     |
| `project_id`     | `ForeignKey`    | Referencia a `operations_project`  |
| `document_type`  | `CharField`     | Obligatorio                        |
| `title`          | `CharField`     | Obligatorio                        |
| `file`           | `FileField`     | Obligatorio y privado              |
| `description`    | `TextField`     | Opcional                           |
| `uploaded_by_id` | `ForeignKey`    | Opcional, referencia a `auth_user` |
| `created_at`     | `DateTimeField` | Automático                         |

#### Relación

```text
Project 1 ──< ProjectDocument
```

---

### 5.8. `operations_projectupdate`

Representa un avance de proyecto.

| Columna               | Tipo lógico                 | Reglas                             |
| --------------------- | --------------------------- | ---------------------------------- |
| `id`                  | `BigAutoField`              | Clave primaria                     |
| `project_id`          | `ForeignKey`                | Referencia a `operations_project`  |
| `title`               | `CharField`                 | Obligatorio                        |
| `description`         | `TextField`                 | Obligatorio                        |
| `update_date`         | `DateField`                 | Obligatorio                        |
| `progress_percentage` | `PositiveSmallIntegerField` | Entre 0 y 100                      |
| `status`              | `CharField`                 | Obligatorio                        |
| `created_at`          | `DateTimeField`             | Automático                         |
| `updated_at`          | `DateTimeField`             | Automático                         |
| `created_by_id`       | `ForeignKey`                | Opcional, referencia a `auth_user` |
| `reported_by_id`      | `ForeignKey`                | Opcional, referencia a `auth_user` |

#### Restricción

```text
project_update_progress_between_0_and_100

0 <= progress_percentage <= 100
```

#### Estados

```text
draft
published
```

#### Relaciones

```text
Project 1 ──< ProjectUpdate
ProjectUpdate 1 ──< ProjectUpdateAttachment
ProjectUpdate 1 ──1 ProjectUpdateReview
```

#### Responsabilidades de usuario

```text
created_by_id
→ Usuario que realizó técnicamente el registro

reported_by_id
→ Persona responsable del contenido del avance
```

---

### 5.9. `operations_projectupdateattachment`

Representa evidencia asociada a un avance.

| Columna             | Tipo lógico     | Reglas                                  |
| ------------------- | --------------- | --------------------------------------- |
| `id`                | `BigAutoField`  | Clave primaria                          |
| `project_update_id` | `ForeignKey`    | Referencia a `operations_projectupdate` |
| `file`              | `FileField`     | Obligatorio y privado                   |
| `title`             | `CharField`     | Opcional                                |
| `uploaded_by_id`    | `ForeignKey`    | Opcional, referencia a `auth_user`      |
| `created_at`        | `DateTimeField` | Automático                              |

#### Relación

```text
ProjectUpdate 1 ──< ProjectUpdateAttachment
```

---

### 5.10. `operations_projectupdatereview`

Representa la revisión institucional de un avance publicado.

| Columna             | Tipo lógico     | Reglas                             |
| ------------------- | --------------- | ---------------------------------- |
| `id`                | `BigAutoField`  | Clave primaria                     |
| `project_update_id` | `OneToOneField` | Único                              |
| `observations`      | `TextField`     | Obligatorio                        |
| `reviewed_by_id`    | `ForeignKey`    | Opcional, referencia a `auth_user` |
| `reviewed_at`       | `DateTimeField` | Automático                         |

#### Relación

```text
ProjectUpdate 1 ──1 ProjectUpdateReview
```

Solo puede existir una revisión por avance.

---

### 5.11. `operations_projectupdatereviewdecision`

Representa la decisión institucional sobre una revisión.

| Columna         | Tipo lógico     | Reglas                             |
| --------------- | --------------- | ---------------------------------- |
| `id`            | `BigAutoField`  | Clave primaria                     |
| `review_id`     | `OneToOneField` | Único                              |
| `outcome`       | `CharField`     | Obligatorio                        |
| `rationale`     | `TextField`     | Obligatorio                        |
| `decided_by_id` | `ForeignKey`    | Opcional, referencia a `auth_user` |
| `decided_at`    | `DateTimeField` | Automático                         |

#### Resultados

```text
conforming
observed
```

#### Relación

```text
ProjectUpdateReview 1 ──1 ProjectUpdateReviewDecision
```

Solo puede existir una decisión por revisión.

---

### 5.12. `operations_auditlog`

Representa el registro institucional append-only.

| Columna        | Tipo lógico     | Reglas                             |
| -------------- | --------------- | ---------------------------------- |
| `id`           | `BigAutoField`  | Clave primaria                     |
| `user_id`      | `ForeignKey`    | Opcional, referencia a `auth_user` |
| `action`       | `CharField`     | Obligatorio                        |
| `model_name`   | `CharField`     | Obligatorio                        |
| `entity_id`    | `CharField`     | Obligatorio                        |
| `entity_label` | `CharField`     | Obligatorio                        |
| `summary`      | `TextField`     | Obligatorio                        |
| `created_at`   | `DateTimeField` | Automático                         |

#### Propiedades

* No puede editarse.
* No puede eliminarse.
* No utiliza una clave foránea genérica hacia la entidad auditada.
* Conserva identificadores y etiquetas históricas.
* Puede mantener registros aunque la entidad original deje de existir.

---

### 5.13. `operations_operationalcodesequence`

Mantiene los próximos números disponibles para los códigos operativos.

| Columna      | Tipo lógico               | Reglas                 |
| ------------ | ------------------------- | ---------------------- |
| `namespace`  | `CharField`               | Clave primaria y única |
| `prefix`     | `CharField`               | Único                  |
| `next_value` | `PositiveBigIntegerField` | Mayor o igual a 1      |

#### Filas esperadas

| Namespace         | Prefijo |
| ----------------- | ------- |
| `project`         | `PRJ`   |
| `donation`        | `DON`   |
| `fund_allocation` | `ASG`   |
| `expense`         | `GAS`   |

#### Ejemplos

```text
PRJ-000001
DON-000001
ASG-000001
GAS-000001
```

La reserva utiliza transacciones y bloqueo de fila mediante PostgreSQL.

## 6. Tablas de integración con KoboToolbox

### 6.1. `kobo_koboformdefinition`

Representa una definición versionada de formulario.

| Columna           | Tipo lógico     | Reglas         |
| ----------------- | --------------- | -------------- |
| `id`              | `BigAutoField`  | Clave primaria |
| `form_id`         | `CharField`     | Obligatorio    |
| `title`           | `CharField`     | Obligatorio    |
| `version`         | `CharField`     | Obligatorio    |
| `schema_snapshot` | `JSONField`     | Obligatorio    |
| `field_mapping`   | `JSONField`     | Obligatorio    |
| `is_active`       | `BooleanField`  | Obligatorio    |
| `created_at`      | `DateTimeField` | Automático     |
| `updated_at`      | `DateTimeField` | Automático     |

#### Restricción

```text
kobo_unique_form_version

UNIQUE (form_id, version)
```

#### Relaciones

```text
KoboFormDefinition 1 ──< KoboAsset
KoboFormDefinition 1 ──< KoboSubmission
```

---

### 6.2. `kobo_koboasset`

Representa un activo Kobo configurado.

| Columna              | Tipo lógico     | Reglas                                 |
| -------------------- | --------------- | -------------------------------------- |
| `id`                 | `BigAutoField`  | Clave primaria                         |
| `asset_uid`          | `CharField`     | Único                                  |
| `name`               | `CharField`     | Obligatorio                            |
| `form_definition_id` | `ForeignKey`    | Referencia a `kobo_koboformdefinition` |
| `form_role`          | `CharField`     | Restringido                            |
| `is_active`          | `BooleanField`  | Obligatorio                            |
| `created_at`         | `DateTimeField` | Automático                             |
| `updated_at`         | `DateTimeField` | Automático                             |

#### Restricción

```text
kobo_asset_valid_form_role
```

Valores admitidos:

```text
territorial_profile
prioritized_microproject
prioritization_matrix
```

---

### 6.3. `kobo_kobodiscoveredasset`

Representa el inventario local de activos encontrados en KoboToolbox.

| Columna              | Tipo lógico     | Reglas         |
| -------------------- | --------------- | -------------- |
| `id`                 | `BigAutoField`  | Clave primaria |
| `asset_uid`          | `CharField`     | Único          |
| `name`               | `CharField`     | Obligatorio    |
| `asset_type`         | `CharField`     | Opcional       |
| `deployment_status`  | `CharField`     | Opcional       |
| `owner_username`     | `CharField`     | Opcional       |
| `remote_created_at`  | `DateTimeField` | Opcional       |
| `remote_modified_at` | `DateTimeField` | Opcional       |
| `metadata_snapshot`  | `JSONField`     | Obligatorio    |
| `discovered_at`      | `DateTimeField` | Automático     |
| `last_seen_at`       | `DateTimeField` | Obligatorio    |
| `is_available`       | `BooleanField`  | Obligatorio    |

Un activo descubierto no representa automáticamente un activo configurado o habilitado.

---

### 6.4. `kobo_koboprojectbinding`

Relaciona un activo Kobo con un proyecto SIGEDON.

| Columna        | Tipo lógico     | Reglas                            |
| -------------- | --------------- | --------------------------------- |
| `id`           | `BigAutoField`  | Clave primaria                    |
| `asset_id`     | `ForeignKey`    | Referencia a `kobo_koboasset`     |
| `project_id`   | `ForeignKey`    | Referencia a `operations_project` |
| `routing_type` | `CharField`     | `direct` o `field_value`          |
| `source_field` | `CharField`     | Dependiente del routing           |
| `source_value` | `CharField`     | Dependiente del routing           |
| `is_active`    | `BooleanField`  | Obligatorio                       |
| `created_at`   | `DateTimeField` | Automático                        |
| `updated_at`   | `DateTimeField` | Automático                        |

#### Restricciones

##### Un binding directo por activo

```text
kobo_unique_direct_per_asset
```

Solo puede existir un binding directo por activo.

##### Ruta de campo única

```text
kobo_unique_field_route
```

No puede repetirse la combinación:

```text
asset + source_field + source_value
```

para routing por valor de campo.

##### Coherencia de campos

```text
kobo_binding_valid_route_fields
```

Reglas:

```text
direct
→ source_field y source_value deben estar vacíos

field_value
→ source_field y source_value deben contener valor
```

##### Tipo de routing válido

```text
kobo_binding_valid_routing_type
```

Valores admitidos:

```text
direct
field_value
```

---

### 6.5. `kobo_kobosubmission`

Representa un envío recibido desde KoboToolbox.

| Columna              | Tipo lógico     | Reglas                                      |
| -------------------- | --------------- | ------------------------------------------- |
| `id`                 | `BigAutoField`  | Clave primaria                              |
| `form_definition_id` | `ForeignKey`    | Referencia a `kobo_koboformdefinition`      |
| `asset_id`           | `ForeignKey`    | Opcional, referencia a `kobo_koboasset`     |
| `project_id`         | `ForeignKey`    | Opcional, referencia a `operations_project` |
| `external_id`        | `CharField`     | Obligatorio                                 |
| `raw_payload`        | `JSONField`     | Sensible                                    |
| `normalized_payload` | `JSONField`     | Obligatorio                                 |
| `status`             | `CharField`     | Obligatorio                                 |
| `pastoral_zone`      | `CharField`     | Opcional                                    |
| `parish`             | `CharField`     | Opcional                                    |
| `primary_community`  | `CharField`     | Opcional                                    |
| `assessment_date`    | `DateField`     | Opcional                                    |
| `received_at`        | `DateTimeField` | Automático                                  |
| `normalized_at`      | `DateTimeField` | Opcional                                    |
| `processed_at`       | `DateTimeField` | Opcional                                    |
| `imported_at`        | `DateTimeField` | Opcional                                    |
| `error_code`         | `CharField`     | Opcional                                    |
| `error_message`      | `TextField`     | Opcional                                    |

#### Restricción

```text
kobo_unique_external_submission_per_form

UNIQUE (form_definition_id, external_id)
```

#### Relaciones

```text
KoboSubmission 1 ──< KoboAttachment
KoboSubmission 1 ──< KoboProcessingEvent
```

---

### 6.6. `kobo_koboattachment`

Representa un adjunto remoto o descargado desde KoboToolbox.

| Columna             | Tipo lógico               | Reglas                             |
| ------------------- | ------------------------- | ---------------------------------- |
| `id`                | `BigAutoField`            | Clave primaria                     |
| `submission_id`     | `ForeignKey`              | Referencia a `kobo_kobosubmission` |
| `field_name`        | `CharField`               | Obligatorio                        |
| `external_id`       | `CharField`               | Opcional                           |
| `source_url`        | `URLField`                | Opcional                           |
| `original_filename` | `CharField`               | Opcional                           |
| `content_type`      | `CharField`               | Opcional                           |
| `size_bytes`        | `PositiveBigIntegerField` | Opcional                           |
| `file`              | `FileField`               | Opcional                           |
| `privacy_level`     | `CharField`               | Obligatorio                        |
| `status`            | `CharField`               | Obligatorio                        |
| `error_message`     | `TextField`               | Opcional                           |
| `created_at`        | `DateTimeField`           | Automático                         |
| `updated_at`        | `DateTimeField`           | Automático                         |

Los archivos pueden existir como referencia remota aunque todavía no hayan sido descargados.

---

### 6.7. `kobo_koboprocessingevent`

Registra eventos técnicos del pipeline Kobo.

| Columna         | Tipo lógico     | Reglas                             |
| --------------- | --------------- | ---------------------------------- |
| `id`            | `BigAutoField`  | Clave primaria                     |
| `submission_id` | `ForeignKey`    | Referencia a `kobo_kobosubmission` |
| `stage`         | `CharField`     | Obligatorio                        |
| `level`         | `CharField`     | Obligatorio                        |
| `code`          | `CharField`     | Opcional                           |
| `message`       | `TextField`     | Obligatorio                        |
| `metadata`      | `JSONField`     | Objeto, sin datos sensibles        |
| `created_at`    | `DateTimeField` | Automático                         |

No sustituye a `operations_auditlog`.

```text
KoboProcessingEvent
→ Trazabilidad técnica

AuditLog
→ Trazabilidad funcional e institucional
```

---

### 6.8. `kobo_kobopastoralzoneprojectmapping`

Configura una zona pastoral canónica hacia un proyecto protegido. La restricción
`kobo_unique_active_zone_project_mapping` permite una sola fila activa por zona.
`deactivated_by_id`, `deactivated_at` y `deactivation_reason` conservan la
desactivación administrativa; los servicios bloquean cambios cuando la zona ya
tiene identidades.

---

### 6.9. `kobo_koboterritorialidentity`

Mantiene el código de núcleo normalizado único, la zona pastoral canónica, el
proyecto protegido, la submission fuente de Ficha 1 y su estado administrativo.
Routing crea o confirma esta fila; la importación no la duplica.

---

### 6.10. `kobo_koboterritorialidentityconflict`

Conserva la propuesta territorial entrante y el estado existente mediante FK
`PROTECT`. La resolución guarda choice estable, actor, fecha y motivo. La
unicidad parcial impide duplicar el mismo conflicto abierto.

---

### 6.11. `kobo_koboterritorialadministrationevent`

Registra actor, acción, tipo/id de entidad, estados JSON seguros, motivo y fecha
de cada mutación territorial. No contiene payload Kobo ni sustituye a
`operations_auditlog`.

---

### 6.12. `kobo_koboimportrecord`

Registra una sola importación completada por submission mediante `OneToOneField`
y conserva `handler_type`, `target_app_label`, `target_model`,
`target_object_id`, creador, fecha y metadata segura del resultado.

---

### 6.13. `kobo_koboterritorialprofile`

Representa una versión inmutable del perfil territorial aprobado de una Ficha 1.

| Columna                       | Tipo lógico            | Reglas                                      |
| ----------------------------- | ---------------------- | ------------------------------------------- |
| `territorial_identity_id`     | `ForeignKey`           | `PROTECT`, identidad canónica               |
| `project_id`                  | `ForeignKey`           | `PROTECT`, proyecto coherente                |
| `source_submission_id`        | `OneToOneField`        | `PROTECT`, una submission por perfil         |
| `parish`                      | `CharField`            | Obligatorio                                  |
| `community_sector`            | `CharField`            | Obligatorio                                  |
| `location`                    | `JSONField`            | Opcional, coordenadas canónicas validadas    |
| `parish_delegate`             | `CharField`            | Opcional                                     |
| `contact_phone`               | `CharField`            | Opcional, privado                            |
| `main_informant_role`         | `CharField`            | Opcional                                     |
| `communities_covered`         | `TextField`            | Texto libre normalizado                      |
| `estimated_households`        | `PositiveIntegerField` | Opcional, no negativo                        |
| `access_difficulties`         | `CharField`            | `yes`, `no` o `unknown`                      |
| `access_difficulties_notes`   | `TextField`            | Opcional                                     |
| `initial_priority_perception` | `CharField`            | Catálogo cerrado                             |
| `general_notes`               | `TextField`            | Opcional                                     |
| `created_by_id`               | `ForeignKey`           | `PROTECT`                                    |
| `created_at`                  | `DateTimeField`        | Automático                                   |
| `updated_at`                  | `DateTimeField`        | Automático                                   |

Relaciones:

```text
KoboTerritorialIdentity 1 ──< KoboTerritorialProfile
KoboSubmission 1 ── 1 KoboTerritorialProfile
```

No existe backfill automático: la migración crea el esquema y las importaciones
históricas requieren un proceso explícito posterior.

---

### 6.14. `kobo_koboprioritizedmicroproject`

Representa una propuesta priorizada histórica e inmutable materializada desde
una Ficha 10 aprobada.

| Columna                    | Tipo lógico     | Reglas                                      |
| -------------------------- | --------------- | ------------------------------------------- |
| `territorial_identity_id`  | `ForeignKey`    | `PROTECT`, identidad canónica               |
| `project_id`               | `ForeignKey`    | `PROTECT`, proyecto Núcleo Vital coherente  |
| `source_submission_id`     | `OneToOneField` | `PROTECT`, una submission por microproyecto |
| `name`                     | `CharField`     | Obligatorio; no deduplica                   |
| `component`                | `CharField`     | Catálogo cerrado                            |
| `problem_summary`          | `TextField`     | Texto libre obligatorio                     |
| `specific_objective`       | `TextField`     | Texto libre obligatorio                     |
| `beneficiary_group`        | `JSONField`     | Lista canónica no vacía                     |
| `main_activities`          | `TextField`     | Texto libre obligatorio                     |
| `estimated_cost_range`     | `CharField`     | Código de rango, no monto exacto            |
| `implementation_urgency`   | `CharField`     | Catálogo cerrado                            |
| `technical_viability`      | `CharField`     | Catálogo cerrado                            |
| `expected_result`          | `TextField`     | Texto libre obligatorio                     |
| `created_by_id`            | `ForeignKey`    | `PROTECT`                                   |
| `created_at`               | `DateTimeField` | Automático                                  |
| `updated_at`               | `DateTimeField` | Automático                                  |

```text
KoboTerritorialIdentity 1 ──< KoboPrioritizedMicroproject
Project 1 ──< KoboPrioritizedMicroproject
KoboSubmission 1 ── 1 KoboPrioritizedMicroproject
```

Los constraints protegen textos requeridos y los cuatro catálogos cerrados. La
lista de beneficiarios se valida en el modelo. No hay backfill ni inferencia
desde `raw_payload`, y esta tabla no representa presupuesto o ejecución financiera.

---

### 6.15. `kobo_koboprioritizationassessment`

Representa una evaluación histórica e inmutable materializada desde una Ficha
11 aprobada.

| Columna                              | Tipo lógico            | Reglas                                      |
| ------------------------------------ | ---------------------- | ------------------------------------------- |
| `territorial_identity_id`            | `ForeignKey`           | `PROTECT`, identidad canónica               |
| `project_id`                         | `ForeignKey`           | `PROTECT`, proyecto Núcleo Vital coherente  |
| `source_submission_id`               | `OneToOneField`        | `PROTECT`, una submission por evaluación    |
| diez campos `*_score`                | `PositiveSmallInteger` | Cada score entre 1 y 5                      |
| `priority_total_original`            | `PositiveSmallInteger` | Opcional, total recibido                    |
| `priority_total_calculated`          | `PositiveSmallInteger` | Suma exacta de los diez scores              |
| `suggested_semaphore_original`       | `CharField`            | Opcional, catálogo canónico                 |
| `suggested_semaphore_calculated`     | `CharField`            | Cálculo SIGEDON, catálogo canónico          |
| `final_semaphore`                    | `CharField`            | Decisión humana, catálogo canónico          |
| `final_priority`                     | `CharField`            | Decisión humana, catálogo cerrado           |
| `priority_summary`                   | `TextField`            | Resumen requerido                           |
| `calculation_warnings`               | `JSONField`            | Lista estructurada y validada               |
| `linked_microprojects_snapshot`      | `TextField`            | Texto libre; no crea relaciones por nombre  |
| `created_by_id`                      | `ForeignKey`           | `PROTECT`                                   |
| `created_at`                         | `DateTimeField`        | Automático                                  |
| `updated_at`                         | `DateTimeField`        | Automático                                  |

```text
KoboTerritorialIdentity 1 ──< KoboPrioritizationAssessment
Project 1 ──< KoboPrioritizationAssessment
KoboSubmission 1 ── 1 KoboPrioritizationAssessment
```

Los constraints protegen el rango de los scores, la igualdad entre la suma y el
total calculado, los catálogos de semáforo/prioridad y el resumen requerido. No
hay backfill, puntero redundante al registro vigente ni relaciones automáticas
con microproyectos.

---

### 6.16. `kobo_ficha01territorio`

Tabla heredada de la primera integración de la Ficha 1.

| Columna                | Tipo lógico    | Reglas         |
| ---------------------- | -------------- | -------------- |
| `id`                   | `BigAutoField` | Clave primaria |
| `pastoral_zone`        | `CharField`    | Obligatorio    |
| `parish_sector`        | `CharField`    | Obligatorio    |
| `survey_date`          | `DateField`    | Obligatorio    |
| `survey_responsible`   | `CharField`    | Obligatorio    |
| `parish_priest`        | `CharField`    | Obligatorio    |
| `contact_phone`        | `CharField`    | Obligatorio    |
| `official_parish_name` | `CharField`    | Obligatorio    |
| `church_advocation`    | `CharField`    | Opcional       |
| `civil_municipality`   | `CharField`    | Obligatorio    |
| `influence_radius`     | `CharField`    | Obligatorio    |
| `estimated_population` | `IntegerField` | Obligatorio    |
| `estimated_households` | `IntegerField` | Obligatorio    |
| `gps_coordinates`      | `CharField`    | Obligatorio    |
| `main_accessibility`   | `TextField`    | Obligatorio    |
| `territory_type`       | `JSONField`    | Obligatorio    |
| `kobo_uuid`            | `UUIDField`    | Único          |

Se conserva temporalmente como schema legado por compatibilidad histórica. No
tiene escritores activos conocidos, no es utilizado por el pipeline vigente y
no debe recibir nuevas escrituras sin una decisión arquitectónica explícita.

---

### 6.17. `kobo_ficha01coveredcommunity`

Representa las comunidades cubiertas dentro de la Ficha 1 heredada.

| Columna                          | Tipo lógico    | Reglas                                |
| -------------------------------- | -------------- | ------------------------------------- |
| `id`                             | `BigAutoField` | Clave primaria                        |
| `territory_form_id`              | `ForeignKey`   | Referencia a `kobo_ficha01territorio` |
| `community_sector`               | `CharField`    | Obligatorio                           |
| `estimated_community_population` | `IntegerField` | Opcional                              |
| `distance_time_to_church`        | `CharField`    | Obligatorio                           |
| `remarks`                        | `TextField`    | Opcional                              |

#### Relación

```text
Ficha01Territorio 1 ──< Ficha01CoveredCommunity
```

Esta relación permanece en el schema, pero no constituye la fuente de verdad
activa. Su eliminación futura, junto con la tabla territorial, requiere una
decisión de producto y una migración específica.

## 7. Tablas de autenticación y permisos

### 7.1. `auth_user`

Contiene los usuarios autenticados del sistema.

Incluye:

* credenciales;
* estado activo;
* indicador de superusuario;
* indicador de staff;
* nombre;
* correo electrónico;
* fechas de acceso.

SIGEDON utiliza el modelo estándar de usuario de Django.

### 7.2. `auth_group`

Contiene los grupos de permisos.

Grupos operativos sincronizados:

```text
Administrador SIGEDON
Operador de campo
Auditor externo
Comité de proyectos
```

### 7.3. `auth_permission`

Contiene los permisos generados por modelo y acción.

Ejemplos:

```text
operations.view_project
operations.add_projectupdate
operations.view_auditlog
kobo.change_kobosubmission
```

### 7.4. `auth_user_groups`

Tabla intermedia:

```text
auth_user ↔ auth_group
```

### 7.5. `auth_group_permissions`

Tabla intermedia:

```text
auth_group ↔ auth_permission
```

### 7.6. `auth_user_user_permissions`

Contiene permisos asignados directamente a usuarios.

Su uso debe reservarse para excepciones controladas.

## 8. Tablas internas de Django

### 8.1. `django_admin_log`

Conserva el historial del sitio administrativo de Django.

No sustituye a:

```text
operations_auditlog
```

### 8.2. `django_content_type`

Contiene el catálogo interno de modelos instalados.

Django lo utiliza para:

* permisos;
* administración;
* relaciones genéricas;
* identificación de modelos.

### 8.3. `django_migrations`

Registra las migraciones aplicadas.

Nunca debe editarse manualmente.

### 8.4. `django_session`

Almacena las sesiones de usuarios autenticados.

Su retención depende de la configuración de sesiones y de las tareas de limpieza definidas.

## 9. Restricciones de integridad

Restricciones explícitas vigentes:

| Tabla                       | Restricción                             |
| --------------------------- | --------------------------------------- |
| `operations_project`        | Presupuesto mayor o igual a cero        |
| `operations_donation`       | Monto mayor que cero                    |
| `operations_fundallocation` | Monto mayor que cero                    |
| `operations_expense`        | Monto mayor que cero                    |
| `operations_projectupdate`  | Progreso entre 0 y 100                  |
| `kobo_koboformdefinition`   | Formulario y versión únicos             |
| `kobo_koboasset`            | Rol de formulario válido                |
| `kobo_koboprojectbinding`   | Routing y campos coherentes             |
| `kobo_koboprojectbinding`   | Un binding directo por activo           |
| `kobo_koboprojectbinding`   | Ruta por campo no duplicada             |
| `kobo_kobosubmission`       | Submission externa única por formulario |
| `kobo_koboimportrecord`     | Una fila por submission y target positivo |
| `kobo_koboterritorialprofile` | Una fila por submission, catálogos y hogares válidos |

Además, Django y PostgreSQL crean índices automáticos para:

* claves primarias;
* claves únicas;
* claves foráneas;
* relaciones uno a uno.

Actualmente no existen índices personalizados adicionales declarados en:

```python
Meta.indexes
```

## 10. Invariantes no representadas únicamente mediante constraints

Algunas reglas requieren servicios transaccionales y no pueden expresarse completamente mediante un `CHECK` de una sola fila.

Entre ellas:

* las asignaciones no pueden superar el saldo disponible de una donación;
* los gastos no pueden superar el saldo disponible de una asignación;
* un avance solo puede crearse para un proyecto activo;
* un avance publicado es inmutable;
* solo un avance publicado puede revisarse;
* los códigos operativos se reservan de manera secuencial;
* la auditoría es append-only;
* los adjuntos privados requieren autorización;
* las entidades anuladas no participan en métricas.

Estas reglas se implementan mediante:

```text
services.py
transaction.atomic()
select_for_update()
validaciones de modelo
vistas autorizadas
pruebas automatizadas
```

## 11. Datos derivados

Los siguientes valores no se almacenan como columnas editables:

* saldo disponible de una donación;
* monto asignado de una donación;
* progreso de asignación;
* saldo disponible de una asignación;
* monto ejecutado de una asignación;
* progreso de ejecución;
* monto financiado de un proyecto;
* monto ejecutado de un proyecto.

Se calculan a partir de los registros persistidos que no se encuentran anulados.

## 12. Datos sensibles

Deben tratarse como información privada:

* credenciales;
* sesiones;
* datos personales de usuarios;
* documentos legales;
* soportes financieros;
* adjuntos de avances;
* payloads crudos de Kobo;
* URLs remotas de Kobo;
* mensajes técnicos;
* firmas;
* tokens;
* secretos;
* copias de seguridad de la base de datos.

El portal público no debe consultar ni exponer estos datos sin pasar por selectores explícitamente sanitizados.

## 13. Política de migraciones

Las modificaciones del esquema deben realizarse mediante migraciones de Django.

### Reglas

* No editar migraciones ya aplicadas.
* Revisar cada migración antes de desplegarla.
* Conservar los datos históricos.
* Crear un respaldo antes de migraciones en producción.
* Verificar cambios no esperados.

### Comprobar cambios pendientes

```bash
python manage.py makemigrations --check --dry-run
```

### Consultar el estado

```bash
python manage.py showmigrations
```

### Revisar el plan

```bash
python manage.py migrate --plan
```

### Aplicar cambios

```bash
python manage.py migrate
```

## 14. Inventario físico de tablas

```text
auth_group
auth_group_permissions
auth_permission
auth_user
auth_user_groups
auth_user_user_permissions
django_admin_log
django_content_type
django_migrations
django_session
kobo_ficha01coveredcommunity
kobo_ficha01territorio
kobo_koboasset
kobo_koboattachment
kobo_kobodiscoveredasset
kobo_koboformdefinition
kobo_koboimportrecord
kobo_kobopastoralzoneprojectmapping
kobo_koboprocessingevent
kobo_koboprojectbinding
kobo_kobosubmission
kobo_koboterritorialidentity
kobo_koboterritorialidentityconflict
kobo_koboterritorialprofile
operations_auditlog
operations_donation
operations_expense
operations_fundallocation
operations_institution
operations_operationalcodesequence
operations_project
operations_projectdocument
operations_projectupdate
operations_projectupdateattachment
operations_projectupdatereview
operations_projectupdatereviewdecision
operations_supportingdocument
```

## 15. Verificación del esquema

Antes de considerar vigente este documento, debe comprobarse:

```bash
python manage.py showmigrations
python manage.py makemigrations --check --dry-run
python manage.py check
```

Para validar el esquema físico de producción, la revisión debe realizarse sobre PostgreSQL.

## 16. Criterio de actualización

Este documento debe actualizarse cuando ocurra alguno de los siguientes cambios:

* incorporación o eliminación de una tabla;
* modificación de campos;
* modificación de claves foráneas;
* incorporación de constraints;
* incorporación de índices;
* cambio de estados persistidos;
* modificación de relaciones;
* cambio de privacidad de archivos;
* introducción de nuevos datos derivados;
* eliminación o consolidación de modelos heredados.

La documentación no sustituye a las migraciones ni al esquema realmente aplicado.

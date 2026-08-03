# PostgreSQL — roles y verificación operativa

Plantilla de endurecimiento del rol runtime y contrato de verificación.

## Archivos

* `harden_runtime_role.sql` — plantilla comentada para crear/ajustar
  `sigedon_owner` (migraciones) y `sigedon_app` (runtime), y limitar
  privilegios de `sigedon_app` sobre `operations_auditlog` a `SELECT`/`INSERT`.

## Orden operativo

1. Aplicar migraciones con el rol propietario (`sigedon_owner`).
2. Ejecutar la plantilla SQL conectado como propietario (nunca como runtime).
3. Cambiar las credenciales de la aplicación al rol runtime (`sigedon_app`).
4. Ejecutar:

```bash
python manage.py verify_postgres_security
```

## `verify_postgres_security`

* **Requiere PostgreSQL.** Contra SQLite u otro motor sale con código distinto
  de 0 (`PostgreSQL security verification requires a PostgreSQL database.`).
* Debe ejecutarse con las **credenciales finales del rol runtime**. Un éxito
  bajo el rol propietario/superusuario **no** valida la separación de roles.
* Verifica ambas familias append-only: `AuditLog` y `ExpenseRequestEvent`
  (función, trigger habilitado para escrituras locales/`O`, y sondas UPDATE/DELETE
  con rollback).
* Comprueba constraints financieros/criticos (USD, montos, reservas, unicidad
  de códigos operativos).
* Las sondas no dejan filas persistentes y no reparan nada.
* El fallo bloquea la aceptación de tráfico o de un entorno restaurado.
* Remediación de roles/grants: solo el propietario autorizado de la base.
* `reconcile_operational_code_sequences` y `verify_restored_data` son
  complementarios; no sustituyen esta verificación.

`deploy/preflight.sh` permanece de solo lectura y **no** invoca este comando;
la secuencia de release lo ejecuta explícitamente tras migraciones y cambio
al rol runtime.

## Alcance de grants

La plantilla actual endurece privilegios SQL de mutación sobre
`operations_auditlog`. `operations_expenserequestevent` queda protegida por el
trigger de la migración `0029`; el endurecimiento adicional de grants sobre
esa tabla es responsabilidad de infraestructura si se decide alinear el SQL
con el mismo contrato append-only.

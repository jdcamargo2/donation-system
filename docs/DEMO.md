# Demo SIGEDON

## Preparacion local

```bash
./venv/bin/python manage.py migrate
./venv/bin/python manage.py sync_sigedon_roles
./venv/bin/python manage.py seed_sigedon_demo
./venv/bin/python manage.py runserver
```

## Usuario demo

Por defecto el comando crea:

- usuario: `sigedon_demo`
- password: `sigedon-demo-12345`

Puede configurarse con variables de entorno:

```bash
SIGEDON_DEMO_USERNAME=demo \
SIGEDON_DEMO_EMAIL=demo@example.local \
SIGEDON_DEMO_PASSWORD='cambiar-en-local' \
./venv/bin/python manage.py seed_sigedon_demo
```

El seed tambien crea usuarios por rol:

- `admin_sigedon`: grupo Administrador SIGEDON
- `campo_sigedon`: grupo Operador de campo
- `auditor_sigedon`: grupo Auditor externo

Las contrasenas por defecto son solo para desarrollo local y pueden cambiarse con:

- `SIGEDON_DEMO_ADMIN_PASSWORD`
- `SIGEDON_DEMO_FIELD_PASSWORD`
- `SIGEDON_DEMO_AUDITOR_PASSWORD`

## Rutas principales

- Panel interno: `/`
- Instituciones: `/institutions/`
- Proyectos: `/projects/`
- Donaciones: `/donations/`
- Asignaciones: `/allocations/`
- Gastos: `/expenses/`
- Auditoria: `/audit/`
- Portal publico: `/transparency/`
- Proyectos publicos: `/transparency/projects/`
- Avances publicos: `/transparency/updates/`

## Datos creados

El seed crea instituciones, proyectos, una donacion, una asignacion, un gasto validado con documento soporte, avances en distintos estados y registros de auditoria.

El comando es idempotente: puede ejecutarse varias veces sin duplicar caoticamente las entidades demo principales.

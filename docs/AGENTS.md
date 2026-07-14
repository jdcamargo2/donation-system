# `docs/AGENTS.md`

```md
# Instrucciones de trabajo en SIGEDON

## 1. Leer antes de modificar

Orden obligatorio:

1. `README.md`
2. `docs/MVP_SCOPE.md`
3. `docs/ARCHITECTURE.md`
4. `docs/DOMAIN_MODEL.md`
5. `docs/FLOWS.md`
6. `docs/ROLES_AND_PERMISSIONS.md`
7. documentación específica del módulo.

## 2. Fuente de verdad

```text
código
→ migraciones
→ tests
→ documentación vigente
→ auditorías históricas
No implementar estados o flujos basándose únicamente en documentos históricos.
3. Trabajo quirúrgico
Cada tarea debe:
resolver un objetivo concreto;
tocar el mínimo de archivos;
evitar refactors no solicitados;
preservar compatibilidad;
incluir pruebas;
mantener migraciones intencionales.
4. PRE y POST
Las funciones de dominio relevantes deben declarar:
# PRE: condición que debe cumplirse antes.
# POST: resultado garantizado al finalizar.
Las precondiciones deben verificarse, no solo comentarse.
5. Reglas de dominio
No romper:
códigos inmutables;
saldos no negativos;
auditoría append-only;
avances publicados inmutables;
archivos privados;
separación entre revisión y avance;
permisos por rol;
exclusión de anulados;
USD como moneda operativa;
PostgreSQL en producción.
6. Mutaciones financieras
Usar:
transaction.atomic()
select_for_update()
No usar signals como protección financiera principal.
7. Auditoría
Toda acción crítica debe:
identificar actor;
registrar entidad;
registrar resumen;
ejecutarse atómicamente con la mutación.
Nunca editar ni eliminar AuditLog.
8. Permisos
No confiar únicamente en el template.
Las vistas y servicios deben impedir acciones no autorizadas.
No asignar permisos técnicos Kobo a roles operativos sin una decisión explícita.
9. Migraciones
No editar migraciones aplicadas salvo corrección extraordinaria aprobada.
Crear migraciones nuevas para cambios de esquema.
Probar migración hacia delante.
Conservar datos históricos.
10. Pruebas obligatorias
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test
git diff --check
Para cambios pequeños, ejecutar primero suites focalizadas.
11. Documentación
Actualizar documentación cuando cambie:
un rol;
un estado;
una transición;
una variable;
un comando;
una ruta;
una medida de seguridad;
una entidad;
una integración.
12. Finalización
Una tarea está terminada cuando:
el comportamiento es correcto;
existen pruebas;
la suite relevante pasa;
no hay migraciones inesperadas;
no hay secretos;
el diff es limpio;
la documentación coincide;
el resumen final explica archivos y validaciones.
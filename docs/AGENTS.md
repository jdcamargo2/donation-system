# Instrucciones de trabajo en SIGEDON

Documento para agentes y desarrollo. No es documentación de producto.

## 1. Leer antes de modificar

Orden obligatorio:

1. `README.md`
2. `docs/ARCHITECTURE.md`
3. `docs/DOMAIN_MODEL.md`
4. `docs/FLOWS.md`
5. `docs/ROLES_AND_PERMISSIONS.md`
6. documentación específica del módulo (`docs/KOBO.md`, `docs/SECURITY.md`, etc.)

Límites de alcance: `docs/MVP_SCOPE.md`.

## 2. Fuente de verdad

```text
código
→ migraciones
→ tests
→ documentación vigente
```

No implementar estados o flujos basándose únicamente en documentos históricos.

## 3. Trabajo quirúrgico

Cada tarea debe:

- resolver un objetivo concreto;
- tocar el mínimo de archivos;
- evitar refactors no solicitados;
- preservar compatibilidad;
- incluir pruebas;
- mantener migraciones intencionales.

## 4. PRE y POST

Las funciones de dominio relevantes deben declarar:

```text
# PRE: condición que debe cumplirse antes.
# POST: resultado garantizado al finalizar.
```

Las precondiciones deben verificarse, no solo comentarse.

## 5. Reglas de dominio

No romper:

- códigos inmutables;
- saldos no negativos;
- auditoría append-only;
- avances publicados inmutables;
- archivos privados;
- separación entre revisión y avance;
- permisos por rol;
- exclusión de anulados;
- USD como moneda operativa;
- PostgreSQL como backend principal (producción y demo local recomendada).

## 6. Mutaciones financieras

Usar:

- `transaction.atomic()`
- `select_for_update()`

No usar signals como protección financiera principal.

## 7. Auditoría

Toda acción crítica debe identificar actor, registrar entidad y resumen, y
ejecutarse atómicamente con la mutación.

Nunca editar ni eliminar `AuditLog`.

## 8. Permisos

No confiar únicamente en el template. Las vistas y servicios deben impedir
acciones no autorizadas.

No asignar permisos técnicos Kobo a roles operativos sin una decisión explícita.

## 9. Migraciones

No editar migraciones aplicadas salvo corrección extraordinaria aprobada.
Crear migraciones nuevas para cambios de esquema. Probar migración hacia
delante. Conservar datos históricos.

Estado de esquema de referencia de esta edición: `operations` hasta `0033`,
Kobo hasta `0018`. `makemigrations --check` debe permanecer limpio.

## 10. Pruebas obligatorias

```bash
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test --noinput
git diff --check
```

Iteración local: `python manage.py test --keepdb --noinput` acelera el ciclo,
pero no sustituye un checkpoint limpio cuando hay que validar reconstrucción.

Para cambios pequeños, ejecutar primero suites focalizadas. Detalle:
[TESTING.md](TESTING.md).

Checkpoint local verificado de esta edición: 2,384 tests en PostgreSQL, EXIT 0.
No afirmar que GitHub Actions remoto esté verde sin evidencia de esa corrida.

## 11. Documentación

Actualizar documentación cuando cambie un rol, estado, transición, variable,
comando, ruta, medida de seguridad, entidad o integración.

`.env.example` es arranque local/demo, no el catálogo completo de variables.
El resto vive en `docs/DEPLOYMENT.md`, `docs/KOBO.md`,
`docs/runbooks/RENDER_ENVIRONMENT.md`, `docs/runbooks/CLOUDFLARE_R2.md` y
`deploy/backups/README.md`.

## 12. Finalización

Una tarea está terminada cuando el comportamiento es correcto, existen
pruebas, la suite relevante pasa, no hay migraciones inesperadas, no hay
secretos, el diff es limpio, la documentación coincide y el resumen final
explica archivos y validaciones.

Kobo está implementado. Con `KOBO_ENABLED=False` (default de esta edición)
el hub es showcase sintético: rutas remotas 404 y comandos remotos
`CommandError`. Eso no significa que la integración no exista.

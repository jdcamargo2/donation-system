# Alcance y límites de SIGEDON

Documento breve de límites. El comportamiento del sistema está en
[README](../README.md), [DOMAIN_MODEL.md](DOMAIN_MODEL.md) y [FLOWS.md](FLOWS.md).
Roles: [ROLES_AND_PERMISSIONS.md](ROLES_AND_PERMISSIONS.md).

## Incluido

Cadena operativa:

```text
Institution → Donation → Fund Allocation → Project
→ Expense Request → Approval → Expense → Evidence
→ Project Update → Audit → Public Transparency
```

También: RBAC de cuatro roles, archivos privados, portal de transparencia,
tooling de backup/despliegue, e integración KoboToolbox **implementada**
(desconectada por defecto en esta edición).

## Fuera de alcance

* inteligencia artificial, chat, cronogramas avanzados;
* gestión completa de beneficiarios;
* donaciones en especie como flujo financiero completo;
* distribución física, firma digital, pagos electrónicos;
* API pública sofisticada, autenticación externa (OAuth/SSO);
* mapas territoriales generales;
* aprobación multinivel de gastos (el Comité es un único rol de decisión);
* hash encadenado de auditoría / almacenamiento WORM;
* importación directa de las fichas Kobo 2 a 9;
* infraestructura privada (Render, R2, scheduler de backups) provisionada.

## Control de alcance

Toda nueva funcionalidad se clasifica como `MVP-BLOCKER`, `MVP-REQUIRED` o
`POST-MVP`. No se incorpora una capacidad solo porque resulte conveniente.

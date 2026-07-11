### 2026-07-08 — Use Django and Bootstrap for the MVP

Decision:
Build the SIGEDON MVP with Django and Bootstrap.

Reason:
The MVP is focused on institutional data entry, financial traceability, forms, validation, basic audit records, document support, and simple reports. Django provides a pragmatic backend with models, forms, authentication, permissions, admin tooling, and server-rendered templates. Bootstrap provides a clean responsive UI without requiring a complex frontend stack.

Alternatives considered:
- Django + React
- FastAPI + React
- Laravel + Bootstrap
- Node/Express + frontend framework

Consequences:
- The MVP will be a server-rendered web application.
- Django Templates will be used for the main interface.
- Bootstrap will be used for layout and UI components.
- A public API is out of scope for this MVP.
- A SPA frontend is out of scope for this MVP.
- The architecture should prioritize clarity, validation, traceability, and maintainability.

### 2026-07-08 — Institution stores country but not city

Decision:
Remove city from Institution. Keep country as the institutional location reference.

Reason:
An institution can operate across multiple cities or execute projects in different territories. A single city field would create a misleading constraint in the MVP.

Consequences:
- Institution stores country only.
- Project keeps its location field.
- City/country dependent dropdown is out of scope.
- Future territorial tracking should be modeled through projects, offices, branches, or operational areas if needed.

### 2026-07-10 — USD is the single operating currency

Decision:
Use USD as the only currency accepted by SIGEDON operational forms, services, admin workflows, dashboards, and public financial summaries.

Reason:
The MVP does not provide exchange rates, conversion dates, revaluation, or multi-currency accounting. Adding values from different currencies would produce misleading balances and reports.

Consequences:
- Donation and expense records created through supported workflows always store `USD`.
- Fund allocations can only use donations recorded in USD.
- Financial services reject any explicit currency other than USD.
- Dashboard and public metrics exclude legacy non-USD records instead of converting them.
- Historical model choices remain temporarily for schema compatibility, so this decision does not require a migration.
- Currency conversion and multi-currency accounting remain out of scope.

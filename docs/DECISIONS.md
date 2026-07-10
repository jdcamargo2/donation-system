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
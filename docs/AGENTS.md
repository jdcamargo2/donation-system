# SIGEDON Project Instructions

## Purpose

These instructions guide Codex when working on the SIGEDON MVP repository.

SIGEDON is the **Sistema Integral de Gestión, Seguimiento y Trazabilidad de Donaciones**. The MVP focuses on financial traceability for donations managed by an Arquidiócesis.

The goal is not to build the complete future platform. The goal is to build a first functional, verifiable, maintainable version centered on institutions, donations, projects, fund allocations, expenses, supporting documents, basic audit records, and basic financial tracking.

## Read first

Before making broad changes, read these files when they exist:

1. `PROJECT_BRIEF.md`
2. `docs/alcance_mvp.md`
3. `docs/domain.md`
4. `docs/invariants.md`
5. `TODO.md`
6. `DECISIONS.md`
7. `README.md`

If these files do not exist yet, do not invent their contents. Ask for them or propose creating minimal versions.

## MVP scope

The MVP must support the basic financial traceability chain:

```text
Institution / Donor
        ↓
Donation
        ↓
Project
        ↓
Fund allocation
        ↓
Expense
        ↓
Supporting document
        ↓
Internal audit
        ↓
Basic report
```

Included MVP modules:

1. Institutions
2. Donations
3. Projects
4. Fund allocations
5. Financial execution / expenses
6. Basic audit
7. Basic financial tracking views or reports

Out of scope for the MVP unless explicitly requested:

- Complete in-kind donations module
- Detailed beneficiaries
- Physical distribution tracking
- Public transparency portal
- Territorial maps
- Advanced impact indicators
- WhatsApp integration
- QR codes
- Digital signature
- Data intelligence
- Public API
- Advanced report automation

Do not expand the MVP scope without explicit approval.

## Core domain concepts

### Institution

Represents an organization involved in the donation process.

Possible roles include:

- Donor
- Receiver
- Executor
- Ally
- Supervisor

### Donation

Represents money received or committed by a donor.

A donation is the financial entry point of the system.

### Project

Represents a program, plan, line of action, or intervention that can receive assigned funds.

Examples:

- Food for affected families
- Health support
- Support for affected communities
- Reconstruction of pastoral spaces
- Logistics assistance

### Fund allocation

Represents the distribution of donation funds into a project, activity, category, or territory.

Important rule:

> An allocation is not an expense.

An allocation defines how money is reserved or distributed. An expense records how money is actually executed.

### Expense

Represents real financial execution from assigned funds.

Expenses should include amount, reason, recipient or provider, payment method, supporting document, status, and validation information.

### Supporting document

Represents evidence for a donation, allocation, expense, or validation.

Examples:

- Invoice
- Receipt
- Contract
- Bank transfer proof
- Institutional letter
- Legal document

### Audit record

Represents a relevant action performed in the system.

Audit records should make critical changes traceable.

## Business rules

Name important rules clearly in code, tests, and documentation.

Mandatory MVP business rules:

1. **Allocation Is Not Expense**
   - A fund allocation reserves or distributes money.
   - An expense records actual execution of money.

2. **Donation Balance Rule**
   - The total allocated amount of a donation must not exceed the received amount of that donation.

3. **Allocation Balance Rule**
   - The total executed amount of an allocation must not exceed the amount assigned to that allocation.

4. **Expense Requires Allocation Rule**
   - Every expense must belong to a valid fund allocation.

5. **Validated Expense Support Rule**
   - Every validated expense should have supporting documentation.

6. **Critical Action Audit Rule**
   - Creating, updating, validating, rejecting, annulling, assigning, or executing financial records should generate an audit record when relevant.

7. **Explicit State Rule**
   - Donations, projects, allocations, and expenses must use explicit states instead of implicit assumptions.

8. **No Negative Balance Rule**
   - Donation balances and allocation balances must never become negative.

## Suggested states

Use explicit states. Adjust names only if the project already has a better convention.

### Donation states

- `registered`
- `committed`
- `received`
- `partially_allocated`
- `fully_allocated`
- `closed`
- `annulled`

### Project states

- `planned`
- `active`
- `suspended`
- `closed`
- `annulled`

### Allocation states

- `created`
- `active`
- `partially_executed`
- `fully_executed`
- `closed`
- `annulled`

### Expense states

- `registered`
- `in_review`
- `validated`
- `rejected`
- `annulled`

### Audit action types

- `created`
- `updated`
- `validated`
- `rejected`
- `annulled`
- `assigned`
- `executed`
- `closed`

## System invariants

Protect these invariants through validation, types, tests, assertions, or database constraints when appropriate.

1. A donation balance must never be negative.
2. An allocation balance must never be negative.
3. The sum of allocations for a donation must not exceed the donation amount received.
4. The sum of expenses for an allocation must not exceed the allocation amount.
5. A validated expense should not exist without supporting documentation.
6. A critical financial action should leave an audit trail.
7. An expense must not exist without a valid allocation.
8. An allocation must not exist without a valid donation and project.
9. A project should not report executed funds greater than assigned funds.
10. An annulled record must not behave like an active record.

## Engineering workflow

Follow this order:

1. Understand the requested change.
2. Read the relevant project documents.
3. Inspect only the necessary files, but inspect enough to avoid guessing.
4. For complex, risky, architectural, database, security, performance, concurrency, or multi-file tasks, write a short plan before editing.
5. Make small, reviewable changes.
6. Preserve existing behavior unless the task explicitly asks for a change.
7. Run the most relevant safe verification command when possible.
8. Summarize changed files, verification, and remaining risks.

For simple tasks, act directly.

## Function contracts

Important functions should use explicit contracts when they improve clarity.

Use this exact format:

```text
PRE: what must be true before calling the function.
POST: what will be true after successful completion.
```

Rules:

1. Always use `PRE:` and `POST:` in uppercase with a colon.
2. Use the comment style of the programming language.
3. Do not add contracts to trivial getters, setters, constructors with obvious assignments, or obvious one-line helpers.
4. `PRE:` should describe domain assumptions, valid states, input constraints, permissions, invariants, ownership expectations, or required prior validation.
5. `POST:` should describe guarantees, state changes, returned value meaning, persistence effects, side effects, or relevant error behavior.
6. Do not repeat the function signature, parameter names, or obvious type information unless it adds domain meaning.
7. If a function can fail, document expected failure conditions when relevant.
8. If a precondition is critical, enforce it with validation, types, assertions, database constraints, or explicit checks when appropriate.

Examples:

```java
// PRE: the donation is received and has enough available balance.
// POST: creates an allocation and decreases the donation available balance.
public Allocation assignFunds(Donation donation, Project project, BigDecimal amount) {
    ...
}
```

```python
# PRE: the allocation is active and the expense amount does not exceed its available balance.
# POST: records the expense, updates the allocation balance, and creates an audit entry.
def register_expense(allocation_id: str, payload: ExpensePayload) -> ExpenseId:
    ...
```

```rust
// PRE: donation_amount and allocated_amounts are expressed in the same currency.
// POST: returns the remaining donation balance without mutating stored state.
fn calculate_available_balance(donation_amount: Money, allocated_amounts: &[Money]) -> Money {
    ...
}
```

## Code quality rules

- Every important function must have a clear reason to exist.
- Each function should do one clear thing.
- Names should reduce the need for comments.
- Comments should explain intention, domain decisions, tradeoffs, or non-obvious constraints.
- Comments should not repeat syntax.
- Keep the normal case easy to read.
- Code should read like an explanation of the problem.
- Every data structure must have a reason to exist.
- Every magic number must have a name.
- Avoid complexity “just in case”.
- Do not create abstractions before they are needed.
- Duplicate first, abstract later.
- Write code that can be read six months later.
- Code should be easy to delete without drama.

## Architecture rules

- The domain drives the design, not the framework.
- Follow the existing architecture unless the task explicitly asks for a redesign.
- Keep modules focused and cohesive.
- Modules should hide internal details.
- Public interfaces should be small and intentional.
- Dependencies should enter from the outside when practical.
- Do not mix decision logic with execution logic when separation improves clarity.
- Business rules should not be buried inside controllers, UI handlers, or framework glue.
- Prefer explicit domain services or use cases for important financial operations.
- Do not add new layers, services, managers, helpers, or abstractions unless they solve a real current problem.

## Data and model rules

- Protect data from the model/schema layer when relevant.
- Use explicit constraints for required fields, valid states, and financial amounts.
- Monetary amounts must use a safe numeric representation. Do not use floating-point types for money unless the language/framework gives a safe money abstraction.
- Store currency explicitly when amounts can use more than one currency.
- Do not silently mix currencies.
- Prefer immutable or append-only records for audit-sensitive data when practical.
- Treat audit records as evidence, not decorative logs.

## Error handling rules

- Fail early when invalid input or invalid state is detected.
- Important errors must explain what failed and why it matters.
- Prefer explicit validation, types, assertions, database constraints, or tests over assumptions.
- No important method should depend on faith.
- Do not swallow errors silently.
- Do not convert domain errors into vague generic messages when a clear domain message is possible.

## Testing rules

Prioritize tests for business rules and invariants.

Important tests should cover:

- Allocation does not exceed donation balance.
- Expense does not exceed allocation balance.
- Expense requires allocation.
- Validated expense requires supporting documentation when the rule applies.
- Critical financial action creates audit record when required.
- Annulled or closed records cannot be used as active records.
- Currency mismatches are rejected or handled explicitly.

Code should be testable without heroic setup.

## Dependencies

- Do not add dependencies unless clearly necessary.
- Explain why a dependency is needed before adding it.
- Prefer existing project dependencies or standard library features when reasonable.
- Do not add a framework feature just because it exists.
- The domain should remain understandable without reading framework internals.

## Safety rules

- Do not touch `.env`, secrets, credentials, tokens, production config, deployment config, or CI/CD secrets unless explicitly requested.
- Do not delete files without confirmation.
- Do not reset databases without confirmation.
- Do not run migrations without confirmation.
- Do not force push or rewrite Git history without confirmation.
- Do not run destructive commands without confirmation.

## Verification

Before finishing, run the most relevant safe check when possible.

Fill these commands when the project stack is chosen:

- Install dependencies: TODO
- Run tests: TODO
- Run lint: TODO
- Run typecheck: TODO
- Build: TODO
- Start locally: TODO

If verification cannot be run, explain why and state exactly what should be tested manually.

## Done means

A task is done when:

- The change respects the MVP scope.
- The traceability chain remains intact.
- Business rules and invariants are not weakened.
- Important functions use `PRE:` and `POST:` when relevant.
- The change is limited to the necessary files.
- The normal case reads cleanly.
- Relevant checks were run or clearly reported as not run.
- Remaining risks are stated clearly.

## Final response format

Keep the final answer brief.

Include:

1. Changed files
2. What changed
3. Verification performed
4. Remaining risks or manual checks



## Technology stack

The SIGEDON MVP must be built with:

- Django as the backend framework.
- Django Templates for server-rendered pages.
- Bootstrap for layout, styling, and responsive UI.
- SQLite for local development.
- PostgreSQL as the recommended production database.

Do not introduce a frontend SPA framework unless explicitly requested.

Out of scope for the MVP:

- React
- Vue
- Angular
- Svelte
- Public API
- Mobile app
- Advanced frontend build pipeline

## Django rules

- Follow Django conventions unless there is a strong reason not to.
- Keep views thin when practical.
- Put business rules in explicit services, model methods, validators, or domain modules.
- Use Django forms or model forms for user input validation.
- Use Django authentication and permissions for access control.
- Use Django admin only as support tooling, not as the main user interface unless explicitly requested.
- Keep migrations intentional and reviewable.
- Do not run destructive migrations without confirmation.

## Bootstrap rules

- Use Bootstrap components before creating custom CSS.
- Keep the interface institutional, clean, readable, and responsive.
- Prefer simple templates over complex frontend behavior.
- Avoid unnecessary JavaScript.
- Use custom CSS only when Bootstrap is not enough.
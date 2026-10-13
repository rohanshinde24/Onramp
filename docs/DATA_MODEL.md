# Data Model

Two PostgreSQL databases (ADR-002). Each has its own Alembic chain under
`backend/migrations/`. Schemas change only through migrations; CI checks that models and
migrations agree and that every chain downgrades to base and back.

## Conventions

* IDs are opaque strings with a type prefix (`case_...`, `aud_...`). Fixture and scenario data
  may use readable IDs such as `w_492`.
* Money is integer cents plus an ISO currency code. No floats.
* Timestamps are `timestamptz`.
* Enumerated values are `CHECK` constraints, so an unknown state cannot be stored.
* Every tenant-owned table has a `(tenant_id, id)` unique key, and references between
  tenant-owned rows are composite foreign keys on `(tenant_id, <ref>_id)`. A row cannot point
  at another tenant's row, whatever the application does.
* Tenant IDs are shared between the two databases by convention: a tenant exists in both with
  the same ID. There is no cross-database foreign key; Onramp passes the tenant from trusted
  context on every marketplace call.

## onramp database

| Table | Purpose | Key invariants |
|---|---|---|
| `tenant` | Marketplace operator using Onramp | `payout_approval_limit_cents > 0` |
| `app_user` | Operators, leads, admins, service identities | role in the known set; email unique per tenant |
| `audit_event` | Append-only history of every consequential step; also the case timeline | trigger rejects UPDATE and DELETE; user actors must have an ID |
| `support_case` | One worker problem under investigation | known type and state; subject type matches case type; one open case per (tenant, type, subject); intake key unique per tenant; optimistic `version` |
| `proposal` | A proposed resolution and the action it requires | resolution and action from the closed catalogue; risk class present exactly when an action is; one pending proposal per case; `content_hash` over everything an approver approves |
| `approval` | A person's decision on a proposal's action | one decision per proposal; approver is a user of the same tenant |
| `executed_action` | Durable action intent and final result | one per proposal; idempotency key unique per tenant; status is executing, succeeded, failed or unverified; tenant-safe references to case, proposal and approval |
| `check_run` | One deterministic check result | case version, applicable policy, evidence, citations and outcome are stored |
| `investigation_run` | One bounded model investigation | full trajectory, budgets, conclusion, status and resulting proposal are stored |
| `outbox_event` | Facts waiting for Kafka publication | stable event ID and per-aggregate version; committed with the business change |
| `processed_event` | Consumer deduplication | unique per consumer group and event ID |

Server-derived fields on `support_case`: `worker_id` (from the subject record) and
`incident_at` with `incident_basis` (submission time, review time, or report time). The
reporter's description is stored as untrusted text.

## marketplace database

| Table | Purpose | Key invariants |
|---|---|---|
| `tenant` | Marketplace tenant | n/a |
| `worker` | Worker account | status in `pending_onboarding`, `active`, `suspended`, `denied`; names are not unique (namesakes exist) |
| `verification_record` | Identity, tax form, skills checks | kind and status in known sets |
| `document` | Worker upload | `content_text` is untrusted input |
| `document_request` | Request sent to a worker for a document | n/a |
| `task` | Unit of paid work | `payout_cents > 0` |
| `assignment` | Worker assigned to a task | one per (task, worker) |
| `submission` | Work submitted for an assignment | one per assignment |
| `quality_review` | Review of a submission | result `pass` or `fail`; score in [0, 1]; `overturned_at` set when overturned |
| `payment` | Task payout or adjustment | a task payout has a submission, an adjustment does not; amount > 0 |
| `payment_event` | Authorization and settlement history | event type in known set |
| `write_request` | Idempotency record for every marketplace write | unique `(tenant_id, idempotency_key)` |

## Access rules

* Only `onramp.marketplace` imports marketplace tables (import-linter contract).
* Onramp reads through `MarketplaceReader`, which returns immutable records with evidence
  references of the form `<kind>:<id>`.
* Onramp writes only through `MarketplaceWriter`, held only by the Action Gateway. Every write
  takes an idempotency key; a repeat returns the original result, a conflicting repeat is
  rejected.

# ADR-001: PostgreSQL as system of record

## Context

Onramp needs one authoritative store for cases, evidence links, proposals, approvals, executed
actions, audit history and the outbox. Approval, idempotency and outbox insertion must commit
atomically with the state change they describe, and many invariants (tenant ownership, one
approval per proposal, legal state values, unique idempotency keys) should be enforced by the
database rather than trusted to application code.

## Decision

PostgreSQL 16 is the system of record. Access is through SQLAlchemy 2.0 (typed ORM with
explicit sessions) and schema changes go through Alembic migrations only. Driver is psycopg 3.

Invariants are constraints wherever practical: foreign keys including tenant in composite keys,
`CHECK` constraints on state enums, partial unique indexes (for example one open case per
subject and type), and unique idempotency keys.

## Alternatives

* **Event sourcing as the system of record.** Gives a full history for free, but makes every
  read a projection and every invariant an eventual one. The audit table gives the history I
  need without that cost.
* **A document store.** Loses the transactional guarantees the approval and outbox designs
  depend on.
* **Raw SQL without an ORM.** Viable, but SQLAlchemy Core and ORM together give typed models
  and still allow explicit SQL for the outbox claim query.

## Tradeoffs

Composite tenant keys add verbosity to every foreign key. In return, a cross-tenant reference is
impossible to insert, not merely rejected by a check someone might forget.

## Consequences

* Every persistence change ships with a migration and a migration test.
* Audit events are append-only: the application role has no `UPDATE` or `DELETE` on the audit
  table.
* Read models for the console are queries, not a separate store, until measured otherwise.

# ADR-002: Simulated marketplace behind a client boundary

## Context

Onramp investigates records it does not own (workers, tasks, submissions, reviews, payments,
verification) and changes them only through actions. I need a realistic marketplace to test
against, including faults that real integrations have: slow reads, failed writes, and writes
that succeed while the response is lost.

If marketplace tables lived in Onramp's database and the Action Gateway wrote them in the same
transaction as the action record, the hardest reliability problem in the project (did the side
effect happen?) would disappear in a way no real deployment allows.

## Decision

The marketplace is a separate module, `onramp.marketplace`, with its **own PostgreSQL
database** and migrations, and its own outbox that publishes marketplace events to Kafka.
Onramp code reaches it only through `MarketplaceClient`:

* typed read methods returning records with stable IDs,
* write methods that require an idempotency key; the simulator stores the key with the write
  and returns the original result on a repeat,
* a fault-injecting wrapper used by tests and evaluation scenarios.

The first client implementation is in-process over the marketplace database. An HTTP server for
the simulator is added only if a scenario needs network-level faults the wrapper cannot model.

## Alternatives

* **Shared schema in one database.** Simpler, but hides the lost-response problem and invites
  cross-boundary joins.
* **A separately deployed marketplace service from day one.** More realistic network behavior,
  but a second API to maintain before anything needs it.

## Tradeoffs

Two databases in local development and tests. The payoff is that idempotency and
postcondition verification in the gateway are tested against a boundary that behaves like a
real one.

## Consequences

* An import-linter rule forbids importing marketplace models outside `onramp.marketplace`.
* Scenario fixtures load marketplace state through the simulator's own service layer.

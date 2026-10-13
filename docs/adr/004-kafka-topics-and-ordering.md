# ADR-004: Kafka topics, keys and ordering

## Context

Two producers publish facts: the marketplace (payments settling, evidence uploaded, reviews
finished) and Onramp (cases created, escalated, actions executed). Several consumers react
independently: the case-signal consumer that wakes Temporal workflows, an audit and analytics
projection, and evaluation capture. Producers should not know their consumers, and a new
consumer should be able to replay history.

## Decision

Kafka (KRaft mode, single broker locally) is the event backbone.

| Topic | Producer | Key | Purpose |
|---|---|---|---|
| `marketplace.payments` | marketplace | `payment_id` | authorization, settlement, failure |
| `marketplace.work` | marketplace | `submission_id` | task completion, submission, review results |
| `marketplace.workers` | marketplace | `worker_id` | profile, verification, evidence uploads |
| `onramp.cases` | Onramp | `case_id` | case lifecycle facts |
| `onramp.actions` | Onramp | `case_id` | proposals approved, actions executed or failed |
| `<topic>.dlq` | consumers | original key | events parked after retry exhaustion |

Ordering rules:

* Kafka orders only within a partition, so the key is the aggregate whose events must stay in
  order. No consumer assumes order across keys.
* Each envelope carries `aggregate_version`, a per-aggregate sequence number assigned in the
  producing transaction. Consumers that care about order compare it to the last version they
  applied and ignore anything older.
* Consumers treat events as notifications and re-read current state from the owning system
  before acting. An event out of order therefore cannot cause a wrong action, only a redundant
  re-check.

Envelope: `event_id`, `event_type`, `schema_version`, `occurred_at`, `tenant_id`,
`aggregate_type`, `aggregate_id`, `aggregate_version`, `correlation_id`, `causation_id`,
`payload`. W3C trace context travels in message headers. Producers use `acks=all` and the
idempotent producer.

## Alternatives

* **Postgres LISTEN/NOTIFY or a polling table.** Enough for one consumer, but no replay, no
  consumer groups, and couples every consumer to Onramp's database.
* **Redis Streams.** Consumer groups and acknowledgements, but weaker retention and replay
  semantics, and a less common choice for integration between systems.
* **Temporal signals only.** Works for waking workflows, but makes the marketplace call
  Temporal directly and leaves no replayable log for other consumers.

## Tradeoffs

Kafka is the heaviest piece of local infrastructure after Elasticsearch, and a single local
broker proves semantics, not durability. Replication factor and partition counts are
environment configuration.

## Consequences

* Event schemas live in `onramp.events.contracts` with compatibility tests; a breaking change
  is a new `schema_version`, and consumers park versions they do not support.
* Consumer lag per group is a first-class metric.

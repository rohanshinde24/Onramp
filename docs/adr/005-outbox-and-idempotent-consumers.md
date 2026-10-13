# ADR-005: Transactional outbox and idempotent consumers

## Context

A state change and the event announcing it must not diverge. Committing to Postgres and then
publishing to Kafka can lose the event if the process dies between the two; publishing first
can announce a change that then rolls back.

## Decision

**Outbox.** Every state change that produces an event inserts an `outbox_event` row in the same
transaction. Columns: `event_id` (UUID, unique), `topic`, `key`, the envelope fields,
`payload`, `created_at`, `published_at`, `attempts`, `last_error`, `next_attempt_at`.

**Publisher.** `onramp-outbox-publisher` loops:

1. In a transaction, claim up to N rows with `published_at IS NULL AND next_attempt_at <= now()
   ORDER BY id FOR UPDATE SKIP LOCKED`.
2. Publish them to Kafka and wait for acknowledgements.
3. Mark acknowledged rows published; record attempts and backoff for failures; commit.

A crash after step 2 and before step 3 republishes those events. That is accepted: delivery is
**at least once**. Several publisher replicas can run safely because of `SKIP LOCKED`; per-key
order across replicas is protected by `aggregate_version` (ADR-004), not by the publisher.

**Consumers.** Every consumer is idempotent:

* If its effect is a database write, it inserts `(consumer_group, event_id)` into
  `processed_event` in the same transaction as the effect. A unique violation means the event
  was already applied, so the consumer skips it.
* If its effect is a Temporal signal, the signal carries the `event_id` and the workflow ignores
  IDs it has seen; the consumer records the event as processed afterwards.
* Offsets are committed only after the effect is committed.
* After a bounded number of attempts, an event is parked to `<topic>.dlq` with the error, and
  the consumer moves on. Parked events are replayable with a make target.

## Alternatives

* **Kafka transactions / exactly-once semantics.** Covers Kafka-to-Kafka pipelines, not a
  Postgres commit followed by a Kafka write, so it does not solve this problem.
* **Change data capture (Debezium) on the outbox table.** Removes the polling loop, adds Kafka
  Connect and replication slots to operate. Not worth it at this volume; the outbox schema stays
  CDC-compatible if that changes.

## Tradeoffs

Polling adds publication latency (bounded by the poll interval) and steady database load. The
dedupe table grows and needs retention by age.

## Consequences

* The two crash windows (before publish, after ack before mark) each have a dedicated test.
* Metrics: outbox backlog count, oldest unpublished age, publish attempts, dedupe hits.
* Nothing in the docs or README claims exactly-once delivery.

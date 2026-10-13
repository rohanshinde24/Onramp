# Events

Decisions behind this document: ADR-004 (topics, keys, ordering) and ADR-005 (outbox,
idempotent consumers). Contracts live in `backend/src/onramp/events/contracts.py`; this file
describes them, and the code is authoritative.

## Envelope

| Field | Meaning |
|---|---|
| `event_id` | Stable unique ID. Consumers deduplicate on it. |
| `event_type` | `<noun>.<past_tense_verb>`, e.g. `case.escalated` |
| `schema_version` | Version of the payload contract for this event type |
| `occurred_at` | When the fact happened (timezone-aware) |
| `tenant_id` | Owning tenant |
| `aggregate_type`, `aggregate_id` | The entity whose history this event belongs to |
| `aggregate_version` | Strictly increasing per aggregate |
| `correlation_id` | Shared by every event caused by one originating request or event |
| `causation_id` | The event or request that directly caused this one, if any |
| `payload` | Validated against the `(event_type, schema_version)` contract |

Unknown envelope fields are rejected. Unknown payload fields are ignored, so adding an optional
payload field is compatible. Anything else (removing, renaming, retyping, or making a field
required) is a new `schema_version`. Consumers park versions they do not support.

## Topics

| Topic | Producer | Key | Events |
|---|---|---|---|
| `onramp.cases` | Onramp | `case_id` | `case.created`, `case.escalated`, `case.resolved`, `case.closed` |
| `onramp.actions` | Onramp | `case_id` | `approval.granted`, `approval.rejected` (action results in Phase 11) |
| `marketplace.payments` | Marketplace | `payment_id` | `payment.authorized`, `payment.settled`, `payment.settlement_failed` |
| `marketplace.work` | Marketplace | `submission_id` | `quality_review.overturned` |
| `marketplace.workers` | Marketplace | `worker_id` | `worker.updated`, `evidence.uploaded` |
| `<topic>.dlq` | Consumers | original key | Parked messages, with `dlq.reason`, `dlq.error`, `dlq.consumer_group` and `dlq.source` headers |

The Kafka key is always `aggregate_id`. All Onramp events use the case as their aggregate, so
every event for one case lands on one partition in order.

The marketplace writes events to its own outbox in its own database, in the same transaction
as the change, and they are relayed by a separate publisher process. Onramp consumes them as
it would a third party's events.

## Delivery and ordering

* Delivery is at least once. A consumer may see an event more than once and must be
  idempotent; it must not rely on exactly-once delivery.
* The publisher does not guarantee per-key order: a failed message is retried later while
  newer messages for the same key may already have been sent, and several publishers can run
  at once. `aggregate_version` is the ordering contract, not arrival order.
* Order holds only within one aggregate. Consumers that care compare `aggregate_version` to
  the last version they applied and ignore older or equal versions.
* Events are notifications. A consumer that needs current state reads it from the owner.

## Consumer groups

| Group | Topics | Handles | Effect |
|---|---|---|---|
| `onramp-case-evidence` | `marketplace.workers`, `marketplace.payments` | `evidence.uploaded` v1, `payment.settled` v1, `payment.settlement_failed` v1 | Records the evidence on the open case it concerns and resumes the case if it was waiting for that kind of evidence |

Every consumer runs on `ConsumerRunner`:

1. Parse the envelope; malformed messages are parked.
2. Event types the group does not handle are acknowledged and ignored. A handled type in an
   unsupported version is parked.
3. Insert `(consumer_group, event_id)` into `processed_event` and apply the effect in one
   transaction. An existing row means the event was already handled; it is skipped.
4. Commit the offset only after the database commit.

## Parking and replay

A handler that keeps failing is retried three times and then parked to `<topic>.dlq` with
`dlq.reason`, `dlq.error`, `dlq.consumer_group` and `dlq.source` headers, and recorded as
`parked`, so one bad event cannot block a partition.

After fixing the cause:

```sh
make dlq-replay TOPIC=marketplace.workers GROUP=onramp-case-evidence
```

Replay removes that group's `parked` record and republishes the original message to the source
topic. Other groups have already processed the event and skip it as a duplicate.

## Replaying a whole topic

A new consumer group starting from the earliest offset sees every retained event again. Its
effects must stay correct: handlers re-read current state and act only when the state calls
for it, so replaying old evidence into a case that has moved on records history but does not
move the case backwards.

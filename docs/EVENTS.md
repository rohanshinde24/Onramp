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

Marketplace topics (`marketplace.payments`, `marketplace.work`, `marketplace.workers`) are
added with the marketplace producer in Phase 7.

The Kafka key is always `aggregate_id`. All Onramp events use the case as their aggregate, so
every event for one case lands on one partition in order.

## Delivery and ordering

* Delivery is at least once. A consumer may see an event more than once and must be
  idempotent; it must not rely on exactly-once delivery.
* The publisher does not guarantee per-key order: a failed message is retried later while
  newer messages for the same key may already have been sent, and several publishers can run
  at once. `aggregate_version` is the ordering contract, not arrival order.
* Order holds only within one aggregate. Consumers that care compare `aggregate_version` to
  the last version they applied and ignore older or equal versions.
* Events are notifications. A consumer that needs current state reads it from the owner.

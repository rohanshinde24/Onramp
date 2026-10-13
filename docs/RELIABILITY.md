# Reliability

This file lists the failure modes Onramp handles, how, and the test that shows it. A row
without a test is a plan, not a property.

## Transaction boundaries

* A case change, its audit event and its outbox event commit in one PostgreSQL transaction.
* Onramp and the marketplace are separate databases. No transaction spans both, so every
  marketplace write is idempotent by key and verified afterwards (ADR-002, ADR-008).

## Failure modes

| Failure | Behavior | Test |
|---|---|---|
| Business change rolls back after `emit` | No event exists | `test_outbox_emit.py::test_rolled_back_transaction_leaves_no_event` |
| Publisher dies before sending | Nothing sent; rows claimed again next cycle | `test_outbox_publisher.py::test_crash_before_send_publishes_nothing_and_loses_nothing` |
| Publisher dies after broker ack, before marking | Same events sent again with the same `event_id` (at least once) | `test_outbox_publisher.py::test_crash_after_ack_republishes_the_same_events` |
| Broker rejects some messages | Those rows retry with capped exponential backoff | `test_outbox_publisher.py::test_failed_messages_retry_with_backoff` |
| Several publishers run at once | `SKIP LOCKED` keeps any row from being claimed twice | `test_outbox_publisher.py::test_concurrent_publishers_never_claim_the_same_row` |
| Concurrent events for one aggregate | Distinct, increasing `aggregate_version` | `test_outbox_emit.py::test_concurrent_emitters_get_distinct_versions` |
| Concurrent intake for one subject | Exactly one case opens | `test_case_intake.py::test_concurrent_reports_open_exactly_one_case` |
| Concurrent or replayed approvals | Exactly one decision recorded | `test_api_approvals.py::test_concurrent_approvals_record_exactly_one` |
| Stale update of a case | Rejected by the version column; 409 | `test_case_persistence.py::test_stale_update_fails` |
| Marketplace write succeeds, response lost | Retry with the same key returns the original result; one payment | `test_marketplace_faults.py::test_lost_response_then_retry_with_same_key_pays_once` |
| Concurrent marketplace writes with one key | Exactly one write | `test_marketplace_writer.py::test_concurrent_calls_with_one_key_write_once` |
| Gateway crashes after recording intent, before the marketplace call | Retry resumes the same action with the same key | `test_action_gateway.py::test_crash_after_record_persistence_resumes_with_the_same_action` |
| Gateway loses the marketplace response | It retries with the same key and records one mutation | `test_action_gateway.py::test_lost_response_retries_the_same_key_and_mutates_once` |
| Same approved proposal executes concurrently | Both callers return one action and one business mutation | `test_action_gateway.py::test_concurrent_execution_produces_one_business_mutation` |
| Marketplace postcondition cannot be verified | Action is unverified, failure is audited and emitted, and the case escalates | `test_action_gateway.py::test_postcondition_failure_is_stored_audited_emitted_and_escalated` |
| Process receives SIGTERM | API drains requests; publisher finishes its cycle before exit | `test_outbox_publisher.py::test_run_until_stopped_exits_when_signalled`; API checked manually in compose (exit 0) |

| Consumer sees an event twice | Second delivery skipped; effect applied once | `test_consumer_runner.py::test_duplicate_delivery_applies_once` |
| Consumer dies after DB commit, before offset commit | Redelivered and skipped | `test_consumer_runner.py::test_crash_after_db_commit_before_offset_commit` |
| Unsupported event version or malformed envelope | Parked once, never guessed at | `test_consumer_runner.py::test_unsupported_version_is_parked_once`, `test_malformed_message_is_parked` |
| Handler keeps failing | Retried, then parked with the error; no partial effect | `test_consumer_runner.py::test_persistent_handler_failure_is_parked_without_effect` |
| Parked event after the fix | Replayed and applied once | `test_dlq_replay.py::test_replayed_event_is_applied_by_the_fixed_consumer` |
| Republished event, consumer restart, full replay by a new group (real Kafka) | Case resumes exactly once | `test_evidence_over_kafka.py` |
| Broker unreachable | Publish fails within the bounded timeout; backlog holds; publishes after recovery | `test_outbox_to_kafka.py::test_events_survive_a_broker_outage_and_publish_after_recovery` |

## Incidents found by running the system

* **2026-10-13, event consumer could not write.** The consumer process imported only the
  models its handler used, so a foreign key to `tenant` could not be resolved and the first
  evidence event was parked. Tests had imported every model up front and hidden it. Fixed in
  `ae2d0a0`, with a test that resolves every foreign key from each entrypoint in a clean
  interpreter. The parked event was replayed with `onramp-dlq-replay` and applied.
* **2026-10-13, intermittent Kafka delivery timeouts in tests.** `localhost` resolved to `::1`
  first, which the broker does not listen on. Fixed by advertising and connecting on
  `127.0.0.1` (`cde2e77`).

## Not yet handled

* Workflow recovery and duplicate signals (Phase 12).
* Retention of published outbox rows and marketplace idempotency records.

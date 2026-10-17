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
| API commits a case before its workflow start reaches Temporal | The workflow worker reconciles every nonterminal case on startup using the deterministic workflow ID | `test_workflow_activities.py::test_reconciler_starts_every_committed_nonterminal_case` |
| Direct workflow signal is lost after a proposal or approval commit | The same outbox `event_id` reaches the case-signal consumer and wakes the workflow | `test_api_workflow_notifications.py`, `test_workflow_event_handler.py` |
| Evidence wait reaches seven days | A durable Temporal timer invokes an idempotent activity that re-reads the deadline and escalates only if the case still waits | `test_case_workflow.py::test_evidence_deadline_uses_time_skipping`, `test_workflow_activities.py::test_evidence_timeout_escalates_only_after_its_deadline` |
| Settlement window reaches its policy deadline | A durable Temporal timer returns the case to checks | `test_case_workflow.py::test_settlement_deadline_uses_time_skipping`, `test_workflow_activities.py::test_settlement_resume_rechecks_current_state_and_deadline` |
| Workflow code changes while workflows are open | Workflows started before the change replay on the code they started with, behind a Temporal patch | `test_case_workflow_replay.py::test_a_workflow_started_before_the_patch_replays_on_current_code` (fails with a nondeterminism error if the patch is bypassed) |
| Workflow receives a duplicate signal | The signal ID is applied once and the workflow re-reads PostgreSQL truth | `test_case_workflow.py::test_duplicate_signal_wakes_the_workflow_once` |
| Model provider is down during an investigation | The run is stored as `model_error`, the case stays in `INVESTIGATING`, and the activity fails so Temporal retries it with capped exponential backoff instead of calling the provider again at once | `test_case_workflow.py::test_provider_failure_is_retried_with_backoff_not_a_tight_loop`, `test_investigation_service.py::test_agent_activity_raises_on_provider_failure_so_temporal_backs_off` |
| An event arrives after its case's workflow completed (late, or replayed after the broker lost its topics) | The coordinator treats the finished workflow as nothing to wake; the consumer commits the offset instead of failing on the same message forever | `test_case_workflow.py::test_a_late_event_for_a_finished_case_is_a_no_op` |
| Broker is down while operators work | Cases still progress through direct Temporal signals; events wait in the outbox with their trace context and publish when the broker returns | `test_outbox_publisher.py::test_failed_messages_retry_with_backoff`; observed end to end in `docs/OPERATIONS.md` |
| Workflow worker stops while a case waits | Temporal retains the wait and queued signal; a replacement worker replays history and continues | `test_case_workflow.py::test_signal_waits_in_history_across_worker_restart` |

| Consumer sees an event twice | Second delivery skipped; effect applied once | `test_consumer_runner.py::test_duplicate_delivery_applies_once` |
| Consumer dies after DB commit, before offset commit | Redelivered and skipped | `test_consumer_runner.py::test_crash_after_db_commit_before_offset_commit` |
| Temporal signal fails after the consumer DB commit | Offset stays uncommitted; redelivery retries the post-commit signal with the same ID while the DB effect remains single | `test_consumer_runner.py::test_post_commit_effect_retries_after_database_claim_without_committing_offset` |
| Unsupported event version or malformed envelope | Parked once, never guessed at | `test_consumer_runner.py::test_unsupported_version_is_parked_once`, `test_malformed_message_is_parked` |
| Handler keeps failing | Retried, then parked with the error; no partial effect | `test_consumer_runner.py::test_persistent_handler_failure_is_parked_without_effect` |
| Parked event after the fix | Replayed and applied once | `test_dlq_replay.py::test_replayed_event_is_applied_by_the_fixed_consumer` |
| Republished event, consumer restart, full replay by a new group (real Kafka) | Case resumes exactly once | `test_evidence_over_kafka.py` |
| Broker unreachable | Publish fails within the bounded timeout; backlog holds; publishes after recovery | `test_outbox_to_kafka.py::test_events_survive_a_broker_outage_and_publish_after_recovery` |

## Whole-case recovery under injected faults

`make fault-eval` runs whole cases through the real services while something goes wrong, with
activity retries that mirror the workflow's retry policies. Each row is one scenario in
`eval/faults/`, checked by `test_fault_evaluation.py::test_every_fault_is_recovered_to_the_normal_outcome_with_one_write` unless another test is named.

| Fault during a case | Behavior |
|---|---|
| Payout response lost once | Gateway retries with the same key; one payment |
| Payout response lost on every gateway attempt | Activity fails, is retried, resumes the stored action with the same key; one payment |
| Activation times out before reaching the marketplace, three times | Retried activity activates once |
| Review overturn lands but every response is lost | Retried activity verifies the existing overturn; one write |
| Evidence request response lost | Checks rerun and repeat the request under the same key; one request |
| Model provider fails twice | Investigation retried with backoff, then concludes |
| Policy search fails twice mid-investigation | Investigation retried from the start, then concludes |
| Process dies right after checks, an investigation, an execution, a settlement resume or an evidence timeout commits | The retry re-reads PostgreSQL, finds the work done and does not repeat it (one check run, one investigation, one payment) |
| Marketplace event delivered twice | The consumer applies it once and records the duplicate |
| Bank settles the original payment after the case paid an adjustment | The resolved case does not reopen or move |
| Payout responses lost on every attempt, past all retries | Run reported as `injected_fault`; still exactly one payment and no safety violation (`test_a_fault_that_outlasts_every_retry_is_reported_and_still_writes_at_most_once`) |

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

* Retention of published outbox rows and marketplace idempotency records.
* A settlement that arrives after Onramp paid an adjustment leaves the worker paid twice. The
  case correctly stays resolved, but nothing flags the overpayment for finance.
* A policy search failure fails the investigation before it is stored, so failed attempts
  leave no `investigation_run` record, unlike model provider failures.

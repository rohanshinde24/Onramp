# Implementation Drift and Follow-ups

This log records material differences between the original phased implementation plan and the
system that was actually built. It is not a defect list. Some changes closed correctness gaps;
others are explicit local-development compromises or deferred production work.

Last reviewed: 2026-10-16 at Phase 17.

## Correctness changes discovered during implementation

### Signals must not race with workflow reads

The first workflow wait loop could lose a wake-up when a signal arrived during a database read.
The workflow then recorded the already-incremented signal generation as its baseline and waited
forever. The implementation now snapshots the wake generation before each read. The failure was
visible in CI at `84067d5` and fixed at `41639d8`.

### Consumer-driven Temporal signals must occur after commit

The original event-consumer shape made it tempting to signal Temporal from inside the database
transaction. A workflow could then wake and read state that had not committed. The consumer now
runs the signal hook only after the transaction commits. If that external signal fails, the Kafka
offset remains uncommitted; redelivery retries the hook even when the durable deduplication claim
already exists.

### Direct and fallback signals need one stable identity

Direct API signaling originally risked creating a different logical wake from the outbox/Kafka
fallback. Proposal and approval paths now pass the exact outbox event ID to both paths. Duplicate
delivery remains harmless, but both paths represent the same fact.

### Proposal decisions need durable events

The initial event inventory did not include proposal acceptance and rejection. A process crash
after a direct-only decision could strand a workflow waiting for that decision. The implementation
adds `proposal.accepted` and `proposal.rejected` outbox events as durable fallback signals.

### Evidence fallback must identify the exact case

Resolving the currently open case again after commit could target a newer case for the same
subject. Evidence fallback now uses the committed audit row's causation ID to recover the exact
case that changed.

### Provider failures must back off

Building the case-evaluation runner showed that a `model_error` investigation returned normally
from its activity. The workflow saw the case still in `INVESTIGATING` and called the provider
again at once, so an outage became a tight loop of failing calls and stored runs per case. The
agent activity now fails on `model_error`, and the workflow retries it with capped exponential
backoff and no attempt limit. The case still waits in `INVESTIGATING` until the provider answers
or a person acts.

### Activation must recheck onboarding requirements

Planning the Phase 14 adversarial scenarios showed that the gateway checked only that a worker
was still pending before activating them. A compromised investigator could propose activation
for a worker whose tax form was uploaded but never verified, and an approving reviewer was the
only defense. The gateway now re-runs the deterministic onboarding check at execution and
refuses activation unless every required verification has passed.

### Quality review wording disagreed with the check

Quality review v5 and v6 section 4.1 says a review is overturned when it scored a criterion
that did not apply, or when the applicable average passes. The check, its tests and
`CHECKS.md` instead exclude inapplicable scores and overturn only when the applicable average
passes. The two disagree when a reviewer scores an inapplicable criterion and the work still
fails on the applicable ones. I chose the check's reading: a reviewer's mistake on something the
task never asked for should not pass work that fails on what it did ask for. Policy versions are
immutable, so the wording is fixed in quality review v7, effective 2026-10-15, and the loader
now accepts closing an open version when its successor is published.

### Overturned quality reviews remain payout eligible

Action Gateway review exposed an edge case where an overturned review could incorrectly suppress
payout eligibility. The deterministic payout computation was corrected in Phase 11 so overturned
reviews count correctly.

## Architecture and dependency adaptations

### Temporal SDK version

The project uses `temporalio` 1.32.x. Versions 1.33 and 1.34 considered during Phase 12 were yanked
at the time of implementation, so the dependency was pinned to the current stable line instead of
the initially expected newest version.

### Local Temporal topology

The formerly common Docker Compose auto-setup repository and image path was archived or
deprecated. Local development therefore uses the Temporal CLI development server with persisted
SQLite. This is intentionally not the production topology. Production packaging remains Phase 19
work.

### Synchronous application with an asynchronous Temporal client

The existing FastAPI, SQLAlchemy, and Kafka paths are synchronous. Converting them to asynchronous
I/O during workflow work would have been unrelated architectural churn. A synchronous coordinator
now owns a dedicated asyncio loop thread for Temporal client calls.

### Separate workflow and agent queues

The broad worker-role plan became a workflow queue plus an isolated agent queue. The agent worker
is an opt-in Compose profile because it is the only role that needs model credentials. No live
model call was made. The shared image still includes embedding and model dependencies for all
roles and is about 785 MB; role-specific images remain Phase 19 work.

### Dense retrieval was selected by evidence

The architecture allowed multiple retrieval strategies. The Phase 8 benchmark selected dense
retrieval rather than hybrid retrieval, and the agent worker uses that selected retriever.

## Explicit assumptions and deferred production work

### Evidence wait timeout

The product and ADRs required a durable evidence timeout but did not specify its duration. The
workflow uses seven days as a named, tested constant. This is a provisional product assumption
and can later become policy-driven or configurable if operating requirements differ.

### Direct Temporal client reconnection

The API attempts its direct Temporal connection once at startup. If Temporal is unavailable, the
API remains usable and the durable Kafka fallback still works, but the low-latency direct client
does not reconnect until the process restarts.

### Workflow reconciliation scope

The reconciler runs when the workflow worker starts and scans all nonterminal cases. It is not yet
paginated and does not run periodically. This is acceptable for the current synthetic scale and
must be measured before adding machinery.

### Retry behavior for unknown marketplace outcomes

`OutcomeUnknownError` remains retryable by Temporal instead of being converted into a terminal
business failure. The Action Gateway reuses the same idempotency key and verifies the
postcondition, which is the required safety behavior.

### Shared integration databases

The integration and Kafka suites recreate shared test databases. Running them concurrently causes
interference, so repository gates run them sequentially. Temporal time-skipping tests also start a
local test server and can require host process/network permission in restricted environments.

## Evaluation sequencing changes

Phase 13 started with schemas and graders before the whole-application runner. This lets tests
demonstrate that deliberately bad outputs fail before the evaluator is trusted to score a system
run. Truth is loaded from a separate artifact and is excluded from investigator inputs; it is
hidden from the system under evaluation, not secret from repository readers.

The runner that followed differs from the original plan in four deliberate ways:

* It drives the workflow's activity implementations directly, chosen by case state, with a
  virtual clock instead of running the Temporal workflow. Scenario worlds use fixed dates so the
  policy version in force is stable, and Temporal's time-skipping clock starts at the wall
  clock, so the two cannot share one timeline without coupling the activities to Temporal time.
  Durable timers and signals stay covered by the workflow tests.
* People are replaced by a scripted reviewer that accepts and approves as shown. Phase 14 added
  attacking reviewers behind the same hook; a reviewer who rejects in good faith is not modeled.
* An escalated case is observed as having no outcome, even if it made a proposal first. The
  earlier adapter reported the abandoned proposal, which graded a correct over-limit escalation
  as wrong.
* Artifacts are written per suite and without Parquet until Phase 16; ADR-009 records this.

The scenario format also gained `subject_id` (what the reporter names) and an optional truth
`note`, before any dataset was published.

## Phase 16 adaptations

* Promptfoo is a viewer, not the runner. The plan named Promptfoo for the model by prompt
  matrix. Running models through Promptfoo would have bypassed the application path this
  project evaluates, so models run through the same case runner and Promptfoo renders the
  recorded results with a provider that replays them. It needs Node 22, the version pinned in
  `.nvmrc`; `make promptfoo-view` takes it from nvm.
* The published runs are live, not batched. Batch mode is built and tested and halves cost, but
  the published comparison uses live calls so every result carries real latency, as the service
  would. Batch remains available for cheaper reruns.
* The judgment suite's labels changed once. Version 1 labelled payout v11 partial settlements
  UNDERPAID; every model in the first live run escalated them, consistent with the
  investigator's rules and the deterministic check, and version 2 labels them escalations.
* The live smoke test found two defects scripted tests could not: conclusion reason codes the
  proposal schema rejected (now constrained in the conclusion schema, with escalation instead
  of a crash), and retries counting their own replayed answers as cost.

## Phase 17 adaptations

* The console holds the bearer token on its own server and signs in with a pasted token. There
  is no identity provider yet; a deployed console needs one (ADR-012).
* Next.js Cache Components are off. Every console page is per-user, authoritative state read
  at request time, so there is nothing to prerender and nothing that may be served stale.
* The evaluation view reads artifacts from disk on the console's server rather than through
  the API, so evaluation data stays off the application's read path (ADR-009, ADR-012).
* The reachability walk from a case to its marketplace records moved from the Action Gateway
  into `cases.evidence`, so the console's evidence view and the gateway's evidence check share
  one definition.
* Running the whole stack for the console found two defects. A fresh Temporal volume was owned
  by root while the dev server runs as its own user, so the workflow profile could not start on
  a new machine; the volume now mounts at that user's home. And every evaluation manifest,
  including live-model runs, said investigations were scripted; live runs now say what they
  are. The two live baselines stored before the fix keep their original manifests, and the
  console flags the wrong line on them.
* Deferred: an automated browser test of the console against a running API (verified by hand
  for this phase), an assignee filter in the inbox (nothing assigns cases yet), queue counts
  above 100, and an admin view of audit history across cases (admins see each case's timeline).

## Deferred evaluation work

* Real-model runs with the production dense retriever instead of the keyword retriever, more
  than one effort level, and repeats for Sonnet and Opus.
* In the only batch run attempted, each batch held 49 requests rather than one per case; the run
  was stopped before this was explained. Check `batch.not_fully_recorded` on the next batch run.
* World events are delivered while a case waits and after it stops. Events that race with
  checks or an investigation in progress are not modeled.
* The runner retries activities with policies mirrored from the workflow module rather than
  through Temporal. Unlimited investigation retries are capped at five.
* A reviewer who rejects a correct proposal, and the lead workflow after escalation, are not
  modeled.

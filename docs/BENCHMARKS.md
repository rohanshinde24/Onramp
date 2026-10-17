# Benchmarks

Every number here comes from a command in this repository. Each entry names the command, the
dataset version, the commit, and the run ID whose artifacts produced it. Artifacts are
written to `artifacts/` and are not committed; rerun the command to reproduce.

## Case evaluation

Commands: `make up && make case-eval`, `make adversarial-eval` and `make fault-eval`. Design and
grading rules: [EVALUATION.md](EVALUATION.md).

Reference suite: `eval/cases/` version 4, 21 scenarios (9 payout, 6 onboarding, 6 quality
review), investigations `scripted:reference:v3`, prompt `investigator-v1`, honest reviewer.

Adversarial suite: `eval/adversarial/` version 1, 15 attacks, investigations
`scripted:compromised:v1`, reviewers per `eval/adversarial/reviewers.yaml`.

Fault suite: `eval/faults/` version 2, 15 scenarios with faults per `eval/faults/faults.yaml`,
investigations `scripted:reference:v2`.

Runs `20261014T073328Z` (reference), `20261014T073333Z` (adversarial) and `20261014T073336Z`
(faults), commit `e8ea3ab`, clean tree:

| Measure | Reference | Adversarial | Faults |
|---|---|---|---|
| Runs: success / app failure / injected fault / harness error | 21 / 0 / 0 / 0 | 15 / 0 / 0 / 0 | 15 / 0 / 0 / 0 |
| Outcome correct (for attacks, the safe outcome) | 21 of 21 | 15 of 15 | 15 of 15 |
| Resolution macro F1, averaged over case types | 1.000 | 1.000 | 1.000 |
| Escalation precision / recall | 1.000 / 1.000 | 1.000 / 1.000 | 1.000 / 1.000 |
| Citation precision / recall | 1.000 / 1.000 | 1.000 / 1.000 | 1.000 / 1.000 |
| Fabricated citations | 0 | 0 | 0 |
| Safety violations | 0 | 0 | 0 |
| Cases settled without the model | 18 of 21 | 7 of 15 | 11 of 15 |

What this shows:

* Every reference scenario goes through intake, checks, investigation where needed, proposal,
  approval and the gateway, and ends where its label says, under the exact policy version in
  force at the incident, including waits that end early when the marketplace changes.
* With an investigator or reviewer that goes along with every attack, no attack produced a
  marketplace write it should not, and each was stopped by the layer named in EVALUATION.md.
* Seeded bad variants (wrong amount, wrong resolution, forged citation, a writer that pays twice,
  the activation recheck removed) fail in tests, so a perfect score is not the grader's default.
* With lost and timed-out marketplace responses, model and search outages, crashes after
  commit and duplicate events, every case recovered to its normal outcome with no extra write.

Limits:

* Investigations are scripted. This measures the application around the model, not any
  model's judgment, and its token and latency numbers mean nothing.
* Reviewers are scripted. Waits and retries run on a virtual clock that mirrors Temporal's
  policies, not on Temporal itself. Search uses a
  keyword retriever, not the production dense one.
* I wrote the worlds, labels, scripts and attacks. These are regression checks of specified
  behavior, not benchmarks, and the scores are expected to be perfect.

## Live models on the judgment suite

Command: `make judgment-eval MODEL=<model> MAX_COST=<dollars> PROMPT=<version>` (live calls, not
batched), compared with `make compare`. Viewable as a model by prompt matrix with
`make promptfoo-view`, which replays these results and makes no model call.

Dataset: `eval/judgment/` version 2, 92 cases that all reach the investigator (41 with a
definite answer, 51 that should escalate, so always escalating scores 0.554). Effort `medium`,
keyword policy retriever, honest reviewer. Every run: commit `295a425`, clean tree, 92 of 92
cases ran, 0 safety violations. Accuracy counts resolution, action, amount, escalation and
policy version together. Escalation recall is the share of cases that should go to a person
that did. Seconds are the investigation's own wall-clock time per case.

| Model | Prompt | Repeat | Accuracy | Escalation recall | Escalation precision | Cost | Cost per correct | Median s | p90 s | Run |
|---|---|---|---|---|---|---|---|---|---|---|
| Haiku 5.5 | v1 | 0 | 0.511 | 0.84 | 0.61 | $0.24 | $0.0051 | 9.7 | 12.9 | `20261014T161154Z` |
| Haiku 5.5 | v1 | 1 | 0.554 | 0.90 | 0.63 | $0.24 | $0.0047 | 10.2 | 12.9 | `20261014T165807Z` |
| Haiku 5.5 | v1 | 2 | 0.533 | 0.86 | 0.62 | $0.25 | $0.0050 | 10.6 | 13.2 | `20261014T171423Z` |
| Haiku 5.5 | v2 | 0 | 0.815 | 1.00 | 0.75 | $0.21 | $0.0027 | 8.5 | 12.3 | `20261014T162708Z` |
| Haiku 5.5 | terse | 0 | 0.489 | 0.61 | 0.63 | $0.26 | $0.0057 | 10.6 | 14.7 | `20261014T164049Z` |
| Sonnet 5.5 | v1 | 0 | 0.587 | 0.63 | 0.67 | $1.35 | $0.0251 | 7.3 | 9.3 | `20261014T173115Z` |
| Sonnet 5.5 | v2 | 0 | 0.989 | 0.98 | 1.00 | $1.10 | $0.0121 | 7.2 | 8.8 | `20261014T174314Z` |
| Sonnet 5.5 | terse | 0 | 0.565 | 0.45 | 0.77 | $1.22 | $0.0234 | 8.5 | 10.4 | `20261014T175442Z` |
| Opus 5.5 | v1 | 0 | 0.533 | 0.90 | 0.55 | $3.44 | $0.0702 | 14.0 | 16.6 | `20261014T180801Z` |
| Opus 5.5 | v2 | 0 | 0.848 | 1.00 | 0.78 | $2.76 | $0.0354 | 11.9 | 18.7 | `20261015T020510Z` |

Regression gate results (`make compare`, 95% paired bootstrap over the 92 cases):

| Base | Candidate | Accuracy change | Gate |
|---|---|---|---|
| Haiku v1 | Haiku v2 | +0.304 (+0.217 to +0.402) | pass |
| Opus v1 | Opus v2 | +0.315 (+0.228 to +0.413) | pass |
| Haiku v2 | Haiku terse | -0.326 (-0.424 to -0.228) | fail |
| Sonnet v2 (baseline) | Sonnet terse | -0.424 (-0.533 to -0.315) | fail |

Total spent on these runs and the smoke test before them: about $11.

What this shows:

* The prompt mattered more than the model. Prompt v2 added 30 to 40 points of accuracy on every
  model, and under v1 the most expensive model scored lowest of the three.
* Cost per correct outcome favours the right prompt on a cheaper model: Haiku v2 reached 0.815
  for about a quarter of a cent per correct case, and Sonnet v2 reached 0.989 for about 1.2
  cents, against 7 cents for Opus v1.
* The gate passes real improvements and fails a real model's regression. Sonnet v2 and Haiku v2
  are stored as baselines under `eval/baselines/judgment-eval/`.
* Three Haiku v1 runs landed between 0.511 and 0.554, so differences of 30 points are not run
  to run noise.
* Under v1, models read "the policy in force at the incident time" as "the facts at the incident
  time" and failed cases where requested evidence arrived later; v2's explicit decision steps
  fixed most of them.

Limits:

* I wrote the cases, the label rules and both prompts. v2 was written before any live run, but
  its decision steps follow the same policies the labels do, so these scores measure agreement
  with my reading of the policies, not an independent ground truth.
* The regression the gate caught is a deliberately weakened prompt (`investigator-terse`), not an
  accidental one.
* One run per configuration except Haiku v1 (three). Sonnet and Opus variance is not measured.
* Single effort level, keyword retrieval rather than the production dense retriever, scripted
  reviewers, synthetic data. Latency is per investigation on one laptop, not a service SLO.

## Policy retrieval

Command: `make up && make migrate && make retrieval-eval` (with `pip install -e 'backend[embeddings]'`).

Dataset: `eval/retrieval/queries.yaml` version 1, 41 queries (15 payout, 14 onboarding, 12
quality review) spanning both versions of each policy. Each query has an incident time and
the passages that answer it under the version in force then.

Run `20261013T161319Z`, commit `3b1a85f`, clean tree:

| Method | Recall@1 | Recall@3 | Recall@5 | MRR@10 | Top-1 from wrong version |
|---|---|---|---|---|---|
| Random ranking of passages in force | 0.146 | 0.439 | 0.659 | 0.374 | 0.000 |
| BM25 | 0.561 | 0.878 | 0.902 | 0.742 | 0.000 |
| BM25 without the version filter | 0.354 | 0.707 | 0.841 | 0.556 | 0.439 |
| Dense (`BAAI/bge-small-en-v1.5`) | 0.817 | 0.963 | 1.000 | 0.939 | 0.000 |
| Hybrid (reciprocal rank fusion) | 0.683 | 0.939 | 0.988 | 0.837 | 0.000 |

Paired bootstrap on per-query reciprocal rank (10,000 resamples, seed 1):

| Comparison | Mean difference | 95% CI |
|---|---|---|
| Dense minus BM25 | +0.197 | +0.098 to +0.303 |
| Dense minus hybrid | +0.102 | +0.030 to +0.177 |
| Hybrid minus BM25 | +0.096 | +0.030 to +0.173 |
| BM25 minus random | +0.368 | +0.240 to +0.486 |

What this shows:

* Without the effective-date filter, 44% of top results come from a policy version that was
  not in force at the incident. With it, none do, for every method.
* Dense retrieval ranks the right passage first far more often than BM25 (decision: ADR-011).

Limits:

* After the version filter, each query chooses among only 6 to 10 passages. Recall at 3 and 5
  is high even for random ranking; Recall@1 and MRR are the discriminating measures.
* I wrote both the corpus and the labels. The query wording avoids copying section headings,
  but this is a check on retrieval behavior, not an independent relevance study.
* 41 queries is small; the intervals above are wide, and only differences whose intervals
  exclude zero are treated as real.

## Intake pipeline under load

Command: `make k8s-up && make k8s-deploy`, then `make load-test RATE=<n> DURATION=60` (k6
v2.3.0 running `deploy/load/intake.js`). Each request opens a payout case for a fresh, settled
in full submission in the `loadtest` tenant, which `onramp-load-run` seeds first. The real
pipeline then carries it: the API commits the case and its `case.created` outbox row and starts
the workflow, the outbox publisher sends the event to Kafka, the case-signal consumer delivers
the fallback start, and the workflow worker runs the checks, which end in a system proposal
waiting for an operator. Checks decide every case, so no model is called. Latency and
throughput are read from PostgreSQL timestamps (case creation, the transition to `proposed`,
outbox publication), not from k6. Artifacts: `artifacts/load/<run_id>/`.

Setup for every run: one kind node (Kubernetes 1.37) in Docker Desktop with 8 GB and 8 CPUs on
a 16 GB laptop; API 2 replicas, workflow worker 1, publishers and consumers 1 each; Postgres,
Kafka (one broker) and the Temporal development server (SQLite) under Compose.

### Finding and removing the first bottleneck

At 25 intakes per second the API answered quickly and the outbox published within half a
second, but fewer than 14 cases per second reached a proposal, and the wait grew for as long as
the load lasted. Temporal's own metrics showed where: workflow tasks waited 5.4 s on average to
start while each one executed in 2 ms, and the worker never used more than 8 of its 100
workflow slots. Doubling the workflow workers changed throughput from 12.6 to 13.4 cases per
second, so the worker was not the limit. Each task completion took about 90 ms on the Temporal
server, and a case needed about 4.4 workflow tasks and 3.3 activities to reach its proposal.

The fix reduced round trips rather than speeding anything up. Every activity already returns the
case as it left it, so the workflow now decides on that snapshot instead of reading again, and
the reads that remain are local activities, which complete inside the workflow task. Workflows
started before the change replay on the original loop behind a Temporal patch
(`test_case_workflow_replay.py` replays a recorded history, and fails with a nondeterminism
error if the patch is bypassed).

Before: commit `7a1e387`. After: commit `5b726b5`. Clean trees, 60 s of load each.

| Run | Offered | Requests (failed) | API p50 / p99 | Reached proposal per s | Intake to proposal p50 / p99 | Outbox publish p99 |
|---|---|---|---|---|---|---|
| `20261017T111106Z` before | 25/s | 1,500 (0) | 91 / 228 ms | 13.7 | 16.2 / 54.6 s | 0.50 s |
| `20261017T111306Z` before | 25/s | 1,501 (0) | 115 / 687 ms | 13.3 | 28.1 / 54.6 s | 0.51 s |
| `20261017T111508Z` before | 50/s | 3,000 (0) | 153 / 414 ms | 13.7 | 87.7 / 158.4 s | 0.51 s |
| `20261017T112032Z` after | 25/s | 1,501 (0) | 80 / 335 ms | 24.0 | 2.3 / 6.4 s | 0.51 s |
| `20261017T112144Z` after | 25/s | 1,501 (0) | 72 / 313 ms | 23.7 | 0.9 / 6.2 s | 0.51 s |
| `20261017T112255Z` after | 50/s | 3,001 (0) | 185 / 552 ms | 21.0 | 46.0 / 84.9 s | 0.51 s |

What this shows:

* At 25 per second the pipeline now keeps up (about 24 cases per second reach a proposal, the
  rest are in flight when the load stops), where before it fell behind by about half.
  Intake to proposal p99 fell from 54.6 s to about 6.3 s.
* The ceiling at 50 per second rose from 13.7 to 21.0 cases per second, about 1.5 times.
* Every request succeeded and every case reached its proposal in every run.

What it costs, and what is left:

* API latency rose. A trace of a slow intake spent 141 of its 153 ms in the API's
  `StartWorkflow` call. With the workers no longer starved, that call competes with them for the
  same Temporal server. The case-signal consumer's fallback start adds another call per case
  (86 ms in the same trace) that the API's direct start has already made redundant.
* The limit is now the Temporal development server's write path. A Temporal deployment with
  production persistence is the next step for throughput, and Phase 21 decides its shape.

### Autoscaling on consumer lag

Command: `backend/.venv/bin/python deploy/k8s/kind/lag_burst.py 20000 --label <text>` at commit
`e5849a7`. It publishes 20,000 valid `payment.settled` events for payments that do not exist, so
the event consumer reads, deduplicates and records each without changing a case, and times the
drain from `processed_event`. Pinned runs pause KEDA with `autoscaling.keda.sh/paused-replicas:
"1"`. Artifacts: `artifacts/drain/<run_id>.json`.

| Run | Consumer replicas | Drain time | Events per s | Processed / distinct |
|---|---|---|---|---|
| `20261017T112748Z` | pinned at 1 | 435.5 s | 45.9 | 20,000 / 20,000 |
| `20261017T113507Z` | pinned at 1 | 426.3 s | 46.9 | 20,000 / 20,000 |
| `20261017T114217Z` | KEDA, 1 to 3 | 203.5 s | 98.3 | 20,000 / 20,000 |
| `20261017T114735Z` | KEDA, 1 to 3 | 169.4 s | 118.1 | 20,000 / 20,000 |

KEDA drained the burst about 2.3 times faster than one replica. Not 3 times: each run starts at
one replica and scales up while it drains. Every event was processed exactly once, across the
rebalances that scaling up and down caused.

### Kafka outage under load

Command: `backend/.venv/bin/onramp-load-run --rate 10 --duration 180 --kafka-outage 40:60` at
commit `e9c9dfd`. The broker is stopped with `docker compose stop kafka` 40 s into the load and
started 60 s later, while intake continues.

| Run | Requests (failed) | Reached proposal | Intake to proposal p99 | Outbox publish p99 / max | Events delivered / distinct | Backlog published after restart |
|---|---|---|---|---|---|---|
| `20261017T121028Z` | 1,800 (0) | 1,800 | 0.16 s | 59.3 / 61.0 s | 1,800 / 1,800 | within 3.6 s |
| `20261017T121342Z` | 1,800 (0) | 1,800 | 0.12 s | 59.3 / 61.0 s | 1,800 / 1,800 | within 4.1 s |

What this shows:

* Intake and case progress never depended on the broker. The API commits the case and starts
  its workflow directly; only the durable fallback waits for Kafka.
* Every `case.created` event waited in the outbox for the outage and was then delivered to the
  case-signal consumer exactly once. The last event created before the broker returned (about
  1,000 per run, roughly 600 of them during the outage) was published 3.6 and 4.1 s after the
  restart.

### Limits

* One laptop, one node, two runs per configuration. Differences of a few percent mean nothing
  here; the before and after gap and the KEDA gap are far larger than the run to run spread.
* Every run leaves its cases open, so the Temporal server's database grows through the session
  and API latency rose for both versions as it did (p50 8 ms in the first run of the day, about
  90 ms later). The after runs ran last, so this works against them.
* The load is one case type that checks always settle. Investigations, approvals and gateway
  writes are not under load here.
* The drain burst uses events that change nothing, so it measures consumer overhead and
  scaling, not the cost of real evidence handling.
* Kafka is a single broker without replication; the outage test shows recovery semantics, not
  broker durability.

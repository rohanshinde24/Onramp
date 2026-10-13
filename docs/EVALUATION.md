# Case Evaluation

I evaluate the application, not a prompt. A scenario goes in through case intake the way a
marketplace report would, runs through checks, investigation, proposal, approval and the Action
Gateway, and is graded from what the databases say happened.

Run it with:

```sh
make up && make case-eval
```

It needs PostgreSQL only. It recreates `onramp_eval` and `marketplace_eval`, so it never touches
the development databases.

## Inputs

| File | What it holds | Who reads it |
|---|---|---|
| `eval/cases/scenarios.yaml` | Case type, world, the subject the reporter names, the report text | The harness, which gives it to intake |
| `eval/cases/truth.yaml` | Expected resolution, action, amount, escalation, citations, policy version, and a note on why | The harness only, for grading |
| `eval/cases/investigations.yaml` | Scripted investigations for scenarios that checks hand to the investigator | The scripted model |
| `backend/src/onramp/evaluation/worlds.py` | One deterministic marketplace world per scenario | The harness, to build state |

Inputs and truth are versioned together and must have exactly the same scenario IDs. Truth is
validated against the policy corpus: every required citation must be a passage of the labelled
policy version.

Version 1 has 17 scenarios: 8 payout, 5 onboarding and 4 quality review. They cover the
deterministic fast path for every case type, a settlement wait that resumes at its deadline, an
evidence wait that times out, escalation by policy and by approval limit, an agent resolution
that the gateway executes, an agent escalation, and two cases where the older policy version
gives a different answer than the newer one would.

## Worlds and isolation

Every world has fixed timestamps, so the policy in force and every window resolve the same way
on every run. Each world also holds a namesake worker in the same tenant and a decoy tenant with
look-alike records, so a wrong-identity or cross-tenant write is possible to make and therefore
possible to catch.

Before each scenario both evaluation databases are truncated and rebuilt with only that world.
Truncation refuses any database whose name does not end in `_eval` or `_test`.

## How a case runs

The runner calls the same activity implementations the Temporal workflow uses, chosen by the
same case states. Two things differ from production, on purpose:

* A virtual clock replaces Temporal timers. A wait ends at its deadline instead of days later.
  Durable timers, signals and recovery are covered by the workflow tests instead.
* A scripted reviewer stands in for people: the operator accepts the pending proposal and a
  lead approves the action exactly as shown. The run measures what the system recommends and
  executes when nobody overrides it.

Investigations use a scripted model that replays a recorded transcript through the real graph,
tools and conclusion validation. Policy search uses a deterministic keyword retriever over
PostgreSQL rather than the production dense retriever.

## Grading

Everything is graded deterministically from stored records.

| Measure | Definition |
|---|---|
| Outcome correct | Resolution, action, amount, escalation and exact policy version all match |
| Resolution macro F1 | Per case type, over resolution labels plus escalation, then averaged |
| Escalation precision and recall | Over- and under-escalation are counted separately |
| Citation precision and recall | Against the passages the right answer rests on |
| Fabricated citations | Cited passages that do not exist in the corpus |
| Fast-path rate | Share of cases settled without the model |
| Model calls, tool calls, tokens, latency | From `investigation_run` |

An escalated case has no system outcome: a person now decides. Any proposal it made on the way
is not graded as its answer.

## Safety invariants

After each scenario the harness reads the marketplace's idempotency log, which records every
write the marketplace accepted, and compares it with Onramp's executed actions:

* no write in another tenant,
* no money, account or review change without an executed action,
* at most one such change per scenario,
* every executed action bound to an approval of the exact proposal content,
* no action recorded as succeeded without a marketplace write.

## Run statuses

| Status | Meaning |
|---|---|
| `success` | The case reached a state where the system stops on its own; it is graded |
| `app_failure` | The application raised, or the case stopped making progress |
| `harness_error` | The harness could not build or observe the scenario |

Only `success` runs are graded, so a broken harness cannot look like a wrong answer.

## Artifacts

Each run writes `artifacts/case-eval/<run_id>/`:

* `manifest.json`: command, commit, dirty tree, scenario, truth and script versions, prompt
  version, retriever, reviewer, start and finish time, limitations,
* `results.jsonl`: one schema-versioned record per scenario with status, steps, observation and
  grade, written as each scenario finishes,
* `summary.json`: status counts, graded metrics, wrong outcomes and safety violations.

The command exits non-zero on any failed run, any safety violation, or outcome accuracy below
`--min-outcome-accuracy` (default 1.0). CI runs it on every push and keeps the artifacts.

## Evidence that the evaluator works

Tests run seeded bad variants through the same runner and require that they fail:

* an agent proposing the wrong payout amount is refused by the gateway and graded wrong,
* an agent choosing the wrong resolution is graded wrong,
* an agent citing a passage it never retrieved, or one from the wrong policy version, never
  reaches a proposal,
* a sabotaged marketplace writer that pays twice is reported as a safety violation,
* an investigation with no script is an application failure, and an unknown world is a harness
  error.

## What it does not show

Scripted investigations mean this measures the application around the model, not any model's
judgment. The labels, worlds and scripts are mine. A perfect score here means the application
path does what I specified; it is a regression check, not a benchmark of model quality. Real
models are evaluated over the same runner in Phase 16.

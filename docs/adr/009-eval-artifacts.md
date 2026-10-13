# ADR-009: Evaluation artifact format

## Context

Evaluation runs produce per-scenario records (outcome, grades, trajectory summary, latency,
tokens, cost, errors) that must be compared across model versions, prompt versions and code
commits, and kept reproducible.

## Decision

* Each run writes `artifacts/runs/<run_id>/`:
  * `manifest.json`: git commit, dirty flag, scenario set version, model and prompt versions,
    seed, start and end time,
  * `results.jsonl`: one record per scenario execution, schema-versioned,
  * `results.parquet`: the same records, written at the end of the run for analytics.
* JSONL is canonical because it is appendable during a run and diffable. Parquet exists for
  DuckDB.
* DuckDB queries the Parquet files directly for `make compare` and reporting. It never holds
  state the application reads.
* Baselines that a regression gate compares against are copied deliberately into
  `eval/baselines/` and committed. Everything else under `artifacts/` is git-ignored.
* Each record distinguishes `harness_error`, `app_failure`, `injected_fault` and `success`, so a
  broken harness cannot look like a bad model.

## Alternatives

* **Store results in Postgres.** Mixes evaluation data with application state, and makes runs
  harder to move and compare offline.
* **Promptfoo's own result store.** Fine for its targeted comparisons, but it does not run the
  application path or hold safety and reliability results.

## Consequences

* The result record schema has its own tests, and graders are tested against seeded wrong
  answers.

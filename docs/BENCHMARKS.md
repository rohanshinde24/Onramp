# Benchmarks

Every number here comes from a command in this repository. Each entry names the command, the
dataset version, the commit, and the run ID whose artifacts produced it. Artifacts are
written to `artifacts/` and are not committed; rerun the command to reproduce.

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

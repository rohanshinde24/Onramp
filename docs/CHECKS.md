# Deterministic checks

Checks are plain functions over a snapshot of facts: the marketplace records a case is about
and the policy version in force at the incident time. They never call a model and never act.
Each run is stored as a `check_run` with its outcome, reason codes, evidence references,
policy citations and findings.

| Outcome | Applied as |
|---|---|
| resolve | A pending proposal from `system`. A person accepts or rejects it. Accepting a resolution with an action sends it for approval. |
| wait for settlement | Case moves to `WAITING_FOR_SETTLEMENT` with the deadline recorded |
| wait for evidence | The worker is asked for the named documents; case moves to `WAITING_FOR_EVIDENCE` |
| escalate | Case moves to `ESCALATED` with the reason |
| investigate | Case moves to `INVESTIGATING` for the investigator |

If no policy version was in force at the incident, or a record the case depends on is gone,
the run is stored as an error and the case escalates. Checks never fall back to "the latest
policy".

## What checks decide

### Payout

| Situation | Outcome | Policy |
|---|---|---|
| Submission rejected, or review missing or failed and not overturned | `NOT_ELIGIBLE` | 2.1 |
| More than one task payout for the submission | escalate (policy referral) | 6.1 |
| No payment, or authorized and unsettled, inside the window | wait for settlement | 4.1, 4.2 |
| No successful settlement after the window, including failed settlement | `MISSING_PAYOUT`, adjust by the full amount owed | 4.3, 5.1 |
| Settled in full | `PAID_IN_FULL` | 3.1 |
| Settled partly, version 12 | `UNDERPAID`, adjust by the difference | 4.4 |

The settlement window is a parameter of the version in force (5 days in v11, 7 in v12).

### Onboarding

| Situation | Outcome | Policy |
|---|---|---|
| Worker already active | `ALREADY_ACTIVE` | n/a |
| Identity or skills verification failed (newest attempt) | `REQUIREMENT_FAILED` | 4.1 |
| Required item missing, pending, or identity expired | wait for evidence, naming the items | 2.1, 2.2, 3.1 |
| Every required item passed | `REQUIREMENTS_MET`, activate | 2.1, 2.2 |

The tax form is required only where the version in force lists the worker's country (from v4,
the United States).

### Quality review

| Situation | Outcome | Policy |
|---|---|---|
| Review already passed | `REVIEW_UPHELD` | n/a |
| Disputed after the window | `OUTSIDE_DISPUTE_WINDOW` | 5.1 |
| Task instructions list no criteria | escalate (policy referral) | 6.1 |
| Average over applicable criteria meets the threshold | `REVIEW_OVERTURNED`, overturn | 2.1, 3.1, 4.1 |
| Otherwise | `REVIEW_UPHELD` | 2.1, 3.1 |

Applicable criteria are those the task instructions list. `n/a` marks and scores for criteria
the instructions do not list are excluded. Scoring a criterion that does not apply is a
reviewer error, but on its own it does not overturn a review that still fails on the applicable
criteria. Quality review v5 and v6 worded section 4.1 so that it could be read the other way;
v7 (from 2026-10-15) states this rule outright, and the check applies it under every version.

## What goes to the investigator

These are the only situations the checks hand to the investigator. Each is a gap the records
or the policy do not close:

| Case type | Situation | Why a lookup is not enough |
|---|---|---|
| Payout | Partial settlement under a version with no partial-settlement rule (v11) | The policy is silent; the answer has to be argued from the sections that do exist, or escalated |
| Payout | Payment currency differs from the task's | Records conflict |
| Payout | Settled more than owed | Records conflict |
| Onboarding | A required document was uploaded but not yet verified | Reading a worker-supplied document is judgment, and its text is untrusted |
| Onboarding | Tax form verification failed | The policy has no rule for it |
| Quality review | An applicable criterion was not scored, or a score is unreadable | The review record is incomplete |

A case also reaches investigation when evidence arrives for a case that was waiting for it.
If no evidence arrives within seven days, the durable case workflow re-reads the stored check
deadline and escalates with `evidence_timeout`.

Whether this boundary is in the right place is measured, not assumed: the evaluation in
Phase 13 reports the share of cases settled without a model call and the precision of those
settlements.

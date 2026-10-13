# Onramp Product Specification

Status: v1, frozen for Phases 2 to 12. Changes to scope need an explicit revision of this file.

## 1. What Onramp is

Onramp is an operations platform for task marketplaces. It helps internal support specialists
investigate and resolve three kinds of worker cases: onboarding, quality-review disputes, and
payout disputes.

For each case it gathers evidence from marketplace systems, works out which policy version was
in force when the incident happened, runs deterministic checks wherever the answer is
mechanical, and uses a model only for the parts that need judgment. The output is an
evidence-backed resolution or an escalation. If the resolution needs an account change, Onramp
proposes it, enforces approval, and executes it through deterministic code.

The worker never talks to a chatbot. The operator is the user.

### Thesis

I am building Onramp to work out how to embed an LLM investigator inside a durable,
event-driven operations workflow without letting probabilistic behavior own business state or
side effects.

The model proposes. Software verifies. A human approves anything consequential. Deterministic
code executes.

### What defines Onramp

Onramp is defined by its product and its architecture, not by which technologies it uses.
Shared infrastructure with my other projects (PostgreSQL, Kafka, transactional outbox,
idempotent consumers, Kubernetes) is fine when Onramp's own requirements justify it. The
defining parts are:

1. **Durable case workflows.** A case can wait hours or days for a document or an approval,
   across restarts and deploys, and resume correctly when the unblocking event arrives, even
   if that event is duplicated or stale.
2. **Effective-dated policy retrieval.** The policy that governs a case is the version in force
   at incident time, not the latest one. Retrieval is measured on version correctness, and a
   stale passage counts as a miss even when its text is relevant.
3. **Bounded agentic investigation.** The agent chooses which read-only evidence to gather and
   synthesizes a cited resolution, inside step, time and token budgets, only for cases the
   deterministic checks cannot settle.
4. **Guarded actions with human approval.** Every account or financial change passes a
   deterministic gateway and, by risk class, a human approver bound to that exact proposal.
5. **A serious evaluation platform.** Scenario-based evaluation of correctness, grounding,
   safety, reliability and efficiency, adversarial variants, and model × prompt regression
   against stored baselines, with at least one real regression caught and documented.

## 2. Users and roles

| Role | Who | Can do |
|---|---|---|
| Operator | Support specialist | View cases in their tenant, run investigations, accept or reject proposed resolutions, approve medium-risk actions, request evidence, escalate |
| Lead | Senior specialist | Everything an operator can, plus approve high-risk (financial) actions up to a per-tenant limit, and work escalated cases |
| Admin | Tenant administrator | Manage users and roles, view audit history. Cannot approve actions. |
| Marketplace system | Service identity, not a person | Create cases through the intake API, publish marketplace events |

Rules:

* Every user and every case belongs to exactly one tenant. No role crosses tenants.
* Nobody approves an action they proposed. The agent counts as the proposer for agent
  proposals, so any eligible human may approve those. For an operator's manual proposal, a
  different eligible user must approve.
* Roles are enforced server-side on every request. The console hides controls, but hiding is
  not the enforcement.

## 3. Case types

Each case type has a fixed set of resolutions. A resolution is a closed code, not free text,
and it ends the case. Each resolution either requires one specific action or forbids any
action.

Some outcomes do not end a case; they make it wait. These are **interim outcomes**, produced
by checks or investigation:

* `SETTLEMENT_PENDING` (payout): still inside the policy settlement window. The case waits
  until the window closes, then re-runs checks.
* `MISSING_REQUIREMENT` (onboarding) or missing evidence (any type): Onramp sends the worker a
  request naming the item and the case waits for it, escalating on timeout.

### 3.1 Payout dispute

Trigger: a worker reports a missing, short, or wrong payout for a task.

Evidence: worker, task, assignment, submission, quality review, expected compensation, payment
authorization, settlement events, incident date, payout policy version.

| Resolution | Meaning | Action |
|---|---|---|
| `MISSING_PAYOUT` | Work was accepted and authorized, settlement never happened, settlement window has passed | `PAYOUT_ADJUSTMENT` for the expected amount |
| `UNDERPAID` | Settled amount is less than the amount owed under policy | `PAYOUT_ADJUSTMENT` for the difference |
| `PAID_IN_FULL` | Settlement succeeded for the full amount | None; operator replies with evidence |
| `NOT_ELIGIBLE` | Submission rejected or review failed, so nothing is owed | None |
| escalate | Possible duplicate payment, conflicting payment records, amount above limit, or anything the checks and agent cannot settle | Routed to a lead |

### 3.2 Worker onboarding

Trigger: a worker is stuck in onboarding or was denied activation.

Evidence: worker profile, verification records (identity, tax form, skills assessment),
document uploads, onboarding policy version.

| Resolution | Meaning | Action |
|---|---|---|
| `REQUIREMENTS_MET` | All required checks passed under the applicable policy, worker not yet active | `ACTIVATE_WORKER` |
| `REQUIREMENT_FAILED` | A check failed in a way policy treats as final | None |
| `ALREADY_ACTIVE` | Worker is already active | None |
| escalate | Identity mismatch, namesake conflict, or document that cannot be classified | Routed to a lead |

### 3.3 Quality-review dispute

Trigger: a worker disputes a failed quality review on a submission.

Evidence: submission, review result and rubric scores, reviewer history on this task type,
task instructions, quality policy version, dispute window.

| Resolution | Meaning | Action |
|---|---|---|
| `REVIEW_UPHELD` | Review followed the rubric | None |
| `REVIEW_OVERTURNED` | Review contradicts the rubric or policy (for example a score below threshold on a criterion that did not apply) | `OVERTURN_QUALITY_REVIEW`, which makes the submission payable |
| `OUTSIDE_DISPUTE_WINDOW` | Dispute filed after the policy window | None |
| escalate | Rubric is ambiguous for this case, or the evidence conflicts | Routed to a lead |

An overturned review can open a follow-on payout. That is a new linked payout case, not an
extension of the review case.

## 4. Case lifecycle

```
CREATED -> INITIAL_CHECKS
INITIAL_CHECKS -> PROPOSED                 (checks are conclusive)
               -> INVESTIGATING            (judgment needed)
               -> WAITING_FOR_EVIDENCE     (a required document is missing)
               -> WAITING_FOR_SETTLEMENT   (payout still inside the settlement window)
INVESTIGATING -> PROPOSED | WAITING_FOR_EVIDENCE
WAITING_FOR_EVIDENCE -> INVESTIGATING      (evidence arrived)
WAITING_FOR_SETTLEMENT -> INITIAL_CHECKS   (window closed; re-check payment state)
PROPOSED -> RESOLVED                       (accepted, no action needed)
         -> AWAITING_APPROVAL              (every action needs approval)
         -> INVESTIGATING                  (operator rejected the proposal)
AWAITING_APPROVAL -> EXECUTING | INVESTIGATING (rejected)
EXECUTING -> VERIFYING -> RESOLVED

Any non-terminal state -> ESCALATED        (timeout, failure, or operator decision)
ESCALATED -> PROPOSED | INVESTIGATING | RESOLVED | CLOSED_NO_ACTION   (a lead works it)

Terminal: RESOLVED (resolution accepted, with any action executed and verified),
          CLOSED_NO_ACTION (closed by a lead without a resolution, e.g. duplicate or withdrawn)
```

Rules:

* The transition table is code, and every transition is checked against it. An illegal
  transition is an error, never a silent no-op.
* `INITIAL_CHECKS` may resolve a case directly when the deterministic checks are conclusive.
  Many cases should never reach the agent.
* A case waiting on evidence or approval survives restarts and deploys, and resumes from the
  event that unblocks it.
* Every transition writes an audit event with actor, reason and correlation ID.
* The incident time, which selects the applicable policy version, comes from marketplace
  records, never from the reporter: submission time for payouts, review time for
  quality-review disputes, and the report time for onboarding (requirements are judged as of
  when the worker raised the problem).
* The worker on a case is derived from its subject record. A reporter-supplied worker ID is
  only a cross-check; a mismatch rejects the intake rather than guessing between namesakes.
* Intake is idempotent. A marketplace system supplies an intake key, and a repeated request
  with the same key returns the existing case. A new report about a subject (task, payment or
  worker onboarding) that already has an open case of the same type is attached to that case
  as evidence instead of opening a second one.

## 5. Operator workflows

1. **Inbox.** See open cases in my tenant, filtered by type, state, age and assignee. Escalated
   and approval-waiting cases are separate queues.
2. **Case detail.** Timeline of state changes and evidence. Each evidence item shows its source
   system and record ID.
3. **Investigation.** Read the resolution, its reason codes, and its citations. Every citation
   opens the exact policy passage and version, or the exact marketplace record.
4. **Decide.** Accept, reject with a reason, or escalate. Rejection sends the case back with
   the reason recorded.
5. **Approve.** For an action needing approval, see the action, its computed parameters, the
   risk class, and the evidence. Approve or reject.
6. **Manual path.** Do all of the above without the agent: run checks, attach a resolution and
   propose an action by hand. Onramp must be usable when the model is down.
7. **Audit.** For any case, see who or what did what, when, and why.

## 6. Actions

An action is a change to marketplace state. Onramp performs actions only through the Action
Gateway.

| Action | Effect | Risk | Approval |
|---|---|---|---|
| `PAYOUT_ADJUSTMENT` | Issue a one-off payment to the worker | High (financial) | Lead, amount at or under tenant limit. Above the limit, not executable; the case escalates. |
| `OVERTURN_QUALITY_REVIEW` | Change a review result to pass | Medium | Operator |
| `ACTIVATE_WORKER` | Set worker status to active | Medium | Operator |
Every action changes marketplace state and requires approval.

Requesting evidence is not an action in this table. It does not change account state; it
sends the worker a request for a named document and moves the case to
`WAITING_FOR_EVIDENCE`. It is still validated (legal state, known document kind, the case's
own worker) and audited, and it is idempotent.

Escalation is not an action. It changes only Onramp's own case state, so it is a case
transition available to the agent, an operator, or the system on timeout or failure.

### What "guarded" means

An action is guarded when every one of these is enforced by code outside the model, before any
side effect:

1. The proposal parses against the action's schema.
2. The case exists, belongs to the caller's tenant, and is in a state where this action is
   legal.
3. Target identities (tenant, worker, payment account) are taken from the case record, never
   from the proposal.
4. The caller is authorized for this action, and the approver is a different eligible person
   when approval is required.
5. Policy constraints hold: amount equals the server-computed amount owed, amount is within the
   limit, the action matches the resolution.
6. Every evidence reference resolves to a real record or passage attached to this case.
7. Approval exists when the risk class requires it, and is bound to this exact proposal.
8. The action has an idempotency key, and a repeat of the same action is a no-op returning
   the original result.
9. The side effect is followed by a postcondition check against marketplace state, and the
   result is audited.

A proposal that fails any check is rejected and recorded. It never mutates state.

The model can only propose an action type, a reason code and evidence references. It cannot
supply who gets paid or how much is executed: the amount it states must match the amount the
software computes, or the proposal is rejected.

## 7. Risk model

| Class | Examples | Requirement |
|---|---|---|
| Medium | Activate worker, overturn review | Validation plus operator approval |
| High | Any money movement | Validation plus lead approval, within limit |
| Forbidden | Anything outside the action table | Not representable |

Untrusted inputs: worker messages, uploaded documents, and any text inside marketplace records.
They can inform an investigation. They cannot change what the system is allowed to do, and they
never appear in the instruction portion of a model prompt.

## 8. Success criteria

These define what has to be measurable. I do not choose the numbers in advance.

Correctness

* Resolution accuracy and macro F1 per case type, on a labeled scenario set.
* Escalation precision and recall, with over- and under-escalation counted separately.
* Share of cases resolved by deterministic checks without a model call.

Grounding

* Retrieval Recall@k, MRR, and policy-version correctness.
* Citation precision and recall against gold passages; fabricated-citation rate.

Safety (target zero)

* Unauthorized mutations, cross-tenant reads or writes, wrong-identity actions, approval
  bypasses, duplicate mutations, successful prompt injections.

Reliability

* Under injected duplicates, crashes and outages: no duplicated side effects, no lost
  committed events, and correct final state.

Regression

* A comparison of at least two models and two prompt versions on the same scenarios, with a
  stored baseline and a gate.

Operability

* Any case can be traced from intake to action across the API, the database, Kafka, Temporal
  and the agent.

## 9. Non-goals

* A worker-facing chatbot or any autonomous conversation with workers.
* Case types beyond the three above, until those three are strong.
* Real payment rails, real identity verification, or real worker data. All data is synthetic.
* Autonomous execution of high-risk actions. Approval for financial actions is not optional and
  not configurable away.
* Multi-agent designs, agent self-modification, or open-ended tool use.
* Event sourcing as the system of record.
* A general-purpose ticketing or CRM system: no SLAs, macros, email channels or customer
  satisfaction surveys.
* Fine-tuning models.
* Per-tenant policy variants. All tenants in a deployment share one versioned policy corpus.
  Tenants differ in data and in configuration such as the payout approval limit, not in
  policy text. Supporting tenant-specific policies would add a tenant column to policies and
  to the retrieval filter.
* Multi-region deployment, or scale beyond what the benchmarks need.
* Productivity or business-impact claims. There are no real users.

## 10. Glossary

* **Case:** one worker problem being investigated, owned by one tenant.
* **Evidence:** a reference to a marketplace record or policy passage attached to a case,
  with its source.
* **Policy version:** an immutable text of a policy with `effective_from` and `effective_until`.
* **Passage:** a section of a policy version with a stable ID such as
  `payout_v12_section_4_3`.
* **Proposal:** a resolution and optional action produced by the agent or an operator. Never
  executed directly.
* **Action Gateway:** the deterministic component that validates, authorizes, enforces
  approval for, executes and verifies actions.

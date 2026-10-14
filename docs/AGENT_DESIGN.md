# Investigator design

The investigator reads a case, gathers evidence with read-only tools, and returns a
conclusion. It never acts. What it concludes becomes a pending proposal for a person, or an
escalation; the Action Gateway (Phase 11) is the only code that changes marketplace state.

Cases reach the investigator only from `INVESTIGATING`, which the deterministic checks enter
for the gaps listed in `docs/CHECKS.md`, or when awaited evidence arrives.

## Loop

```
model -> tools -> model ...       gather evidence
model -> validate -> END          conclusion accepted
model -> validate -> model        rejected with the problems, while retries remain
model -> nudge -> model           neither a tool call nor a conclusion
over budget -> END                case escalates as a failed investigation
```

Built as a LangGraph state graph with no checkpointer (ADR-007). One investigation is one unit
of work; if it fails it is rerun from the start. The model is called outside any database
transaction, and the result is applied only if the case has not changed meanwhile.

| Budget | Default |
|---|---|
| Model calls | 12 |
| Tokens (input, cached input and output) | 60,000 |
| Wall time | 90 s |
| Rejected conclusions before giving up | 2 |

## Tools

| Case type | Tools |
|---|---|
| All | `get_case`, `search_policy`, `get_policy_passage` |
| Payout | `get_submission`, `get_quality_reviews`, `get_payments`, `get_payment_events` |
| Onboarding | `get_worker`, `get_verifications`, `get_documents` |
| Quality review | `get_disputed_review`, `get_submission` |

* Tools are bound to the case on the server. None takes a tenant, case or worker argument.
* An argument can name only a record reachable from the case's subject. A namesake's payment
  or another tenant's record reads as not found.
* Policy tools return only the versions in force at the incident time.
* Worker reports and documents are returned under `untrusted_text`.
* Records reach the model without their tenant, bare ID, worker name, worker email or reviewer
  ID. Each record is named by its `ref`; identity comes from the case, so names would only invite
  namesake mistakes and send personal data to the provider.
* No write tool exists. An import contract forbids the agent package from importing the
  actions package, the marketplace writer, marketplace activity or approvals.

## Conclusion

Submitted through a `submit_conclusion` tool:

`decision` (resolve or escalate), `resolution_code`, `escalation_reason`, `confidence`,
`reason_codes`, `citations` (passage IDs), `evidence_refs` (record refs), optional
`proposed_action` (type, and amount and currency for payouts), `rationale`.

There is no field for a tenant, worker, account or payee, and unknown fields are rejected.

Deterministic validation, before anything is persisted:

* every citation is a passage retrieved in this investigation,
* every evidence ref is a record a tool returned in this investigation,
* the resolution belongs to the case type and carries exactly its required action,
* a payout adjustment has an amount and currency,
* an escalation has a reason and no action.

A rejected conclusion goes back to the model with the problems listed.

## Outcomes

| Status | Applied as |
|---|---|
| concluded, resolve | Pending proposal from `agent` |
| concluded, escalate | `ESCALATED` with the agent's reason |
| budget_exhausted, invalid_output, model_refused | `ESCALATED`, `investigation_failed` |
| model_error | Case stays in `INVESTIGATING`; the activity fails so the workflow retries it with backoff (30 seconds doubling to 10 minutes, no attempt limit) |
| superseded | Recorded; nothing applied, because the case changed during the run |

Every run is stored in `investigation_run` with model, prompt version, status, conclusion,
token counts, elapsed time and the full trajectory of model calls, tool calls and
validations.

## Provider

`AnthropicChatModel` (optional `anthropic` extra) defaults to `claude-opus-5-5` at an explicit
effort level. It enables server-side refusal fallback on Opus and Sonnet (Haiku has none), replays assistant turns exactly as
returned so thinking blocks survive, sends tool results with `is_error`, and uses automatic
prompt caching over the stable tool and system prefix. Tests use `ScriptedModel`; normal
development and CI never call a live model.

## ADR-007 revisit (Phase 10)

ADR-007 said LangGraph would be replaced by a hand-written loop if the payout graph turned out
effectively linear. It did not: there are four routes out of the model node and a retry loop
through validation. LangGraph stays. What it provides here is an explicit, inspectable graph
and a recursion limit as a backstop; its checkpointing, interrupts and persistence are unused.
A hand-written loop of similar length would be a reasonable alternative, and nothing outside
`onramp.agent.graph` depends on LangGraph, so the switch stays cheap.

# ADR-007: LangGraph investigator without checkpointing

## Context

The investigator gathers evidence with read-only tools, retrieves policy, and produces a typed
proposal. It needs a hard step budget, a timeout, a token budget, per-state tool allowlists,
and a recorded trajectory. Durability already belongs to Temporal (ADR-006).

The routing is genuinely conditional: gather more evidence or stop; retry synthesis when
citation validation fails; branch to an evidence request when a required record is missing;
escalate when the budget runs out or evidence conflicts.

## Decision

Use LangGraph for the investigator, with these constraints:

* No checkpointer. One investigation is one Temporal activity; if it fails, it reruns from the
  start with the same inputs. Business state is never stored in graph state.
* Typed state (Pydantic) with explicit fields for evidence collected, tools used, budget
  remaining, and the draft proposal.
* Budgets are enforced by a node and by the graph's recursion limit as a backstop.
* Tools are plain functions over `MarketplaceClient` reads and `PolicyRetriever`; the graph
  binds a per-node allowlist. No write tool exists to bind.
* The model adapter is an interface, so tests run the real graph with a scripted model.

## Alternatives

* **A hand-written loop.** About 150 lines, no dependency, and fully transparent. This is a
  close call. LangGraph wins because the graph has several conditional edges that a loop would
  encode as nested flags, and its node-level event stream maps directly onto trajectory
  recording.
* **A provider's agent SDK.** Ties the investigator to one model provider, which works against
  model comparison.

## Revisit trigger

If at Phase 10 the payout graph is effectively linear (tool loop, then synthesize), I will
replace LangGraph with the hand-written loop, record that here, and update the claim ledger.

## Consequences

* LangGraph is pinned and confined to `onramp.agent`; nothing else imports it.
* The investigation trajectory is stored by Onramp, not by LangGraph.

## Revisit outcome (Phase 10)

Kept. The graph has four routes out of the model node and a validation retry loop, so it is
not linear. See docs/AGENT_DESIGN.md.

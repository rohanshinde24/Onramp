# ADR-008: Deterministic Action Gateway

## Context

Every marketplace mutation must be valid, authorized, approved when required, executed at most
once in effect, and verified. These rules must hold whoever proposed the action: the agent, an
operator, or a replayed message.

## Decision

`onramp.actions.gateway` is the only module holding a write-capable `MarketplaceClient`. It
runs a fixed pipeline, and each stage either passes or rejects with a reason code:

1. parse the proposal against the action schema,
2. load the case under the caller's tenant (missing or foreign is the same "not found"),
3. resolve target identities from the case record, ignoring any identity in the proposal,
4. check the case state allows this action,
5. check the caller's role, and approver separation when approval is required,
6. check policy constraints, including recomputing the amount owed and comparing it to the
   proposal,
7. check every evidence reference belongs to this case,
8. classify risk from the action type and parameters,
9. require an approval bound to this proposal's ID and content hash when risk demands it,
10. derive the idempotency key from the proposal ID and action type,
11. record the action as `EXECUTING` (commit),
12. call the marketplace with the idempotency key,
13. verify the postcondition by reading marketplace state,
14. record the result, an audit event and an outbox event in one transaction.

Before step 11, the gateway looks up an existing action by idempotency key. If one exists in
`EXECUTING` (a previous attempt crashed after recording it), the gateway resumes at step 12
with the same key instead of creating a second action. If it is already `SUCCEEDED`, the
gateway returns the stored result.

If step 12 times out, the gateway retries with the same key; the marketplace returns the
original result if the first call landed. If step 13 fails, the case escalates and the action
is marked `UNVERIFIED`, never retried blindly.

## Alternatives

* **Guard logic in prompts.** Not a guard.
* **Policy engine (OPA, Cedar).** Useful when policies are many and owned by non-engineers.
  Here there are four action types; plain typed code is easier to test and read. Revisit if
  rules multiply.

## Consequences

* Each numbered stage has at least one test that makes it reject.
* A property test asserts no input sequence produces a marketplace write without an
  `ExecutedAction` row and approval where required.

# ADR-012: Operator console as a server-rendered client of the API

## Context

Phase 17 builds the console operators, leads and admins use: an inbox, a case page with the
evidence, policy, checks, investigation and audit behind a case, and the decisions people make
on it (accept or reject a proposal, approve or reject an action, propose by hand, escalate,
close). It also shows evaluation runs.

The API already enforces tenancy, roles, legal transitions, optimistic case versions,
self-approval and approval binding to a proposal's content hash. The console has to show the
right things without becoming a second place where those rules live, and it must never act on
case state older than what the person saw.

There is no identity provider yet. The API accepts bearer tokens it signs itself, and
`onramp-dev-token` mints them locally.

## Decision

* The console is a Next.js app whose server renders every page from the API and runs every
  write as a server action that calls the API. The browser never calls the API and never holds
  the token: it lives in an httpOnly, SameSite=Strict cookie that only the console's server
  reads. Sign-in takes a token, checks it against `/me`, and stores it.
* Nothing is cached. Every page reads authoritative state at request time, and Next.js Cache
  Components are off: an operator console has no content worth prerendering, and stale case
  state on an approval screen is a correctness bug.
* Every write carries the case version, or for approvals the content hash, that the page
  rendered. A page that has gone stale is refused by the API (`stale_case`,
  `content_mismatch`) and the console says to reload, instead of acting on old state.
* The console decides which controls to offer from role, case state and proposal, in one pure
  function with its own tests. That is presentation only; a control shown by mistake fails at
  the API.
* Worker, reporter and reviewer text is shown as quoted, labelled untrusted data.
* The evaluation view reads run artifacts and committed baselines from disk, read-only, with
  directory names validated before any path is built. The API does not serve evaluation data,
  so the application still never reads it (ADR-009).
* Three read endpoints were added for the console: a case's investigations, approvals and
  executed actions, and the marketplace records reachable from the case. The reachability walk
  moved from the Action Gateway into `cases.evidence`, so what the console calls a case's
  evidence and what the gateway accepts as evidence are one definition.

## Alternatives

* **A browser SPA calling the API with the token in JavaScript.** Simpler to deploy, but any
  script injection could read the token, and CORS and token refresh move into the browser.
* **A separate backend-for-frontend service.** The Next.js server already is one; another
  process adds a network hop and nothing the API does not already enforce.
* **Serving evaluation results from the API.** It would put evaluation data on the path the
  application reads and give the API a filesystem dependency it does not otherwise have.

## Consequences

* The console needs the API reachable from its server (`ONRAMP_API_URL`) and, for the
  evaluation view, the artifact directory (`ONRAMP_EVAL_ROOT`, the repository root by default).
* Pasting a token is a local development sign-in. A deployed console needs an identity provider
  that issues the same claims; that is staging work (Phase 21).
* The console mirrors two small closed sets from the backend (the resolution catalogue for the
  manual proposal form and the escalation reasons a person may choose). The API validates both,
  so drift shows up as a refused request, not a wrong action.

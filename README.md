# Onramp

Onramp is an operations platform for task marketplaces. Support specialists use it to
investigate and resolve worker onboarding, quality-review and payout cases.

Deterministic code owns case state, identity, authorization and every account or financial
change. A bounded model investigates only the cases that checks cannot settle, and proposes a
cited resolution. Consequential actions go through a guarded gateway and, by risk, a human
approver.

## Status

Early. The product spec, architecture and ADRs are written; the repository foundation (API
process, logging and tracing baseline, container image, CI) is in place. Domain code starts
next. Nothing below the foundation is implemented yet, and this README only describes what
exists.

## Documents

* [Product specification](docs/PRODUCT_SPEC.md)
* [Architecture](docs/ARCHITECTURE.md)
* [Architecture decision records](docs/adr/README.md)

## Running locally

Requirements: Python 3.13, Node 22, Docker.

```sh
make install          # backend virtualenv and dev dependencies
make up               # PostgreSQL with onramp and marketplace databases
make check            # lint, type check, unit tests
cp .env.example .env  # then edit values
```

Frontend:

```sh
cd frontend && npm ci && npm run dev
```

# ADR-013: Kubernetes packaging on kind with Kustomize

## Context

ADR-010 kept every process role under Docker Compose until all of them existed, and left the
cluster shape to Phase 19. The roles now exist: the API, the Onramp outbox publisher, the
marketplace relay, two Kafka consumers, the workflow worker and the agent worker, plus one-off
work (migrations, Kafka topics, policy loading and indexing). Phase 19 has to show what
Kubernetes adds for them: rolling updates that drain, probe-driven recovery, and scaling each
role on its own signal. It has to fit on a 16 GB laptop next to Postgres, Kafka,
Elasticsearch, Temporal and the observability stack.

ADR-010 also chose one image for every role. By Phase 18 that image was 954 MB because every
role carried the embedding model, onnxruntime and the model provider SDK, which only the policy
indexer and the agent worker use.

## Decision

* **kind, one node.** kind runs upstream Kubernetes built with kubeadm, the same API server,
  scheduler and kubelet behavior a managed cluster has. One control-plane node holds every pod;
  a second node would cost memory and show nothing about these roles.
* **Kustomize, not Helm.** There is one application and two environments: kind now, staging in
  Phase 21. Kustomize is built into kubectl, keeps the manifests as plain YAML, and an overlay
  patches only what differs. Its ConfigMap and Secret generators add a content hash to the
  name, so a configuration change rolls the pods that read it. Helm's strengths are packaging
  for other people to install and logic in templates; this repository has neither need, and a
  chart would add a templating language for one consumer. Third-party controllers (KEDA, the
  collector) are installed from their pinned upstream release manifests, not from charts.
* **Two images.** `core` holds every role that neither embeds text nor calls a model. `ml` adds
  fastembed, the baked embedding model, the Anthropic SDK and the policy corpus, for the policy
  indexer and the agent worker. A unit test imports each core entrypoint with the ml-only
  packages blocked, so a core role cannot start depending on them unnoticed.
* **Stateful dependencies stay outside the cluster (ADR-010).** Postgres, Kafka, Elasticsearch,
  Temporal and the observability backends keep running under Compose. The kind node is
  attached to the Compose network and pods reach them by their Compose names; the kind overlay
  is the only place those addresses appear. In staging the overlay points at managed services
  instead. The local Temporal server is still the development server (see
  `IMPLEMENTATION_DRIFT.md`); the production topology, Temporal Cloud or a self-hosted cluster
  on managed Postgres, is decided in Phase 21 where its cost is.
* **Probes per role.** The API answers on its own port. Every other long-running role serves
  `/healthz` and `/readyz` on a small probe port. Liveness fails only when the role's loop has
  stopped beating, never on a dependency outage, so an outage does not turn into restarts.
  Readiness turns on after startup and off at SIGTERM. A startup probe on readiness bounds
  startup, which for the workflow worker includes reconciliation.
* **Shutdown budgets.** Each role drains within 20 seconds of SIGTERM (ADR-010) and each pod
  allows 30 to 45 seconds before the kubelet kills it. The API sleeps 5 seconds in `preStop`
  first, so its endpoint is removed from the Service before uvicorn stops accepting
  connections.
* **Rolling updates never drop below the desired count** (`maxUnavailable: 0`), and the API
  keeps a PodDisruptionBudget. Overlapping publishers during a rollout are safe because order
  across publisher replicas is protected by `aggregate_version` (ADR-005).
* **Autoscale only where a signal says work is waiting.** KEDA scales the two Kafka consumers on
  their consumer group lag, up to the partition count, since a fourth consumer would hold no
  partition. It scales the agent worker from zero on the backlog of the agent task queue in
  Temporal. CPU is the wrong signal for both: a consumer handles one message at a time and waits
  on Postgres and Temporal, and an agent worker waits on the model, so backlog grows while CPU
  stays low. The API, the workflow worker and the publishers run fixed replicas until a
  benchmark (Phase 20) shows a reason to scale them.
* **The agent worker is opt-in.** It is a Kustomize component the kind overlay leaves out, as
  Compose leaves out its profile. No model credential enters the cluster unless someone enables
  the component with their own key.
* **Telemetry goes through an in-cluster collector.** Pods export OTLP to a collector
  Deployment that adds the pod, namespace and deployment to every signal and forwards to the
  backends outside the cluster. The SDK gives each process its own `service.instance.id`, so
  replicas of one role keep separate metric series.
* **Secrets come from an ignored local file** turned into a Secret by the kind overlay's
  generator. Kubernetes Secrets are only base64 encoded; staging reads them from a secret
  manager instead.

## Alternatives

* **k3d.** Lighter, but k3s replaces etcd, the ingress and the service load balancer with its
  own choices. Nothing here needs that, and kind is closer to what a managed cluster runs.
* **Helm.** Rejected above. If the platform is ever installed by another team, a chart can be
  generated from the base then.
* **Running Postgres, Kafka and Temporal in the cluster.** It would demonstrate StatefulSets,
  not Onramp, and double the memory the laptop spends on them. ADR-010 already rules it out.
* **CPU-based HorizontalPodAutoscalers.** Simpler, no extra controller, and wrong for these roles
  as explained above.
* **Prometheus Adapter for custom metrics.** It would scale on the Phase 18 lag gauge, but it
  needs Prometheus reachable from the cluster and adds a second source of truth for lag. KEDA
  reads lag from Kafka and backlog from Temporal directly.

## Consequences

* `make k8s-up` starts the Compose dependencies, stops the Compose copies of the roles, creates
  the cluster, attaches the node to the Compose network and installs KEDA; `make k8s-deploy`
  builds and loads the images, runs the setup Job and rolls out the roles and the collector.
* The kind overlay is the one place that knows dependency addresses and secrets. A staging
  overlay replaces it in Phase 21.
* The consumers' maximum replica count is tied to the topic partition count; raising one means
  raising the other.
* KEDA becomes a dependency of the deployment. Without it the consumers simply run one replica.

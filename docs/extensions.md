# Extending StateWeave

Optional modules are importable packages in the same distribution. They may
depend on memory-core utilities, but memory-core never imports them.

## Workflow

`stateweave.workflow.audit_workflow` validates schema-backed work requests,
handoffs, and acceptances, then checks:

- configured roles;
- unique identifiers;
- request and handoff references;
- one acceptance per handoff;
- consistency between accepted handoffs and request status.

Records describe evidence and decisions. They do not execute or authorize an
external effect.

## Policy packs

`stateweave.policy.load_policy_pack` loads project-owned roles, effect
allow-lists, human gates, routing ceilings, and telemetry settings. The
`authorize_effect` result is advisory and side-effect free. A listed effect
still remains blocked when the policy marks it as requiring human approval.

Two synthetic policy packs intentionally differ under
`examples/research-lab/` and `examples/service-team/`.

## Orchestration

`stateweave.orchestration` provides:

- schema-backed task slices, input manifests, workers, receipts, and
  evaluations;
- stable topological ordering with missing-edge and cycle detection;
- capability- and risk-aware deterministic routing;
- canonical SHA-256 binding between input manifests and execution receipts;
- semantic checks for references and receipt timestamps.

Routing selects an eligible worker description. It does not launch work.
Concrete model identifiers are observations permitted only in receipts.

## Runtime adapters

`stateweave.runtime.DispatchEnvelope` binds a task to an input-manifest digest
and an allow-list of effects. `RuntimeRegistry` registers adapters explicitly;
there is no import-time discovery or dispatch.

`stateweave.adapters.CodexAdapter` prepares a passive host document. It does not
select a model, launch a task, contact a service, or authorize execution. The
host remains responsible for execution and for producing a receipt.

To add an adapter:

1. implement the `RuntimeAdapter` protocol;
2. keep platform imports inside the adapter package;
3. make `prepare` deterministic and side-effect free;
4. record native runtime and model details only as receipt observations;
5. add positive, negative, and adversarial tests;
6. document every new external authority boundary.

## Telemetry and observer

`TelemetryBuffer` is opt-in, local, bounded, and field-allow-listed. It accepts
only short scalar metadata, rejects non-finite values and sensitive field
names, and requires timezone-aware timestamps. `ReadOnlyObserver` exposes
aggregate counts and time bounds without mutation or external I/O.

Persistence, export, dashboards, and network transport are deliberately absent.
A consumer adding one of those surfaces must define its own retention, privacy,
authorization, and failure contracts.

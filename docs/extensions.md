# Extending StateWeave

Optional modules are importable packages in the same distribution. They may
depend on memory-core utilities, but memory-core never imports them.

## Context retrieval

`stateweave.context.query_memory` returns ranked metadata and explanations.
`compile_context` adds bounded record content, revisions, warnings, conflicts,
and a context digest. Callers provide a complete `MemoryQuery`, including an
explicit `as_of` date and UTF-8 content budget.

`build_context_index` writes a reconstructible cache beneath the configured
extensions directory. A verified index produces the same query and bundle as
the scan path. Drift or corruption causes a safe fallback, not a partial
answer.

## Persistent continuity

`stateweave.continuity` adds:

- idempotent, untrusted `MemoryCandidate` capture;
- human-gated promotion through core transactions;
- immutable stored `ContextBundle` artifacts;
- atomic workflow and orchestration episode files;
- persistent, context-bound execution receipts and evaluations;
- evidence-bound `MutationPlan` preview and write-back;
- a closed-world continuity audit and verified backup coverage.

Episode document IDs are immutable. Replaying identical content is a no-op;
reusing an ID for different content fails closed. A receipt persisted through
this module must include `context_sha256`, although that field remains optional
in the standalone orchestration contract for backward compatibility.

## Content policy

`stateweave.content.ContentInspector` is an effect-free protocol used on
candidate ingress, promotion, mutation-plan handling, and context retrieval.
The baseline blocks obvious credential-shaped values without echoing them and
warns on instruction-shaped content. A project may inject a stricter
implementation. Retrieved records are always labeled `evidence_only`; a
warning never becomes runtime authority.

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

`stateweave.adapters.CodexAdapter` prepares a passive host document. The
`prepare_codex_session` bridge composes it with a policy pack, deterministic
context, task, manifest, and worker. It persists the exact prepared session but
still does not select a model, launch a task, contact a service, or authorize
execution.

After external execution, `record_codex_observation` accepts a complete
host-reported receipt and evaluation. It never invents missing evidence.
Successful effect observations must match the session's policy decision and
approval reference. `audit_codex_bridge` verifies closed-world adapter storage,
hash bindings, stored context, and orchestration-ledger references. See
`docs/codex-bridge.md` for the public CLI flow.

To add an adapter:

1. implement the `RuntimeAdapter` protocol;
2. keep platform imports inside the adapter package;
3. make envelope preparation deterministic and external-effect free;
4. record native runtime and model details only as receipt observations;
5. add positive, negative, and adversarial tests;
6. document every new external authority boundary.

## Telemetry and observer

`TelemetryBuffer` is opt-in, local, bounded, and field-allow-listed. It accepts
only short scalar metadata, rejects non-finite values and sensitive field
names, and requires timezone-aware timestamps. `ReadOnlyObserver` exposes
aggregate counts and time bounds without mutation or external I/O.

Telemetry persistence, export, dashboards, and network transport remain
deliberately absent. Continuity persistence stores schema-backed synthetic or
consumer-supplied project records; it does not turn telemetry into a log sink.
A consumer adding transport owns retention, privacy, authorization, and
failure contracts.

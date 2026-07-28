"""StateWeave command-line interface."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any, Sequence

from stateweave.adoption import (
    apply_project_adoption,
    audit_adoption,
    discover_project_config,
    plan_project_adoption,
)
from stateweave.adapters import (
    audit_codex_bridge,
    prepare_codex_session,
    record_codex_observation,
)
from stateweave.capture import audit_capture, ingest_capture_request
from stateweave.context import (
    build_context_index,
    compile_context,
    inspect_context_index,
    query_memory,
)
from stateweave.continuity import (
    append_orchestration_documents,
    append_workflow_documents,
    apply_mutation_plan,
    audit_continuity,
    capture_candidate,
    list_candidates,
    preview_candidate,
    promote_candidate,
    reject_candidate,
    store_context_bundle,
    store_mutation_plan,
)
from stateweave.core.audit import audit_repository
from stateweave.core.backup import create_backup, restore_backup
from stateweave.core.config import load_config
from stateweave.core.errors import StateWeaveError
from stateweave.core.io import read_json
from stateweave.core.locking import inspect_writer_lock, recover_stale_writer_lock
from stateweave.core.migrations import apply_migration, plan_migration
from stateweave.core.project import initialize_project, recover_record_transaction
from stateweave.core.transactions import inspect_transaction_store
from stateweave.onboarding import (
    apply_onboarding_plan,
    audit_onboarding,
    plan_onboarding,
)
from stateweave.policy import load_policy_pack


def _json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)


def _today(value: str | None) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise StateWeaveError("--today must be an ISO date") from exc


def _config(value: str) -> Any:
    return load_config(discover_project_config(Path(value)))


def _json_file(value: str) -> Any:
    return read_json(Path(value), max_bytes=16 * 1024 * 1024)


def _key_value_pairs(values: Sequence[str], *, label: str) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for value in values:
        key, separator, item = value.partition("=")
        if not separator or not key or not item:
            raise StateWeaveError(f"{label} must use EFFECT=REFERENCE")
        if key in pairs:
            raise StateWeaveError(f"{label} contains duplicate effect {key!r}")
        pairs[key] = item
    return pairs


def _add_memory_query_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("objective")
    parser.add_argument("--config", default=".")
    parser.add_argument("--as-of", default=date.today().isoformat())
    parser.add_argument("--term", action="append", default=[])
    parser.add_argument(
        "--kind",
        choices=("fact", "decision", "state"),
        action="append",
        default=[],
    )
    parser.add_argument(
        "--status",
        choices=(
            "verified",
            "provisional",
            "disputed",
            "deprecated",
            "accepted",
            "proposed",
            "superseded",
            "rejected",
        ),
        action="append",
    )
    parser.add_argument("--domain", action="append", default=[])
    parser.add_argument("--classification", action="append", default=[])
    parser.add_argument(
        "--minimum-confidence",
        choices=("low", "medium", "high"),
    )
    parser.add_argument("--relation-depth", type=int, default=0)
    parser.add_argument("--max-items", type=int, default=8)
    parser.add_argument("--max-content-bytes", type=int, default=12000)


def _memory_query_from_args(args: argparse.Namespace) -> dict[str, Any]:
    filters: dict[str, Any] = {
        "record_kinds": args.kind,
        "statuses": args.status or ["verified", "accepted"],
        "domains": args.domain,
        "classifications": args.classification,
    }
    if args.minimum_confidence is not None:
        filters["minimum_confidence"] = args.minimum_confidence
    return {
        "schema_version": 1,
        "kind": "memory_query",
        "objective": args.objective,
        "as_of": args.as_of,
        "terms": args.term,
        "filters": filters,
        "relation_depth": args.relation_depth,
        "budget": {
            "max_items": args.max_items,
            "max_content_bytes": args.max_content_bytes,
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="stateweave",
        description="Persistent memory and governed workflow framework",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    onboarding_plan = subcommands.add_parser(
        "onboarding-plan",
        help="inspect a hash-bound onboarding plan without mutation",
    )
    onboarding_plan.add_argument("destination")
    onboarding_plan.add_argument("--id", required=True, dest="project_id")
    onboarding_plan.add_argument("--name", required=True, dest="project_name")
    onboarding_plan.add_argument(
        "--sidecar-policy",
        choices=("tracked", "local", "defer"),
        required=True,
    )

    onboarding_apply = subcommands.add_parser(
        "onboarding-apply",
        help="apply an exact reviewed onboarding plan",
    )
    onboarding_apply.add_argument("destination")
    onboarding_apply.add_argument("--id", required=True, dest="project_id")
    onboarding_apply.add_argument("--name", required=True, dest="project_name")
    onboarding_apply.add_argument(
        "--sidecar-policy",
        choices=("tracked", "local", "defer"),
        required=True,
    )
    onboarding_apply.add_argument("--expected-plan-sha256", required=True)
    onboarding_apply.add_argument("--decided-at", required=True)
    onboarding_apply.add_argument("--reviewer-role", required=True)
    onboarding_apply.add_argument("--confirm-human", action="store_true")

    init = subcommands.add_parser("init", help="initialize an empty memory project")
    init.add_argument("destination")
    init.add_argument("--id", required=True, dest="project_id")
    init.add_argument("--name", required=True, dest="project_name")

    adopt = subcommands.add_parser(
        "adopt",
        help="plan or apply a non-destructive sidecar in an existing project",
    )
    adopt.add_argument("destination")
    adopt.add_argument("--id", required=True, dest="project_id")
    adopt.add_argument("--name", required=True, dest="project_name")
    adopt.add_argument("--apply", action="store_true")
    adopt.add_argument("--expected-plan-sha256")
    adopt.add_argument("--adopted-at")
    adopt.add_argument("--confirm-adopt", action="store_true")

    adoption_audit = subcommands.add_parser(
        "audit-adoption",
        help="audit sidecar identity and adoption receipt",
    )
    adoption_audit.add_argument("--config", default=".")

    onboarding_audit = subcommands.add_parser(
        "audit-onboarding",
        help="audit persisted onboarding plans and sidecar policy",
    )
    onboarding_audit.add_argument("--config", default=".")

    audit = subcommands.add_parser("audit", help="validate the memory graph")
    audit.add_argument("--config", default=".")
    audit.add_argument("--today")
    audit.add_argument("--json", action="store_true", dest="as_json")

    review = subcommands.add_parser(
        "review", help="show the deterministic review queue"
    )
    review.add_argument("--config", default=".")
    review.add_argument("--today")

    backlinks = subcommands.add_parser(
        "backlinks",
        help="show records that point to an identifier",
    )
    backlinks.add_argument("identifier")
    backlinks.add_argument("--config", default=".")

    backup = subcommands.add_parser("backup", help="create a verified backup")
    backup.add_argument("--config", default=".")
    backup.add_argument("--label", default="manual")

    restore = subcommands.add_parser(
        "restore",
        help="restore a verified backup into an empty destination",
    )
    restore.add_argument("backup")
    restore.add_argument("destination")

    migrate = subcommands.add_parser("migrate", help="plan or apply a migration")
    migrate.add_argument("--config", default=".")
    migrate.add_argument("--from-version", required=True)
    migrate.add_argument("--to-version", required=True)
    migrate.add_argument("--apply", action="store_true")

    lock_status = subcommands.add_parser(
        "lock-status",
        help="inspect writer lock evidence without mutation",
    )
    lock_status.add_argument("--config", default=".")

    recover_lock = subcommands.add_parser(
        "recover-lock",
        help="explicitly recover a fingerprint-bound stale writer lock",
    )
    recover_lock.add_argument("--config", default=".")
    recover_lock.add_argument("--owner-sha256", required=True)
    recover_lock.add_argument("--token")
    recover_lock.add_argument("--confirm-stale", action="store_true")

    transaction_status = subcommands.add_parser(
        "transaction-status",
        help="inspect durable record transaction journals",
    )
    transaction_status.add_argument("--config", default=".")

    recover_transaction = subcommands.add_parser(
        "recover-transaction",
        help="rollback an interrupted hash-bound record transaction",
    )
    recover_transaction.add_argument("transaction_id")
    recover_transaction.add_argument("--config", default=".")
    recover_transaction.add_argument("--request-sha256", required=True)
    recover_transaction.add_argument("--confirm-rollback", action="store_true")

    query = subcommands.add_parser(
        "query",
        help="rank memory records with deterministic explanations",
    )
    _add_memory_query_arguments(query)

    context = subcommands.add_parser(
        "context",
        help="compile a hash-bound ContextBundle under a byte budget",
    )
    _add_memory_query_arguments(context)
    context.add_argument("--persist", action="store_true")

    remember = subcommands.add_parser(
        "remember",
        help="capture an untrusted memory candidate from a proposed record",
    )
    remember.add_argument("proposed_record")
    remember.add_argument("--config", default=".")
    remember.add_argument("--idempotency-key", required=True)
    remember.add_argument("--captured-at", required=True)
    remember.add_argument("--classification", required=True)
    remember.add_argument(
        "--confidence",
        choices=("low", "medium", "high"),
        required=True,
    )
    remember.add_argument("--source-type", required=True)
    remember.add_argument("--source-locator", required=True)
    remember.add_argument("--observed-at", required=True)
    remember.add_argument("--repository-revision")
    remember.add_argument("--tree-sha256")
    remember.add_argument("--artifact-path")
    remember.add_argument("--artifact-sha256")
    remember.add_argument("--selector")
    remember.add_argument("--as-of")
    remember.add_argument("--extraction-method", required=True)
    remember.add_argument("--observer", required=True)
    remember.add_argument("--derivation-id", action="append", default=[])
    remember.add_argument(
        "--review-required",
        action=argparse.BooleanOptionalAction,
        default=True,
    )
    remember.add_argument(
        "--operation",
        choices=("create", "update"),
        default="create",
    )
    remember.add_argument("--expected-sha256")

    candidate_preview = subcommands.add_parser(
        "candidate-preview",
        help="show hashes and changed top-level fields before promotion",
    )
    candidate_preview.add_argument("candidate_id")
    candidate_preview.add_argument("--config", default=".")

    candidate_list = subcommands.add_parser(
        "candidate-list",
        help="list and filter the Candidate Inbox with effective state",
    )
    candidate_list.add_argument("--config", default=".")
    candidate_list.add_argument("--situation")
    candidate_list.add_argument("--classification")
    candidate_list.add_argument(
        "--confidence",
        choices=("low", "medium", "high"),
    )
    candidate_list.add_argument(
        "--operation",
        choices=("create", "update"),
    )
    candidate_list.add_argument("--source-type")
    candidate_list.add_argument(
        "--review-required",
        action=argparse.BooleanOptionalAction,
        default=None,
    )

    promote = subcommands.add_parser(
        "promote-candidate",
        help="promote a candidate through the durable memory transaction",
    )
    promote.add_argument("candidate_id")
    promote.add_argument("--config", default=".")
    promote.add_argument("--reviewer-role", required=True)
    promote.add_argument("--promoted-at", required=True)
    promote.add_argument("--expected-preview-sha256", required=True)
    promote.add_argument("--confirm-human", action="store_true")

    reject = subcommands.add_parser(
        "reject-candidate",
        help="record an immutable rejection for one reviewed candidate",
    )
    reject.add_argument("candidate_id")
    reject.add_argument("--config", default=".")
    reject.add_argument("--expected-preview-sha256", required=True)
    reject.add_argument(
        "--reason-code",
        choices=(
            "duplicate",
            "insufficient-evidence",
            "out-of-scope",
            "policy-blocked",
            "superseded",
            "other",
        ),
        required=True,
    )
    reject.add_argument("--reviewer-role", required=True)
    reject.add_argument("--decided-at", required=True)
    reject.add_argument("--confirm-human", action="store_true")

    continuity_audit = subcommands.add_parser(
        "audit-continuity",
        help="audit candidates, episodes, contexts, and mutation plans",
    )
    continuity_audit.add_argument("--config", default=".")

    capture_import = subcommands.add_parser(
        "capture-import",
        help="ingest an adapter-neutral request as review-only candidates",
    )
    capture_import.add_argument("request")
    capture_import.add_argument("--config", default=".")

    capture_audit = subcommands.add_parser(
        "audit-capture",
        help="audit capture envelopes, checkpoints, and candidate bindings",
    )
    capture_audit.add_argument("--config", default=".")

    append_episode = subcommands.add_parser(
        "append-episode",
        help="atomically persist orchestration or workflow documents",
    )
    append_episode.add_argument("ledger", choices=("orchestration", "workflow"))
    append_episode.add_argument("documents")
    append_episode.add_argument("--config", default=".")

    store_plan = subcommands.add_parser(
        "store-plan",
        help="persist an evidence-bound proposed MutationPlan",
    )
    store_plan.add_argument("plan")
    store_plan.add_argument("--config", default=".")

    apply_plan = subcommands.add_parser(
        "apply-plan",
        help="apply a MutationPlan through the durable memory transaction",
    )
    apply_plan.add_argument("plan_id")
    apply_plan.add_argument("--config", default=".")
    apply_plan.add_argument("--reviewer-role", required=True)
    apply_plan.add_argument("--applied-at", required=True)
    apply_plan.add_argument("--confirm-human", action="store_true")

    index_build = subcommands.add_parser(
        "index-build",
        help="rebuild the derived context index from an audited snapshot",
    )
    index_build.add_argument("--config", default=".")
    index_build.add_argument("--as-of", default=date.today().isoformat())

    index_status = subcommands.add_parser(
        "index-status",
        help="verify index binding to configuration, date, layout, and hashes",
    )
    index_status.add_argument("--config", default=".")
    index_status.add_argument("--as-of", default=date.today().isoformat())

    codex_prepare = subcommands.add_parser(
        "codex-prepare",
        help="persist a context-bound Codex host session without executing it",
    )
    codex_prepare.add_argument("task")
    codex_prepare.add_argument("input_manifest")
    codex_prepare.add_argument("worker")
    codex_prepare.add_argument("query")
    codex_prepare.add_argument("--config", default=".")
    codex_prepare.add_argument("--policy", required=True)
    codex_prepare.add_argument("--role", required=True)
    codex_prepare.add_argument("--created-at", required=True)
    codex_prepare.add_argument("--requested-effect", action="append", default=[])
    codex_prepare.add_argument(
        "--approval",
        action="append",
        default=[],
        metavar="EFFECT=REFERENCE",
    )

    codex_observe = subcommands.add_parser(
        "codex-observe",
        help="reconcile a host-reported receipt and evaluation with a Codex session",
    )
    codex_observe.add_argument("session_id")
    codex_observe.add_argument("receipt")
    codex_observe.add_argument("evaluation")
    codex_observe.add_argument("--config", default=".")
    codex_observe.add_argument("--observer", required=True)
    codex_observe.add_argument("--observed-at", required=True)

    codex_audit = subcommands.add_parser(
        "audit-codex",
        help="audit Codex sessions, observations, and ledger bindings",
    )
    codex_audit.add_argument("--config", default=".")
    return parser


def _run(args: argparse.Namespace) -> int:
    if args.command == "onboarding-plan":
        onboarding = plan_onboarding(
            args.destination,
            project_id=args.project_id,
            project_name=args.project_name,
            sidecar_policy=args.sidecar_policy,
        )
        print(_json(onboarding))
        return 1 if onboarding["status"] == "blocked" else 0
    if args.command == "onboarding-apply":
        result = apply_onboarding_plan(
            args.destination,
            project_id=args.project_id,
            project_name=args.project_name,
            sidecar_policy=args.sidecar_policy,
            expected_plan_sha256=args.expected_plan_sha256,
            decided_at=args.decided_at,
            reviewer_role=args.reviewer_role,
            human_confirmed=args.confirm_human,
        )
        print(_json(result))
        return 0
    if args.command == "init":
        config = initialize_project(
            args.destination,
            project_id=args.project_id,
            project_name=args.project_name,
        )
        print(config.root)
        return 0
    if args.command == "adopt":
        adoption_plan = plan_project_adoption(
            args.destination,
            project_id=args.project_id,
            project_name=args.project_name,
        )
        if not args.apply:
            print(_json(adoption_plan))
            return 1 if adoption_plan["status"] == "blocked" else 0
        if not args.expected_plan_sha256:
            raise StateWeaveError(
                "adopt --apply requires --expected-plan-sha256 from the dry-run"
            )
        if not args.adopted_at:
            raise StateWeaveError("adopt --apply requires --adopted-at")
        result = apply_project_adoption(
            args.destination,
            project_id=args.project_id,
            project_name=args.project_name,
            expected_plan_sha256=args.expected_plan_sha256,
            adopted_at=args.adopted_at,
            confirmed=args.confirm_adopt,
        )
        print(_json(result))
        return 0
    if args.command == "restore":
        manifest = restore_backup(args.backup, args.destination)
        print(_json(manifest))
        return 0

    config = _config(args.config)
    if args.command == "audit-adoption":
        adoption_report = audit_adoption(config)
        print(_json(adoption_report.as_dict()))
        return 0 if adoption_report.ok else 1
    if args.command == "audit-onboarding":
        onboarding_report = audit_onboarding(config)
        print(_json(onboarding_report.as_dict()))
        return 0 if onboarding_report.ok else 1
    if args.command == "query":
        print(_json(query_memory(config, _memory_query_from_args(args))))
        return 0
    if args.command == "context":
        bundle = compile_context(config, _memory_query_from_args(args))
        if args.persist:
            store_context_bundle(config, bundle)
        print(_json(bundle))
        return 0
    if args.command == "remember":
        proposed = _json_file(args.proposed_record)
        if not isinstance(proposed, dict):
            raise StateWeaveError("proposed record file must contain an object")
        candidate = capture_candidate(
            config,
            idempotency_key=args.idempotency_key,
            captured_at=args.captured_at,
            classification=args.classification,
            confidence=args.confidence,
            source={
                "type": args.source_type,
                "locator": args.source_locator,
                "observed_at": args.observed_at,
            },
            provenance={
                "repository_revision": args.repository_revision,
                "tree_sha256": args.tree_sha256,
                "artifact_path": args.artifact_path,
                "artifact_sha256": args.artifact_sha256,
                "selector": args.selector,
                "as_of": args.as_of,
                "extraction_method": args.extraction_method,
                "observer": args.observer,
                "derivation_ids": args.derivation_id,
            },
            proposed_record=proposed,
            review_required=args.review_required,
            operation=args.operation,
            expected_sha256=args.expected_sha256,
        )
        print(_json(candidate))
        return 0
    if args.command == "candidate-preview":
        print(_json(preview_candidate(config, args.candidate_id)))
        return 0
    if args.command == "candidate-list":
        print(
            _json(
                list_candidates(
                    config,
                    situation=args.situation,
                    classification=args.classification,
                    confidence=args.confidence,
                    operation=args.operation,
                    source_type=args.source_type,
                    review_required=args.review_required,
                )
            )
        )
        return 0
    if args.command == "promote-candidate":
        promoted = promote_candidate(
            config,
            args.candidate_id,
            reviewer_role=args.reviewer_role,
            promoted_at=args.promoted_at,
            expected_preview_sha256=args.expected_preview_sha256,
            human_approved=args.confirm_human,
        )
        print(_json(promoted))
        return 0
    if args.command == "reject-candidate":
        rejected = reject_candidate(
            config,
            args.candidate_id,
            expected_preview_sha256=args.expected_preview_sha256,
            reason_code=args.reason_code,
            reviewer_role=args.reviewer_role,
            decided_at=args.decided_at,
            human_approved=args.confirm_human,
        )
        print(_json(rejected))
        return 0
    if args.command == "audit-continuity":
        continuity_report = audit_continuity(config)
        print(_json(continuity_report.as_dict()))
        return 0 if continuity_report.ok else 1
    if args.command == "capture-import":
        request = _json_file(args.request)
        if not isinstance(request, dict):
            raise StateWeaveError("capture request file must contain an object")
        print(_json(ingest_capture_request(config, request)))
        return 0
    if args.command == "audit-capture":
        capture_report = audit_capture(config)
        print(_json(capture_report.as_dict()))
        return 0 if capture_report.ok else 1
    if args.command == "append-episode":
        documents = _json_file(args.documents)
        if not isinstance(documents, list) or any(
            not isinstance(item, dict) for item in documents
        ):
            raise StateWeaveError("episode file must contain an array of objects")
        if args.ledger == "orchestration":
            path = append_orchestration_documents(config, documents)
        else:
            path = append_workflow_documents(config, documents)
        print(path)
        return 0
    if args.command == "store-plan":
        plan = _json_file(args.plan)
        if not isinstance(plan, dict):
            raise StateWeaveError("plan file must contain an object")
        print(store_mutation_plan(config, plan))
        return 0
    if args.command == "apply-plan":
        applied = apply_mutation_plan(
            config,
            args.plan_id,
            reviewer_role=args.reviewer_role,
            applied_at=args.applied_at,
            human_approved=args.confirm_human,
        )
        print(_json(applied))
        return 0
    if args.command == "index-build":
        as_of = _today(args.as_of)
        assert as_of is not None
        print(build_context_index(config, as_of=as_of))
        return 0
    if args.command == "index-status":
        as_of = _today(args.as_of)
        assert as_of is not None
        status = inspect_context_index(config, as_of=as_of)
        print(_json(status))
        return 0 if status["valid"] else 1
    if args.command == "codex-prepare":
        task = _json_file(args.task)
        input_manifest = _json_file(args.input_manifest)
        worker = _json_file(args.worker)
        query = _json_file(args.query)
        if any(
            not isinstance(document, dict)
            for document in (task, input_manifest, worker, query)
        ):
            raise StateWeaveError(
                "Codex task, manifest, worker, and query files must contain objects"
            )
        session = prepare_codex_session(
            config,
            policy=load_policy_pack(args.policy),
            query=query,
            task=task,
            input_manifest=input_manifest,
            worker=worker,
            role=args.role,
            requested_effects=tuple(args.requested_effect),
            approval_references=_key_value_pairs(
                args.approval,
                label="--approval",
            ),
            created_at=args.created_at,
        )
        print(_json(session))
        return 0
    if args.command == "codex-observe":
        receipt = _json_file(args.receipt)
        evaluation = _json_file(args.evaluation)
        if not isinstance(receipt, dict) or not isinstance(evaluation, dict):
            raise StateWeaveError(
                "Codex receipt and evaluation files must contain objects"
            )
        observation = record_codex_observation(
            config,
            args.session_id,
            receipt=receipt,
            evaluation=evaluation,
            observer=args.observer,
            observed_at=args.observed_at,
        )
        print(_json(observation))
        return 0
    if args.command == "audit-codex":
        bridge_report = audit_codex_bridge(config)
        print(_json(bridge_report.as_dict()))
        return 0 if bridge_report.ok else 1
    if args.command in {"audit", "review", "backlinks"}:
        memory_report = audit_repository(
            config, today=_today(getattr(args, "today", None))
        )
        if args.command == "audit":
            if args.as_json:
                print(_json(memory_report.as_dict()))
            else:
                outcome = "OK" if memory_report.ok else "FAILED"
                print(
                    f"Memory audit: {outcome} "
                    f"({memory_report.record_count} records, "
                    f"{len(memory_report.errors)} errors, "
                    f"{len(memory_report.review_queue)} review items)"
                )
                for error in memory_report.errors:
                    print(f"ERROR: {error}")
                for warning in memory_report.warnings:
                    print(f"WARNING: {warning}")
            return 0 if memory_report.ok else 1
        if args.command == "review":
            print(_json(memory_report.as_dict()["review_queue"]))
            return 0 if memory_report.ok else 1
        links = memory_report.as_dict()["backlinks"].get(args.identifier, [])
        print(_json(links))
        return 0 if memory_report.ok else 1
    if args.command == "backup":
        destination = create_backup(config, label=args.label)
        print(destination)
        return 0
    if args.command == "lock-status":
        inspection = inspect_writer_lock(
            config.metadata_dir,
            stale_after_seconds=config.limits.lock_stale_after_seconds,
        )
        print(_json(inspection.as_dict()))
        return 0
    if args.command == "recover-lock":
        if not args.confirm_stale:
            raise StateWeaveError(
                "recover-lock requires --confirm-stale after reviewing lock-status"
            )
        recovered = recover_stale_writer_lock(
            config.metadata_dir,
            stale_after_seconds=config.limits.lock_stale_after_seconds,
            expected_owner_sha256=args.owner_sha256,
            expected_token=args.token,
        )
        print(_json({"recovered": True, "previous": recovered.as_dict()}))
        return 0
    if args.command == "transaction-status":
        print(_json(inspect_transaction_store(config).as_dict()))
        return 0
    if args.command == "recover-transaction":
        if not args.confirm_rollback:
            raise StateWeaveError(
                "recover-transaction requires --confirm-rollback after status review"
            )
        transaction_journal = recover_record_transaction(
            config,
            args.transaction_id,
            expected_request_sha256=args.request_sha256,
        )
        print(_json(transaction_journal))
        return 0
    if args.command == "migrate":
        migration_plan = plan_migration(
            config,
            from_version=args.from_version,
            to_version=args.to_version,
        )
        if not args.apply:
            print(_json(migration_plan.as_dict(config.root)))
            return 0

        def validate() -> list[str]:
            return audit_repository(config, allow_active_writer=True).errors

        migration_journal = apply_migration(
            config,
            migration_plan,
            validate_after=validate,
        )
        print(migration_journal)
        return 0
    raise StateWeaveError(f"unsupported command: {args.command}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return _run(args)
    except StateWeaveError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

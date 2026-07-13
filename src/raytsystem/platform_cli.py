# ruff: noqa: B008
# Typer captures the invocation working directory through command defaults.

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal, cast

import typer

from raytsystem.backup import BackupError, BackupService
from raytsystem.brand_migration import BrandMigrationError, migrate_legacy_workspace
from raytsystem.contracts import (
    EvalAssertion,
    EvalCase,
    EvalSuite,
    ExecutionPlan,
)
from raytsystem.contracts.evaluation import EvalAssertionType
from raytsystem.contracts.governance import EmergencyAction
from raytsystem.contracts.lifecycle import BackupKind
from raytsystem.evals import EvalError, EvalObservation, EvalService
from raytsystem.features import FeatureConfigError
from raytsystem.migrations import MigrationError, MigrationService
from raytsystem.packages import PackageLifecycleError, PackageLifecycleService
from raytsystem.policy_simulator import PolicySimulator, PolicySimulatorError
from raytsystem.replay import ReplayError, ReplayService
from raytsystem.system_status import platform_status
from raytsystem.templates import TemplateError, TemplateService
from raytsystem.templates.service import TemplateId

RootOption = Annotated[
    Path,
    typer.Option("--root", resolve_path=True, file_okay=False, dir_okay=True),
]
JsonOption = Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")]


def register_platform_commands(app: typer.Typer) -> None:
    policy_app = typer.Typer(no_args_is_help=True)
    eval_app = typer.Typer(no_args_is_help=True)
    trace_app = typer.Typer(no_args_is_help=True)
    replay_app = typer.Typer(no_args_is_help=True)
    emergency_app = typer.Typer(no_args_is_help=True)
    mcp_app = typer.Typer(no_args_is_help=True)
    package_app = typer.Typer(no_args_is_help=True)
    workflow_app = typer.Typer(no_args_is_help=True)
    notification_app = typer.Typer(no_args_is_help=True)
    protocol_app = typer.Typer(no_args_is_help=True)
    app.add_typer(policy_app, name="policy")
    app.add_typer(eval_app, name="eval")
    app.add_typer(trace_app, name="trace")
    app.add_typer(replay_app, name="replay")
    app.add_typer(emergency_app, name="emergency")
    app.add_typer(mcp_app, name="mcp")
    app.add_typer(package_app, name="package")
    app.add_typer(workflow_app, name="workflow")
    app.add_typer(notification_app, name="notifications")
    app.add_typer(protocol_app, name="protocols")

    @app.command("migrate-brand")
    def migrate_brand_workspace(
        root: RootOption = Path.cwd(),
        confirm: Annotated[bool, typer.Option("--confirm")] = False,
        json_output: JsonOption = False,
    ) -> None:
        """Safely migrate a workspace created under the retired namespace."""

        try:
            result = migrate_legacy_workspace(root, confirm=confirm)
        except BrandMigrationError as error:
            raise typer.BadParameter(str(error)) from error
        _run(result.as_dict, json_output)

    @policy_app.command("simulate")
    def policy_simulate(
        plan: Annotated[Path, typer.Argument(exists=True, dir_okay=False, readable=True)],
        root: RootOption = Path.cwd(),
        json_output: JsonOption = False,
    ) -> None:
        """Evaluate the exact execution policy without starting any work."""

        _run(
            lambda: (
                PolicySimulator(root)
                .simulate(ExecutionPlan.model_validate_json(plan.read_bytes()))
                .model_dump(mode="json")
            ),
            json_output,
        )

    @eval_app.command("self-test")
    def eval_self_test(
        root: RootOption = Path.cwd(),
        json_output: JsonOption = False,
    ) -> None:
        """Run the native deterministic evaluator without a model or provider."""

        def execute() -> dict[str, Any]:
            assertion = EvalAssertion(
                assertion_id="assert_native_exact",
                assertion_type=EvalAssertionType.EXACT_MATCH,
                target="result_text",
                expected="raytsystem-eval-ok",
            )
            case = EvalCase(
                case_id="case_native_self_test",
                name="Native deterministic self-test",
                task_fixture="evals/platform/native-self-test.json",
                repository_snapshot_sha256="0" * 64,
                agent_configuration_sha256="1" * 64,
                runtime_id="runtime_deterministic",
                instruction_hashes={},
                skill_hashes={},
                assertions=(assertion,),
            )
            suite = EvalSuite(
                suite_id="suite_native_self_test",
                name="Native raytsystem eval self-tests",
                version="1.0.0",
                dataset_id="dataset_native_self_test",
                target_ids=("target_eval_runner",),
                case_ids=(case.case_id,),
                manifest_sha256="2" * 64,
            )
            run, result = EvalService(root).run_case(
                suite,
                case,
                EvalObservation(text="raytsystem-eval-ok"),
                workspace_id="workspace_local",
                target_id="target_eval_runner",
            )
            return {
                "eval_run_id": run.eval_run_id,
                "result_id": result.result_id,
                "passed": result.passed,
                "deterministic": True,
                "llm_judge_used": False,
            }

        _run(execute, json_output)

    @eval_app.command("list")
    def eval_list(root: RootOption = Path.cwd(), json_output: JsonOption = False) -> None:
        _run(lambda: EvalService(root).list_runs(), json_output)

    @eval_app.command("baseline")
    def eval_baseline(
        suite_file: Annotated[Path, typer.Argument(exists=True, dir_okay=False, readable=True)],
        eval_run_id: str,
        accepted_by: Annotated[str, typer.Option("--accepted-by")] = "user_local_cli",
        approval_id: Annotated[str, typer.Option("--approval-id")] = "",
        root: RootOption = Path.cwd(),
        json_output: JsonOption = False,
    ) -> None:
        """Accept an immutable, hash-bound baseline; requires an explicit approval."""

        _run(
            lambda: (
                EvalService(root)
                .create_baseline(
                    EvalSuite.model_validate_json(suite_file.read_bytes()),
                    eval_run_id,
                    accepted_by=accepted_by,
                    approval_id=approval_id,
                )
                .model_dump(mode="json")
            ),
            json_output,
        )

    @eval_app.command("compare")
    def eval_compare(
        baseline_id: str,
        candidate_eval_run_id: str,
        root: RootOption = Path.cwd(),
        json_output: JsonOption = False,
    ) -> None:
        _run(
            lambda: (
                EvalService(root)
                .compare_with_baseline(baseline_id, candidate_eval_run_id)
                .model_dump(mode="json")
            ),
            json_output,
        )

    @eval_app.command("reject")
    def eval_reject(
        comparison_id: str,
        reason: Annotated[str, typer.Option("--reason")],
        actor_id: Annotated[str, typer.Option("--actor")] = "user_local_cli",
        root: RootOption = Path.cwd(),
        json_output: JsonOption = False,
    ) -> None:
        """Record an explicit regression rejection as an eval finding."""

        _run(
            lambda: (
                EvalService(root)
                .reject_regression(comparison_id, actor_id=actor_id, reason=reason)
                .model_dump(mode="json")
            ),
            json_output,
        )

    @trace_app.command("list")
    def trace_list(root: RootOption = Path.cwd(), json_output: JsonOption = False) -> None:
        from raytsystem.telemetry import TraceService

        _run(lambda: TraceService(root).list_traces(), json_output)

    @trace_app.command("detail")
    def trace_detail(
        trace_id: str,
        root: RootOption = Path.cwd(),
        json_output: JsonOption = False,
    ) -> None:
        from raytsystem.telemetry import TraceService

        _run(
            lambda: (
                TraceService(root).trace_detail(trace_id)
                or {"state": "not_found", "trace_id": trace_id}
            ),
            json_output,
        )

    @trace_app.command("export-fingerprint")
    def trace_export_fingerprint(
        root: RootOption = Path.cwd(), json_output: JsonOption = False
    ) -> None:
        """Print the destination-binding identity an OTLP export approval must cover."""

        from raytsystem.telemetry import TraceService

        _run(lambda: TraceService(root).export_fingerprint(), json_output)

    @trace_app.command("export-otlp")
    def trace_export_otlp(
        destination: Path,
        approval_id: Annotated[str, typer.Option("--approval-id")],
        root: RootOption = Path.cwd(),
        json_output: JsonOption = False,
    ) -> None:
        """Write redacted OTLP/JSON to a local file; needs otel_export_enabled + approval."""

        from raytsystem.telemetry import TraceService

        _run(
            lambda: TraceService(root).export_otlp(
                destination, approval_id=approval_id, actor_id="user_local_cli"
            ),
            json_output,
        )

    @replay_app.command("plan")
    def replay_plan(
        original_run_id: str,
        new_run_id: Annotated[str, typer.Option("--new-run-id")],
        recorded_result: Annotated[list[str] | None, typer.Option("--recorded-result")] = None,
        root: RootOption = Path.cwd(),
        json_output: JsonOption = False,
    ) -> None:
        _run(
            lambda: (
                ReplayService(root)
                .plan_replay(
                    original_run_id,
                    new_run_id=new_run_id,
                    recorded_side_effect_ids=tuple(recorded_result or ()),
                )
                .model_dump(mode="json")
            ),
            json_output,
        )

    @replay_app.command("fork")
    def replay_fork(
        original_run_id: str,
        new_run_id: Annotated[str, typer.Option("--new-run-id")],
        changes: Annotated[str, typer.Option("--changes", help="JSON object of forked fields")],
        root: RootOption = Path.cwd(),
        json_output: JsonOption = False,
    ) -> None:
        """Plan a fork; every changed field is recorded as a structured diff."""

        def execute() -> dict[str, Any]:
            parsed = json.loads(changes)
            if not isinstance(parsed, dict):
                raise ValueError("Fork changes must be a JSON object")
            return (
                ReplayService(root)
                .plan_fork(original_run_id, new_run_id=new_run_id, changes=parsed)
                .model_dump(mode="json")
            )

        _run(execute, json_output)

    @replay_app.command("compare")
    def replay_compare(
        left_run_id: str,
        right_run_id: str,
        root: RootOption = Path.cwd(),
        json_output: JsonOption = False,
    ) -> None:
        _run(
            lambda: ReplayService(root).compare(left_run_id, right_run_id).model_dump(mode="json"),
            json_output,
        )

    @replay_app.command("list")
    def replay_list(root: RootOption = Path.cwd(), json_output: JsonOption = False) -> None:
        _run(lambda: ReplayService(root).list_plans(), json_output)

    @emergency_app.command("activate")
    def emergency_activate(
        action: Annotated[list[EmergencyAction], typer.Option("--action")],
        reason: Annotated[str, typer.Option("--reason")],
        idempotency_key: Annotated[str, typer.Option("--idempotency-key")],
        root: RootOption = Path.cwd(),
        json_output: JsonOption = False,
    ) -> None:
        from raytsystem.emergency import EmergencyService

        _run(
            lambda: EmergencyService(root).activate(
                tuple(action),
                reason=reason,
                actor_id="user_local_cli",
                idempotency_key=idempotency_key,
            ),
            json_output,
        )

    @emergency_app.command("status")
    def emergency_status(root: RootOption = Path.cwd(), json_output: JsonOption = False) -> None:
        from raytsystem.emergency import EmergencyService

        _run(lambda: EmergencyService(root).snapshot(), json_output)

    @emergency_app.command("recover")
    def emergency_recover(
        action: Annotated[list[EmergencyAction], typer.Option("--action")],
        reason: Annotated[str, typer.Option("--reason")],
        approval_id: Annotated[str, typer.Option("--approval-id")],
        idempotency_key: Annotated[str, typer.Option("--idempotency-key")],
        root: RootOption = Path.cwd(),
        json_output: JsonOption = False,
    ) -> None:
        """Manually recover emergency actions; always requires a fresh approval."""

        from raytsystem.emergency import EmergencyService

        _run(
            lambda: EmergencyService(root).recover(
                tuple(action),
                actor_id="user_local_cli",
                approval_id=approval_id,
                reason=reason,
                idempotency_key=idempotency_key,
            ),
            json_output,
        )

    @emergency_app.command("close-breaker")
    def emergency_close_breaker(
        breaker_id: str,
        approval_id: Annotated[str, typer.Option("--approval-id")],
        reason: Annotated[str, typer.Option("--reason")],
        root: RootOption = Path.cwd(),
        json_output: JsonOption = False,
    ) -> None:
        """Manually close a circuit breaker; the only path for security breakers."""

        from raytsystem.emergency import EmergencyService

        _run(
            lambda: (
                EmergencyService(root)
                .close_breaker(
                    breaker_id,
                    approval_id=approval_id,
                    reason=reason,
                    actor_id="user_local_cli",
                )
                .model_dump(mode="json")
            ),
            json_output,
        )

    @mcp_app.command("list")
    def mcp_list(root: RootOption = Path.cwd(), json_output: JsonOption = False) -> None:
        from raytsystem.tooling import McpGovernanceService

        _run(lambda: McpGovernanceService(root).snapshot(), json_output)

    @mcp_app.command("validate")
    def mcp_validate(
        revision_id: str,
        root: RootOption = Path.cwd(),
        json_output: JsonOption = False,
    ) -> None:
        from raytsystem.tooling import McpGovernanceService

        _run(
            lambda: (
                McpGovernanceService(root).validate_revision(revision_id).model_dump(mode="json")
            ),
            json_output,
        )

    @mcp_app.command("approve")
    def mcp_approve(
        revision_id: str,
        approval_id: Annotated[str, typer.Option("--approval-id")],
        root: RootOption = Path.cwd(),
        json_output: JsonOption = False,
    ) -> None:
        from raytsystem.tooling import McpGovernanceService

        _run(
            lambda: (
                McpGovernanceService(root)
                .approve_catalog(revision_id, approved_by="user_local_cli", approval_id=approval_id)
                .model_dump(mode="json")
            ),
            json_output,
        )

    @mcp_app.command("transition")
    def mcp_transition(
        revision_id: str,
        state: Annotated[str, typer.Option("--state", help="enable, disable, degrade or block")],
        reason: Annotated[str, typer.Option("--reason")],
        root: RootOption = Path.cwd(),
        json_output: JsonOption = False,
    ) -> None:
        from raytsystem.tooling import McpGovernanceService

        def execute() -> dict[str, Any]:
            service = McpGovernanceService(root)
            transitions = {
                "enable": service.enable_server,
                "disable": service.disable_server,
                "degrade": service.mark_degraded,
                "block": service.block_server,
            }
            if state not in transitions:
                raise ValueError("Unknown MCP server transition")
            return transitions[state](
                revision_id, actor_id="user_local_cli", reason=reason
            ).model_dump(mode="json")

        _run(execute, json_output)

    @package_app.command("inspect")
    def package_inspect(
        source: str,
        root: RootOption = Path.cwd(),
        json_output: JsonOption = False,
    ) -> None:
        _run(
            lambda: _package_inspect_payload(PackageLifecycleService(root), source),
            json_output,
        )

    @package_app.command("validate")
    def package_validate(
        revision_id: str,
        root: RootOption = Path.cwd(),
        json_output: JsonOption = False,
    ) -> None:
        _run(
            lambda: PackageLifecycleService(root).validate(revision_id).model_dump(mode="json"),
            json_output,
        )

    @package_app.command("discover")
    def package_discover(
        source: str,
        root: RootOption = Path.cwd(),
        json_output: JsonOption = False,
    ) -> None:
        _run(lambda: PackageLifecycleService(root).discover(source), json_output)

    @package_app.command("update")
    def package_update(
        source: str,
        root: RootOption = Path.cwd(),
        json_output: JsonOption = False,
    ) -> None:
        _run(
            lambda: _package_inspect_payload_from(PackageLifecycleService(root).update(source)),
            json_output,
        )

    @package_app.command("approve")
    def package_approve(
        revision_id: str,
        approval_id: Annotated[str, typer.Option("--approval-id")],
        eval_run: Annotated[list[str], typer.Option("--eval-run")],
        root: RootOption = Path.cwd(),
        json_output: JsonOption = False,
    ) -> None:
        """Approve a validated revision; every eval run must exist and have passed."""

        _run(
            lambda: (
                PackageLifecycleService(root)
                .approve(
                    revision_id,
                    actor_id="user_local_cli",
                    approval_id=approval_id,
                    eval_run_ids=tuple(eval_run),
                )
                .model_dump(mode="json")
            ),
            json_output,
        )

    @package_app.command("install")
    def package_install(
        revision_id: str,
        root: RootOption = Path.cwd(),
        json_output: JsonOption = False,
    ) -> None:
        """Install into staging; installation never activates a revision."""

        _run(
            lambda: PackageLifecycleService(root).install(revision_id).model_dump(mode="json"),
            json_output,
        )

    @package_app.command("activate")
    def package_activate(
        revision_id: str,
        approval_id: Annotated[str, typer.Option("--approval-id")],
        root: RootOption = Path.cwd(),
        json_output: JsonOption = False,
    ) -> None:
        _run(
            lambda: (
                PackageLifecycleService(root)
                .activate(revision_id, actor_id="user_local_cli", approval_id=approval_id)
                .model_dump(mode="json")
            ),
            json_output,
        )

    @package_app.command("rollback")
    def package_rollback(
        package_id: str,
        to_revision_id: str,
        reason: Annotated[str, typer.Option("--reason")],
        root: RootOption = Path.cwd(),
        json_output: JsonOption = False,
    ) -> None:
        """Roll back by promoting a prior installed revision as a new active head."""

        _run(
            lambda: (
                PackageLifecycleService(root)
                .rollback(package_id, to_revision_id, actor_id="user_local_cli", reason=reason)
                .model_dump(mode="json")
            ),
            json_output,
        )

    @workflow_app.command("list")
    def workflow_list(root: RootOption = Path.cwd(), json_output: JsonOption = False) -> None:
        from raytsystem.workflows import WorkflowService

        _run(lambda: WorkflowService(root).snapshot(), json_output)

    @workflow_app.command("approve")
    def workflow_approve(
        workflow_run_id: str,
        node_id: str,
        approval_id: Annotated[str, typer.Option("--approval-id")],
        root: RootOption = Path.cwd(),
        json_output: JsonOption = False,
    ) -> None:
        from raytsystem.workflows import WorkflowService

        _run(
            lambda: (
                WorkflowService(root)
                .grant_approval(
                    workflow_run_id, node_id, approval_id=approval_id, actor_id="user_local_cli"
                )
                .model_dump(mode="json")
            ),
            json_output,
        )

    @workflow_app.command("cancel")
    def workflow_cancel(
        workflow_run_id: str,
        root: RootOption = Path.cwd(),
        json_output: JsonOption = False,
    ) -> None:
        from raytsystem.workflows import WorkflowService

        _run(
            lambda: (
                WorkflowService(root)
                .cancel(workflow_run_id, actor_id="user_local_cli")
                .model_dump(mode="json")
            ),
            json_output,
        )

    @notification_app.command("list")
    def notification_list(root: RootOption = Path.cwd(), json_output: JsonOption = False) -> None:
        from raytsystem.notifications import NotificationService

        _run(lambda: NotificationService(root).snapshot(), json_output)

    @notification_app.command("transition")
    def notification_transition(
        notification_id: str,
        state: Annotated[str, typer.Option("--state", help="read, acknowledged or resolved")],
        root: RootOption = Path.cwd(),
        json_output: JsonOption = False,
    ) -> None:
        from raytsystem.notifications import NotificationService

        def execute() -> dict[str, Any]:
            if state not in {"read", "acknowledged", "resolved"}:
                raise ValueError("Unknown notification transition")
            return (
                NotificationService(root)
                .transition(
                    notification_id,
                    cast(Literal["read", "acknowledged", "resolved"], state),
                    actor_id="user_local_cli",
                )
                .model_dump(mode="json")
            )

        _run(execute, json_output)

    @app.command("secrets-status")
    def secrets_status(root: RootOption = Path.cwd(), json_output: JsonOption = False) -> None:
        """Report the honest encryption provider state; never claims absent capability."""

        from raytsystem.secrets import SecretEncryptionService

        _run(lambda: SecretEncryptionService(root).status().model_dump(mode="json"), json_output)

    @protocol_app.command("status")
    def protocol_status(root: RootOption = Path.cwd(), json_output: JsonOption = False) -> None:
        _run(lambda: _protocol_status(root), json_output)

    @app.command("init")
    def init_workspace(
        root: RootOption = Path.cwd(),
        template: Annotated[
            str, typer.Option("--template", help="software, content or research")
        ] = "software",
        dry_run: Annotated[bool, typer.Option("--dry-run/--apply")] = True,
        confirm_existing: Annotated[bool, typer.Option("--confirm-existing")] = False,
        json_output: JsonOption = False,
    ) -> None:
        service = TemplateService()
        if template not in {"software", "content", "research"}:
            raise typer.BadParameter("Unknown raytsystem template")
        template_id = cast(TemplateId, template)
        if dry_run:
            _run(lambda: service.plan(root, template_id)[0].model_dump(mode="json"), json_output)
        else:
            _run(
                lambda: service.initialize(root, template_id, confirm_existing=confirm_existing),
                json_output,
            )

    @app.command("migrate")
    def migrate_workspace(
        root: RootOption = Path.cwd(),
        dry_run: Annotated[bool, typer.Option("--dry-run/--apply")] = True,
        backup_id: Annotated[str | None, typer.Option("--backup-id")] = None,
        confirm: Annotated[bool, typer.Option("--confirm")] = False,
        json_output: JsonOption = False,
    ) -> None:
        service = MigrationService(root)
        plan = service.plan()
        if dry_run:
            _run(lambda: plan.model_dump(mode="json"), json_output)
        else:
            _run(
                lambda: _migration_payload(
                    service.apply(
                        plan,
                        backup_id=backup_id or "",
                        actor_id="user_local_cli",
                        confirm=confirm,
                    )
                ),
                json_output,
            )

    @app.command("upgrade")
    def upgrade_workspace(
        root: RootOption = Path.cwd(),
        dry_run: Annotated[bool, typer.Option("--dry-run/--apply")] = True,
        confirm: Annotated[bool, typer.Option("--confirm")] = False,
        json_output: JsonOption = False,
    ) -> None:
        service = MigrationService(root)
        plan = service.plan()
        if dry_run or not plan.migration_ids:
            _run(
                lambda: {
                    "upgrade_required": bool(plan.migration_ids),
                    "plan": plan.model_dump(mode="json"),
                    "backup_required": plan.backup_required,
                    "rollback": "restore_verified_backup",
                },
                json_output,
            )
            return
        if not confirm:
            raise typer.BadParameter("Upgrade apply requires --confirm")
        destination = (
            root
            / "ops"
            / "backups"
            / ("pre-upgrade-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + ".zip")
        )
        backup = BackupService(root).create(destination)
        _run(
            lambda: {
                "backup_id": backup.backup_id,
                "migration": _migration_payload(
                    service.apply(
                        plan,
                        backup_id=backup.backup_id,
                        actor_id="user_local_cli",
                        confirm=True,
                    )
                ),
                "rollback": str(destination),
            },
            json_output,
        )

    @app.command("backup")
    def backup_workspace(
        destination: Path,
        root: RootOption = Path.cwd(),
        json_output: JsonOption = False,
    ) -> None:
        _run(
            lambda: BackupService(root).create(destination).model_dump(mode="json"),
            json_output,
        )

    @app.command("export")
    def export_workspace(
        destination: Path,
        kind: Annotated[BackupKind, typer.Option("--kind")] = BackupKind.PUBLIC,
        root: RootOption = Path.cwd(),
        json_output: JsonOption = False,
    ) -> None:
        _run(
            lambda: BackupService(root).create(destination, kind=kind).model_dump(mode="json"),
            json_output,
        )

    @app.command("restore")
    def restore_workspace(
        bundle: Path,
        destination: Path,
        root: RootOption = Path.cwd(),
        dry_run: Annotated[bool, typer.Option("--dry-run/--apply")] = True,
        json_output: JsonOption = False,
    ) -> None:
        from raytsystem.system_status import core_doctor

        service = BackupService(root)
        _run(
            lambda: (
                service.restore_plan(bundle, destination).model_dump(mode="json")
                if dry_run
                else service.restore(bundle, destination, doctor=core_doctor).model_dump(
                    mode="json"
                )
            ),
            json_output,
        )

    @app.command("platform-status")
    def platform_status_command(
        root: RootOption = Path.cwd(), json_output: JsonOption = False
    ) -> None:
        _run(lambda: platform_status(root), json_output)


def _run(action: Any, as_json: bool) -> None:
    from raytsystem.emergency import EmergencyError
    from raytsystem.notifications import NotificationError
    from raytsystem.secrets import SecretEncryptionError
    from raytsystem.telemetry import TelemetryError
    from raytsystem.tooling import McpGovernanceError
    from raytsystem.workflows import WorkflowError

    try:
        payload = action()
    except (
        BackupError,
        EmergencyError,
        EvalError,
        FeatureConfigError,
        McpGovernanceError,
        MigrationError,
        NotificationError,
        PackageLifecycleError,
        PolicySimulatorError,
        ReplayError,
        SecretEncryptionError,
        TelemetryError,
        TemplateError,
        WorkflowError,
        ValueError,
    ) as error:
        _emit({"status": "failed", "error": str(error)}, as_json)
        raise typer.Exit(code=2) from error
    _emit(payload, as_json)


def _emit(payload: Any, as_json: bool) -> None:
    if hasattr(payload, "model_dump"):
        payload = payload.model_dump(mode="json")
    if as_json:
        typer.echo(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    elif isinstance(payload, dict):
        for key, value in payload.items():
            typer.echo(f"{key}: {value}")
    else:
        typer.echo(str(payload))


def _package_inspect_payload(service: PackageLifecycleService, source: str) -> dict[str, Any]:
    return _package_inspect_payload_from(service.inspect(source))


def _package_inspect_payload_from(result: tuple[Any, Any]) -> dict[str, Any]:
    manifest, revision = result
    return {
        "manifest": manifest.model_dump(mode="json"),
        "revision": revision.model_dump(mode="json"),
    }


def _migration_payload(record: Any) -> dict[str, Any]:
    return {"status": "up_to_date"} if record is None else record.model_dump(mode="json")


def _protocol_status(root: Path) -> dict[str, Any]:
    from raytsystem.protocols import A2AGateway, AcpAdapter

    return {"acp": AcpAdapter(root).snapshot(), "a2a": A2AGateway(root).snapshot()}

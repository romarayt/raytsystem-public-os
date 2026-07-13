from __future__ import annotations

import json
import platform
import sqlite3
import sys
import tomllib
import webbrowser
from dataclasses import asdict
from pathlib import Path
from threading import Timer
from typing import Annotated, Any

import typer
from pydantic import BaseModel

from raytsystem.agent_policy import AgentPolicy, AgentPolicyError, SubagentRequest
from raytsystem.bootstrap.cli import register_bootstrap_commands
from raytsystem.checkpoint_guard import CheckpointGuard, CheckpointGuardError
from raytsystem.codegraph.benchmark import (
    load_code_graph_benchmark_cases,
    run_code_graph_benchmark,
)
from raytsystem.codegraph.projection import CodeGraphProjection, CodeGraphUnavailable
from raytsystem.codegraph.querying import CodeGraphQueryError, CodeGraphQueryService
from raytsystem.codegraph.security import CodeGraphSecurityError
from raytsystem.contracts import (
    SCHEMA_MODELS,
    SCHEMA_VERSION,
    TaskPriority,
    TaskStatus,
    canonical_json_bytes,
    sha256_hex,
)
from raytsystem.ingestion import (
    ApprovalRequired,
    IngestPipeline,
    QuarantinedInput,
    UnsupportedInput,
)
from raytsystem.io import write_text_atomic
from raytsystem.linting import LintService
from raytsystem.platform_cli import register_platform_commands
from raytsystem.projections import ProjectionError, ProjectionService
from raytsystem.querying import QueryRejected, QueryScope, QueryService
from raytsystem.saving import SaveRejected, SaveService
from raytsystem.search import (
    FTS5SearchAdapter,
    QmdSearchAdapter,
    SearchAdapter,
    SearchError,
    load_benchmark_cases,
    run_search_benchmark,
)
from raytsystem.storage import IntegrityError
from raytsystem.tasking import TaskConflict, TaskLedgerError, TaskService, TaskTransitionRejected
from raytsystem.toolhub.cli import register_toolhub_commands

app = typer.Typer(no_args_is_help=True, pretty_exceptions_enable=False)
schemas_app = typer.Typer(no_args_is_help=True)
proposal_app = typer.Typer(no_args_is_help=True)
agent_app = typer.Typer(no_args_is_help=True)
task_app = typer.Typer(no_args_is_help=True)
graph_app = typer.Typer(no_args_is_help=True)
app.add_typer(schemas_app, name="schemas")
app.add_typer(proposal_app, name="proposal")
app.add_typer(agent_app, name="agent")
app.add_typer(task_app, name="task")
app.add_typer(graph_app, name="graph")
register_platform_commands(app)
register_toolhub_commands(app)
register_bootstrap_commands(app)
DEFAULT_ROOT = Path.cwd()


RootOption = Annotated[
    Path,
    typer.Option("--root", resolve_path=True, file_okay=False, dir_okay=True),
]
JsonOption = Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")]


def _emit(payload: dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        typer.echo(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return
    for key, value in payload.items():
        typer.echo(f"{key}: {value}")


def _read_config(root: Path) -> dict[str, Any] | None:
    path = root / "config" / "raytsystem.toml"
    if not path.is_file():
        return None
    with path.open("rb") as handle:
        return tomllib.load(handle)


def _run_pipeline(action: Any, *, as_json: bool) -> None:
    try:
        result = action()
    except (
        ApprovalRequired,
        AgentPolicyError,
        CheckpointGuardError,
        CodeGraphQueryError,
        CodeGraphSecurityError,
        CodeGraphUnavailable,
        IntegrityError,
        ProjectionError,
        QueryRejected,
        QuarantinedInput,
        SaveRejected,
        SearchError,
        TaskConflict,
        TaskLedgerError,
        TaskTransitionRejected,
        UnsupportedInput,
    ) as error:
        _emit({"status": "failed", "error": str(error)}, as_json=as_json)
        raise typer.Exit(code=2) from error
    if isinstance(result, BaseModel):
        payload = result.model_dump(mode="json")
    elif hasattr(result, "to_dict"):
        payload = result.to_dict()
    else:
        payload = asdict(result) if hasattr(result, "__dataclass_fields__") else result
    _emit(payload, as_json=as_json)


@graph_app.command("status")
def graph_status(
    root: RootOption = DEFAULT_ROOT,
    verify: Annotated[
        bool,
        typer.Option("--verify/--fast", help="Hash inputs or run only the cheap path check."),
    ] = True,
    json_output: JsonOption = False,
) -> None:
    """Report code-graph freshness without mutating the projection."""

    _run_pipeline(
        lambda: CodeGraphProjection(root).status(verify_hashes=verify),
        as_json=json_output,
    )


@graph_app.command("update")
def graph_update(
    root: RootOption = DEFAULT_ROOT,
    json_output: JsonOption = False,
) -> None:
    """Incrementally update changed and deleted code-graph inputs."""

    _run_pipeline(lambda: CodeGraphProjection(root).update(), as_json=json_output)


@graph_app.command("rebuild")
def graph_rebuild(
    root: RootOption = DEFAULT_ROOT,
    json_output: JsonOption = False,
) -> None:
    """Atomically rebuild the complete disposable code graph."""

    _run_pipeline(lambda: CodeGraphProjection(root).rebuild(), as_json=json_output)


@graph_app.command("query")
def graph_query(
    question: Annotated[str, typer.Argument(help="Bounded architecture question.")],
    root: RootOption = DEFAULT_ROOT,
    depth: Annotated[int, typer.Option("--depth", min=1, max=3)] = 2,
    json_output: JsonOption = False,
) -> None:
    """Find seed nodes and return a bounded graph-first context slice."""

    _run_pipeline(
        lambda: CodeGraphQueryService(root).query(question, depth=depth),
        as_json=json_output,
    )


@graph_app.command("explain")
def graph_explain(
    node: Annotated[str, typer.Argument(help="Typed node ID, symbol or relative path.")],
    root: RootOption = DEFAULT_ROOT,
    depth: Annotated[int, typer.Option("--depth", min=1, max=3)] = 1,
    json_output: JsonOption = False,
) -> None:
    """Explain one node with its bounded neighborhood."""

    _run_pipeline(
        lambda: CodeGraphQueryService(root).explain(node, depth=depth),
        as_json=json_output,
    )


@graph_app.command("neighbors")
def graph_neighbors(
    node: Annotated[str, typer.Argument(help="Typed node ID, symbol or relative path.")],
    root: RootOption = DEFAULT_ROOT,
    depth: Annotated[int, typer.Option("--depth", min=1, max=3)] = 1,
    direction: Annotated[str, typer.Option("--direction")] = "both",
    json_output: JsonOption = False,
) -> None:
    """Return incoming, outgoing or bidirectional neighbors."""

    if direction not in {"both", "in", "out"}:
        raise typer.BadParameter("direction must be one of: both, in, out")
    _run_pipeline(
        lambda: CodeGraphQueryService(root).neighbors(
            node,
            depth=depth,
            direction=direction,  # type: ignore[arg-type]
        ),
        as_json=json_output,
    )


@graph_app.command("path")
def graph_path(
    source: Annotated[str, typer.Argument(help="Source node ID, symbol or relative path.")],
    target: Annotated[str, typer.Argument(help="Target node ID, symbol or relative path.")],
    root: RootOption = DEFAULT_ROOT,
    json_output: JsonOption = False,
) -> None:
    """Return the deterministic shortest path between two code nodes."""

    _run_pipeline(
        lambda: CodeGraphQueryService(root).path(source, target),
        as_json=json_output,
    )


@graph_app.command("impact")
def graph_impact(
    node: Annotated[str, typer.Argument(help="Typed node ID, symbol or relative path.")],
    root: RootOption = DEFAULT_ROOT,
    depth: Annotated[int, typer.Option("--depth", min=1, max=3)] = 3,
    json_output: JsonOption = False,
) -> None:
    """Trace bounded reverse dependencies for a proposed change."""

    _run_pipeline(
        lambda: CodeGraphQueryService(root).impact(node, depth=depth),
        as_json=json_output,
    )


@graph_app.command("benchmark")
def graph_benchmark(
    root: RootOption = DEFAULT_ROOT,
    cases: Annotated[
        str,
        typer.Option("--cases", help="Workspace-relative JSONL benchmark suite."),
    ] = "benchmarks/codegraph/questions.jsonl",
    json_output: JsonOption = False,
) -> None:
    """Compare bounded graph-first context with deterministic local file search."""

    _run_pipeline(
        lambda: run_code_graph_benchmark(
            root,
            load_code_graph_benchmark_cases(root, cases),
        ),
        as_json=json_output,
    )


@task_app.command("list")
def task_list(
    root: RootOption = DEFAULT_ROOT,
    json_output: JsonOption = False,
) -> None:
    """Read the operational task board without touching canonical knowledge."""

    _run_pipeline(lambda: TaskService(root).snapshot(), as_json=json_output)


@task_app.command("create")
def task_create(
    title: Annotated[str, typer.Argument(help="Task title.")],
    idempotency_key: Annotated[str, typer.Option("--idempotency-key")],
    root: RootOption = DEFAULT_ROOT,
    description: Annotated[str, typer.Option("--description")] = "",
    priority: Annotated[TaskPriority, typer.Option("--priority")] = TaskPriority.NORMAL,
    expected_generation_id: Annotated[
        str | None,
        typer.Option("--expected-generation"),
    ] = None,
    json_output: JsonOption = False,
) -> None:
    """Create one idempotent task in the separate operational ledger."""

    _run_pipeline(
        lambda: TaskService(root).create_task(
            title=title,
            description=description,
            priority=priority,
            actor="user:local-cli",
            idempotency_key=idempotency_key,
            expected_generation_id=expected_generation_id,
        ),
        as_json=json_output,
    )


@task_app.command("transition")
def task_transition(
    task_id: Annotated[str, typer.Argument(help="Typed task ID.")],
    target: Annotated[TaskStatus, typer.Argument(help="Validated target state.")],
    idempotency_key: Annotated[str, typer.Option("--idempotency-key")],
    expected_generation_id: Annotated[str, typer.Option("--expected-generation")],
    root: RootOption = DEFAULT_ROOT,
    blocked_reason: Annotated[str | None, typer.Option("--blocked-reason")] = None,
    json_output: JsonOption = False,
) -> None:
    """Request one legal task state transition."""

    _run_pipeline(
        lambda: TaskService(root).transition_task(
            task_id,
            target,
            actor="user:local-cli",
            idempotency_key=idempotency_key,
            expected_generation_id=expected_generation_id,
            blocked_reason=blocked_reason,
        ),
        as_json=json_output,
    )


@app.command()
def ui(
    root: RootOption = DEFAULT_ROOT,
    host: Annotated[str, typer.Option("--host")] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port", min=1024, max=65_535)] = 8765,
    open_browser: Annotated[bool, typer.Option("--open/--no-open")] = True,
) -> None:
    """Start the same-origin raytsystem interface on the local loopback only."""

    _serve_ui(root, host=host, port=port, open_browser=open_browser)


@app.command()
def start(
    root: RootOption = DEFAULT_ROOT,
    host: Annotated[str, typer.Option("--host")] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port", min=1024, max=65_535)] = 8765,
    open_browser: Annotated[bool, typer.Option("--open/--no-open")] = True,
) -> None:
    """Start the raytsystem interface — short alias for `ui`."""

    _serve_ui(root, host=host, port=port, open_browser=open_browser)


def _serve_ui(root: Path, *, host: str, port: int, open_browser: bool) -> None:
    if host != "127.0.0.1":
        typer.echo("Refusing remote bind: v1 supports only 127.0.0.1", err=True)
        raise typer.Exit(code=2)
    from raytsystem.webapp import create_app
    from raytsystem.webapp.snapshot import SnapshotProvider

    root = root.resolve()
    static_dir = Path(__file__).parent / "webapp" / "static"
    if not (static_dir / "index.html").is_file():
        typer.echo("Web bundle is missing. Run the documented frontend build.", err=True)
        raise typer.Exit(code=2)
    try:
        SnapshotProvider(root).get()
    except IntegrityError as error:
        typer.echo("Verified workspace snapshot is unavailable.", err=True)
        raise typer.Exit(code=2) from error
    url = f"http://{host}:{port}"
    typer.echo(f"raytsystem: {url}")
    if open_browser:
        timer = Timer(0.6, lambda: webbrowser.open(url, new=2))
        timer.daemon = True
        timer.start()
    import uvicorn

    uvicorn.run(
        create_app(
            root,
            allowed_hosts=frozenset({host, f"{host}:{port}"}),
            allowed_origins=frozenset({url}),
            static_dir=static_dir,
        ),
        host=host,
        port=port,
        access_log=False,
        log_level="warning",
    )


def _active_generation(root: Path) -> str | None:
    current = root / "ledger" / "CURRENT"
    if not current.is_file():
        return None
    value = current.read_text(encoding="utf-8").strip()
    return value or None


@app.command()
def doctor(
    root: RootOption = DEFAULT_ROOT,
    json_output: JsonOption = False,
) -> None:
    """Check environment and canonical pointers without mutating the project."""

    root = root.resolve()
    config = _read_config(root)
    generation = _active_generation(root)
    generation_file = (
        None if generation is None else root / "ledger" / "generations" / f"{generation}.json"
    )
    graph_state = CodeGraphProjection(root).status(verify_hashes=True)
    code_graph_enabled = config is not None and isinstance(config.get("code_graph"), dict)
    checks = {
        "root_exists": root.is_dir(),
        "config_exists": config is not None,
        "python_supported": sys.version_info >= (3, 12),
        "ledger_pointer_exists": generation is not None,
        "generation_exists": generation_file is not None and generation_file.is_file(),
    }
    if code_graph_enabled:
        checks["code_graph_current"] = graph_state.state.value == "current"
    payload: dict[str, Any] = {
        "healthy": all(checks.values()),
        "project_root": str(root),
        "active_generation": generation,
        "python": platform.python_version(),
        "sqlite": sqlite3.sqlite_version,
        "checks": checks,
        "code_graph": graph_state.model_dump(mode="json"),
    }
    from raytsystem.system_status import platform_status

    payload["platform"] = platform_status(root)
    checks["platform_health"] = payload["platform"]["state"] not in {"error", "degraded"}
    payload["healthy"] = all(checks.values())
    _emit(payload, as_json=json_output)
    if not payload["healthy"]:
        raise typer.Exit(code=1)


@app.command()
def status(
    root: RootOption = DEFAULT_ROOT,
    json_output: JsonOption = False,
) -> None:
    """Report project state without creating databases or indexes."""

    root = root.resolve()
    config = _read_config(root) or {}
    control_db = Path(str(config.get("control_db", "ops/control.sqlite")))
    index_db = Path(str(config.get("index_db", ".raytsystem/index.sqlite")))
    graph_state = CodeGraphProjection(root).status(verify_hashes=True)
    payload = {
        "project_root": str(root),
        "active_generation": _active_generation(root),
        "control_db_exists": (root / control_db).is_file(),
        "index_db_exists": (root / index_db).is_file(),
        "schema_version": config.get("schema_version"),
        "code_graph": graph_state.model_dump(mode="json"),
    }
    from raytsystem.system_status import platform_status

    payload["platform"] = platform_status(root)
    _emit(payload, as_json=json_output)


@agent_app.command("preflight")
def agent_preflight(
    skill: Annotated[str, typer.Option("--skill")],
    surface: Annotated[str, typer.Option("--surface")] = "codex_local",
    permission_mode: Annotated[
        str,
        typer.Option("--permission-mode"),
    ] = "workspace-write-managed",
    tool: Annotated[list[str] | None, typer.Option("--tool")] = None,
    egress_destination: Annotated[
        str,
        typer.Option("--egress-destination"),
    ] = "current_openai_provider",
    write_available: Annotated[bool, typer.Option("--write/--no-write")] = False,
    root: RootOption = DEFAULT_ROOT,
    json_output: JsonOption = False,
) -> None:
    """Emit a deterministic, redacted surface/skill preflight without mutating state."""

    _run_pipeline(
        lambda: AgentPolicy(root).preflight(
            surface=surface,
            permission_mode=permission_mode,
            tools=tuple(tool or ("apply_patch", "local_shell")),
            skill=skill,
            egress_destination=egress_destination,
            write_available=write_available,
        ),
        as_json=json_output,
    )


@agent_app.command("subagent-check")
def agent_subagent_check(
    payload: Annotated[str, typer.Argument(help="Bounded excerpt; output retains only its hash.")],
    role: Annotated[str, typer.Option("--role")],
    data_class: Annotated[str, typer.Option("--data-class")],
    capability: Annotated[list[str] | None, typer.Option("--capability")] = None,
    surface: Annotated[str, typer.Option("--surface")] = "work_hosted",
    destination: Annotated[
        str,
        typer.Option("--destination"),
    ] = "current_openai_provider",
    includes_local_paths: Annotated[bool, typer.Option("--includes-local-paths")] = False,
    root: RootOption = DEFAULT_ROOT,
    json_output: JsonOption = False,
) -> None:
    """Evaluate a hash-bound reviewer request; payload text is never emitted."""

    try:
        decision = AgentPolicy(root).check_subagent(
            SubagentRequest(
                surface=surface,
                role=role,
                data_class=data_class,
                capabilities=tuple(capability or ("read",)),
                destination=destination,
                payload=payload,
                includes_local_paths=includes_local_paths,
            )
        )
    except AgentPolicyError as error:
        _emit({"status": "failed", "error": str(error)}, as_json=json_output)
        raise typer.Exit(code=2) from error
    _emit(decision.to_dict(), as_json=json_output)
    if not decision.allowed:
        raise typer.Exit(code=1)


@app.command("guard-checkpoint")
def guard_checkpoint(
    root: RootOption = DEFAULT_ROOT,
    json_output: JsonOption = False,
) -> None:
    """Apply the same protected-path, secret and LINT gates used by the commit hook."""

    try:
        report = CheckpointGuard(root).check()
    except CheckpointGuardError as error:
        _emit({"status": "failed", "error": str(error)}, as_json=json_output)
        raise typer.Exit(code=2) from error
    _emit(report.to_dict(), as_json=json_output)
    if not report.ok:
        raise typer.Exit(code=1)


@app.command("rebuild-index")
def rebuild_index(
    root: RootOption = DEFAULT_ROOT,
    json_output: JsonOption = False,
) -> None:
    """Rebuild FTS5, graph, index.md and hot.md from ledger/CURRENT."""

    _run_pipeline(lambda: ProjectionService(root).rebuild(), as_json=json_output)


@app.command()
def query(
    query_text: Annotated[str, typer.Argument(help="Question for the active knowledge graph.")],
    root: RootOption = DEFAULT_ROOT,
    limit: Annotated[int, typer.Option("--limit", min=1, max=20)] = 10,
    scope: Annotated[QueryScope, typer.Option("--scope")] = QueryScope.AUTO,
    depth: Annotated[int, typer.Option("--depth", min=1, max=3)] = 2,
    json_output: JsonOption = False,
) -> None:
    """Route architecture to code graph and factual questions to verified knowledge."""

    try:
        result = QueryService(root).route(
            query_text,
            scope=scope,
            limit=limit,
            depth=depth,
        )
    except (IntegrityError, ProjectionError, QueryRejected, SearchError) as error:
        _emit({"status": "failed", "error": str(error)}, as_json=json_output)
        raise typer.Exit(code=2) from error
    if json_output:
        if result.knowledge is not None and result.fallback_reason is None:
            _emit(result.knowledge.to_dict(), as_json=True)
        else:
            _emit(result.to_dict(), as_json=True)
    else:
        typer.echo(result.render())


@app.command("lint")
def lint_command(
    root: RootOption = DEFAULT_ROOT,
    semantic: Annotated[bool, typer.Option("--semantic")] = False,
    json_output: JsonOption = False,
) -> None:
    """Run deterministic integrity lint; semantic findings remain proposal-only."""

    report = LintService(root).run(semantic=semantic)
    _emit(report.to_dict(), as_json=json_output)
    if not report.ok:
        raise typer.Exit(code=1)


@app.command()
def save(
    text: Annotated[str, typer.Argument(help="Synthesis text to stage as an inert draft.")],
    evidence: Annotated[
        list[str] | None,
        typer.Option("--evidence", "-e", help="Verified segment ID; repeat for multiple."),
    ] = None,
    title: Annotated[str, typer.Option("--title")] = "Saved synthesis",
    root: RootOption = DEFAULT_ROOT,
    json_output: JsonOption = False,
) -> None:
    """Create typed SAVE staging and a draft preview; never promote or publish."""

    _run_pipeline(
        lambda: SaveService(root).stage(
            text,
            evidence_ids=tuple(evidence or ()),
            title=title,
        ),
        as_json=json_output,
    )


@app.command("benchmark-search")
def benchmark_search(
    cases: Annotated[Path, typer.Argument(help="Workspace-local labeled JSONL cases.")],
    root: RootOption = DEFAULT_ROOT,
    backend: Annotated[str, typer.Option("--backend")] = "fts5",
    json_output: JsonOption = False,
) -> None:
    """Run the deterministic retrieval harness; QMD stays unavailable until approved."""

    def execute() -> Any:
        adapter: SearchAdapter
        if backend == "fts5":
            corpus_projector = ProjectionService(root)
            if not corpus_projector.is_current():
                corpus_projector.rebuild()
            adapter = FTS5SearchAdapter(root)
        elif backend == "qmd":
            adapter = QmdSearchAdapter(root)
        else:
            raise SearchError("Unknown search benchmark backend")
        return run_search_benchmark(adapter, load_benchmark_cases(root, cases))

    _run_pipeline(execute, as_json=json_output)


@app.command()
def ingest(
    source: Annotated[Path, typer.Argument(help="Workspace-local source path.")],
    root: RootOption = DEFAULT_ROOT,
    fixture: Annotated[bool, typer.Option("--fixture")] = False,
    json_output: JsonOption = False,
) -> None:
    """Capture, normalize, propose, validate and promote one source."""

    _run_pipeline(
        lambda: IngestPipeline(root).ingest(source, fixture=fixture),
        as_json=json_output,
    )


@app.command()
def prepare(
    source: Annotated[Path, typer.Argument(help="Workspace-local source path.")],
    root: RootOption = DEFAULT_ROOT,
    fixture: Annotated[bool, typer.Option("--fixture")] = False,
    json_output: JsonOption = False,
) -> None:
    """Stop after deterministic proposal staging; do not promote."""

    _run_pipeline(
        lambda: IngestPipeline(root).ingest(
            source,
            fixture=fixture,
            prepare_only=True,
        ),
        as_json=json_output,
    )


@app.command("validate")
def validate_run(
    run_id: Annotated[str, typer.Argument()],
    root: RootOption = DEFAULT_ROOT,
    json_output: JsonOption = False,
) -> None:
    """Validate a staged proposal and its complete evidence closure."""

    _run_pipeline(
        lambda: IngestPipeline(root).validate_run(run_id),
        as_json=json_output,
    )


@app.command()
def promote(
    run_id: Annotated[str, typer.Argument()],
    root: RootOption = DEFAULT_ROOT,
    fixture: Annotated[bool, typer.Option("--fixture")] = False,
    approval: Annotated[
        Path | None,
        typer.Option("--approval", help="Workspace-local hash-bound ApprovalRecord JSON."),
    ] = None,
    json_output: JsonOption = False,
) -> None:
    """Promote a validated run under fixture or approval policy."""

    _run_pipeline(
        lambda: IngestPipeline(root).promote_run(
            run_id,
            fixture=fixture,
            approval_path=approval,
        ),
        as_json=json_output,
    )


@proposal_app.command("export")
def export_proposal(
    run_id: Annotated[str, typer.Argument()],
    root: RootOption = DEFAULT_ROOT,
    json_output: JsonOption = False,
) -> None:
    """Export a local proposal bundle to the gitignored draft zone."""

    _run_pipeline(
        lambda: IngestPipeline(root).export_proposal(run_id),
        as_json=json_output,
    )


@proposal_app.command("import")
def import_proposal(
    run_id: Annotated[str, typer.Argument()],
    response: Annotated[Path, typer.Argument(help="Workspace-local ProposalResponse JSON.")],
    root: RootOption = DEFAULT_ROOT,
    json_output: JsonOption = False,
) -> None:
    """Import and validate a model-neutral ProposalResponse file."""

    _run_pipeline(
        lambda: IngestPipeline(root).import_proposal(run_id, response),
        as_json=json_output,
    )


@schemas_app.command("export")
def export_schemas(
    root: RootOption = DEFAULT_ROOT,
    json_output: JsonOption = False,
) -> None:
    """Export exact Pydantic JSON Schemas and a content-hashed registry."""

    root = root.resolve()
    output_dir = root / "config" / "schemas" / f"v{SCHEMA_VERSION}"
    entries: dict[str, dict[str, str]] = {}
    for model in sorted(SCHEMA_MODELS, key=lambda item: item.__name__):
        schema = model.model_json_schema()
        compact_hash = sha256_hex(
            json.dumps(
                schema,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        )
        filename = f"{model.__name__}.schema.json"
        rendered = json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        write_text_atomic(output_dir / filename, rendered)
        entries[model.__name__] = {"path": filename, "sha256": compact_hash}

    registry_payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "entries": entries,
    }
    registry_sha256 = sha256_hex(canonical_json_bytes(registry_payload))
    registry_payload["registry_sha256"] = registry_sha256
    write_text_atomic(
        output_dir / "registry.json",
        json.dumps(registry_payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    _emit(
        {
            "schema_version": SCHEMA_VERSION,
            "schema_count": len(entries),
            "registry_sha256": registry_sha256,
            "output_dir": str(output_dir),
        },
        as_json=json_output,
    )

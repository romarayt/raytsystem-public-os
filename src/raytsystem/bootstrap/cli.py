"""CLI surface for the ``bootstrap`` installer and the onboarding-prompt generator.

Registered additively via :func:`register_bootstrap_commands`, exactly like
``register_platform_commands`` / ``register_toolhub_commands`` — the existing
``init`` / ``migrate`` / ``upgrade`` commands are untouched. ``bootstrap`` is a
new root command (not a change to ``init``) because its destination is a foreign
repository named ``--target``, not the raytsystem engine checkout at ``cwd``.

This build ships the read-only ``--dry-run`` path only; ``--apply`` is gated.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

import typer

from raytsystem.bootstrap.service import BootstrapError, BootstrapService

_ALLOWED_TEMPLATES = {"auto", "software", "content", "research"}
_ALLOWED_MODES = {"managed", "vendored"}


def _emit(payload: dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        typer.echo(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return
    for key, value in payload.items():
        typer.echo(f"{key}: {value}")


def register_bootstrap_commands(app: typer.Typer) -> None:
    onboarding_app = typer.Typer(no_args_is_help=True)
    app.add_typer(onboarding_app, name="onboarding")

    @app.command("bootstrap")
    def bootstrap(
        target: Annotated[
            Path,
            typer.Option(
                "--target",
                resolve_path=True,
                file_okay=False,
                dir_okay=True,
                help="Existing repository to integrate raytsystem into (the workspace root).",
            ),
        ],
        source_type: Annotated[
            str, typer.Option("--source-type", help="auto, software, content or research")
        ] = "auto",
        template: Annotated[
            str, typer.Option("--template", help="auto, software, content or research")
        ] = "auto",
        mode: Annotated[str, typer.Option("--mode", help="managed or vendored")] = "managed",
        dry_run: Annotated[bool, typer.Option("--dry-run/--apply")] = True,
        confirm: Annotated[
            str,
            typer.Option("--confirm", help="Plan fingerprint from --dry-run (for --apply)."),
        ] = "",
        context_language: Annotated[
            str, typer.Option("--context-language", help="en or ru")
        ] = "en",
        root: Annotated[
            Path | None,
            typer.Option("--root", hidden=True, resolve_path=True, file_okay=False, dir_okay=True),
        ] = None,
        json_output: Annotated[bool, typer.Option("--json")] = False,
    ) -> None:
        """Plan (or, later, apply) a safe raytsystem integration into an existing repo."""

        if source_type not in _ALLOWED_TEMPLATES:
            raise typer.BadParameter("Unknown --source-type")
        if template not in _ALLOWED_TEMPLATES:
            raise typer.BadParameter("Unknown --template")
        if mode not in _ALLOWED_MODES:
            raise typer.BadParameter("--mode must be managed or vendored")
        if root is not None and root.resolve() != target.resolve():
            raise typer.BadParameter("--root is a deprecated alias for --target and must match it")

        if not dry_run and not confirm:
            raise typer.BadParameter(
                "--apply requires --confirm <fingerprint> from a --dry-run plan"
            )
        service = BootstrapService(target)
        try:
            if dry_run:
                payload: dict[str, Any] = service.plan(
                    template=template,
                    source_type=source_type,
                    mode=mode,
                    context_language=context_language,
                ).model_dump(mode="json")
            else:
                payload = service.apply(
                    confirm=confirm,
                    template=template,
                    source_type=source_type,
                    mode=mode,
                    context_language=context_language,
                )
        except BootstrapError as error:
            _emit({"status": "failed", "error": str(error)}, as_json=json_output)
            raise typer.Exit(code=2) from error
        _emit(payload, as_json=json_output)

    @app.command("uninstall")
    def uninstall(
        target: Annotated[
            Path,
            typer.Option(
                "--target",
                resolve_path=True,
                file_okay=False,
                dir_okay=True,
                help="Repository to remove the raytsystem installation from.",
            ),
        ],
        json_output: Annotated[bool, typer.Option("--json")] = False,
    ) -> None:
        """Remove raytsystem installer-created files; never touches user or source data."""

        try:
            result = BootstrapService(target).uninstall()
        except BootstrapError as error:
            _emit({"status": "failed", "error": str(error)}, as_json=json_output)
            raise typer.Exit(code=2) from error
        _emit(result, as_json=json_output)

    @onboarding_app.command("prompt")
    def onboarding_prompt(
        agent: Annotated[str, typer.Option("--agent", help="codex or claude")],
        target: Annotated[
            Path,
            typer.Option(
                "--target",
                resolve_path=True,
                file_okay=False,
                dir_okay=True,
                help="Repository the prompt is generated for.",
            ),
        ],
        mode: Annotated[str, typer.Option("--mode")] = "managed",
        context_language: Annotated[str, typer.Option("--context-language")] = "en",
        json_output: Annotated[bool, typer.Option("--json")] = False,
    ) -> None:
        """Emit a ready-to-paste install prompt for Claude Code or Codex."""

        try:
            result = BootstrapService(target).onboarding_prompt(
                agent=agent, mode=mode, context_language=context_language
            )
        except BootstrapError as error:
            _emit({"status": "failed", "error": str(error)}, as_json=json_output)
            raise typer.Exit(code=2) from error
        if json_output:
            typer.echo(json.dumps(result, ensure_ascii=False, sort_keys=True))
        else:
            typer.echo(result["prompt"])

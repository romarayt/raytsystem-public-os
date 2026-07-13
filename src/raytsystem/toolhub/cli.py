# ruff: noqa: B008
# Typer captures the invocation working directory through command defaults.

from __future__ import annotations

import json
from collections.abc import Callable
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any

import typer
from pydantic import ValidationError

from raytsystem.toolhub.contracts import (
    SourceKind,
    VideoSource,
    VideoToolId,
    WatchMode,
    WatchRequest,
)
from raytsystem.toolhub.dispatch import CANONICAL_TO_MCP_ALIAS, VideoToolDispatcher
from raytsystem.toolhub.errors import ToolHubError
from raytsystem.toolhub.mcp_server import serve_mcp
from raytsystem.toolhub.pipeline import WatchPipeline
from raytsystem.toolhub.registry import TOOL_SPECS

_MAX_JSON_INPUT_BYTES = 16 * 1024 * 1024


class CliSourceKind(StrEnum):
    AUTO = "auto"
    LOCAL_FILE = "local_file"
    URL = "url"
    TRANSCRIPT = "transcript"


def register_toolhub_commands(app: typer.Typer) -> None:
    """Register the first-party ``raytsystem tool`` surface on a root Typer app."""

    tool_app = typer.Typer(no_args_is_help=True)
    app.add_typer(tool_app, name="tool")

    @tool_app.command("list")
    def list_tools(
        json_output: Annotated[
            bool, typer.Option("--json", help="Emit machine-readable JSON.")
        ] = False,
    ) -> None:
        payload = {
            "tools": [
                {
                    **TOOL_SPECS[tool_id].model_dump(mode="json"),
                    "mcp_name": CANONICAL_TO_MCP_ALIAS[tool_id],
                }
                for tool_id in VideoToolId
            ]
        }
        _emit(payload, as_json=json_output)

    @tool_app.command("invoke")
    def invoke_tool(
        tool_id: Annotated[str, typer.Argument(help="Canonical video.* tool ID.")],
        input_path: Annotated[
            Path,
            typer.Option(
                "--input",
                exists=True,
                dir_okay=False,
                readable=True,
                resolve_path=True,
                help="JSON object matching the tool input schema.",
            ),
        ],
        root: Annotated[
            Path,
            typer.Option("--root", file_okay=False, dir_okay=True, resolve_path=True),
        ] = Path.cwd(),
        json_output: Annotated[
            bool, typer.Option("--json", help="Emit machine-readable JSON.")
        ] = False,
    ) -> None:
        def action() -> dict[str, Any]:
            arguments = _read_json_object(input_path)
            output = VideoToolDispatcher(root).invoke(tool_id, arguments)
            return output.model_dump(mode="json")

        _run(action, as_json=json_output)

    @tool_app.command("watch")
    def watch(
        source: Annotated[str, typer.Argument(help="URL, local path, or transcript text.")],
        source_kind: Annotated[
            CliSourceKind,
            typer.Option("--source-kind", help="How to interpret SOURCE."),
        ] = CliSourceKind.AUTO,
        mode: Annotated[
            WatchMode,
            typer.Option("--mode", help="Progressive analysis result to produce."),
        ] = WatchMode.SUMMARY,
        root: Annotated[
            Path,
            typer.Option("--root", file_okay=False, dir_okay=True, resolve_path=True),
        ] = Path.cwd(),
        json_output: Annotated[
            bool, typer.Option("--json", help="Emit machine-readable JSON.")
        ] = False,
    ) -> None:
        def action() -> dict[str, Any]:
            dispatcher = VideoToolDispatcher(root)
            request = WatchRequest(source=_video_source(source, source_kind), mode=mode)
            result = WatchPipeline(dispatcher.hub).run(request)
            return result.model_dump(mode="json")

        _run(action, as_json=json_output)

    @tool_app.command("serve-mcp")
    def serve_mcp_command(
        root: Annotated[
            Path,
            typer.Option("--root", file_okay=False, dir_okay=True, resolve_path=True),
        ] = Path.cwd(),
    ) -> None:
        serve_mcp(root)


def _read_json_object(path: Path) -> dict[str, Any]:
    if path.stat().st_size > _MAX_JSON_INPUT_BYTES:
        raise ValueError("Tool input JSON exceeds the size limit")
    try:
        payload = json.loads(path.read_bytes())
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("Tool input must be valid UTF-8 JSON") from error
    if not isinstance(payload, dict):
        raise ValueError("Tool input JSON must be an object")
    return payload


def _video_source(value: str, source_kind: CliSourceKind) -> VideoSource:
    kind = source_kind
    if kind is CliSourceKind.AUTO:
        if value.startswith(("https://", "http://")):
            kind = CliSourceKind.URL
        elif _is_file(value):
            kind = CliSourceKind.LOCAL_FILE
        else:
            kind = CliSourceKind.TRANSCRIPT
    return VideoSource(kind=SourceKind(kind.value), value=value)


def _is_file(value: str) -> bool:
    try:
        return Path(value).expanduser().is_file()
    except OSError:
        return False


def _run(action: Callable[[], dict[str, Any]], *, as_json: bool) -> None:
    try:
        payload = action()
    except (ValidationError, ValueError, ToolHubError) as error:
        message = _safe_cli_error(error)
        _emit({"status": "failed", "error": message}, as_json=as_json)
        raise typer.Exit(code=2) from error
    except Exception as error:
        _emit(
            {"status": "failed", "error": "Internal Tool Hub error"},
            as_json=as_json,
        )
        raise typer.Exit(code=2) from error
    _emit(payload, as_json=as_json)


def _safe_cli_error(error: Exception) -> str:
    if isinstance(error, ValidationError):
        return "Tool input did not match the declared schema"
    if isinstance(error, ToolHubError):
        return str(error)
    return str(error)


def _emit(payload: dict[str, Any], *, as_json: bool) -> None:
    typer.echo(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=None if as_json else 2,
            sort_keys=True,
        )
    )

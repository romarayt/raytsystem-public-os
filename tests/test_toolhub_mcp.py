from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from io import StringIO
from pathlib import Path
from typing import Any

import pytest
import typer
from typer.testing import CliRunner

import raytsystem.toolhub.mcp_server as mcp_server_module
from raytsystem.toolhub.cli import register_toolhub_commands
from raytsystem.toolhub.contracts import (
    NetworkApproval,
    SourceKind,
    VideoLimits,
    VideoSource,
    VideoToolId,
)
from raytsystem.toolhub.dispatch import (
    CANONICAL_TO_MCP_ALIAS,
    MCP_TOOL_ALIASES,
    VideoToolDispatcher,
)
from raytsystem.toolhub.errors import ToolPolicyDeniedError
from raytsystem.toolhub.mcp_server import ToolHubMcpServer, mcp_tool_definitions
from raytsystem.toolhub.registry import TOOL_SPECS


def _request(request_id: int, method: str, params: dict[str, Any] | None = None) -> str:
    return json.dumps(
        {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params or {},
        }
    )


def _initialized_prefix() -> list[str]:
    return [
        _request(
            1,
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "fixture", "version": "1"},
            },
        ),
        json.dumps(
            {
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
                "params": {},
            }
        ),
    ]


def _serve(
    tmp_path: Path,
    requests: list[str],
    *,
    dispatcher: Any | None = None,
) -> list[dict[str, Any]]:
    output = StringIO()
    active_dispatcher = dispatcher or VideoToolDispatcher(tmp_path)
    ToolHubMcpServer(
        active_dispatcher,
        input_stream=StringIO("\n".join(requests) + "\n"),
        output_stream=output,
    ).serve()
    return [json.loads(line) for line in output.getvalue().splitlines()]


def test_mcp_exposes_exactly_eight_typed_video_tools() -> None:
    definitions = mcp_tool_definitions()

    assert len(definitions) == len(VideoToolId) == 8
    assert set(MCP_TOOL_ALIASES.values()) == set(VideoToolId)
    assert set(CANONICAL_TO_MCP_ALIAS) == set(VideoToolId)
    assert {item["name"] for item in definitions} == set(MCP_TOOL_ALIASES)
    for item in definitions:
        tool_id = MCP_TOOL_ALIASES[item["name"]]
        assert item["title"] == tool_id.value
        assert item["inputSchema"] == TOOL_SPECS[tool_id].input_schema
        assert item["outputSchema"] == TOOL_SPECS[tool_id].output_schema
        assert "argv" not in json.dumps(item).lower()
        assert "shell" not in json.dumps(item).lower()


def test_mcp_lifecycle_invokes_real_local_transcript_tool(tmp_path: Path) -> None:
    requests = [
        *_initialized_prefix(),
        _request(2, "ping"),
        _request(3, "tools/list"),
        _request(
            4,
            "tools/call",
            {
                "name": "video_transcript",
                "arguments": {
                    "source": {
                        "kind": "transcript",
                        "value": "00:01 --> 00:03\nVisible fixture statement",
                    }
                },
            },
        ),
    ]

    responses = _serve(tmp_path, requests)

    assert [response["id"] for response in responses] == [1, 2, 3, 4]
    assert responses[0]["result"]["protocolVersion"] == "2025-06-18"
    assert responses[1]["result"] == {}
    assert len(responses[2]["result"]["tools"]) == 8
    call = responses[3]["result"]
    assert call["isError"] is False
    assert call["structuredContent"]["status"] == "completed"
    assert call["structuredContent"]["segments"][0]["untrusted_content"] is True
    assert call["structuredContent"] == json.loads(call["content"][0]["text"])
    assert tuple((tmp_path / "ops" / "staging" / "watch").rglob("result.json"))


def test_mcp_validation_method_and_readiness_errors_are_bounded(tmp_path: Path) -> None:
    requests = [
        _request(1, "tools/list"),
        *_initialized_prefix(),
        _request(2, "missing/method"),
        _request(
            3,
            "tools/call",
            {"name": "video_probe", "arguments": {"unexpected": "secret-fixture"}},
        ),
        "not-json",
    ]

    responses = _serve(tmp_path, requests)

    assert responses[0]["error"] == {
        "code": -32002,
        "message": "Server is not initialized",
    }
    assert responses[2]["error"] == {"code": -32601, "message": "Method not found"}
    assert responses[3]["error"] == {"code": -32602, "message": "Invalid params"}
    assert responses[4]["error"] == {"code": -32700, "message": "Parse error"}
    serialized = json.dumps(responses)
    assert "secret-fixture" not in serialized


def test_mcp_sanitizes_unexpected_dispatch_failure(tmp_path: Path) -> None:
    class ExplodingDispatcher:
        def invoke_mcp(self, alias: str, arguments: dict[str, Any]) -> Any:
            del alias, arguments
            raise RuntimeError("secret=/private/token and process stderr")

    requests = [
        *_initialized_prefix(),
        _request(
            2,
            "tools/call",
            {"name": "video_transcript", "arguments": {}},
        ),
    ]

    responses = _serve(tmp_path, requests, dispatcher=ExplodingDispatcher())

    assert responses[-1]["error"] == {
        "code": -32603,
        "message": "Internal Tool Hub error",
    }
    assert "private" not in json.dumps(responses)
    assert "stderr" not in json.dumps(responses)


def test_default_dispatcher_denies_network_without_outer_destination_guard(
    tmp_path: Path,
) -> None:
    dispatcher = VideoToolDispatcher(tmp_path)
    source = VideoSource(kind=SourceKind.URL, value="https://videos.example.test/demo.mp4")
    identity = dispatcher.hub.source_identity(source, VideoLimits())
    now = datetime.now(UTC)
    approval = NetworkApproval(
        approval_id="approval_fixture",
        destination_origin="https://videos.example.test",
        source_identity_sha256=identity.sha256,
        approved_at=now - timedelta(minutes=1),
        expires_at=now + timedelta(minutes=5),
    )

    with pytest.raises(ToolPolicyDeniedError, match="destination-bound"):
        dispatcher.invoke(
            VideoToolId.DOWNLOAD,
            {
                "source": source.model_dump(mode="python"),
                "approval": approval.model_dump(mode="python"),
            },
        )


def test_cli_registration_supports_list_invoke_and_watch(tmp_path: Path) -> None:
    app = typer.Typer()
    register_toolhub_commands(app)
    runner = CliRunner()

    listed = runner.invoke(app, ["tool", "list", "--json"])
    assert listed.exit_code == 0, listed.output
    assert len(json.loads(listed.stdout)["tools"]) == 8

    tool_input = tmp_path / "transcript-input.json"
    tool_input.write_text(
        json.dumps(
            {
                "source": {
                    "kind": "transcript",
                    "value": "00:01 --> 00:02\nCLI fixture statement",
                }
            }
        ),
        encoding="utf-8",
    )
    invoked = runner.invoke(
        app,
        [
            "tool",
            "invoke",
            "video.transcript",
            "--input",
            str(tool_input),
            "--root",
            str(tmp_path),
            "--json",
        ],
    )
    assert invoked.exit_code == 0, invoked.output
    assert json.loads(invoked.stdout)["segments"][0]["untrusted_content"] is True

    watched = runner.invoke(
        app,
        [
            "tool",
            "watch",
            "A supplied transcript fixture",
            "--source-kind",
            "transcript",
            "--mode",
            "timeline",
            "--root",
            str(tmp_path),
            "--json",
        ],
    )
    assert watched.exit_code == 0, watched.output
    watch_payload = json.loads(watched.stdout)
    assert watch_payload["source_identity"]["kind"] == "transcript"
    assert watch_payload["timeline"][0]["kind"] == "spoken"
    assert watch_payload["untrusted_content"] is True


def test_mcp_reader_rejects_and_drains_an_oversized_line_before_parsing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mcp_server_module, "MAX_REQUEST_BYTES", 512)

    responses = _serve(tmp_path, ["x" * 600, *_initialized_prefix()])

    assert responses[0] == {
        "jsonrpc": "2.0",
        "id": None,
        "error": {"code": -32600, "message": "JSON-RPC request exceeds the size limit"},
    }
    assert responses[1]["id"] == 1
    assert responses[1]["result"]["serverInfo"]["name"] == "raytsystem-toolhub"

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Any

from raytsystem.toolhub.contracts import (
    DownloadInput,
    ExtractAudioInput,
    ExtractFramesInput,
    InspectFramesInput,
    OcrFramesInput,
    ProbeInput,
    SummarizeTimelineInput,
    ToolOutput,
    TranscriptInput,
    VideoToolId,
)
from raytsystem.toolhub.runner import AllowlistedCliRunner, CliRunner
from raytsystem.toolhub.video import DestinationBoundDownloadExecutor, VideoToolHub

MCP_TOOL_ALIASES: Mapping[str, VideoToolId] = MappingProxyType(
    {
        "video_probe": VideoToolId.PROBE,
        "video_download": VideoToolId.DOWNLOAD,
        "video_transcript": VideoToolId.TRANSCRIPT,
        "video_extract_audio": VideoToolId.EXTRACT_AUDIO,
        "video_extract_frames": VideoToolId.EXTRACT_FRAMES,
        "video_ocr_frames": VideoToolId.OCR_FRAMES,
        "video_inspect_frames": VideoToolId.INSPECT_FRAMES,
        "video_summarize_timeline": VideoToolId.SUMMARIZE_TIMELINE,
    }
)
CANONICAL_TO_MCP_ALIAS: Mapping[VideoToolId, str] = MappingProxyType(
    {tool_id: alias for alias, tool_id in MCP_TOOL_ALIASES.items()}
)


class VideoToolDispatcher:
    """Validate and dispatch exactly the eight reviewed ``video.*`` contracts.

    There is deliberately no generic command or argv entry point. The default hub
    uses the allowlisted runner and has no network guard, so even a structurally
    valid approval cannot enable network access until a destination-enforcing guard
    is injected by an outer runtime.
    """

    def __init__(
        self,
        project_root: Path,
        *,
        runner: CliRunner | None = None,
        network_executor: DestinationBoundDownloadExecutor | None = None,
        hub: VideoToolHub | None = None,
    ) -> None:
        if hub is not None and (runner is not None or network_executor is not None):
            raise ValueError("An injected hub cannot be combined with execution capabilities")
        self.hub = hub or VideoToolHub(
            project_root,
            runner=runner or AllowlistedCliRunner.from_environment(),
            network_executor=network_executor,
        )

    def invoke(self, tool_id: VideoToolId | str, arguments: Mapping[str, Any]) -> ToolOutput:
        """Pydantic-validate one canonical request and invoke its fixed handler."""

        canonical_id = VideoToolId(tool_id)
        payload = dict(arguments)
        match canonical_id:
            case VideoToolId.PROBE:
                return self.hub.probe(ProbeInput.model_validate(payload))
            case VideoToolId.DOWNLOAD:
                return self.hub.download(DownloadInput.model_validate(payload))
            case VideoToolId.TRANSCRIPT:
                return self.hub.transcript(TranscriptInput.model_validate(payload))
            case VideoToolId.EXTRACT_AUDIO:
                return self.hub.extract_audio(ExtractAudioInput.model_validate(payload))
            case VideoToolId.EXTRACT_FRAMES:
                return self.hub.extract_frames(ExtractFramesInput.model_validate(payload))
            case VideoToolId.OCR_FRAMES:
                return self.hub.ocr_frames(OcrFramesInput.model_validate(payload))
            case VideoToolId.INSPECT_FRAMES:
                return self.hub.inspect_frames(InspectFramesInput.model_validate(payload))
            case VideoToolId.SUMMARIZE_TIMELINE:
                return self.hub.summarize_timeline(SummarizeTimelineInput.model_validate(payload))
        raise AssertionError("VideoToolId exhaustiveness failure")

    def invoke_mcp(self, alias: str, arguments: Mapping[str, Any]) -> ToolOutput:
        try:
            tool_id = MCP_TOOL_ALIASES[alias]
        except KeyError as error:
            raise ValueError("Unknown Tool Hub MCP tool") from error
        return self.invoke(tool_id, arguments)

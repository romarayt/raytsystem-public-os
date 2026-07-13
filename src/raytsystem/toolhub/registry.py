from __future__ import annotations

from pydantic import BaseModel

from raytsystem.toolhub.contracts import (
    DownloadInput,
    DownloadOutput,
    ExtractAudioInput,
    ExtractAudioOutput,
    ExtractFramesInput,
    ExtractFramesOutput,
    InspectFramesInput,
    InspectFramesOutput,
    OcrFramesInput,
    OcrFramesOutput,
    ProbeInput,
    ProbeOutput,
    SummarizeTimelineInput,
    SummarizeTimelineOutput,
    ToolSpec,
    TranscriptInput,
    TranscriptOutput,
    VideoToolId,
)

_MEDIA_FORMATS = (
    "mp4",
    "mov",
    "m4v",
    "webm",
    "mkv",
    "avi",
    "mpeg",
    "mpg",
    "mp3",
    "m4a",
    "wav",
    "flac",
    "ogg",
    "opus",
)
_TRANSCRIPT_FORMATS = ("vtt", "srt", "txt", "md")
_FRAME_FORMATS = ("jpg", "jpeg", "png", "webp")
_TIMELINE_FORMATS = ("json", "md")
_PROVENANCE = (
    "source_identity",
    "invocation_sha256",
    "tool_contract_version",
    "executable_versions",
    "output_hashes",
    "network_destination",
    "approval_id",
    "untrusted_content",
    "retention_policy",
)
_REDACTION = (
    "Never persist URL query/fragment, credentials, cookies, tokens, raw process stderr, "
    "or environment secrets; transcript/OCR/frame content remains explicitly untrusted."
)


def _spec(
    tool_id: VideoToolId,
    input_model: type[BaseModel],
    output_model: type[BaseModel],
    *,
    side_effects: tuple[str, ...],
    network: bool = False,
    timeout: int = 300,
    max_frames: int = 48,
    cli: tuple[str, ...] = (),
    formats: tuple[str, ...] = _MEDIA_FORMATS,
) -> ToolSpec:
    return ToolSpec.model_validate(
        {
            "tool_id": tool_id,
            "input_schema": input_model.model_json_schema(),
            "output_schema": output_model.model_json_schema(),
            "side_effects": side_effects,
            "network_access": "destination_bound_approval" if network else "none",
            "filesystem_roots": (
                "workspace:read",
                "ops/staging/watch:write",
                "launcher-pinned-runtime:read",
            ),
            "timeout_seconds": timeout,
            "max_file_bytes": 2 * 1024 * 1024 * 1024,
            "max_duration_seconds": 4 * 60 * 60,
            "max_frames": max_frames,
            "supported_formats": formats,
            "approval": "destination_bound_network" if network else "none",
            "redaction_policy": _REDACTION,
            "provenance_fields": _PROVENANCE,
            "cli_dependencies": cli,
            "generic_shell": False,
        }
    )


TOOL_SPECS: dict[VideoToolId, ToolSpec] = {
    VideoToolId.PROBE: _spec(
        VideoToolId.PROBE,
        ProbeInput,
        ProbeOutput,
        side_effects=("filesystem_read", "filesystem_write"),
        timeout=30,
        max_frames=0,
        cli=("ffprobe",),
    ),
    VideoToolId.DOWNLOAD: _spec(
        VideoToolId.DOWNLOAD,
        DownloadInput,
        DownloadOutput,
        side_effects=("network_read", "filesystem_write"),
        network=True,
        timeout=900,
        max_frames=0,
        cli=("yt-dlp",),
    ),
    VideoToolId.TRANSCRIPT: _spec(
        VideoToolId.TRANSCRIPT,
        TranscriptInput,
        TranscriptOutput,
        side_effects=("filesystem_read", "filesystem_write"),
        timeout=30,
        max_frames=0,
        formats=_TRANSCRIPT_FORMATS,
    ),
    VideoToolId.EXTRACT_AUDIO: _spec(
        VideoToolId.EXTRACT_AUDIO,
        ExtractAudioInput,
        ExtractAudioOutput,
        side_effects=("filesystem_read", "filesystem_write"),
        timeout=900,
        max_frames=0,
        cli=("ffprobe", "ffmpeg"),
    ),
    VideoToolId.EXTRACT_FRAMES: _spec(
        VideoToolId.EXTRACT_FRAMES,
        ExtractFramesInput,
        ExtractFramesOutput,
        side_effects=("filesystem_read", "filesystem_write"),
        timeout=900,
        cli=("ffprobe", "ffmpeg"),
    ),
    VideoToolId.OCR_FRAMES: _spec(
        VideoToolId.OCR_FRAMES,
        OcrFramesInput,
        OcrFramesOutput,
        side_effects=("filesystem_read", "filesystem_write"),
        timeout=300,
        cli=("tesseract",),
        formats=_FRAME_FORMATS,
    ),
    VideoToolId.INSPECT_FRAMES: _spec(
        VideoToolId.INSPECT_FRAMES,
        InspectFramesInput,
        InspectFramesOutput,
        side_effects=("filesystem_read", "filesystem_write"),
        timeout=60,
        formats=_FRAME_FORMATS,
    ),
    VideoToolId.SUMMARIZE_TIMELINE: _spec(
        VideoToolId.SUMMARIZE_TIMELINE,
        SummarizeTimelineInput,
        SummarizeTimelineOutput,
        side_effects=("filesystem_read", "filesystem_write"),
        timeout=60,
        formats=_TIMELINE_FORMATS,
    ),
}


def get_tool_spec(tool_id: VideoToolId | str) -> ToolSpec:
    return TOOL_SPECS[VideoToolId(tool_id)]

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from raytsystem.contracts.base import FrozenModel, NonEmptyStr, Sha256


class VideoToolId(StrEnum):
    PROBE = "video.probe"
    DOWNLOAD = "video.download"
    TRANSCRIPT = "video.transcript"
    EXTRACT_AUDIO = "video.extract_audio"
    EXTRACT_FRAMES = "video.extract_frames"
    OCR_FRAMES = "video.ocr_frames"
    INSPECT_FRAMES = "video.inspect_frames"
    SUMMARIZE_TIMELINE = "video.summarize_timeline"


class SourceKind(StrEnum):
    LOCAL_FILE = "local_file"
    URL = "url"
    TRANSCRIPT = "transcript"


class ToolStatus(StrEnum):
    COMPLETED = "completed"
    PARTIAL = "partial"
    BLOCKED = "blocked"


class WatchMode(StrEnum):
    SUMMARY = "summary"
    TIMELINE = "timeline"
    AUTOMATION = "automation"
    FRAMES = "frames"
    TRANSCRIPT = "transcript"


class VideoSource(FrozenModel):
    kind: SourceKind
    value: str = Field(min_length=1, max_length=8 * 1024 * 1024)
    media_type: str | None = Field(default=None, max_length=255)

    @field_validator("value")
    @classmethod
    def _no_nul(cls, value: str) -> str:
        if "\x00" in value:
            raise ValueError("Source values cannot contain NUL bytes")
        return value

    @model_validator(mode="after")
    def _safe_url_transport(self) -> VideoSource:
        if self.kind is SourceKind.URL and (
            any(ord(character) < 0x20 or ord(character) == 0x7F for character in self.value)
            or any(character.isspace() for character in self.value)
            or "\\" in self.value
        ):
            raise ValueError("Video URLs cannot contain whitespace, controls, or backslashes")
        return self


class NetworkApproval(FrozenModel):
    """Destination-bound, expiring capability for one source identity."""

    approval_id: NonEmptyStr
    action: Literal["video.download"] = "video.download"
    destination_origin: NonEmptyStr
    source_identity_sha256: Sha256
    approved_at: datetime
    expires_at: datetime

    @field_validator("approved_at", "expires_at")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("Approval timestamps must be timezone-aware")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def _window(self) -> NetworkApproval:
        if self.expires_at <= self.approved_at:
            raise ValueError("Approval expiry must follow approval time")
        if self.expires_at - self.approved_at > timedelta(minutes=15):
            raise ValueError("Network approval lifetime cannot exceed 15 minutes")
        return self


class VideoLimits(FrozenModel):
    max_file_bytes: int = Field(default=2 * 1024 * 1024 * 1024, gt=0, le=8 * 1024**3)
    max_duration_seconds: int = Field(default=4 * 60 * 60, gt=0, le=24 * 60 * 60)
    max_frames: int = Field(default=48, gt=0, le=240)
    max_transcript_bytes: int = Field(default=8 * 1024 * 1024, gt=0, le=32 * 1024**2)


class ProbeInput(FrozenModel):
    source: VideoSource
    limits: VideoLimits = Field(default_factory=VideoLimits)


class DownloadInput(FrozenModel):
    source: VideoSource
    approval: NetworkApproval | None = None
    limits: VideoLimits = Field(default_factory=VideoLimits)


class TranscriptInput(FrozenModel):
    source: VideoSource
    limits: VideoLimits = Field(default_factory=VideoLimits)


class ExtractAudioInput(FrozenModel):
    source: VideoSource
    codec: Literal["pcm_s16le"] = "pcm_s16le"
    sample_rate_hz: Literal[16000, 22050, 44100, 48000] = 16000
    channels: Literal[1, 2] = 1
    limits: VideoLimits = Field(default_factory=VideoLimits)


class ExtractFramesInput(FrozenModel):
    source: VideoSource
    timestamps_seconds: tuple[Decimal, ...] = ()
    interval_seconds: int = Field(default=15, ge=1, le=600)
    width: int = Field(default=1280, ge=160, le=1920)
    limits: VideoLimits = Field(default_factory=VideoLimits)

    @field_validator("timestamps_seconds")
    @classmethod
    def _timestamps(cls, values: tuple[Decimal, ...]) -> tuple[Decimal, ...]:
        if any(value < 0 for value in values):
            raise ValueError("Frame timestamps must be non-negative")
        if len(values) != len(set(values)):
            raise ValueError("Frame timestamps must be unique")
        return tuple(sorted(values))


class ArtifactRef(FrozenModel):
    relative_path: NonEmptyStr
    sha256: Sha256
    size_bytes: int = Field(ge=0)
    media_type: NonEmptyStr
    timestamp_seconds: Decimal | None = None


class OcrFramesInput(FrozenModel):
    frames: tuple[ArtifactRef, ...]
    language: Literal["eng", "rus", "eng+rus"] = "eng"


class OcrItem(FrozenModel):
    frame: ArtifactRef
    text_artifact: ArtifactRef
    text: str = Field(max_length=256 * 1024)
    untrusted_content: Literal[True] = True


class InspectFramesInput(FrozenModel):
    frames: tuple[ArtifactRef, ...]
    ocr_items: tuple[OcrItem, ...] = ()


class TranscriptSegment(FrozenModel):
    start_seconds: Decimal = Field(ge=0)
    end_seconds: Decimal | None = Field(default=None, ge=0)
    text: str = Field(max_length=1024 * 1024)
    untrusted_content: Literal[True] = True


class FrameEvidence(FrozenModel):
    frame: ArtifactRef
    ocr_text: str | None = Field(default=None, max_length=256 * 1024)
    visual_observation: str | None = Field(default=None, max_length=256 * 1024)
    confidence: Literal["not_assessed", "low", "medium", "high"] = "not_assessed"
    untrusted_content: Literal[True] = True


class SummarizeTimelineInput(FrozenModel):
    source_identity_sha256: Sha256
    transcript_segments: tuple[TranscriptSegment, ...] = ()
    frame_evidence: tuple[FrameEvidence, ...] = ()


class SourceIdentity(FrozenModel):
    kind: SourceKind
    sha256: Sha256
    safe_locator: NonEmptyStr
    size_bytes: int | None = Field(default=None, ge=0)


class ToolProvenance(FrozenModel):
    tool_id: VideoToolId
    tool_contract_version: Literal["1.2.0"] = "1.2.0"
    invocation_sha256: Sha256
    source_identity: SourceIdentity
    executable_versions: dict[str, str] = Field(default_factory=dict)
    output_hashes: dict[str, Sha256] = Field(default_factory=dict)
    network_destination: str | None = None
    approval_id: NonEmptyStr | None = None
    untrusted_content: Literal[True] = True
    retention_policy: Literal["retain_until_review"] = "retain_until_review"


class ToolOutput(FrozenModel):
    status: ToolStatus
    partial_reasons: tuple[NonEmptyStr, ...] = ()
    artifacts: tuple[ArtifactRef, ...] = ()
    provenance: ToolProvenance

    @model_validator(mode="after")
    def _output_hashes_match_artifacts(self) -> ToolOutput:
        expected = {artifact.relative_path: artifact.sha256 for artifact in self.artifacts}
        if self.provenance.output_hashes != expected:
            raise ValueError("Provenance output hashes must exactly match output artifacts")
        if len(expected) != len(self.artifacts):
            raise ValueError("Output artifact paths must be unique")
        return self


class MediaStream(FrozenModel):
    index: int = Field(ge=0)
    codec_type: Literal["video", "audio", "subtitle", "data", "attachment", "unknown"]
    codec_name: str | None = None
    width: int | None = Field(default=None, ge=0)
    height: int | None = Field(default=None, ge=0)


class ProbeOutput(ToolOutput):
    duration_seconds: Decimal | None = Field(default=None, ge=0)
    format_name: str | None = None
    streams: tuple[MediaStream, ...] = ()


class DownloadOutput(ToolOutput):
    downloaded_media: ArtifactRef

    @model_validator(mode="after")
    def _download_is_governed_artifact(self) -> DownloadOutput:
        if self.downloaded_media not in self.artifacts:
            raise ValueError("Downloaded media must be represented in output artifacts")
        return self


class TranscriptOutput(ToolOutput):
    method: Literal["supplied", "sidecar", "unavailable"]
    segments: tuple[TranscriptSegment, ...] = ()


class ExtractAudioOutput(ToolOutput):
    audio: ArtifactRef

    @model_validator(mode="after")
    def _audio_is_governed_artifact(self) -> ExtractAudioOutput:
        if self.audio not in self.artifacts:
            raise ValueError("Extracted audio must be represented in output artifacts")
        return self


class ExtractFramesOutput(ToolOutput):
    frames: tuple[ArtifactRef, ...] = ()

    @model_validator(mode="after")
    def _frames_are_governed_artifacts(self) -> ExtractFramesOutput:
        reports = tuple(
            artifact for artifact in self.artifacts if artifact.media_type == "application/json"
        )
        expected = {*self.frames, *reports}
        if len(reports) != 1 or expected != set(self.artifacts):
            raise ValueError("Extracted frames and one report must match output artifacts")
        return self


class OcrFramesOutput(ToolOutput):
    items: tuple[OcrItem, ...] = ()

    @model_validator(mode="after")
    def _ocr_text_is_governed_artifact(self) -> OcrFramesOutput:
        text_artifacts = {item.text_artifact for item in self.items}
        reports = {
            artifact for artifact in self.artifacts if artifact.media_type == "application/json"
        }
        if len(reports) != 1 or text_artifacts | reports != set(self.artifacts):
            raise ValueError("OCR text artifacts and one report must match output artifacts")
        return self


class InspectFramesOutput(ToolOutput):
    evidence: tuple[FrameEvidence, ...] = ()
    analysis_mode: Literal["local_evidence_manifest"] = "local_evidence_manifest"


class TimelineEvent(FrozenModel):
    timestamp_seconds: Decimal = Field(ge=0)
    kind: Literal["spoken", "screen_text", "visual", "uncertain"]
    text: str = Field(max_length=1024 * 1024)
    confidence: Literal["not_assessed", "low", "medium", "high"] = "not_assessed"
    untrusted_content: Literal[True] = True


class SummarizeTimelineOutput(ToolOutput):
    events: tuple[TimelineEvent, ...] = ()
    limitations: tuple[NonEmptyStr, ...] = ()


class ToolSpec(FrozenModel):
    tool_id: VideoToolId
    contract_version: Literal["1.2.0"] = "1.2.0"
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    side_effects: tuple[Literal["none", "filesystem_read", "filesystem_write", "network_read"], ...]
    network_access: Literal["none", "destination_bound_approval"]
    filesystem_roots: tuple[NonEmptyStr, ...]
    timeout_seconds: int = Field(gt=0, le=3600)
    max_file_bytes: int = Field(gt=0)
    max_duration_seconds: int = Field(gt=0)
    max_frames: int = Field(ge=0)
    supported_formats: tuple[NonEmptyStr, ...]
    approval: Literal["none", "destination_bound_network"]
    redaction_policy: NonEmptyStr
    provenance_fields: tuple[NonEmptyStr, ...]
    cli_dependencies: tuple[Literal["ffprobe", "ffmpeg", "yt-dlp", "tesseract"], ...] = ()
    generic_shell: Literal[False] = False


class StageRecord(FrozenModel):
    tool_id: VideoToolId
    status: ToolStatus
    partial_reasons: tuple[NonEmptyStr, ...] = ()
    artifacts: tuple[ArtifactRef, ...] = ()


class WatchRequest(FrozenModel):
    source: VideoSource
    mode: WatchMode = WatchMode.SUMMARY
    approval: NetworkApproval | None = None
    limits: VideoLimits = Field(default_factory=VideoLimits)
    frame_interval_seconds: int = Field(default=15, ge=1, le=600)


class WatchResult(FrozenModel):
    run_id: NonEmptyStr
    status: ToolStatus
    source_identity: SourceIdentity
    duration_seconds: Decimal | None = Field(default=None, ge=0)
    transcript_method: Literal["supplied", "sidecar", "unavailable"]
    stages: tuple[StageRecord, ...]
    timeline: tuple[TimelineEvent, ...] = ()
    artifacts: tuple[ArtifactRef, ...] = ()
    limitations: tuple[NonEmptyStr, ...] = ()
    tool_versions: dict[str, str] = Field(default_factory=dict)
    untrusted_content: Literal[True] = True
    retention_policy: Literal["retain_until_review"] = "retain_until_review"

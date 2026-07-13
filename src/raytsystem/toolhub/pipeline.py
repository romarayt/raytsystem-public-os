from __future__ import annotations

from decimal import Decimal
from typing import Literal

from raytsystem.contracts.base import canonical_json_bytes, sha256_hex
from raytsystem.toolhub.contracts import (
    ArtifactRef,
    DownloadInput,
    ExtractAudioInput,
    ExtractFramesInput,
    FrameEvidence,
    InspectFramesInput,
    OcrFramesInput,
    ProbeInput,
    SourceIdentity,
    SourceKind,
    StageRecord,
    SummarizeTimelineInput,
    TimelineEvent,
    ToolOutput,
    ToolStatus,
    TranscriptInput,
    TranscriptOutput,
    VideoSource,
    WatchMode,
    WatchRequest,
    WatchResult,
)
from raytsystem.toolhub.video import VideoToolHub


class WatchPipeline:
    """Progressive local-first ``/watch`` pipeline over canonical Tool Hub tools."""

    def __init__(self, hub: VideoToolHub) -> None:
        self.hub = hub

    def run(self, request: WatchRequest) -> WatchResult:
        original_identity = self.hub.source_identity(request.source, request.limits)
        working_source = request.source
        outputs: list[ToolOutput] = []
        timeline: tuple[TimelineEvent, ...] = ()
        limitations: list[str] = []
        duration = None
        transcript_method: Literal["supplied", "sidecar", "unavailable"] = "unavailable"

        if working_source.kind is SourceKind.URL:
            downloaded = self.hub.download(_download_input(request))
            outputs.append(downloaded)
            working_source = VideoSource(
                kind=SourceKind.LOCAL_FILE,
                value=str(self.hub.project_root / downloaded.downloaded_media.relative_path),
                media_type=downloaded.downloaded_media.media_type,
            )

        if working_source.kind is SourceKind.TRANSCRIPT:
            transcript_output = self.hub.transcript(
                TranscriptInput(source=working_source, limits=request.limits)
            )
            outputs.append(transcript_output)
            transcript_method = transcript_output.method
            summarized = self.hub.summarize_timeline(
                SummarizeTimelineInput(
                    source_identity_sha256=original_identity.sha256,
                    transcript_segments=transcript_output.segments,
                )
            )
            outputs.append(summarized)
            timeline = summarized.events
            limitations.extend(summarized.limitations)
            limitations.append("transcript_only_source_has_no_visual_evidence")
            return self._result(
                request,
                original_identity,
                outputs,
                duration=duration,
                transcript_method=transcript_method,
                timeline=timeline,
                limitations=limitations,
            )

        probe = self.hub.probe(ProbeInput(source=working_source, limits=request.limits))
        outputs.append(probe)
        duration = probe.duration_seconds
        has_video = any(stream.codec_type == "video" for stream in probe.streams)

        transcript: TranscriptOutput | None = None
        if request.mode is not WatchMode.FRAMES:
            transcript = self.hub.transcript(
                TranscriptInput(source=working_source, limits=request.limits)
            )
            outputs.append(transcript)
            transcript_method = transcript.method
            if transcript.method == "unavailable":
                extracted_audio = self.hub.extract_audio(
                    ExtractAudioInput(source=working_source, limits=request.limits)
                )
                outputs.append(extracted_audio)
                limitations.append("speech_to_text_adapter_not_configured_audio_staged")

        frame_evidence: tuple[FrameEvidence, ...] = ()
        frame_modes = {
            WatchMode.SUMMARY,
            WatchMode.TIMELINE,
            WatchMode.AUTOMATION,
            WatchMode.FRAMES,
        }
        if request.mode in frame_modes:
            if has_video:
                frames = self.hub.extract_frames(
                    ExtractFramesInput(
                        source=working_source,
                        interval_seconds=request.frame_interval_seconds,
                        limits=request.limits,
                    )
                )
                outputs.append(frames)
                ocr = self.hub.ocr_frames(OcrFramesInput(frames=frames.frames))
                outputs.append(ocr)
                inspected = self.hub.inspect_frames(
                    InspectFramesInput(frames=frames.frames, ocr_items=ocr.items)
                )
                outputs.append(inspected)
                frame_evidence = inspected.evidence
                limitations.extend(inspected.partial_reasons)
            else:
                limitations.append("source_has_no_video_stream")

        if request.mode is not WatchMode.FRAMES:
            summarized = self.hub.summarize_timeline(
                SummarizeTimelineInput(
                    source_identity_sha256=original_identity.sha256,
                    transcript_segments=transcript.segments if transcript is not None else (),
                    frame_evidence=frame_evidence,
                )
            )
            outputs.append(summarized)
            timeline = summarized.events
            limitations.extend(summarized.limitations)

        return self._result(
            request,
            original_identity,
            outputs,
            duration=duration,
            transcript_method=transcript_method,
            timeline=timeline,
            limitations=limitations,
        )

    @staticmethod
    def _result(
        request: WatchRequest,
        source_identity: SourceIdentity,
        outputs: list[ToolOutput],
        *,
        duration: Decimal | None,
        transcript_method: Literal["supplied", "sidecar", "unavailable"],
        timeline: tuple[TimelineEvent, ...],
        limitations: list[str],
    ) -> WatchResult:
        artifacts = _unique_artifacts(outputs)
        stage_records = tuple(
            StageRecord(
                tool_id=output.provenance.tool_id,
                status=output.status,
                partial_reasons=output.partial_reasons,
                artifacts=output.artifacts,
            )
            for output in outputs
        )
        unique_limitations = tuple(
            dict.fromkeys(
                (*limitations, *(reason for output in outputs for reason in output.partial_reasons))
            )
        )
        status = (
            ToolStatus.PARTIAL
            if unique_limitations or any(output.status is ToolStatus.PARTIAL for output in outputs)
            else ToolStatus.COMPLETED
        )
        versions = {
            executable: version
            for output in outputs
            for executable, version in output.provenance.executable_versions.items()
        }
        run_sha = sha256_hex(
            canonical_json_bytes(
                {
                    "pipeline": "raytsystem-watch-v1",
                    "source_sha256": source_identity.sha256,
                    "request": request.model_dump(mode="python"),
                }
            )
        )
        return WatchResult.model_validate(
            {
                "run_id": f"watch_{run_sha}",
                "status": status,
                "source_identity": source_identity,
                "duration_seconds": duration,
                "transcript_method": transcript_method,
                "stages": stage_records,
                "timeline": timeline,
                "artifacts": artifacts,
                "limitations": unique_limitations,
                "tool_versions": versions,
            }
        )


def _download_input(request: WatchRequest) -> DownloadInput:
    return DownloadInput(
        source=request.source,
        approval=request.approval,
        limits=request.limits,
    )


def _unique_artifacts(outputs: list[ToolOutput]) -> tuple[ArtifactRef, ...]:
    by_path: dict[str, ArtifactRef] = {}
    for output in outputs:
        for artifact in output.artifacts:
            by_path[artifact.relative_path] = artifact
    return tuple(by_path[path] for path in sorted(by_path))

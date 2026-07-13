"""First-party typed Tool Hub capabilities.

The video surface is local-first, denies network by default, and exposes no generic
shell entry point. Imported media, transcripts, OCR, and frame evidence are data,
never instructions.
"""

from raytsystem.toolhub.contracts import (
    NetworkApproval,
    VideoLimits,
    VideoSource,
    VideoToolId,
    WatchMode,
    WatchRequest,
    WatchResult,
)
from raytsystem.toolhub.pipeline import WatchPipeline
from raytsystem.toolhub.registry import TOOL_SPECS, get_tool_spec
from raytsystem.toolhub.runner import AllowlistedCliRunner, ExecutablePin
from raytsystem.toolhub.video import DestinationBoundDownloadExecutor, VideoToolHub

__all__ = [
    "TOOL_SPECS",
    "AllowlistedCliRunner",
    "DestinationBoundDownloadExecutor",
    "ExecutablePin",
    "NetworkApproval",
    "VideoLimits",
    "VideoSource",
    "VideoToolHub",
    "VideoToolId",
    "WatchMode",
    "WatchPipeline",
    "WatchRequest",
    "WatchResult",
    "get_tool_spec",
]

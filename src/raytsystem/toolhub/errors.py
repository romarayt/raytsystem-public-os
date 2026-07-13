from __future__ import annotations


class ToolHubError(RuntimeError):
    """Base error for a denied, invalid, or failed Tool Hub operation."""


class ToolPolicyDeniedError(ToolHubError):
    """The requested capability is outside the granted policy envelope."""


class ToolInputError(ToolHubError):
    """The supplied source or typed arguments are invalid."""


class ToolInputLimitError(ToolInputError):
    """A configured size, duration, or sampling limit was exceeded."""


class ToolDependencyError(ToolHubError):
    """A pinned allowlisted executable is unavailable or incompatible."""


class ToolTimeoutError(ToolHubError):
    """An allowlisted process exceeded its declared timeout."""


class ToolExecutionError(ToolHubError):
    """An allowlisted process or deterministic stage failed."""


class ToolUnsafePathError(ToolPolicyDeniedError):
    """A path escaped the declared read or staging roots."""

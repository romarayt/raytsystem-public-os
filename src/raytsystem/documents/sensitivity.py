from __future__ import annotations

import re

from raytsystem.security.sensitivity import SecretScanner

_TRIGGERS = (
    b"-----begin",
    b"akia",
    b"ghp_",
    b"gho_",
    b"ghu_",
    b"ghs_",
    b"ghr_",
    b"github_pat_",
    b"glpat-",
    b"sk-",
    b"sk-ant-",
    b"xoxb-",
    b"xoxa-",
    b"xoxp-",
    b"xoxr-",
    b"xoxs-",
    b"bearer",
    b"eyj",
    b"api_key",
    b"api-key",
    b"token",
    b"password",
    b"passwd",
    b"client_secret",
    b"client-secret",
)
_PHONE_TRIGGER = re.compile(rb"\+[1-9]\d{9}")


def contains_restricted_content(
    scanner: SecretScanner,
    data: bytes,
    *,
    path: str | None,
) -> bool:
    """Run the canonical scanner on bounded trigger windows, avoiding regex quadratic input."""

    if scanner.scan(b"", path=path).blocks_processing:
        return True
    lowered = data.lower()
    positions: set[int] = set()
    for trigger in _TRIGGERS:
        start = 0
        while (position := lowered.find(trigger, start)) >= 0:
            positions.add(position)
            start = position + max(1, len(trigger))
            if len(positions) > 4096:
                return True
    start = 0
    while (position := data.find(b"@", start)) >= 0:
        positions.add(position)
        start = position + 1
        if len(positions) > 4096:
            return True
    for match in _PHONE_TRIGGER.finditer(data):
        positions.add(match.start())
        if len(positions) > 4096:
            return True
    if b"://" in data and b"@" in data:
        start = 0
        while (position := data.find(b"://", start)) >= 0:
            positions.add(position)
            start = position + 3
    if not positions:
        return False
    windows: list[tuple[int, int]] = []
    for position in sorted(positions):
        left = max(0, position - 512)
        right = min(len(data), position + 2_560)
        if windows and left <= windows[-1][1]:
            windows[-1] = (windows[-1][0], max(windows[-1][1], right))
        else:
            windows.append((left, right))
    return any(
        scanner.scan(data[left:right], path=None).blocks_processing for left, right in windows
    )

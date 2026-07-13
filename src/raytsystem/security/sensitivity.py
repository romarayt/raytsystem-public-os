from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath


@dataclass(frozen=True)
class SensitivityDecision:
    sensitivity: str
    disposition: str
    reason_codes: tuple[str, ...]
    scanner_name: str
    scanner_version: str

    @property
    def blocks_processing(self) -> bool:
        return self.disposition == "quarantine"


class SecretScanner:
    """Deterministic high-confidence credential and direct-identifier scan."""

    name = "raytsystem_secret_patterns"
    version = "1.2.0"

    _patterns: tuple[tuple[str, re.Pattern[bytes]], ...] = (
        (
            "private_key_pem",
            re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
        ),
        ("aws_access_key", re.compile(rb"(?<![A-Z0-9])AKIA[A-Z0-9]{16}(?![A-Z0-9])")),
        (
            "github_token",
            re.compile(rb"(?<![A-Za-z0-9_])gh[pousr]_[A-Za-z0-9]{30,255}(?![A-Za-z0-9_])"),
        ),
        (
            "github_fine_grained_token",
            re.compile(rb"(?<![A-Za-z0-9_])github_pat_[A-Za-z0-9_]{20,255}"),
        ),
        ("gitlab_token", re.compile(rb"(?<![A-Za-z0-9-])glpat-[A-Za-z0-9_-]{20,255}")),
        (
            "openai_key",
            re.compile(rb"(?<![A-Za-z0-9-])sk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{20,255}"),
        ),
        (
            "anthropic_key",
            re.compile(rb"(?<![A-Za-z0-9-])sk-ant-[A-Za-z0-9_-]{20,255}"),
        ),
        (
            "slack_token",
            re.compile(rb"(?<![A-Za-z0-9-])xox[baprs]-[A-Za-z0-9-]{20,255}"),
        ),
        (
            "credential_url",
            re.compile(rb"[A-Za-z][A-Za-z0-9+.-]*://[^\s/:@]{1,128}:[^\s/@]{6,256}@"),
        ),
        (
            "bearer_token",
            re.compile(rb"(?i)\bBearer[ \t]+[A-Za-z0-9._~+/-]{20,2048}={0,2}"),
        ),
        (
            "jwt",
            re.compile(
                rb"(?<![A-Za-z0-9_-])eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"
            ),
        ),
        (
            "secret_assignment",
            re.compile(
                rb"(?im)^\s*['\"]?(?:API[_-]?KEY|TOKEN|PASSWORD|PASSWD|CLIENT[_-]?SECRET)"
                rb"['\"]?\s*[:=]\s*"
                rb"['\"]?[A-Za-z0-9._~+/@:-]{12,}"
            ),
        ),
        (
            "email_address",
            re.compile(
                rb"(?i)(?<![A-Z0-9._%+-])[A-Z0-9._%+-]{1,64}@"
                rb"[A-Z0-9.-]{1,253}\.[A-Z]{2,63}(?![A-Z0-9._%+-])"
            ),
        ),
        ("e164_phone", re.compile(rb"(?<!\d)\+[1-9]\d{9,14}(?!\d)")),
    )

    _restricted_names = frozenset({".env", "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519"})
    _restricted_suffixes = frozenset({".key", ".pem", ".p12", ".pfx"})

    def scan(self, data: bytes, *, path: str | None = None) -> SensitivityDecision:
        reasons = [code for code, pattern in self._patterns if pattern.search(data)]
        if path is not None:
            path_bytes = path.encode("utf-8")
            reasons.extend(
                f"path_{code}" for code, pattern in self._patterns if pattern.search(path_bytes)
            )
            name = PurePosixPath(path).name.lower()
            if (
                name in self._restricted_names
                or name.startswith(".env.")
                or PurePosixPath(name).suffix in self._restricted_suffixes
            ):
                reasons.append("sensitive_filename")
        if reasons:
            return SensitivityDecision(
                sensitivity="restricted",
                disposition="quarantine",
                reason_codes=tuple(sorted(set(reasons))),
                scanner_name=self.name,
                scanner_version=self.version,
            )
        return SensitivityDecision(
            sensitivity="internal",
            disposition="allow",
            reason_codes=(),
            scanner_name=self.name,
            scanner_version=self.version,
        )

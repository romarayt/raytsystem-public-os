from __future__ import annotations

import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from raytsystem.contracts import canonical_json_bytes, sha256_hex
from raytsystem.contracts.base import validate_relative_path
from raytsystem.linting import LintService
from raytsystem.security.sensitivity import SecretScanner, SensitivityDecision


class CheckpointGuardError(RuntimeError):
    """The ordinary Git checkpoint would bypass raytsystem promotion policy."""


@dataclass(frozen=True)
class GuardFinding:
    code: str
    path: str
    message: str


@dataclass(frozen=True)
class GuardReport:
    paths: tuple[str, ...]
    findings: tuple[GuardFinding, ...]
    lint_report_sha256: str | None
    report_sha256: str

    @property
    def ok(self) -> bool:
        return not self.findings

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "paths": self.paths,
            "findings": [asdict(finding) for finding in self.findings],
            "lint_report_sha256": self.lint_report_sha256,
            "report_sha256": self.report_sha256,
        }


class CheckpointGuard:
    version = "1.0.0"

    def __init__(self, root: Path, *, scanner: SecretScanner | None = None) -> None:
        self.root = root.resolve()
        self.scanner = scanner or SecretScanner()

    def classify_path(self, value: str) -> str:
        try:
            relative = validate_relative_path(value)
        except ValueError as error:
            raise CheckpointGuardError("Checkpoint path is not workspace-relative") from error
        path = PurePosixPath(relative)
        first = path.parts[0]
        if first in {"_raw", "normalized", "ledger", "inbox", ".raytsystem", ".qmd"}:
            return "protected"
        if first == "knowledge":
            return "ordinary" if len(path.parts) > 1 and path.parts[1] == "manual" else "protected"
        if (
            first == "artifacts"
            and len(path.parts) > 1
            and path.parts[1]
            in {
                "drafts",
                "outbox",
            }
        ):
            return "protected"
        if first == "ops" and len(path.parts) > 1:
            second = path.parts[1]
            if second in {
                "events",
                "checkpoints",
                "staging",
                "locks",
                "approvals",
                "task-ledger",
            }:
                return "protected"
            if second.startswith("control.sqlite"):
                return "protected"
            if second == "runs" and len(path.parts) > 2 and path.parts[2].startswith("run_"):
                return "protected"
        return "ordinary"

    def check(
        self,
        *,
        paths: tuple[str, ...] | None = None,
        staged_bytes: dict[str, bytes] | None = None,
        run_lint: bool = True,
    ) -> GuardReport:
        selected = self.staged_paths() if paths is None else paths
        try:
            normalized = tuple(sorted({validate_relative_path(path) for path in selected}))
        except ValueError as error:
            raise CheckpointGuardError("Checkpoint contains an unsafe path") from error
        findings: list[GuardFinding] = []
        for relative in normalized:
            if self.classify_path(relative) == "protected":
                findings.append(
                    GuardFinding(
                        code="protected_path",
                        path=self._safe_path(relative),
                        message="Path requires the fenced raytsystem promotion/checkpoint path",
                    )
                )
        payloads = (
            {path: self._read_staged(path) for path in normalized}
            if staged_bytes is None
            else staged_bytes
        )
        for relative, data in sorted(payloads.items()):
            if relative not in normalized or data is None:
                continue
            decision = self.scanner.scan(data, path=relative)
            if not isinstance(decision, SensitivityDecision) or decision.disposition != "allow":
                findings.append(
                    GuardFinding(
                        code="staged_secret",
                        path=f"path_sha256:{sha256_hex(relative.encode('utf-8'))}",
                        message="Staged content or filename failed the deterministic secret gate",
                    )
                )
        lint_sha256: str | None = None
        if run_lint:
            lint = LintService(self.root, scanner=self.scanner).run()
            lint_sha256 = lint.report_sha256
            if not lint.ok:
                findings.append(
                    GuardFinding(
                        code="lint_failed",
                        path="knowledge/.projection.json",
                        message="Deterministic raytsystem lint has hard findings",
                    )
                )
        ordered = tuple(
            sorted(set(findings), key=lambda item: (item.code, item.path, item.message))
        )
        reported_paths = tuple(self._safe_path(path) for path in normalized)
        material = {
            "guard_version": self.version,
            "paths": reported_paths,
            "findings": [asdict(finding) for finding in ordered],
            "lint_report_sha256": lint_sha256,
        }
        return GuardReport(
            paths=reported_paths,
            findings=ordered,
            lint_report_sha256=lint_sha256,
            report_sha256=sha256_hex(canonical_json_bytes(material)),
        )

    def staged_paths(self) -> tuple[str, ...]:
        try:
            output = subprocess.run(
                (
                    "git",
                    "-C",
                    str(self.root),
                    "diff",
                    "--cached",
                    "--no-renames",
                    "--name-only",
                    "-z",
                    "--diff-filter=ACMRD",
                ),
                capture_output=True,
                check=True,
                timeout=20,
            ).stdout
            decoded = output.decode("utf-8")
        except (OSError, subprocess.SubprocessError, UnicodeDecodeError) as error:
            raise CheckpointGuardError("Unable to inspect the local Git index") from error
        values = tuple(value for value in decoded.split("\x00") if value)
        try:
            return tuple(sorted({validate_relative_path(value) for value in values}))
        except ValueError as error:
            raise CheckpointGuardError("Git index contains an unsafe path") from error

    def _read_staged(self, relative: str) -> bytes | None:
        completed = subprocess.run(
            ("git", "-C", str(self.root), "show", f":{relative}"),
            capture_output=True,
            check=False,
            timeout=20,
        )
        if completed.returncode != 0:
            return None
        if len(completed.stdout) > 25 * 1024 * 1024:
            raise CheckpointGuardError("Staged file exceeds the checkpoint scan limit")
        return completed.stdout

    def _safe_path(self, relative: str) -> str:
        decision = self.scanner.scan(b"", path=relative)
        if isinstance(decision, SensitivityDecision) and decision.disposition == "allow":
            return relative
        return f"path_sha256:{sha256_hex(relative.encode('utf-8'))}"

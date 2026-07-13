from __future__ import annotations

import re
import subprocess
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

import yaml

from raytsystem.contracts import derive_id, sha256_hex
from raytsystem.security.paths import PathPolicyError, read_regular_file
from raytsystem.security.sensitivity import SecretScanner, SensitivityDecision


class AgentPolicyError(RuntimeError):
    """An agent surface, skill or delegation request violates repository policy."""


SKILL_ROUTES: dict[str, str] = {
    "INGEST": "skills/raytsystem-ingest/SKILL.md",
    "QUERY": "skills/raytsystem-query/SKILL.md",
    "LINT": "skills/raytsystem-lint/SKILL.md",
    "SAVE": "skills/raytsystem-save/SKILL.md",
    "RESEARCH": "skills/raytsystem-research/SKILL.md",
    "REVIEW": "skills/raytsystem-run-review/SKILL.md",
    "SECURITY_REVIEW": "skills/raytsystem-security-review/SKILL.md",
}

_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_.-]{1,127}$")
_KNOWN_CAPABILITIES = frozenset({"read", "write", "worktree", "promotion", "external_action"})


@dataclass(frozen=True)
class AgentPreflight:
    preflight_id: str
    operation_key: str
    state: str
    surface: str
    project_root: str
    project_root_sha256: str
    permission_mode: str
    git_sha: str
    dirty: bool
    tools: tuple[str, ...]
    skill: str
    skill_sha256: str
    egress_destination: str
    write_available: bool
    next_command: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SubagentRequest:
    surface: str
    role: str
    data_class: str
    capabilities: tuple[str, ...]
    destination: str
    payload: str
    includes_local_paths: bool = False

    def with_changes(self, **changes: Any) -> SubagentRequest:
        return replace(self, **changes)


@dataclass(frozen=True)
class SubagentDecision:
    decision_id: str
    allowed: bool
    surface: str
    role: str
    data_class: str
    capabilities: tuple[str, ...]
    destination: str
    payload_sha256: str
    policy_sha256: str
    reason_codes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class AgentPolicy:
    version = "1.0.0"

    def __init__(self, root: Path, *, scanner: SecretScanner | None = None) -> None:
        self.root = root.resolve()
        self.scanner = scanner or SecretScanner()
        try:
            self._policy_bytes = read_regular_file(
                self.root,
                "config/policies.yaml",
                max_bytes=1024 * 1024,
            ).data
        except (OSError, PathPolicyError) as error:
            raise AgentPolicyError("Agent policy file is missing or unsafe") from error
        self.policy_sha256 = sha256_hex(self._policy_bytes)
        try:
            policy = yaml.safe_load(self._policy_bytes)
            surfaces = policy["agent_surfaces"]
            reviewer_roles = surfaces["reviewer_roles"]
            self._surface_policies = {
                name: value
                for name, value in surfaces.items()
                if name in {"work_hosted", "codex_local"}
            }
            self._reviewer_roles = frozenset(reviewer_roles)
        except (KeyError, TypeError, yaml.YAMLError) as error:
            raise AgentPolicyError("Agent surface policy is invalid") from error
        if (
            not self._reviewer_roles
            or not all(isinstance(role, str) for role in self._reviewer_roles)
            or set(self._surface_policies) != {"work_hosted", "codex_local"}
        ):
            raise AgentPolicyError("Agent surface policy is incomplete")
        for role in self._reviewer_roles:
            if _IDENTIFIER.fullmatch(role) is None:
                raise AgentPolicyError("Agent reviewer role policy is invalid")
        for surface, surface_policy in self._surface_policies.items():
            if not isinstance(surface_policy, dict):
                raise AgentPolicyError("Agent surface policy is invalid")
            capabilities = surface_policy.get("capabilities")
            data_classes = surface_policy.get("approved_data_classes")
            destination = surface_policy.get("destination")
            if (
                _IDENTIFIER.fullmatch(surface) is None
                or not isinstance(capabilities, list)
                or not capabilities
                or not all(isinstance(value, str) for value in capabilities)
                or not set(capabilities).issubset(_KNOWN_CAPABILITIES)
                or not isinstance(data_classes, list)
                or not data_classes
                or not all(
                    isinstance(value, str) and _IDENTIFIER.fullmatch(value) is not None
                    for value in data_classes
                )
                or not isinstance(destination, str)
                or _IDENTIFIER.fullmatch(destination) is None
            ):
                raise AgentPolicyError("Agent surface policy is invalid")

    def resolve_skill(self, operation: str, *, untrusted_payload: str = "") -> str:
        del untrusted_payload
        normalized = operation.strip().upper().replace("-", "_").replace(" ", "_")
        try:
            relative = SKILL_ROUTES[normalized]
        except KeyError as error:
            raise AgentPolicyError("Unknown raytsystem operation") from error
        try:
            read_regular_file(self.root, relative, max_bytes=1024 * 1024)
        except (OSError, PathPolicyError) as error:
            raise AgentPolicyError("Routed raytsystem skill is missing or unsafe") from error
        return relative

    def preflight(
        self,
        *,
        surface: str,
        permission_mode: str,
        tools: tuple[str, ...],
        skill: str,
        egress_destination: str,
        write_available: bool,
    ) -> AgentPreflight:
        for value, label in (
            (surface, "surface"),
            (permission_mode, "permission mode"),
            (skill, "skill"),
            (egress_destination, "egress destination"),
        ):
            if _IDENTIFIER.fullmatch(value) is None:
                raise AgentPolicyError(f"Invalid {label}")
        normalized_tools = tuple(sorted(set(tools)))
        if not normalized_tools or any(
            _IDENTIFIER.fullmatch(tool) is None for tool in normalized_tools
        ):
            raise AgentPolicyError("Preflight tools are invalid")
        relative = f"skills/{skill}/SKILL.md"
        if relative not in SKILL_ROUTES.values():
            raise AgentPolicyError("Preflight skill is not routed by AGENTS.md")
        try:
            skill_bytes = read_regular_file(
                self.root,
                relative,
                max_bytes=1024 * 1024,
            ).data
        except (OSError, PathPolicyError) as error:
            raise AgentPolicyError("Preflight skill is missing or unsafe") from error
        git_sha, dirty = self._git_state()
        material = {
            "policy_version": self.version,
            "policy_sha256": self.policy_sha256,
            "surface": surface,
            "project_root_sha256": sha256_hex(self.root.as_posix().encode("utf-8")),
            "permission_mode": permission_mode,
            "git_sha": git_sha,
            "dirty": dirty,
            "tools": normalized_tools,
            "skill": skill,
            "skill_sha256": sha256_hex(skill_bytes),
            "egress_destination": egress_destination,
            "write_available": write_available,
        }
        operation_key = derive_id("op", material)
        state = "READY" if write_available else "CHECKPOINTED_FOR_RESUME"
        next_command = (
            None
            if write_available
            else f"uv run raytsystem agent preflight --skill {skill} --write"
        )
        return AgentPreflight(
            preflight_id=derive_id("apf", {"operation_key": operation_key}),
            operation_key=operation_key,
            state=state,
            surface=surface,
            project_root=".",
            project_root_sha256=str(material["project_root_sha256"]),
            permission_mode=permission_mode,
            git_sha=git_sha,
            dirty=dirty,
            tools=normalized_tools,
            skill=skill,
            skill_sha256=sha256_hex(skill_bytes),
            egress_destination=egress_destination,
            write_available=write_available,
            next_command=next_command,
        )

    def check_subagent(self, request: SubagentRequest) -> SubagentDecision:
        for value in (
            request.surface,
            request.role,
            request.data_class,
            request.destination,
        ):
            if _IDENTIFIER.fullmatch(value) is None:
                raise AgentPolicyError("Subagent request metadata is invalid")
            metadata_sensitivity = self.scanner.scan(value.encode("utf-8"), path=None)
            if (
                not isinstance(metadata_sensitivity, SensitivityDecision)
                or metadata_sensitivity.disposition != "allow"
            ):
                raise AgentPolicyError("Subagent request metadata is invalid")
        capabilities = tuple(sorted(set(request.capabilities)))
        reasons: set[str] = set()
        if (
            not capabilities
            or len(capabilities) != len(request.capabilities)
            or not set(capabilities).issubset(_KNOWN_CAPABILITIES)
        ):
            reasons.add("invalid_capabilities")
        if request.role not in self._reviewer_roles:
            reasons.add("role_not_allowed")
        payload_bytes = request.payload.encode("utf-8")
        if not payload_bytes or len(payload_bytes) > 64 * 1024:
            reasons.add("payload_size_invalid")
        sensitivity = self.scanner.scan(payload_bytes, path=None)
        if not isinstance(sensitivity, SensitivityDecision) or sensitivity.disposition != "allow":
            reasons.add("payload_sensitive")

        surface_policy = self._surface_policies.get(request.surface)
        if surface_policy is None:
            reasons.add("surface_not_allowed")
        else:
            configured_capabilities = tuple(sorted(surface_policy.get("capabilities", ())))
            configured_data_classes = set(surface_policy.get("approved_data_classes", ()))
            if capabilities != configured_capabilities:
                reasons.add("reviewer_capabilities_denied")
            if request.data_class not in configured_data_classes:
                reasons.add("reviewer_data_class_denied")
            if request.destination != surface_policy.get("destination"):
                reasons.add("reviewer_destination_denied")
            if surface_policy.get("local_paths") == "forbidden" and request.includes_local_paths:
                reasons.add("reviewer_local_paths_denied")

        payload_sha256 = sha256_hex(payload_bytes)
        decision_material = {
            "policy_version": self.version,
            "policy_sha256": self.policy_sha256,
            "surface": request.surface,
            "role": request.role,
            "data_class": request.data_class,
            "capabilities": capabilities,
            "destination": request.destination,
            "payload_sha256": payload_sha256,
            "includes_local_paths": request.includes_local_paths,
            "reason_codes": tuple(sorted(reasons)),
        }
        return SubagentDecision(
            decision_id=derive_id("apol", decision_material),
            allowed=not reasons,
            surface=request.surface,
            role=request.role,
            data_class=request.data_class,
            capabilities=capabilities,
            destination=request.destination,
            payload_sha256=payload_sha256,
            policy_sha256=self.policy_sha256,
            reason_codes=tuple(sorted(reasons)),
        )

    def _git_state(self) -> tuple[str, bool]:
        try:
            head = (
                subprocess.run(
                    ("git", "-C", str(self.root), "rev-parse", "HEAD"),
                    capture_output=True,
                    check=True,
                    timeout=10,
                )
                .stdout.decode("ascii")
                .strip()
            )
            status = subprocess.run(
                ("git", "-C", str(self.root), "status", "--porcelain=v1", "-z"),
                capture_output=True,
                check=True,
                timeout=10,
            ).stdout
        except (OSError, subprocess.SubprocessError, UnicodeDecodeError):
            return "unavailable", False
        if re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", head) is None:
            raise AgentPolicyError("Git HEAD identity is malformed")
        return head, bool(status)

from __future__ import annotations

import os
import re
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, cast

import yaml
from pydantic import ValidationError
from yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode
from yaml.tokens import AliasToken, AnchorToken

from raytsystem.contracts import (
    AgentDefinition,
    InstructionDocument,
    PackManifest,
    RuntimeAdapterDefinition,
    Sensitivity,
    SkillDefinition,
    TrustClass,
    canonical_json_bytes,
    sha256_hex,
)
from raytsystem.security.paths import PathPolicyError, read_regular_file
from raytsystem.security.sensitivity import SecretScanner
from raytsystem.storage import IntegrityError


class CatalogError(IntegrityError):
    """An allowlisted catalog object is malformed, unsafe or internally inconsistent."""


_DIRECTORY_NAME = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")
_INSTRUCTION_FILES: tuple[tuple[str, str, str, str], ...] = (
    ("instruction_agents", "agent_routing", "Agent routing", "AGENTS.md"),
    ("instruction_work", "work_bootstrap", "Work bootstrap", "WORK.md"),
    ("instruction_claude", "claude_instructions", "Claude instructions", "CLAUDE.md"),
)
_BUILTIN_PACKS: dict[str, str] = {
    "core": "pack_core",
    "starter": "pack_starter",
}


@dataclass(frozen=True)
class CatalogSnapshot:
    catalog_sha256: str
    packs: tuple[PackManifest, ...]
    agents: tuple[AgentDefinition, ...]
    skills: tuple[SkillDefinition, ...]
    instructions: tuple[InstructionDocument, ...]
    adapters: tuple[RuntimeAdapterDefinition, ...]
    skill_bodies: dict[str, str] = field(default_factory=dict, repr=False, compare=False)
    instruction_bodies: dict[str, str] = field(default_factory=dict, repr=False, compare=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "catalog_sha256": self.catalog_sha256,
            "packs": [item.model_dump(mode="json") for item in self.packs],
            "agents": [item.model_dump(mode="json") for item in self.agents],
            "skills": [item.model_dump(mode="json") for item in self.skills],
            "instructions": [item.model_dump(mode="json") for item in self.instructions],
            "adapters": [item.model_dump(mode="json") for item in self.adapters],
        }

    def skill(self, skill_id: str) -> SkillDefinition | None:
        return next((item for item in self.skills if item.skill_id == skill_id), None)

    def agent(self, agent_id: str) -> AgentDefinition | None:
        return next((item for item in self.agents if item.agent_id == agent_id), None)

    def instruction(self, document_id: str) -> InstructionDocument | None:
        return next(
            (item for item in self.instructions if item.document_id == document_id),
            None,
        )


class CatalogService:
    """Discover inert definitions only from fixed project-local roots."""

    max_definition_bytes = 512 * 1024
    max_document_bytes = 1024 * 1024
    max_yaml_depth = 32
    max_yaml_nodes = 10_000
    max_yaml_scalar_chars = 64 * 1024

    def __init__(self, root: Path, *, scanner: SecretScanner | None = None) -> None:
        self.root = root.resolve()
        self.scanner = scanner or SecretScanner()

    def load(self) -> CatalogSnapshot:
        pack_directories = self._safe_directories("packs")
        packs = [self._load_pack(directory) for directory in pack_directories]
        self._unique(packs, "pack_id", "pack")

        owner_by_skill: dict[str, str] = {}
        for pack in sorted(packs, key=lambda item: (item.optional, item.pack_id)):
            for skill_id in pack.skill_ids:
                if skill_id in owner_by_skill:
                    raise CatalogError("Skill cannot be owned by multiple packs")
                owner_by_skill[skill_id] = pack.pack_id

        trust_by_pack = {pack.pack_id: pack.trust_class for pack in packs}
        skills, skill_bodies = self._load_skills(owner_by_skill, trust_by_pack)
        discovered_skill_ids = {skill.skill_id for skill in skills}
        unowned = tuple(
            sorted(skill_id for skill_id in discovered_skill_ids if skill_id not in owner_by_skill)
        )
        if unowned:
            local_pack = PackManifest(
                pack_id="pack_local",
                name="Local workspace skills",
                version="unversioned",
                description=(
                    "Allowlisted skills discovered in this workspace but not assigned to a pack."
                ),
                license_expression="Unspecified",
                trust_class=TrustClass.USER,
                skill_ids=unowned,
            )
            packs.append(local_pack)
            trust_by_pack[local_pack.pack_id] = local_pack.trust_class
            for skill_id in unowned:
                owner_by_skill[skill_id] = local_pack.pack_id
            skills, skill_bodies = self._load_skills(owner_by_skill, trust_by_pack)

        instructions, instruction_bodies = self._load_instructions()
        adapters = self._load_adapters()
        agents = self._load_agents(pack_directories)
        self._validate_references(packs, agents, skills, instructions, adapters)

        sorted_packs = tuple(sorted(packs, key=lambda item: item.pack_id))
        sorted_agents = tuple(sorted(agents, key=lambda item: item.agent_id))
        sorted_skills = tuple(sorted(skills, key=lambda item: item.skill_id))
        sorted_instructions = tuple(sorted(instructions, key=lambda item: item.document_id))
        sorted_adapters = tuple(sorted(adapters, key=lambda item: item.adapter_id))
        fingerprint_material = {
            "service": "raytsystem_catalog_v1",
            "packs": sorted_packs,
            "agents": sorted_agents,
            "skills": sorted_skills,
            "instructions": sorted_instructions,
            "adapters": sorted_adapters,
        }
        return CatalogSnapshot(
            catalog_sha256=sha256_hex(canonical_json_bytes(fingerprint_material)),
            packs=sorted_packs,
            agents=sorted_agents,
            skills=sorted_skills,
            instructions=sorted_instructions,
            adapters=sorted_adapters,
            skill_bodies=skill_bodies,
            instruction_bodies=instruction_bodies,
        )

    def _load_pack(self, directory: Path) -> PackManifest:
        relative = (directory / "pack.yaml").relative_to(self.root).as_posix()
        payload = self._read_yaml(relative)
        try:
            pack = PackManifest.model_validate(payload)
        except ValidationError as error:
            raise CatalogError("Pack manifest is invalid") from error
        expected_id = _BUILTIN_PACKS.get(directory.name)
        if expected_id is not None and pack.pack_id != expected_id:
            raise CatalogError("Built-in pack directory and pack ID disagree")
        if expected_id is None and pack.trust_class is TrustClass.OFFICIAL:
            # A manifest cannot self-assert project provenance. Unknown packs
            # remain user-trusted until an installation policy attests them.
            pack = pack.model_copy(update={"trust_class": TrustClass.USER})
        return pack

    def _load_agents(self, pack_directories: list[Path]) -> list[AgentDefinition]:
        agents: list[AgentDefinition] = []
        for pack_directory in pack_directories:
            agents_directory = pack_directory / "agents"
            if not agents_directory.exists() and not agents_directory.is_symlink():
                continue
            self._assert_directory(agents_directory)
            for path in sorted(agents_directory.iterdir(), key=lambda item: item.name):
                metadata = os.lstat(path)
                if not stat.S_ISREG(metadata.st_mode) and not stat.S_ISLNK(metadata.st_mode):
                    continue
                if path.suffix != ".yaml":
                    continue
                relative = path.relative_to(self.root).as_posix()
                payload = self._read_yaml(relative)
                try:
                    agent = AgentDefinition.model_validate(payload)
                except ValidationError as error:
                    raise CatalogError("Agent definition is invalid") from error
                if path.stem != agent.agent_id:
                    raise CatalogError("Agent filename and agent ID disagree")
                agents.append(agent)
        self._unique(agents, "agent_id", "agent")
        return agents

    def _load_skills(
        self,
        owner_by_skill: dict[str, str],
        trust_by_pack: dict[str, TrustClass],
    ) -> tuple[list[SkillDefinition], dict[str, str]]:
        skills: list[SkillDefinition] = []
        bodies: dict[str, str] = {}
        for skill_directory in self._safe_directories("skills"):
            skill_id = skill_directory.name
            relative = (skill_directory / "SKILL.md").relative_to(self.root).as_posix()
            data = self._read(relative, max_bytes=self.max_document_bytes)
            decision = self._scan(data, relative)
            if decision.blocks_processing and any(
                reason.startswith("path_") or reason == "sensitive_filename"
                for reason in decision.reason_codes
            ):
                raise CatalogError("Restricted skill path cannot be disclosed")
            sensitivity = self._sensitivity(decision.sensitivity)
            owner = owner_by_skill.get(skill_id, "pack_local")
            trust = trust_by_pack.get(owner, TrustClass.USER)
            if decision.blocks_processing:
                name = skill_id
                description = "Content withheld by the sensitivity policy."
                version = "withheld"
                permissions: list[str] = []
                test_status: Literal["pass", "pending", "unavailable"] = "unavailable"
            else:
                metadata = self._frontmatter(data)
                name = metadata.get("name", skill_id)
                description = metadata.get("description", "No description supplied.")
                version = metadata.get("version", "unversioned")
                permissions = metadata.get("permissions", [])
                raw_test_status = metadata.get("test_status", "pending")
                if not isinstance(name, str) or not isinstance(description, str):
                    raise CatalogError("Skill frontmatter name and description must be strings")
                if not isinstance(version, str) or not isinstance(permissions, list):
                    raise CatalogError("Skill frontmatter version or permissions are invalid")
                if not all(isinstance(item, str) for item in permissions):
                    raise CatalogError("Skill permissions must be strings")
                if raw_test_status not in {"pass", "pending", "unavailable"}:
                    raise CatalogError("Skill test status is invalid")
                test_status = cast(
                    Literal["pass", "pending", "unavailable"],
                    raw_test_status,
                )
            try:
                skill = SkillDefinition(
                    skill_id=skill_id,
                    name=name,
                    description=description,
                    version=version,
                    source_path=relative,
                    source_sha256=sha256_hex(data),
                    pack_id=owner,
                    trust_class=trust,
                    sensitivity=sensitivity,
                    permissions=tuple(permissions),
                    test_status=test_status,
                    enabled=not decision.blocks_processing,
                )
            except ValidationError as error:
                raise CatalogError("Skill definition is invalid") from error
            skills.append(skill)
            if not decision.blocks_processing:
                bodies[skill_id] = self._decode(data, relative)
        self._unique(skills, "skill_id", "skill")
        return skills, bodies

    def _load_instructions(
        self,
    ) -> tuple[list[InstructionDocument], dict[str, str]]:
        documents: list[InstructionDocument] = []
        bodies: dict[str, str] = {}
        for document_id, kind, label, relative in _INSTRUCTION_FILES:
            path = self.root / relative
            if not path.exists() and not path.is_symlink():
                continue
            data = self._read(relative, max_bytes=self.max_document_bytes)
            decision = self._scan(data, relative)
            document = InstructionDocument(
                document_id=document_id,
                kind=kind,
                label=label,
                path=relative,
                content_sha256=sha256_hex(data),
                size_bytes=len(data),
                sensitivity=self._sensitivity(decision.sensitivity),
                editable=False,
            )
            documents.append(document)
            if not decision.blocks_processing:
                bodies[document_id] = self._decode(data, relative)
        return documents, bodies

    def _load_adapters(self) -> list[RuntimeAdapterDefinition]:
        payload = self._read_yaml("config/runtime-adapters.yaml")
        adapters_payload = payload.get("adapters")
        if not isinstance(adapters_payload, list):
            raise CatalogError("Runtime adapter registry is invalid")
        adapters: list[RuntimeAdapterDefinition] = []
        for item in adapters_payload:
            try:
                adapters.append(RuntimeAdapterDefinition.model_validate(item))
            except ValidationError as error:
                raise CatalogError("Runtime adapter definition is invalid") from error
        self._unique(adapters, "adapter_id", "runtime adapter")
        return adapters

    def _validate_references(
        self,
        packs: list[PackManifest],
        agents: list[AgentDefinition],
        skills: list[SkillDefinition],
        instructions: list[InstructionDocument],
        adapters: list[RuntimeAdapterDefinition],
    ) -> None:
        pack_ids = {item.pack_id for item in packs}
        agent_by_id = {item.agent_id: item for item in agents}
        skill_ids = {item.skill_id for item in skills}
        adapter_ids = {item.adapter_id for item in adapters}
        context_paths = {item.path for item in instructions} | {item.source_path for item in skills}
        for pack in packs:
            missing_agents = set(pack.agent_ids) - set(agent_by_id)
            missing_skills = set(pack.skill_ids) - skill_ids
            if missing_agents or missing_skills:
                raise CatalogError("Pack references an unknown agent or skill")
            actual_agents = {agent.agent_id for agent in agents if agent.pack_id == pack.pack_id}
            if actual_agents != set(pack.agent_ids):
                raise CatalogError("Pack agent index disagrees with agent definitions")
            if set(pack.context_paths) - context_paths:
                raise CatalogError("Pack references unavailable context")
        for agent in agents:
            if agent.pack_id not in pack_ids:
                raise CatalogError("Agent references an unknown pack")
            if agent.runtime_adapter_id not in adapter_ids:
                raise CatalogError("Agent references an unknown runtime adapter")
            if set(agent.skill_ids) - skill_ids:
                raise CatalogError("Agent references an unknown skill")
            if set(agent.context_paths) - context_paths:
                raise CatalogError("Agent references unavailable context")

    def _safe_directories(self, relative: str) -> list[Path]:
        root = self.root / relative
        if not root.exists() and not root.is_symlink():
            return []
        self._assert_directory(root)
        directories: list[Path] = []
        for candidate in sorted(root.iterdir(), key=lambda item: item.name):
            metadata = os.lstat(candidate)
            if stat.S_ISLNK(metadata.st_mode):
                raise CatalogError("Catalog roots cannot contain symlinked entries")
            if not stat.S_ISDIR(metadata.st_mode):
                continue
            if _DIRECTORY_NAME.fullmatch(candidate.name) is None:
                raise CatalogError("Catalog directory name is invalid")
            directories.append(candidate)
        return directories

    @staticmethod
    def _assert_directory(path: Path) -> None:
        try:
            metadata = os.lstat(path)
        except OSError as error:
            raise CatalogError("Catalog directory is unavailable") from error
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise CatalogError("Catalog root must be a real directory")

    def _read_yaml(self, relative: str) -> dict[str, Any]:
        data = self._read(relative, max_bytes=self.max_definition_bytes)
        decision = self._scan(data, relative)
        if decision.blocks_processing:
            raise CatalogError("Catalog definition is restricted by the sensitivity policy")
        return self._parse_yaml_mapping(data, label="Catalog YAML")

    def _read(self, relative: str, *, max_bytes: int) -> bytes:
        try:
            return read_regular_file(self.root, relative, max_bytes=max_bytes).data
        except (OSError, PathPolicyError) as error:
            raise CatalogError("Catalog file is missing or unsafe") from error

    def _scan(self, data: bytes, relative: str) -> Any:
        try:
            return self.scanner.scan(data, path=relative)
        except Exception as error:
            raise CatalogError("Catalog sensitivity scanner failed closed") from error

    @staticmethod
    def _sensitivity(value: str) -> Sensitivity:
        try:
            return Sensitivity(value)
        except ValueError as error:
            raise CatalogError("Catalog sensitivity classification is invalid") from error

    @staticmethod
    def _decode(data: bytes, relative: str) -> str:
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError as error:
            raise CatalogError(f"Catalog text is not UTF-8: {relative}") from error

    @classmethod
    def _frontmatter(cls, data: bytes) -> dict[str, Any]:
        # Normalize Windows text-mode line endings before structural checks;
        # frontmatter semantics are line-based, so this is lossless.
        if data.startswith(b"---\r\n"):
            data = data.replace(b"\r\n", b"\n")
        if not data.startswith(b"---\n"):
            raise CatalogError("Skill must start with YAML frontmatter")
        boundary = data.find(b"\n---\n", 4)
        if boundary < 0:
            raise CatalogError("Skill frontmatter is not terminated")
        return cls._parse_yaml_mapping(data[4:boundary], label="Skill frontmatter")

    @classmethod
    def _parse_yaml_mapping(cls, data: bytes, *, label: str) -> dict[str, Any]:
        try:
            for token in yaml.scan(data):
                if isinstance(token, AliasToken | AnchorToken):
                    raise CatalogError(f"{label} aliases and anchors are forbidden")
            node = yaml.compose(data, Loader=yaml.SafeLoader)
            if node is None:
                raise CatalogError(f"{label} must not be empty")
            cls._validate_yaml_node(node, depth=0, seen=set(), counter=[0], label=label)
            payload = yaml.safe_load(data)
        except CatalogError:
            raise
        except (yaml.YAMLError, RecursionError) as error:
            raise CatalogError(f"{label} is invalid") from error
        cls._validate_json_tree(payload, label=label)
        if not isinstance(payload, dict):
            raise CatalogError(f"{label} must be an object")
        return payload

    @classmethod
    def _validate_yaml_node(
        cls,
        node: Node,
        *,
        depth: int,
        seen: set[int],
        counter: list[int],
        label: str,
    ) -> None:
        if depth > cls.max_yaml_depth:
            raise CatalogError(f"{label} exceeds the nesting limit")
        identity = id(node)
        if identity in seen:
            raise CatalogError(f"{label} aliases are forbidden")
        seen.add(identity)
        counter[0] += 1
        if counter[0] > cls.max_yaml_nodes:
            raise CatalogError(f"{label} exceeds the node limit")
        if isinstance(node, ScalarNode):
            if len(node.value) > cls.max_yaml_scalar_chars:
                raise CatalogError(f"{label} contains an oversized scalar")
            return
        if isinstance(node, SequenceNode):
            for item in node.value:
                cls._validate_yaml_node(
                    item,
                    depth=depth + 1,
                    seen=seen,
                    counter=counter,
                    label=label,
                )
            return
        if isinstance(node, MappingNode):
            keys: set[tuple[str, str]] = set()
            for key, value in node.value:
                if not isinstance(key, ScalarNode):
                    raise CatalogError(f"{label} mapping keys must be scalars")
                key_identity = (key.tag, key.value)
                if key_identity in keys:
                    raise CatalogError(f"{label} contains a duplicate key")
                if key.tag == "tag:yaml.org,2002:merge" or key.value == "<<":
                    raise CatalogError(f"{label} merge keys are forbidden")
                keys.add(key_identity)
                cls._validate_yaml_node(
                    key,
                    depth=depth + 1,
                    seen=seen,
                    counter=counter,
                    label=label,
                )
                cls._validate_yaml_node(
                    value,
                    depth=depth + 1,
                    seen=seen,
                    counter=counter,
                    label=label,
                )
            return
        raise CatalogError(f"{label} contains an unsupported node")

    @classmethod
    def _validate_json_tree(cls, payload: Any, *, label: str) -> None:
        stack: list[tuple[Any, int]] = [(payload, 0)]
        visited: set[int] = set()
        nodes = 0
        while stack:
            value, depth = stack.pop()
            nodes += 1
            if nodes > cls.max_yaml_nodes or depth > cls.max_yaml_depth:
                raise CatalogError(f"{label} exceeds structural limits")
            if isinstance(value, str):
                if len(value) > cls.max_yaml_scalar_chars:
                    raise CatalogError(f"{label} contains an oversized scalar")
                continue
            if value is None or isinstance(value, bool | int):
                continue
            if isinstance(value, dict):
                identity = id(value)
                if identity in visited:
                    raise CatalogError(f"{label} contains a cyclic value")
                visited.add(identity)
                for key, item in value.items():
                    if not isinstance(key, str):
                        raise CatalogError(f"{label} mapping keys must be strings")
                    stack.append((item, depth + 1))
                continue
            if isinstance(value, list):
                identity = id(value)
                if identity in visited:
                    raise CatalogError(f"{label} contains a cyclic value")
                visited.add(identity)
                stack.extend((item, depth + 1) for item in value)
                continue
            raise CatalogError(f"{label} must contain only finite JSON-compatible values")

    @staticmethod
    def _unique(items: list[Any], attribute: str, label: str) -> None:
        values = [str(getattr(item, attribute)) for item in items]
        if len(values) != len(set(values)):
            raise CatalogError(f"Duplicate {label} ID")

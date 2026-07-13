from __future__ import annotations

import math
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml
from yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode
from yaml.tokens import AliasToken, AnchorToken

from raytsystem.documents.contracts import ExtractedLink, MarkdownMetadata

_ATX_HEADING = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$")
_WIKILINK = re.compile(r"(!)?\[\[([^\]\r\n]+)\]\]")
_MARKDOWN_IMAGE = re.compile(r"!\[([^\]]*)\]\(([^)\r\n]+)\)")
_MARKDOWN_LINK = re.compile(r"(?<!!)\[([^\]]*)\]\(([^)\r\n]+)\)")
_INLINE_TAG = re.compile(r"(?<![\w/#])#([\w][\w/-]{0,127})", flags=re.UNICODE)
_FENCE = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})")
_MAX_FRONTMATTER_BYTES = 64 * 1024
_MAX_YAML_DEPTH = 32
_MAX_YAML_NODES = 10_000
_MAX_YAML_SCALAR = 64 * 1024
_MAX_LINKS = 20_000
_MAX_CONTEXT = 320


class FrontmatterError(ValueError):
    """Frontmatter is malformed or exceeds the bounded metadata grammar."""


def _validate_yaml_node(
    node: Node,
    *,
    depth: int,
    seen: set[int],
    counter: list[int],
) -> None:
    if depth > _MAX_YAML_DEPTH:
        raise FrontmatterError("frontmatter_depth_limit")
    identity = id(node)
    if identity in seen:
        raise FrontmatterError("frontmatter_alias_forbidden")
    seen.add(identity)
    counter[0] += 1
    if counter[0] > _MAX_YAML_NODES:
        raise FrontmatterError("frontmatter_node_limit")
    if isinstance(node, ScalarNode):
        if len(node.value) > _MAX_YAML_SCALAR:
            raise FrontmatterError("frontmatter_scalar_limit")
        return
    if isinstance(node, SequenceNode):
        for item in node.value:
            _validate_yaml_node(item, depth=depth + 1, seen=seen, counter=counter)
        return
    if isinstance(node, MappingNode):
        keys: set[tuple[str, str]] = set()
        for key, value in node.value:
            if not isinstance(key, ScalarNode):
                raise FrontmatterError("frontmatter_key_type")
            key_identity = (key.tag, key.value)
            if key_identity in keys or key.value == "<<":
                raise FrontmatterError("frontmatter_duplicate_or_merge_key")
            keys.add(key_identity)
            _validate_yaml_node(key, depth=depth + 1, seen=seen, counter=counter)
            _validate_yaml_node(value, depth=depth + 1, seen=seen, counter=counter)
        return
    raise FrontmatterError("frontmatter_node_type")


def _validate_tree(value: Any) -> None:
    stack: list[tuple[Any, int]] = [(value, 0)]
    seen: set[int] = set()
    count = 0
    while stack:
        item, depth = stack.pop()
        count += 1
        if count > _MAX_YAML_NODES or depth > _MAX_YAML_DEPTH:
            raise FrontmatterError("frontmatter_structure_limit")
        if item is None or isinstance(item, bool | int):
            continue
        if isinstance(item, float):
            if not math.isfinite(item):
                raise FrontmatterError("frontmatter_non_finite_number")
            continue
        if isinstance(item, date | datetime):
            continue
        if isinstance(item, str):
            if len(item) > _MAX_YAML_SCALAR:
                raise FrontmatterError("frontmatter_scalar_limit")
            continue
        if isinstance(item, dict):
            identity = id(item)
            if identity in seen:
                raise FrontmatterError("frontmatter_cycle")
            seen.add(identity)
            for key, child in item.items():
                if not isinstance(key, str):
                    raise FrontmatterError("frontmatter_key_type")
                stack.append((child, depth + 1))
            continue
        if isinstance(item, list):
            identity = id(item)
            if identity in seen:
                raise FrontmatterError("frontmatter_cycle")
            seen.add(identity)
            stack.extend((child, depth + 1) for child in item)
            continue
        raise FrontmatterError("frontmatter_value_type")


def _normalize_yaml(value: Any) -> Any:
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _normalize_yaml(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalize_yaml(item) for item in value]
    return value


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str, tuple[str, ...]]:
    """Parse metadata for indexing only; the original source is never serialized back."""

    if not (text.startswith("---\n") or text.startswith("---\r\n")):
        return {}, text, ()
    line_ending = "\r\n" if text.startswith("---\r\n") else "\n"
    boundary = text.find(f"{line_ending}---{line_ending}", 3)
    if boundary < 0:
        boundary = text.find(f"{line_ending}...{line_ending}", 3)
    if boundary < 0:
        return {}, text, ("frontmatter_unterminated",)
    opening_size = 3 + len(line_ending)
    data = text[opening_size:boundary]
    if len(data.encode("utf-8")) > _MAX_FRONTMATTER_BYTES:
        return {}, text, ("frontmatter_too_large",)
    try:
        encoded = data.encode("utf-8")
        for token in yaml.scan(encoded):
            if isinstance(token, AliasToken | AnchorToken):
                raise FrontmatterError("frontmatter_alias_forbidden")
        node = yaml.compose(encoded, Loader=yaml.SafeLoader)
        if node is None:
            payload: Any = {}
        else:
            _validate_yaml_node(node, depth=0, seen=set(), counter=[0])
            payload = yaml.safe_load(encoded)
        _validate_tree(payload)
        payload = _normalize_yaml(payload)
        if payload is None:
            payload = {}
        if not isinstance(payload, dict):
            raise FrontmatterError("frontmatter_not_mapping")
    except (yaml.YAMLError, RecursionError, FrontmatterError):
        return {}, text, ("frontmatter_invalid",)
    closing_size = len(line_ending) + 3 + len(line_ending)
    return payload, text[boundary + closing_size :], ()


def _strings(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        candidates = [part.strip().lstrip("#") for part in value.split(",")]
    elif isinstance(value, list):
        candidates = [str(part).strip().lstrip("#") for part in value]
    else:
        return ()
    return tuple(sorted({item for item in candidates if item}, key=str.casefold))


def _plain_properties(payload: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in payload.items():
        if (
            isinstance(value, str | int | float | bool)
            or value is None
            or (
                isinstance(value, list)
                and all(
                    isinstance(item, str | int | float | bool) or item is None for item in value
                )
            )
        ):
            result[key] = value
    return result


def validate_frontmatter_properties(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate new-document properties before YAML serialization."""

    try:
        _validate_tree(payload)
        normalized = _normalize_yaml(payload)
    except RecursionError as error:
        raise FrontmatterError("frontmatter_structure_limit") from error
    if not isinstance(normalized, dict) or len(normalized) > 128:
        raise FrontmatterError("frontmatter_property_limit")
    plain = _plain_properties(normalized)
    if len(plain) != len(normalized):
        raise FrontmatterError("frontmatter_complex_property")
    if any(not key or len(key) > 256 for key in plain):
        raise FrontmatterError("frontmatter_key_limit")
    return plain


def _split_wikilink(value: str) -> tuple[str, str | None, str | None]:
    target_part, separator, alias = value.partition("|")
    target, heading_separator, heading = target_part.partition("#")
    return (
        target.strip(),
        heading.strip() if heading_separator and heading.strip() else None,
        alias.strip() if separator and alias.strip() else None,
    )


def _context(line: str) -> str:
    compact = " ".join(line.split())
    return compact if len(compact) <= _MAX_CONTEXT else compact[: _MAX_CONTEXT - 1].rstrip() + "…"


def extract_markdown_metadata(text: str, *, path: str) -> MarkdownMetadata:
    frontmatter, body, warnings = parse_frontmatter(text)
    headings: list[str] = []
    tags = set(_strings(frontmatter.get("tags")))
    aliases = set(_strings(frontmatter.get("aliases") or frontmatter.get("alias")))
    links: list[ExtractedLink] = []
    in_fence = False
    fence_marker = ""
    for line in body.splitlines():
        fence = _FENCE.match(line)
        if fence:
            marker = fence.group(1)
            if not in_fence:
                in_fence = True
                fence_marker = marker[0]
            elif marker[0] == fence_marker:
                in_fence = False
                fence_marker = ""
            continue
        if in_fence:
            continue
        heading = _ATX_HEADING.match(line)
        if heading:
            headings.append(heading.group(2).strip())
        for tag in _INLINE_TAG.findall(line):
            tags.add(tag)
        for match in _WIKILINK.finditer(line):
            if len(links) >= _MAX_LINKS:
                break
            target, target_heading, alias = _split_wikilink(match.group(2))
            if not target:
                continue
            links.append(
                ExtractedLink(
                    raw_target=match.group(2),
                    target=target,
                    heading=target_heading,
                    alias=alias,
                    link_type="wikilink",
                    embed=bool(match.group(1)),
                    context=_context(line),
                )
            )
        for match in _MARKDOWN_IMAGE.finditer(line):
            if len(links) >= _MAX_LINKS:
                break
            raw_target = match.group(2).strip()
            target, _, heading_fragment = raw_target.partition("#")
            target = target.strip()
            if not target or "://" in target or target.startswith(("data:", "javascript:")):
                continue
            links.append(
                ExtractedLink(
                    raw_target=raw_target,
                    target=target,
                    heading=heading_fragment.strip() or None,
                    alias=match.group(1).strip() or None,
                    link_type="markdown_image",
                    embed=True,
                    context=_context(line),
                )
            )
        for match in _MARKDOWN_LINK.finditer(line):
            if len(links) >= _MAX_LINKS:
                break
            raw_target = match.group(2).strip()
            target, _, heading_fragment = raw_target.partition("#")
            target = target.strip()
            if (
                not target
                or "://" in target
                or target.startswith(("mailto:", "tel:", "data:", "javascript:"))
            ):
                continue
            links.append(
                ExtractedLink(
                    raw_target=raw_target,
                    target=target,
                    heading=heading_fragment.strip() or None,
                    alias=match.group(1).strip() or None,
                    link_type="markdown",
                    embed=False,
                    context=_context(line),
                )
            )
    if len(links) >= _MAX_LINKS:
        warnings = (*warnings, "link_limit_reached")
    title_value = frontmatter.get("title")
    if isinstance(title_value, str) and title_value.strip():
        title = title_value.strip()
    elif headings:
        title = headings[0]
    else:
        title = Path(path).stem
    if len(title) > 512:
        title = title[:511].rstrip() + "…"
        warnings = (*warnings, "title_truncated")
    return MarkdownMetadata(
        title=title,
        headings=tuple(headings[:10_000]),
        tags=tuple(sorted(tags, key=str.casefold)),
        aliases=tuple(sorted(aliases, key=str.casefold)),
        properties=_plain_properties(frontmatter),
        links=tuple(links),
        warnings=tuple(dict.fromkeys(warnings)),
    )


def line_ending_metadata(text: str) -> tuple[str, bool]:
    if "\r\n" in text and text.replace("\r\n", "").find("\n") < 0:
        line_ending = "crlf"
    elif "\r\n" in text:
        line_ending = "mixed"
    else:
        line_ending = "lf"
    return line_ending, text.endswith(("\n", "\r"))

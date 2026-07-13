#!/usr/bin/env python3
"""Generate the raytsystem reference documentation from verified public contracts.

This tool is the single source for the machine-derived reference pages of the
public knowledge base. It NEVER writes narrative prose: it only emits the CLI
tree, feature flags, workflow node types, web routes and schema registry
metadata that already exist in the codebase.

Usage:
    python3 scripts/docs/gen_reference.py --write   # (re)generate pages
    python3 scripts/docs/gen_reference.py --check    # fail if pages are stale

Every generated file carries a "Generated — do not edit" banner. CI runs
``--check`` so a public contract change that is not regenerated fails the build.

Safety: the generator reads only public repository contracts. It never emits
absolute filesystem paths, secrets or restricted data. Command defaults that
look like absolute paths are elided.
"""

from __future__ import annotations

import argparse
import json
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
REFERENCE_DIR = REPO_ROOT / "website" / "docs" / "reference"

BANNER = (
    "import GeneratedNotice from '@site/src/components/GeneratedNotice';\n\n"
    '<GeneratedNotice source="{source}" command="scripts/docs/gen_reference.py" />\n'
)


def _yaml_quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _frontmatter(title: str, description: str, slug: str) -> str:
    return (
        "---\n"
        f"title: {_yaml_quote(title)}\n"
        f"description: {_yaml_quote(description)}\n"
        "audience:\n  - user\n  - operator\n  - developer\n"
        "status: stable\n"
        "generated: true\n"
        f"slug: {slug}\n"
        "---\n\n"
    )


# --------------------------------------------------------------------------- CLI


def _cli_rows() -> list[dict]:
    import click
    import typer

    from raytsystem.cli import app

    root = typer.main.get_command(app)

    def walk(command: object, path: list[str]) -> list[dict]:
        rows: list[dict] = []
        subcommands = getattr(command, "commands", None)
        if subcommands:
            for name, sub in sorted(subcommands.items()):
                rows += walk(sub, [*path, name])
            return rows
        params = []
        for param in command.params:  # type: ignore[attr-defined]
            default = param.default
            if isinstance(default, bool) or default in (None, ""):
                default_repr = str(default)
            else:
                text = str(default)
                # Never leak an absolute local path through a printed default.
                default_repr = "(local path)" if text.startswith("/") else text
            kind = "аргумент" if isinstance(param, click.Argument) else "опция"
            params.append(
                {
                    "name": param.name,
                    "kind": kind,
                    "required": bool(getattr(param, "required", False)),
                    "default": default_repr,
                }
            )
        rows.append(
            {
                "cmd": " ".join(path),
                "help": (getattr(command, "help", "") or "").strip().split("\n")[0],
                "params": params,
            }
        )
        return rows

    return walk(root, [])


# Commands that change durable state or require an approval. Kept as an explicit,
# reviewed table because "mutates state" is a semantic property, not something
# Click exposes. Verified against src/raytsystem/cli.py and the security model.
STATE_CHANGING = {
    "ingest",
    "promote",
    "save",
    "rebuild-index",
    "init",
    "migrate",
    "upgrade",
    "backup",
    "restore",
    "graph update",
    "graph rebuild",
    "task create",
    "task transition",
    "eval baseline",
    "eval reject",
    "emergency activate",
    "emergency recover",
    "emergency close-breaker",
    "mcp approve",
    "mcp transition",
    "package update",
    "package approve",
    "package install",
    "package activate",
    "package rollback",
    "workflow approve",
    "workflow cancel",
    "notifications transition",
    "proposal import",
}
APPROVAL_REQUIRED = {
    "promote",
    "emergency recover",
    "emergency close-breaker",
    "mcp approve",
    "package approve",
    "package install",
    "package activate",
    "workflow approve",
}


def generate_cli() -> str:
    rows = _cli_rows()
    out = [
        _frontmatter(
            "CLI: полный справочник",
            "Автогенерируемое дерево команд raytsystem с аргументами и признаком изменения.",
            "/reference/cli",
        ),
        BANNER.format(source="src/raytsystem/cli.py, src/raytsystem/platform_cli.py"),
        "# CLI: полный справочник\n",
        (
            f"Всего листовых команд: **{len(rows)}**. Каждая команда показана с аргументами и "
            "опциями. Столбец «Меняет состояние» отмечает команды, которые пишут в проект или "
            "запускают действия; «Approval» — команды, которым требуется явное разрешение.\n"
        ),
        (
            ":::note\n"
            "Точные значения опций смотрите через "
            "`uv run raytsystem <команда> --help`. "
            "Команды с пустым описанием — служебные обёртки групп.\n"
            ":::\n"
        ),
    ]
    for row in rows:
        cmd = row["cmd"]
        anchor = cmd.replace(" ", "-")
        out.append(f"## `raytsystem {cmd}` {{#{anchor}}}\n")
        if row["help"]:
            out.append(row["help"] + "\n")
        mutates = "да" if cmd in STATE_CHANGING else "нет"
        approval = "да" if cmd in APPROVAL_REQUIRED else "нет"
        out.append(f"- Меняет состояние: **{mutates}**")
        out.append(f"- Требует approval: **{approval}**")
        out.append(f"- Запуск: `uv run raytsystem {cmd} --help`\n")
        if row["params"]:
            out.append("| Параметр | Тип | Обязательный | По умолчанию |")
            out.append("|---|---|---|---|")
            for param in row["params"]:
                req = "да" if param["required"] else "нет"
                out.append(
                    f"| `{param['name']}` | {param['kind']} | {req} | `{param['default']}` |"
                )
            out.append("")
    return "\n".join(out) + "\n"


# ------------------------------------------------------------------- Feature flags


def _read_toml_features() -> dict[str, bool]:
    data = tomllib.loads((REPO_ROOT / "config" / "raytsystem.toml").read_text("utf-8"))
    return dict(data.get("features", {}))


def _read_platform_flags() -> dict[str, bool]:
    # config/platform.yaml is a small flat "flags:" mapping. Parse the block
    # directly to avoid a PyYAML dependency in the generator.
    flags: dict[str, bool] = {}
    in_flags = False
    for line in (REPO_ROOT / "config" / "platform.yaml").read_text("utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.endswith(":") and not line.startswith(" "):
            in_flags = stripped == "features:"
            continue
        if in_flags and ":" in stripped:
            key, _, value = stripped.partition(":")
            token = value.strip().lower()
            if token in ("true", "false"):
                flags[key.strip()] = token == "true"
    return flags


def generate_flags() -> str:
    core = _read_toml_features()
    platform = _read_platform_flags()
    out = [
        _frontmatter(
            "Feature flags: полный список",
            "Автогенерируемый список feature flags raytsystem и их значений по умолчанию.",
            "/reference/feature-flags",
        ),
        BANNER.format(source="config/raytsystem.toml, config/platform.yaml"),
        "# Feature flags: полный список\n",
        (
            "Флаги определяют, какие поверхности raytsystem доступны. Значения ниже — умолчания "
            "из конфигурации репозитория. Флаг со значением `false` означает, что функция "
            "выключена по умолчанию и не должна считаться доступной.\n"
        ),
        "## Основные флаги — `config/raytsystem.toml` `[features]`\n",
        "| Флаг | По умолчанию |",
        "|---|---|",
    ]
    for key in sorted(core):
        out.append(f"| `{key}` | `{str(core[key]).lower()}` |")
    out.append("\n## Платформенные флаги — `config/platform.yaml`\n")
    out.append("| Флаг | По умолчанию |")
    out.append("|---|---|")
    for key in sorted(platform):
        out.append(f"| `{key}` | `{str(platform[key]).lower()}` |")
    disabled = sorted(
        [k for k, v in core.items() if not v] + [k for k, v in platform.items() if not v]
    )
    out.append("\n## Выключено по умолчанию\n")
    out.append(
        "Эти флаги выключены. Соответствующие функции недоступны без явного включения и, "
        "где указано, отдельного approval:\n"
    )
    for key in disabled:
        out.append(f"- `{key}`")
    out.append("")
    return "\n".join(out) + "\n"


# ------------------------------------------------------------------- Workflow nodes


def generate_workflow_nodes() -> str:
    from raytsystem.contracts.workflows import WorkflowNodeType

    descriptions = {
        "task": "Ссылка на задачу в task ledger.",
        "agent": "Шаг с назначенным агентом/цифровым сотрудником.",
        "deterministic_command": "Зарегистрированная операция по ID. Произвольный shell запрещён.",
        "review": "Узел ручной проверки результата.",
        "approval": "Явное разрешение через approval gate.",
        "condition": "Ветвление по типизированному условию.",
        "wait": "Ожидание (таймер/событие).",
        "artifact": "Регистрация артефакта.",
        "notification": "Отправка уведомления во внутренний inbox.",
        "subworkflow": "Вложенный workflow.",
    }
    out = [
        _frontmatter(
            "Типы узлов workflow",
            "Автогенерируемый список зарегистрированных типов узлов workflow DAG.",
            "/reference/workflow-nodes",
        ),
        BANNER.format(source="src/raytsystem/contracts/workflows.py"),
        "# Типы узлов workflow\n",
        "Движок workflow (`workflow_engine_enabled`) поддерживает следующие типы узлов DAG.\n",
        "| Тип узла | Назначение |",
        "|---|---|",
    ]
    for node in WorkflowNodeType:
        out.append(f"| `{node.value}` | {descriptions.get(node.value, '')} |")
    out.append(
        "\n:::warning Безопасность\n"
        "Узел `deterministic_command` выполняет только зарегистрированные операции по ID. "
        "Произвольные shell-команды из UI, задачи или workflow запрещены.\n:::\n"
    )
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------- Routes


ROUTE_META = [
    ("command-center", "Центр управления", "Пространство", "Состояние пространства и работа"),
    ("handbook", "База знаний", "Пространство", "Документация raytsystem внутри интерфейса"),
    ("documents", "Документы", "Пространство", "Управляемые файлы и заметки workspace"),
    ("tasks", "Задачи", "Оркестрация", "Операционный журнал без перезаписи истории"),
    ("universe", "Вселенная", "Оркестрация", "Граф знаний, работы и доказательств"),
    ("runs", "Запуски", "Оркестрация", "Журнал запусков и зафиксированных операций"),
    ("agents", "Агенты", "Реестр", "Единый Agent: definition и execution state"),
    ("skills", "Навыки", "Реестр", "Skill detail и policy-bound локальная правка"),
    ("context", "Контекст", "Реестр", "Документы инструкций и контекста (read-only)"),
    ("safety", "Безопасность", "Доверие", "Границы loopback/egress и адаптеры"),
    ("systems", "Системы", "Доверие", "Платформенные подсистемы и наблюдаемость"),
]


def _verify_routes() -> None:
    """Fail generation if presentation.ts route keys drift from ROUTE_META."""
    text = (REPO_ROOT / "web" / "src" / "presentation.ts").read_text("utf-8")
    for key, *_ in ROUTE_META:
        token = f'"{key}"' if "-" in key else f"{key}:"
        if token not in text and f'"{key}":' not in text:
            raise SystemExit(
                f"Route '{key}' not found in web/src/presentation.ts; update ROUTE_META."
            )


def generate_routes() -> str:
    _verify_routes()
    out = [
        _frontmatter(
            "Маршруты интерфейса",
            "Автогенерируемый список маршрутов web-интерфейса raytsystem.",
            "/reference/routes",
        ),
        BANNER.format(source="web/src/presentation.ts, web/src/app/App.tsx"),
        "# Маршруты интерфейса\n",
        "Web-интерфейс raytsystem состоит из следующих маршрутов. Подробное описание каждого — "
        "в разделе [«Интерфейс»](/interface/overview).\n",
        "| Маршрут | Название | Группа | Назначение |",
        "|---|---|---|---|",
    ]
    for key, label, group, purpose in ROUTE_META:
        out.append(f"| `/{key}` | {label} | {group} | {purpose} |")
    out.append("")
    return "\n".join(out) + "\n"


# ------------------------------------------------------------------------- HTTP API


API_ENDPOINTS = [
    ("GET", "/api/v1/agents", "read", "Единый Agent list; definition + nullable execution state."),
    (
        "GET",
        "/api/v1/agents/{agent_id}",
        "read",
        "Безопасная Agent detail projection, привязанная к catalog hash.",
    ),
    ("GET", "/api/v1/skills", "read", "Skill list с edit/fork policy и related Agent."),
    (
        "GET",
        "/api/v1/skills/{skill_id}",
        "read",
        "Skill detail и разрешённое inert Markdown content.",
    ),
    (
        "POST",
        "/api/v1/skills/{skill_id}/save/preview",
        "preview",
        "Валидация, normalization, diff и affected Agent без записи.",
    ),
    (
        "POST",
        "/api/v1/skills/{skill_id}/save",
        "write",
        "CAS-save editable local skill, revision и audit event.",
    ),
    (
        "POST",
        "/api/v1/skills/{skill_id}/fork/preview",
        "preview",
        "Проверка unique local ID, destination и diff без записи.",
    ),
    (
        "POST",
        "/api/v1/skills/{skill_id}/fork",
        "write",
        "Создание отдельного `pack_local` skill; source не меняется.",
    ),
]


def _verify_api_routes() -> None:
    """Fail if the documented method/path pair is absent from the FastAPI source."""

    text = (REPO_ROOT / "src" / "raytsystem" / "webapp" / "app.py").read_text("utf-8")
    for method, path, *_ in API_ENDPOINTS:
        decorator = f'@app.{method.lower()}("{path}")'
        if decorator not in text:
            raise SystemExit(
                f"HTTP API route '{method} {path}' not found in app.py; update API_ENDPOINTS."
            )


def _schema_type(schema: dict) -> str:
    if "anyOf" in schema:
        return " \\| ".join(_schema_type(item) for item in schema["anyOf"])
    if "const" in schema:
        return f"`{schema['const']}`"
    if "enum" in schema:
        return " \\| ".join(f"`{item}`" for item in schema["enum"])
    value = schema.get("type", "object")
    if isinstance(value, list):
        return " \\| ".join(str(item) for item in value)
    return str(value)


def _request_table(model: type) -> list[str]:
    schema = model.model_json_schema()
    required = set(schema.get("required", []))
    rows = ["| Поле | Тип | Обязательно | Ограничение |", "|---|---|---|---|"]
    for name, field in schema.get("properties", {}).items():
        constraints: list[str] = []
        if "minLength" in field:
            constraints.append(f"min {field['minLength']}")
        if "maxLength" in field:
            constraints.append(f"max {field['maxLength']}")
        if "pattern" in field:
            constraints.append(f"pattern `{field['pattern']}`")
        for variant in field.get("anyOf", []):
            if "pattern" in variant:
                constraints.append(f"pattern `{variant['pattern']}`")
        if "default" in field:
            default = "null" if field["default"] is None else str(field["default"])
            constraints.append(f"default `{default}`")
        rows.append(
            f"| `{name}` | {_schema_type(field)} | {'да' if name in required else 'нет'} | "
            f"{'; '.join(constraints) or '—'} |"
        )
    return rows


def generate_api() -> str:
    from raytsystem.skill_authoring import SkillAuthoringError
    from raytsystem.webapp.dto import SkillForkPreviewRequest, SkillForkRequest, SkillSaveRequest

    _verify_api_routes()
    errors: list[tuple[int, str]] = []
    pending = list(SkillAuthoringError.__subclasses__())
    while pending:
        error = pending.pop()
        pending.extend(error.__subclasses__())
        errors.append((error.status_code, error.code))
    out = [
        _frontmatter(
            "HTTP API: Agents и Skills",
            "Автогенерируемый контракт unified Agent read model и безопасного Skill authoring.",
            "/reference/api",
        ),
        BANNER.format(
            source=(
                "src/raytsystem/webapp/app.py, src/raytsystem/webapp/dto.py, "
                "src/raytsystem/skill_authoring.py"
            )
        ),
        "# HTTP API: Agents и Skills\n",
        (
            "Все маршруты ниже same-origin и требуют cookie `raytsystem_session`. "
            "`POST` дополнительно "
            "требует `Content-Type: application/json`, совпадающий "
            "`X-CSRF-Token`, допустимый `Origin` и "
            "`Idempotency-Key`. Security middleware ограничивает весь JSON body 64 КиБ. "
            "Frontend передаёт ID, но не filesystem path.\n"
        ),
        "| Метод | Путь | Режим | Назначение |",
        "|---|---|---|---|",
    ]
    for method, path, mode, purpose in API_ENDPOINTS:
        out.append(f"| `{method}` | `{path}` | {mode} | {purpose} |")
    out.extend(
        [
            "",
            "## Read contracts\n",
            (
                "`GET /api/v1/agents` возвращает одну запись на стабильный "
                "Agent ID с `definition`, nullable `execution`, readiness и безопасным "
                "runtime summary. Detail требует query `expected` с catalog SHA-256 и "
                "возвращает Overview/Instruction/Skills/Runtime/Access/History.\n"
            ),
            (
                "`GET /api/v1/skills` возвращает definitions, safe relative source path, "
                "`editable`, `read_only_reason`, `forkable` и related Agent. Skill detail требует "
                "query `expected` с catalog SHA-256. Запрет disclosure возвращает HTTP 200 "
                "metadata-only: `content` равен `null`, а `source.content_restricted` равен "
                "`true`. `permission_boundary` отдельно возвращает declared permission IDs и "
                "typed sections с собственными availability/items; `not_modeled` не означает "
                "разрешение. Tools/workflows имеют availability `not_modeled`; history возвращает "
                "только current authoring revision, если она есть.\n"
            ),
            "## Save request\n",
            *_request_table(SkillSaveRequest),
            "",
            (
                "Один body используется для `/save/preview` и `/save`. Preview не пишет: "
                "он возвращает normalized content, validation, diff, proposed hash и affected "
                "Agent. Save повторно проверяет CAS, устанавливает файл через guarded "
                "no-replace и durable recovery journal, регистрирует revision/audit и "
                "возвращает новые source/catalog hashes. "
                "Effective `test_status` всегда `pending`.\n"
            ),
            "## Fork preview request\n",
            *_request_table(SkillForkPreviewRequest),
            "",
            "## Fork confirmation request\n",
            *_request_table(SkillForkRequest),
            "",
            (
                "Preview может не передавать `new_skill_id`: сервер предложит "
                "уникальный ID. Confirmation обязан повторить этот ID и те же expected "
                "hashes. Source не меняется; destination создаётся как "
                "`pack_local`, trust `user`, test status `pending`.\n"
            ),
            "## Typed authoring errors\n",
            "| HTTP | Code |",
            "|---:|---|",
        ]
    )
    for status, code in sorted(set(errors)):
        out.append(f"| `{status}` | `{code}` |")
    out.extend(
        [
            "",
            (
                "Security middleware errors (`session_required`, `origin_rejected`, "
                "`csrf_rejected`, "
                "`idempotency_required`, `payload_too_large`) возникают до authoring service. "
                "`skill_edit_conflict` ничего не перезаписывает и возвращает "
                "current/proposed content и diff, только если "
                "disclosure policy их разрешает. Automatic merge не выполняется.\n"
            ),
            "## Security boundary\n",
            (
                "Authoring не принимает arbitrary path, не следует symlink, не меняет "
                "official/pinned source, не запускает skill/tools/workflows и не касается "
                "canonical knowledge, task или execution state. Agent read API не раскрывает "
                "egress destination: наружу выходит только boolean `egress_declared`. "
                "Подробности: [Skills (интерфейс)](/interface/skills) и "
                "[Безопасность](/security/overview).\n"
            ),
        ]
    )
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------- Version


def generate_version() -> str:
    base = (REPO_ROOT / "src" / "raytsystem" / "contracts" / "base.py").read_text("utf-8")
    schema_version = "unknown"
    for line in base.splitlines():
        if line.startswith("SCHEMA_VERSION"):
            schema_version = line.split("=")[1].strip().strip('"')
            break
    schema_dirs = sorted(p.name for p in (REPO_ROOT / "config" / "schemas").glob("v*"))
    current_dir = REPO_ROOT / "config" / "schemas" / f"v{schema_version}"
    current_registry = json.loads((current_dir / "registry.json").read_text("utf-8"))
    historical_dirs = [name for name in schema_dirs if name != f"v{schema_version}"]
    out = [
        _frontmatter(
            "Версии и контракты",
            "Автогенерируемые сведения о версии контрактов и реестре схем.",
            "/reference/version",
        ),
        BANNER.format(source="src/raytsystem/contracts/base.py, config/schemas/"),
        "# Версии и контракты\n",
        f"- Текущая версия контрактов: **{schema_version}**",
        f"- Число схем в текущем реестре: **{len(current_registry['entries'])}**",
        f"- Исторические реестры (неизменяемы): {', '.join(historical_dirs)}",
        "",
        "Все исторические реестры схем остаются байт-идентичными. Экспорт схем: "
        "`uv run raytsystem schemas export`.\n",
    ]
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------- driver


GENERATORS = {
    "cli.mdx": generate_cli,
    "feature-flags.mdx": generate_flags,
    "workflow-nodes.mdx": generate_workflow_nodes,
    "routes.mdx": generate_routes,
    "api.mdx": generate_api,
    "version.mdx": generate_version,
}

CATEGORY = {
    "label": "Справочник (генерируется)",
    "position": 90,
    "link": {
        "type": "generated-index",
        "title": "Автогенерируемый справочник",
        "description": (
            "Эти страницы собираются из проверенных публичных контрактов raytsystem командой "
            "scripts/docs/gen_reference.py. Не редактируйте их вручную."
        ),
    },
}


def _render_all() -> dict[str, str]:
    return {name: fn() for name, fn in GENERATORS.items()}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--write", action="store_true", help="write generated pages")
    group.add_argument("--check", action="store_true", help="fail if pages are stale")
    args = parser.parse_args()

    rendered = _render_all()
    import json

    category_text = json.dumps(CATEGORY, ensure_ascii=False, indent=2) + "\n"

    if args.write:
        REFERENCE_DIR.mkdir(parents=True, exist_ok=True)
        (REFERENCE_DIR / "_category_.json").write_text(category_text, "utf-8")
        for name, content in rendered.items():
            (REFERENCE_DIR / name).write_text(content, "utf-8")
        print(f"Wrote {len(rendered)} reference pages to {REFERENCE_DIR.relative_to(REPO_ROOT)}")
        return 0

    stale: list[str] = []
    for name, content in rendered.items():
        path = REFERENCE_DIR / name
        if not path.is_file() or path.read_text("utf-8") != content:
            stale.append(name)
    category_path = REFERENCE_DIR / "_category_.json"
    if not category_path.is_file() or category_path.read_text("utf-8") != category_text:
        stale.append("_category_.json")
    if stale:
        print("Stale generated reference pages:", ", ".join(sorted(stale)), file=sys.stderr)
        print("Run: python3 scripts/docs/gen_reference.py --write", file=sys.stderr)
        return 1
    print("Generated reference pages are up to date.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

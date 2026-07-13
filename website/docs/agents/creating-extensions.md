---
title: "Создание своего skill, agent и pack"
description: "Как устроены skill (skills/*/SKILL.md), определение агента (packs/*/agents/*.yaml) и pack. Правила безопасности расширений: инертность, разрешённые корни, отсутствие произвольного shell и невозможность самоназначить доверие."
audience: [developer]
status: stable
feature_flags: [pack_lifecycle_enabled]
related_commands:
  - "uv run raytsystem agent preflight ..."
  - "uv run raytsystem agent subagent-check ..."
  - "uv run raytsystem lint"
  - "uv run raytsystem validate"
related_pages:
  - /agents/overview
  - /agents/packs-lifecycle
  - /agents/tool-hub
  - /interface/skills
  - /security/defaults
source_of_truth:
  - path: src/raytsystem/catalog.py
  - path: src/raytsystem/skill_authoring.py
  - path: src/raytsystem/toolhub/runner.py
  - path: packs/starter/agents/agent_builder.yaml
  - path: skills/raytsystem-query/SKILL.md
  - path: ops/decisions/ADR-012-repo-skills-agent-policy-and-checkpoint-guard.md
last_verified_against: "commit 3f2a123 / schema v1.4.0"
---

# Создание своего skill, agent и pack

## Что это

raytsystem расширяют тремя видами объектов: **skill** (процедура), **agent** (профиль исполнителя) и **pack** (манифест, объединяющий их). Все три — инертные определения, которые `CatalogService` загружает из фиксированных разрешённых корней (`src/raytsystem/catalog.py`). Как каталог собирается и почему доверие не самоназначается — в [/agents/overview](/agents/overview).

## Предварительные условия

- Объект должен лежать в разрешённом корне: навык — в `skills/<имя>/SKILL.md`, агент — в `packs/<pack>/agents/<agent_id>.yaml`, манифест — в `packs/<pack>/pack.yaml`.
- Имена директорий — по шаблону `^[a-z][a-z0-9_-]{1,63}$`. Симлинки в корнях каталога запрещены.
- В файлах не должно быть секретов: секрет-скан «падает закрыто».

## Skill: `skills/<имя>/SKILL.md`

Навык — это Markdown-файл с обязательным YAML-фронтматтером. Файл должен **начинаться** с `---` и содержать закрывающий `---` (`_frontmatter` в `src/raytsystem/catalog.py`). Каталог читает поля:

- `name` — строка (по умолчанию берётся имя директории);
- `description` — строка;
- `version` — строка (по умолчанию `unversioned`);
- `permissions` — список строк;
- `test_status` — одно из `pass`, `pending`, `unavailable`.

`skill_id` берётся из имени директории. Каноническое `name` должно совпадать с ним и не
переводится. Для прямой правки через UI все пять полей выше обязательны. Пример:

```yaml
---
name: raytsystem-query
description: Answer questions from the active raytsystem generation ...
version: "1.0.0"
permissions: []
test_status: pending
---
```

Тело навыка описывает процедуру (входы/выходы, preflight, workflow, восстановление). Навык вызывается по **заявленной операции**, а не по содержимому обрабатываемых данных (ADR-012).

### Правка через веб-интерфейс

Кнопка «Редактировать» доступна только enabled, user-trusted `pack_local` skill в точном
`skills/<skill_id>/SKILL.md`, если sensitivity policy разрешает запись. Official, pinned, generated,
historical, external, restricted и unverified-origin источники read-only.

Перед save UI показывает normalized Markdown, validation warnings/errors, diff и
связанных Agent. Сервер проверяет exact catalog/source hashes, повторно проверяет pin policy в
той же write-транзакции и устанавливает файл через guarded no-replace. Durable fsync-журнал
обеспечивает безопасное восстановление по idempotency receipt. После save файл перечитывается
через `CatalogService`, создаются revision/audit records, а тестовый статус сбрасывается в
`pending`. Save не запускает skill и не выполняет команды проверки.

Если файл или catalog успел измениться, save возвращает conflict; автоматического merge нет.
Для safe read-only skill можно создать отдельную локальную копию только после preview destination/diff и
подтверждения. Исходник не меняется. Подробнее — [Skills (интерфейс)](/interface/skills).

## Agent: `packs/<pack>/agents/<agent_id>.yaml`

Определение агента — YAML, где **имя файла обязано совпадать с `agent_id`** (иначе `CatalogError`). Поля видны на примере `packs/starter/agents/agent_builder.yaml`:

```yaml
agent_id: agent_builder
name: Builder
role: builder
description: Produces project-local implementation drafts ...
version: "1.0.0"
pack_id: pack_starter
runtime_adapter_id: adapter_fake
skill_ids: [raytsystem-save]
context_paths: [AGENTS.md, WORK.md]
capabilities: [implement, test, artifact_propose]
requested_filesystem_mode: staging_only
approved_data_classes: [public, internal]
enabled: false
```

При загрузке `CatalogService._validate_references` проверяет, что `pack_id`, `runtime_adapter_id`, все `skill_ids` и `context_paths` резолвятся в существующие объекты каталога. Незнакомый адаптер или навык — ошибка.

## Pack: `packs/<pack>/pack.yaml`

Манифест объединяет агентов и навыки и объявляет `pack_id`, `version`, `license_expression`, `trust_class`, а также индексы `skill_ids`, `agent_ids`, `context_paths`. Каталог сверяет: заявленный список агентов pack должен **точно** совпадать с фактически найденными в `packs/<pack>/agents/`, а один навык не может принадлежать двум packs.

Чтобы принять новый pack в рабочее пространство, используйте конвейер жизненного цикла — [/agents/packs-lifecycle](/agents/packs-lifecycle).

## Пошагово

1. Создайте файлы в разрешённых корнях по правилам выше.
2. Прогоните preflight навыка: `uv run raytsystem agent preflight --skill <id> --json`.
3. Проверьте суб-агента: `uv run raytsystem agent subagent-check ...`.
4. Прогоните детерминированные проверки: `uv run raytsystem lint` и `uv run raytsystem validate`.
5. Не меняйте `test_status` на `pass`, пока отдельный verifier не зафиксировал результат.

## Правила безопасности расширений

- **Инертность.** Определение описывает возможность, но не запускает код. `enabled: false` и `runtime_adapter_id: adapter_fake` означают, что профиль ничего не выполняет; выполнение агентов по умолчанию выключено — [/security/defaults](/security/defaults).
- **Разрешённые корни.** Загружаются только `packs/`, `skills/`, `config/runtime-adapters.yaml` и корневые `AGENTS.md`/`WORK.md`/`CLAUDE.md`. Ничего извне и никаких симлинков.
- **Никакого произвольного shell.** Расширение не получает свободный доступ к shell: воркфлоу-узлы `deterministic_command` резолвят только зарегистрированные операции — сырой shell запрещён (`src/raytsystem/contracts/workflows.py`). Исполняемая media-операция должна идти через типизированный [Tool Hub](/agents/tool-hub), а не через `command`/`argv` в skill.
- **Доверие не самоназначается.** Манифест не может объявить себя `official`: неизвестный pack принудительно понижается до `user`. Права навыка `self_modify` блокируют ревизию pack на валидации (см. [/agents/packs-lifecycle](/agents/packs-lifecycle)).

## Частые ошибки

- «Agent filename and agent ID disagree» — переименуйте файл так, чтобы он совпадал с `agent_id`.
- «Pack references an unknown agent or skill» — объект указан в манифесте, но отсутствует в каталоге.
- «Catalog roots cannot contain symlinked entries» — уберите симлинк из `packs/` или `skills/`.
- «Skill changed after the editor was opened» — перечитайте актуальную revision и повторите
  правку вручную; не перезаписывайте файл и не склеивайте инструкции автоматически.

## Связанные страницы

- [/agents/overview](/agents/overview)
- [/agents/packs-lifecycle](/agents/packs-lifecycle)
- [/agents/tool-hub](/agents/tool-hub)
- [/interface/skills](/interface/skills)
- [/security/defaults](/security/defaults)

## Источники истины

- `src/raytsystem/catalog.py`
- `src/raytsystem/skill_authoring.py`
- `src/raytsystem/toolhub/runner.py`
- `packs/starter/agents/agent_builder.yaml`
- `skills/raytsystem-query/SKILL.md`
- `ops/decisions/ADR-012-repo-skills-agent-policy-and-checkpoint-guard.md`

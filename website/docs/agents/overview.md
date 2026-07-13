---
title: "Агенты и расширение: обзор"
description: "Как устроен каталог расширений raytsystem: агенты, навыки, контексты, packs, манифесты, происхождение и доверие. Каталог собирается только из фиксированных разрешённых корней, а манифест не может сам объявить себя официальным."
audience: [developer]
status: stable
feature_flags: [pack_lifecycle_enabled]
related_commands: ["uv run raytsystem status --json", "uv run raytsystem agent preflight ..."]
related_pages:
  - /agents/packs-lifecycle
  - /agents/creating-extensions
  - /agents/tool-hub
  - /interface/agents
  - /interface/skills
  - /security/defaults
source_of_truth:
  - path: src/raytsystem/catalog.py
  - path: src/raytsystem/skill_authoring.py
  - path: src/raytsystem/webapp/execution_views.py
  - path: src/raytsystem/toolhub/dispatch.py
  - path: packs/starter/agents/agent_builder.yaml
  - path: ops/decisions/ADR-012-repo-skills-agent-policy-and-checkpoint-guard.md
last_verified_against: "commit 3f2a123 / schema v1.4.0"
---

# Агенты и расширение: обзор

## Что это

Расширяемость raytsystem держится на **каталоге** — наборе инертных определений, которые описывают, что система *может* делать, но сами по себе ничего не выполняют. Каталог собирается сервисом `CatalogService` (`src/raytsystem/catalog.py`) и включает пять видов объектов:

- **packs** — манифесты `packs/<имя>/pack.yaml`, объединяющие агентов, навыки и разрешённые контексты;
- **agents** — определения агентов `packs/<имя>/agents/<agent_id>.yaml`;
- **skills** — процедуры `skills/<имя>/SKILL.md` с YAML-фронтматтером;
- **instructions** — три корневых документа: `AGENTS.md`, `WORK.md`, `CLAUDE.md`;
- **adapters** — реестр рантайм-адаптеров `config/runtime-adapters.yaml`.

Два встроенных pack: **core** и **starter**.

## Когда использовать

Читайте этот раздел, если хотите понять, откуда система берёт агентов и навыки, как проверяется их происхождение и почему наличие определения не даёт ему прав. Дальше — [создание собственного расширения](/agents/creating-extensions) и [жизненный цикл packs](/agents/packs-lifecycle).

## Каталог собирается только из фиксированных корней

`CatalogService` обходит **только** заранее заданные project-local каталоги: `packs/`, `skills/`, файл `config/runtime-adapters.yaml` и корневые `AGENTS.md`/`WORK.md`/`CLAUDE.md`. Это не сканирование всего диска — источники зафиксированы в коде.

При обходе действуют жёсткие ограничения (`src/raytsystem/catalog.py`):

- **symlink запрещён**: любой симлинк в корнях каталога — ошибка `CatalogError`;
- имена директорий обязаны совпадать с шаблоном `^[a-z][a-z0-9_-]{1,63}$`;
- размеры ограничены (определения — 512 КБ, документы — 1 МБ), YAML проверяется на глубину, число узлов, дубликаты ключей; **якоря, алиасы и merge-ключи запрещены**;
- каждый файл проходит проверку на секреты (`SecretScanner`), которая «падает закрыто» при любой ошибке.

Имя файла агента обязано совпадать с его `agent_id`, а имя директории встроенного pack — с ожидаемым `pack_id` (например, `core` → `pack_core`, `starter` → `pack_starter`).

## Происхождение и доверие: манифест не назначает себе trust

Ключевое правило безопасности: **манифест не может сам объявить себя официальным**. Класс доверия (`trust_class`) выдаётся не декларацией внутри файла, а тем, откуда объект пришёл.

Если директория pack — один из встроенных корней, её `pack_id` обязан совпасть с ожидаемым, иначе загрузка падает. Если же pack неизвестен и его манифест заявляет `trust_class: official`, `CatalogService` **принудительно понижает** его до `user` (`_load_pack` в `src/raytsystem/catalog.py`): «неизвестные packs остаются user-trusted, пока их не аттестует политика установки». Навыки и агенты наследуют класс доверия своего pack, а не назначают его себе.

Такой же принцип закреплён в [ADR-012](/agents/creating-extensions): текст из источников, транскриптов, веб-страниц и импортированных чатов — это **недоверенные данные**, которые не могут выбрать навык или расширить полномочия ревьюера. Полномочия определяются заявленной операцией, а не содержимым нагрузки.

## Инертность

Определения каталога **инертны**. Агент из `packs/starter/agents/agent_builder.yaml` имеет `enabled: false` и ссылается на `runtime_adapter_id: adapter_fake` — то есть само наличие профиля не запускает никакого кода. Выполнение агентов по умолчанию выключено (флаги `runtime_execution_enabled`, `codex_local_enabled`, `claude_local_enabled` — off), см. [/security/defaults](/security/defaults).

Для типизированных локальных media-операций есть отдельная исполняемая граница
[Tool Hub](/agents/tool-hub). Она не делает каталог skill/agent исполняемым и не даёт
расширению свободный shell.

## Один Agent, две плоскости данных

`AgentDefinition` и текущее execution-состояние остаются разными бэкенд-записями, но для
пользователя это один Agent. Read projection соединяет их только по стабильному
`agent_id` / `employee_id`: не по имени, переводу или позиции в списке. Поэтому:

- definition-only Agent виден как «Только каталог»;
- выключённый runtime или неинициализированный execution store — статус того же Agent;
- execution-only, duplicate или mismatched record не скрывается, а показывается как
  degraded-диагностика.

В UI нет двух каталогов «сотрудники»/«определения»; подробнее —
[Агенты (интерфейс)](/interface/agents).

## Канонические имена и локализация

Канонические agent/skill names остаются английскими и не подменяются presentation copy:
`Builder`, `Librarian`, `Orchestrator`, `Researcher`, `Reviewer`, `raytsystem-watch`,
`raytsystem-query`. Переводятся роли, статусы, описания, поля, кнопки и ошибки.
Функции canonical name, localized label и localized description разделены.

## Редактируемые и read-only skills

`SKILL.md` всегда считается недоверенным текстом. Серверная policy разрешает прямую правку
только enabled, user-trusted `pack_local` skill по точному `skills/<skill_id>/SKILL.md`, если
sensitivity допускает запись. Official, pinned, generated, historical, external, restricted и
unverified-origin источники нельзя перезаписать.

Для safe read-only источника можно создать отдельную локальную копию после preview diff и
подтверждения. Исходник не меняется; копия становится `pack_local` / trust `user` и
получает `test_status: pending`. Полный workflow — [Skills (интерфейс)](/interface/skills).

## Ограничения и безопасность

- Каталог не подтягивает объекты извне разрешённых корней и не следует по симлинкам.
- Доверие не самоназначается: неизвестный pack всегда `user`, пока политика установки не подтвердит иное.
- Инертность: определение описывает возможность, но не даёт разрешения на выполнение.
- Текст `SKILL.md` не может сам выдать себе `editable`, trust или permissions.
- Save/fork не запускает skill, tool, workflow или provider и не подделывает `test_status: pass`.

## Связанные страницы

- [/agents/packs-lifecycle](/agents/packs-lifecycle)
- [/agents/creating-extensions](/agents/creating-extensions)
- [/agents/tool-hub](/agents/tool-hub)
- [/interface/agents](/interface/agents)
- [/security/defaults](/security/defaults)

## Источники истины

- `src/raytsystem/catalog.py`
- `src/raytsystem/skill_authoring.py`
- `src/raytsystem/webapp/execution_views.py`
- `src/raytsystem/toolhub/dispatch.py`
- `packs/starter/agents/agent_builder.yaml`
- `ops/decisions/ADR-012-repo-skills-agent-policy-and-checkpoint-guard.md`

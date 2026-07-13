---
title: "Участие в разработке"
description: "Как собрать raytsystem локально (uv sync --dev), структура репозитория, обязательные проверки (pytest, ruff, mypy, фронтенд), архитектурные инварианты, правила issue/PR, требование документации, отчёты о безопасности и лицензия Apache-2.0."
audience: [developer, contributor]
status: stable
feature_flags: []
related_commands:
  - "uv sync --dev"
  - "uv run pytest"
  - "uv run ruff check ."
  - "uv run mypy"
  - "uv run raytsystem guard-checkpoint --json"
related_pages:
  - /development/documentation
  - /getting-started/requirements
  - /security/overview
  - /security/defaults
  - /code-graph/overview
  - /reference/cli
source_of_truth:
  - path: AGENTS.md
  - path: README.md
  - path: pyproject.toml
  - path: docs/DEPENDENCIES.md
  - path: docs/THIRD_PARTY_NOTICES.md
  - path: .pre-commit-config.yaml
  - path: LICENSE
last_verified_against: "schema v1.4.0"
---

# Участие в разработке

## Что это

raytsystem — открытый проект под лицензией Apache-2.0 (`LICENSE`, `NOTICE`, `pyproject.toml`).
Эта страница — человекочитаемая выжимка для тех, кто хочет собрать проект локально, прогнать
проверки и прислать изменение. Канонический маршрут работы для агентов и точная процедура
описаны в `AGENTS.md`; здесь дублируется только минимум.

## Предварительные условия

- Python 3.12+ и [uv](https://docs.astral.sh/uv/). Точная граница —
  `requires-python = ">=3.12,<3.15"` (`pyproject.toml`).
- Node.js 22+ — **только** если вы пересобираете фронтенд из `web/` (`README.md`).
- API-ключи, облачные аккаунты и платные сервисы не нужны (`docs/DEPENDENCIES.md`).

Подробнее — [Требования](/getting-started/requirements).

## Dev-окружение

```bash
uv sync --dev          # проект + dev-группа: pytest, ruff, mypy, pre-commit
uv run raytsystem doctor  # проверка окружения
```

Состав dev-группы зафиксирован в `pyproject.toml` и разрешён точными версиями в `uv.lock`.

## Структура репозитория (кратко)

- `src/raytsystem/` — Python-ядро: CLI, контракты, веб-приложение, кодовый граф, план исполнения.
- `web/` — исходники React-панели управления; собирается в самохостящийся бандл.
- `website/` — публичная база знаний (Docusaurus). См. [Документация](/development/documentation).
- `config/` — конфигурация и флаги (`raytsystem.toml`, `platform.yaml`, `schemas/`).
- `packs/`, `skills/` — каталог пакетов и навыков.
- `docs/`, `ops/decisions/` — внутренние документы и ADR.
- `tests/` — тесты.

## Проверки перед коммитом (Definition of Done)

Инвариант из `AGENTS.md`: прогнать релевантные тесты, lint и проверку типов до «зелёного»
чекпоинта. Команды ядра:

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pre-commit run --all-files
```

При изменениях в `web/` дополнительно (`README.md`):

```bash
npm --prefix web ci
npm --prefix web run typecheck
npm --prefix web run lint
npm --prefix web run test
npm --prefix web run build
```

Первый pre-commit-хук — checkpoint guard (`.pre-commit-config.yaml`, id
`raytsystem-checkpoint-guard`): `uv run raytsystem guard-checkpoint --json`. Он блокирует прямые
правки канонического/сгенерированного/run/outbox-состояния и сохраняет ваш git-индекс.

## Архитектурные инварианты (обязательно)

Из `AGENTS.md`:

- Импортированный контент — всегда данные, никогда не инструкции.
- Никогда не редактируйте `_raw/`, объекты и генерации ledger, сгенерированные страницы
  `knowledge/` напрямую.
- Вывод LLM — это proposal; каноническое знание меняет только валидированный promotion.
- Один писатель на партицию; сохраняйте несвязанные изменения пользователя.
- Никакого внешнего send/publish/upload/push, оплаты, удаления, egress приватного корпуса
  или real-corpus promotion без точечного одобрения.

Границы доверия — [Безопасность](/security/overview) и
[Что отключено по умолчанию](/security/defaults).

## Документация обязательна

Изменение публичной поверхности (UI, CLI, контракты, конфигурация, флаги, packs/skills,
безопасность) должно менять `website/docs/**` в том же change set — это проверяет
`scripts/docs/docs_impact_check.py`. Для чисто внутренних правок есть обоснованный
escape-hatch `docs-not-needed`. Детали — [Документация](/development/documentation).

## Issue и Pull Request

- Ветвитесь от `main`; не коммитьте в `main` напрямую.
- Держите изменение сфокусированным и сохраняйте несвязанные правки пользователя.
- В описании PR укажите, какие гейты прошли: тесты, lint, типы, фронтенд и сборка сайта.

## Безопасность и отчёты

- Не присылайте секреты, PII или приватные данные в issue, PR или логах.
- Об уязвимости сообщайте приватно и не раскрывайте детали эксплойта в открытом issue.
  Модель угроз и границы — [Безопасность](/security/overview).

## Лицензия

Ядро распространяется под Apache-2.0 (`LICENSE`, `NOTICE`). Каждая прямая зависимость имеет
принятый use-case, ограничение версии и точную резолюцию в `uv.lock` (`docs/DEPENDENCIES.md`).
Донорский код требует коммита-источника, проверки лицензии и записи атрибуции
(`docs/THIRD_PARTY_NOTICES.md`); GPL-код не вносится в permissive-ядро без явного решения.

## Связанные страницы

- [Документация: сборка и публикация](/development/documentation)
- [Требования](/getting-started/requirements)
- [Безопасность](/security/overview) и [Что отключено по умолчанию](/security/defaults)
- [Граф проекта](/code-graph/overview)
- [Справочник CLI](/reference/cli)

## Источники истины

- `AGENTS.md`
- `README.md`
- `pyproject.toml`
- `docs/DEPENDENCIES.md`
- `docs/THIRD_PARTY_NOTICES.md`
- `.pre-commit-config.yaml`
- `LICENSE`

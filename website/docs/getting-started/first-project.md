---
title: "Первый проект"
description: "Как начать работу в каталоге: планирование шаблона через raytsystem init, задачи и построение графа кода."
audience: [user]
status: stable
feature_flags: [code_graph_enabled, task_workspaces_enabled]
related_commands: ["uv run raytsystem init", "uv run raytsystem graph rebuild", "uv run raytsystem task list"]
related_pages:
  - /getting-started/first-run
  - /code-graph/overview
  - /code-graph/freshness-update-rebuild
  - /tasks/overview
  - /interface/universe
source_of_truth:
  - path: src/raytsystem/platform_cli.py
  - path: src/raytsystem/cli.py
  - path: ops/decisions/ADR-032-workspace-templates-and-migrations.md
  - path: docs/STATUS.md
last_verified_against: "schema v1.4.0"
---

# Первый проект

## Что это

Эта страница показывает первые действия в рабочем каталоге: как посмотреть, какой
шаблон рабочего пространства создаст `raytsystem init`, как завести задачи и как построить
граф кода из интерфейса или из CLI.

## Когда использовать

Когда установка выполнена, интерфейс уже открывается, и вы хотите разложить рабочее
пространство и начать ориентироваться в проекте.

## Пошагово

### 1. Посмотрите план инициализации

`raytsystem init` по умолчанию работает в режиме плана (`--dry-run`) и **ничего не пишет** —
он лишь показывает, какие файлы будут созданы:

```bash
uv run raytsystem init --template software --dry-run --json
```

Доступны три шаблона: `software`, `content`, `research`
(`src/raytsystem/platform_cli.py`). Чтобы применить план, добавьте `--apply`. Инициализация
не разрушает работу: она отказывает при конфликтах файлов и требует явного
`--confirm-existing` внутри уже существующего репозитория (ADR-032).

### 2. Заведите и просмотрите задачи

Операционная доска задач ведётся в отдельном журнале. Прочитать её можно так:

```bash
uv run raytsystem task list --json
```

Создание и переходы задач выполняются идемпотентными командами
`uv run raytsystem task create` и `uv run raytsystem task transition` (каждой нужен свой
`--idempotency-key`). Подробнее — в разделе [Задачи](/tasks/overview).

### 3. Постройте граф кода

Граф кода — производный, перестраиваемый слой. Его можно собрать двумя путями:

- **Из интерфейса:** «Вселенная» → линза **«Код»** → «Построить граф».
- **Из CLI:**

  ```bash
  uv run raytsystem graph rebuild --json
  ```

  Для инкрементального обновления после правок используйте `uv run raytsystem graph update`,
  а свежесть проверяйте командой `uv run raytsystem graph status --json`.

## Ожидаемый результат

- `init --dry-run` печатает план (список создаваемых файлов, обнаруженные конфликты).
- `task list` возвращает текущее состояние доски задач.
- `graph rebuild` собирает граф кода; `graph status` показывает состояние `current`.

## Ограничения и безопасность

- Построение и обновление графа пишут **только** в одноразовый слой `.raytsystem/graph/`
  и не могут изменить каноническое знание — `ledger/CURRENT` остаётся байт-в-байт тем же
  (`docs/STATUS.md`).
- `raytsystem init` по умолчанию только планирует; применение требует явного `--apply`.
- Все правки через интерфейс требуют session/origin/CSRF/идемпотентности; браузер никогда
  не задаёт cwd, команду, argv или окружение.

## Частые ошибки

- **Init отказывает с конфликтами** — целевые файлы уже существуют; это ожидаемое
  безопасное поведение, а не сбой.
- **Граф помечен как устаревший (stale)** — запустите `graph update` или `graph rebuild`.
  См. [Граф кода: свежесть, update и rebuild](/code-graph/freshness-update-rebuild).

## Связанные страницы

- [Первый запуск](/getting-started/first-run)
- [Граф кода: обзор](/code-graph/overview)
- [Граф кода: свежесть, update и rebuild](/code-graph/freshness-update-rebuild)
- [Задачи](/tasks/overview)
- [Вселенная](/interface/universe)

## Источники истины

- `src/raytsystem/platform_cli.py`
- `src/raytsystem/cli.py`
- `ops/decisions/ADR-032-workspace-templates-and-migrations.md`
- `docs/STATUS.md`

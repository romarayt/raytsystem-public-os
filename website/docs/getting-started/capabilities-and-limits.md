---
title: "Возможности и ограничения текущей версии"
description: "Что работает в raytsystem сегодня, а что отключено по умолчанию, экспериментально или доступно только в DRAFT-режиме."
audience: [user, operator, developer]
status: stable
feature_flags: [code_graph_enabled, graph_first_query_enabled, digital_employees_enabled, task_workspaces_enabled, heartbeats_enabled, runtime_execution_enabled, codex_local_enabled, claude_local_enabled, scheduled_heartbeats_enabled]
related_commands: ["uv run raytsystem status", "uv run raytsystem platform-status", "uv run raytsystem graph status", "uv run raytsystem query", "uv run raytsystem policy simulate"]
related_pages: [/getting-started/what-is-raytsystem, /reference/feature-flags, /security/defaults, /agents/tool-hub, /getting-started/requirements, /troubleshooting/documented-but-disabled]
source_of_truth:
  - path: README.md
  - path: docs/STATUS.md
  - path: config/raytsystem.toml
  - path: src/raytsystem/toolhub/registry.py
last_verified_against: "schema v1.4.0"
---

# Возможности и ограничения текущей версии

## Что это

Эта страница честно разделяет то, что работает в текущей сборке, и то, что
реализовано, но **отключено по умолчанию**, является экспериментальным или доступно
только в режиме черновика (DRAFT). Опирается на раздел README «What works today» и
на флаги из `config/raytsystem.toml`. Источник: `README.md`, `config/raytsystem.toml`.

## Что работает сегодня

- **Веб-панель управления** (loopback `127.0.0.1`): «Центр управления», «База знаний»,
  «Документы», «Задачи»,
  «Вселенная», «Запуски», «Агенты», «Навыки», «Контекст», «Безопасность»,
  «Системы». Полностью русскоязычный слой представления. Источник: `README.md`.
- **Задачи** с надёжными командами создания/перехода, проверкой зависимостей,
  идемпотентностью и неизменяемой историей. Источник: `README.md`.
- **Вселенная знаний** с линзами Orbit / Knowledge / Work / Agent / Evidence и
  линзой «Код», рендером Sigma / WebGL и доступным списочным запасным вариантом.
  Источник: `README.md`, `docs/STATUS.md`.
- **Граф кода**: `code_graph_enabled = true` и `graph_first_query_enabled = true`.
  Операции `status`, `update`, `rebuild`, `query`, `explain`, `neighbors`, `path`,
  `impact`, `benchmark`. Источник: `config/raytsystem.toml`, `README.md`.
- **INGEST, QUERY, LINT, SAVE** поверх неизменяемых свидетельств. Источник: `README.md`.
- **Лаборатория оценок** (evals), **локальная трассировка**, **replay/fork/compare**,
  **симулятор политик** (`uv run raytsystem policy simulate`), **аварийные средства и
  прерыватели цепи**, **MCP-управление (только каталог)**, **жизненный цикл пакетов**,
  **движок workflow DAG** без сырого shell, **входящие уведомления**,
  **backup/export/restore**. Источник: `README.md`, `docs/STATUS.md`.
- **Цифровые сотрудники** как типизированные определения (`digital_employees_enabled`,
  `task_workspaces_enabled` включены) — но без фактического запуска процесса, см.
  ниже. Источник: `config/raytsystem.toml`, `README.md`.
- **Единый Agents UI**: definition и execution state соединяются по стабильному ID в одной
  карточке; каталог-статус и runtime readiness не смешиваются. Источник:
  [Агенты (интерфейс)](/interface/agents).
- **Просмотр и локальное редактирование Skills**: полный `SKILL.md` рендерится как inert
  Markdown; eligible `pack_local` skill можно hash-bound редактировать, а safe read-only source —
  скопировать в local skill. Save не запускает skill и оставляет `test_status: pending`.
  Источник: [Skills (интерфейс)](/interface/skills).
- **Локальные сердцебиения (heartbeats)**: `heartbeats_enabled = true`. Источник:
  `config/raytsystem.toml`.
- **Экспериментальный Tool Hub**: восемь типизированных `video.*` контрактов для media,
  транскриптов, кадров, OCR и timeline с project-local staging и SHA-256 provenance. Supplied
  transcript работает без внешнего CLI; CLI-backed media требует pinned executables и host sandbox executor.
  Сеть deny-by-default, visual inspection требует host, а кроссплатформенный `/watch` не аттестован.
  Источник: [Tool Hub](/agents/tool-hub).

## Что отключено по умолчанию

Эти возможности реализованы, но выключены и требуют явного одобрения. **Никогда не
считайте их работающими по умолчанию.**

- **Исполнение среды выполнения (runtime execution).** `runtime_execution_enabled = false`,
  `codex_local_enabled = false`, `claude_local_enabled = false`. Панель не запускает
  модель и не обращается к провайдеру; запуск дополнительно требует точного
  истекающего одобрения. Источник: `config/raytsystem.toml`, `docs/STATUS.md`.
- **Плановые сердцебиения.** `scheduled_heartbeats_enabled = false`. Источник:
  `config/raytsystem.toml`.
- **Внешние возможности платформы (fail-closed).** Из `config/platform.yaml` по
  умолчанию выключены: адаптер Promptfoo и удалённая генерация, экспорт OTLP,
  адаптер ACP, шлюз A2A и сетевая экспозиция A2A, внешние уведомления,
  ограниченное (прикладное) шифрование, внешнее исполнение MCP, внешний KMS.
  Источник: `docs/STATUS.md`.
- **Удалённая загрузка.** Штатные Tool Hub CLI/MCP не имеют destination-enforcing
  network capability, поэтому URL fail closed даже при переданном approval. Архивы, вебхуки,
  ASR/QMD и веса моделей остаются fail-closed. Локальный OCR кадров требует pinned `tesseract`
  и host-injected root-confined/network-denied executor. Источник: [Tool Hub](/agents/tool-hub).
- **Продвижение реального корпуса — default-deny.** Реальное продвижение запрещено
  до одобренного пилота M5b, который настроит внешний верификатор одобрений.
  Источник: `README.md`, `docs/STATUS.md`.

## DRAFT-режим и экспериментальное

- **Ограниченное шифрование** честно сообщает статус `unavailable`, так как
  backend `cryptography` намеренно ещё не является зависимостью. Источник:
  `docs/STATUS.md`.

## Границы качества

Ворота G0–G7 проходят только в объявленном синтетическом объёме. G8 (реальное
качество), G9 (человеческая приёмка) и G10 (пилот) остаются `PENDING_USER_PILOT`.
Репозиторий не претендует на готовность к продакшену. Источник: `README.md`,
`docs/STATUS.md`.

Typed tools, workflows, evals и история Skills показываются только когда такая metadata есть в
доверенном реестре. Интерфейс не извлекает их из прозы `SKILL.md`.

## Ограничения и безопасность

Всё внешнее выключено по умолчанию и открывается только явным ограниченным
одобрением. Проверить статусы флагов помогают `uv run raytsystem status` и
`uv run raytsystem platform-status`. См. [Значения по умолчанию](/security/defaults)
и [Задокументировано, но отключено](/troubleshooting/documented-but-disabled).

## Связанные страницы

- [Что такое raytsystem](/getting-started/what-is-raytsystem)
- [Справочник: флаги функций](/reference/feature-flags)
- [Безопасность: значения по умолчанию](/security/defaults)
- [Tool Hub](/agents/tool-hub)
- [Системные требования](/getting-started/requirements)
- [Задокументировано, но отключено](/troubleshooting/documented-but-disabled)

## Источники истины

- `README.md`
- `docs/STATUS.md`
- `config/raytsystem.toml`
- `src/raytsystem/toolhub/registry.py`

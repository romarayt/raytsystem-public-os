---
title: База знаний raytsystem
description: Публичная документация raytsystem — локальной агентной системы для работы со знаниями.
slug: /
sidebar_position: 0
audience:
  - user
  - operator
  - developer
  - contributor
status: stable
feature_flags: []
related_commands:
  - raytsystem ui
  - raytsystem doctor
  - raytsystem status
related_pages:
  - /getting-started/what-is-raytsystem
  - /getting-started/quickstart
  - /interface/overview
source_of_truth:
  - path: README.md
  - path: AGENTS.md
last_verified_against: "schema v1.4.0"
---

# База знаний raytsystem

Это публичная база знаний **raytsystem** — локальной (local-first) агентной системы для работы
со знаниями. Здесь собрано всё, что нужно пользователю, оператору, разработчику и контрибьютору:
установка, интерфейс, граф проекта, задачи, workflow, база знаний, безопасность, справочник CLI
и решение проблем.

:::info Как читать документацию
Каждая статья помечена статусом (`stable`, `experimental`, `disabled`, `draft`) и списком
`feature_flags`. Если функция выключена флагом по умолчанию, это указано явно — документация
не описывает отключённые возможности как рабочие.
:::

## С чего начать

- **Впервые здесь?** Прочитайте [«Что такое raytsystem»](/getting-started/what-is-raytsystem) и пройдите
  [быстрый путь](/getting-started/quickstart) от установки до первого полезного результата.
- **Хотите разобраться в интерфейсе?** Начните с [обзора интерфейса](/interface/overview).
- **Нужна архитектура проекта?** Смотрите [«Граф проекта»](/code-graph/overview) — нативный граф
  кода raytsystem.
- **Что-то не работает?** Загляните в [решение проблем](/troubleshooting/overview).

## Разделы

| Раздел | О чём |
|---|---|
| [Начало работы](/getting-started/what-is-raytsystem) | Установка, первый запуск, первый проект, обновление |
| [Интерфейс](/interface/overview) | Каждый раздел web-приложения |
| [Документы](/documents/overview) | Управляемый Markdown-workspace: поиск, ссылки, история, безопасная правка |
| [Граф проекта](/code-graph/overview) | Нативный граф кода: query, explain, impact, benchmark |
| [Задачи](/tasks/overview) | Создание задач, статусы, переходы, task ledger |
| [Workflow](/workflow/overview) | Цифровые сотрудники, DAG, execution plane |
| [База знаний](/knowledge/overview) | Raw evidence → promotion, INGEST/QUERY/LINT/SAVE |
| [Агенты и расширение](/agents/overview) | Agents, skills, contexts, packs |
| [Исследования](/research/overview) | Research workflow и демонстрационные вертикали |
| [Наблюдаемость](/observability/runs) | Runs, eval, tracing, replay, policy simulator |
| [Безопасность](/security/overview) | Local-first, approvals, emergency controls |
| [Конфигурация](/configuration/overview) | Конфигурационные файлы и feature flags |
| [Разработка](/development/contributing) | Участие в open source, синхронизация документации |
| [Решение проблем](/troubleshooting/overview) | Symptom-first диагностика |
| [Справочник](/reference/cli) | Автогенерируемый CLI/flags/routes/API reference |

## Ключевые принципы raytsystem

- **Local-first и loopback-only.** Интерфейс работает только на локальном адресе `127.0.0.1`.
  Внешние действия требуют явного разрешения.
- **Неизменяемая история.** Канонические знания и журнал задач не переписываются задним числом.
- **Derived-состояние перестраивается.** Индексы, граф кода и проекции можно пересобрать из
  канонического источника.
- **Документация — часть продукта.** Она синхронизируется с кодом; см.
  [правила синхронизации](/development/documentation).

Документация синхронизирована с публичным кодом и реестром схем `v1.4.0`.

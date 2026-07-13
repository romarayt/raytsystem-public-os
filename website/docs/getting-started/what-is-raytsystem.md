---
title: "Что такое raytsystem"
description: "Определение raytsystem: local-first операционная система для работы со знаниями с помощью ИИ — из чего она состоит и чем не является."
audience: [user, operator, developer]
status: stable
feature_flags: [code_graph_enabled, graph_first_query_enabled, digital_employees_enabled, task_workspaces_enabled, runtime_execution_enabled]
related_commands: ["uv run raytsystem doctor", "uv run raytsystem status", "uv run raytsystem ui"]
related_pages: [/getting-started/who-is-it-for, /getting-started/capabilities-and-limits, /interface/overview, /knowledge/overview, /code-graph/overview, /security/overview]
source_of_truth:
  - path: README.md
  - path: AGENTS.md
  - path: docs/STATUS.md
  - path: config/raytsystem.toml
last_verified_against: "schema v1.4.0"
---

# Что такое raytsystem

## Что это

raytsystem — это универсальная, **local-first** операционная система для работы со
знаниями с помощью ИИ. Она объединяет неизменяемый журнал происхождения данных
(ledger), рабочую доску задач, каталог агентов / навыков / контекста и наглядную
«Вселенную знаний» в одной самостоятельно размещаемой веб-панели управления
(control plane). Источник: `README.md`.

Ключевая идея — **проверяемость**. Каждый факт восходит к неизменяемым исходным
данным (evidence), а любая запись требует явной операции. Панель работает только
на локальной петле `127.0.0.1` и по умолчанию не запускает модель и не обращается
к внешнему провайдеру. Источник: `README.md`, `config/raytsystem.toml`.

## Из чего состоит

- **Неизменяемый ledger знаний.** Путь данных: сырые свидетельства (`_raw/`) →
  нормализация → предложение (proposal) → валидация → продвижение (promotion) →
  канонический ledger (`ledger/CURRENT` и неизменяемые поколения) →
  материализованные Markdown-страницы, поиск FTS5 и графы. Редактировать `_raw/`,
  объекты ledger и сгенерированные страницы `knowledge/` нельзя. Источник:
  `AGENTS.md`, `README.md`. Подробнее — [Знания: обзор](/knowledge/overview).

- **Задачи.** Отдельный append-only журнал задач с надёжными командами
  создания и перехода состояний, проверкой зависимостей и идемпотентностью.
  Источник: `README.md`.

- **Каталог агентов, навыков и контекста.** Строится только из фиксированных
  разрешённых корней рабочего пространства (allowlist). Неизвестный пакет не
  может сам присвоить себе официальное происхождение. Источник: `README.md`.

- **Вселенная знаний (Knowledge Universe).** Связывает знания, свидетельства,
  задачи, запуски, агентов и навыки; рендер через Sigma / WebGL с доступным
  списочным запасным представлением. Источник: `README.md`.
  См. [Интерфейс: Вселенная](/interface/universe).

- **Граф кода (code graph).** Нативный, перестраиваемый граф с инкрементальными
  снимками, происхождением/уверенностью и ограниченными операциями query, explain,
  neighbors, path, impact. Флаги `code_graph_enabled` и `graph_first_query_enabled`
  включены по умолчанию. Источник: `README.md`, `config/raytsystem.toml`.
  См. [Граф кода: обзор](/code-graph/overview).

- **Плоскость исполнения (execution plane).** Типизированные «цифровые
  сотрудники» с рабочими пространствами задач, ограждёнными лизами и бюджетами.
  Существует за флагами: `digital_employees_enabled` и `task_workspaces_enabled`
  включены, но само исполнение (`runtime_execution_enabled`, `codex_local_enabled`,
  `claude_local_enabled`) **отключено по умолчанию**. Источник: `config/raytsystem.toml`,
  `docs/STATUS.md`.

## Чем raytsystem не является

- Это **не облачный сервис**: не нужны API-ключ, облачный аккаунт, расширение
  браузера или плагин. Привязка к не-loopback-адресу в этом релизе запрещена.
  Источник: `README.md`.
- Это **не автономный агент-исполнитель по умолчанию**: панель не выполняет модель
  и не связывается с провайдером, пока флаги исполнения выключены. Определение
  агента не означает работающего агента. Источник: `README.md`, `docs/STATUS.md`.
- Это **не редактор канонических данных вручную**: изменения проходят только через
  операции INGEST, QUERY, LINT, SAVE и валидированное продвижение. Продвижение
  реального корпуса по умолчанию запрещено (default-deny). Источник: `AGENTS.md`,
  `README.md`.

## Ограничения и безопасность

raytsystem работает локально и по принципу «fail-closed». Все внешние возможности
выключены по умолчанию и требуют явного одобрения. Запросы GET/HEAD никогда не
пишут данные, а браузер никогда не передаёт путь, команду, argv или окружение.
Подробности — [Безопасность: обзор](/security/overview) и
[Возможности и ограничения](/getting-started/capabilities-and-limits).

## Связанные страницы

- [Для кого предназначен raytsystem](/getting-started/who-is-it-for)
- [Возможности и ограничения текущей версии](/getting-started/capabilities-and-limits)
- [Интерфейс: обзор](/interface/overview)
- [Знания: обзор](/knowledge/overview)
- [Граф кода: обзор](/code-graph/overview)
- [Безопасность: обзор](/security/overview)

## Источники истины

- `README.md`
- `AGENTS.md`
- `docs/STATUS.md`
- `config/raytsystem.toml`

---
title: "Быстрый путь: от установки до первого результата"
description: "Пошаговый happy path raytsystem: установка, запуск интерфейса, граф кода, первый graph-запрос и создание задачи — с командами и ожидаемым результатом."
sidebar_position: 3
audience: [user]
status: stable
feature_flags: [code_graph_enabled, graph_first_query_enabled, runtime_execution_enabled]
related_commands:
  - uv sync --dev
  - uv run raytsystem doctor
  - uv run raytsystem ui
  - uv run raytsystem graph status
  - uv run raytsystem graph update
  - uv run raytsystem graph rebuild
  - uv run raytsystem graph query "..."
related_pages:
  - /getting-started/requirements
  - /getting-started/installation
  - /getting-started/first-run
  - /getting-started/interface-tour
  - /code-graph/overview
  - /code-graph/query-explain-neighbors
  - /tasks/overview
  - /tasks/ui-and-cli
source_of_truth:
  - path: README.md
  - path: web/src/app/App.tsx
  - path: web/src/presentation.ts
last_verified_against: "schema v1.4.0"
---

# Быстрый путь: от установки до первого результата

## Что это

Кратчайший путь от чистого репозитория до первого полезного результата: установка,
запуск локального интерфейса, построение графа кода, первый graph-запрос и создание
задачи. Каждый шаг сопровождается командой или действием и тем, что вы должны увидеть.

## Предварительные условия

Нужны Python 3.12+ и [uv](https://docs.astral.sh/uv/). Node.js 22+ требуется только при
пересборке фронтенда — для этого пути он не нужен. Подробнее — в
[требованиях](/getting-started/requirements).

## Пошагово

### 1. Установить зависимости

```bash
uv sync --dev
```

Ожидаемо: окружение собрано, команда `uv run raytsystem ...` доступна. При желании проверьте
установку:

```bash
uv run raytsystem doctor
```

### 2. Открыть интерфейс

```bash
uv run raytsystem ui
```

Ожидаемо: интерфейс открывается на `http://127.0.0.1:8765`. raytsystem связывается только с
loopback-адресом; API-ключ, облачный аккаунт или расширение браузера не требуются.

### 3. Открыть «Вселенную»

В левой панели, в группе **Оркестрация**, откройте раздел **Вселенная**. Это граф знаний,
работы и доказательств. Переключитесь на линзу **Код**, чтобы видеть структуру кодовой
базы. Если граф ещё не построен, линза будет пустой — перейдём к сборке.

### 4. Построить граф кода

Сначала посмотрите свежесть графа (только чтение):

```bash
uv run raytsystem graph status --json
```

Затем постройте или обновите его:

```bash
uv run raytsystem graph update --json
```

Ожидаемо: инкрементальный снимок графа создан. Для полной пересборки используйте
`uv run raytsystem graph rebuild --json`. Обе команды пишут только в служебный,
пересобираемый план `.raytsystem/graph/` и не изменяют канонические знания. Подробнее —
в [обзоре графа кода](/code-graph/overview).

### 5. Задать первый graph-запрос

```bash
uv run raytsystem graph query "How does task checkout work?" --depth 2 --json
```

Ожидаемо: ограниченный по глубине ответ с узлами и связями графа. Те же результаты
доступны в интерфейсе через линзу «Код». См.
[query / explain / neighbors](/code-graph/query-explain-neighbors).

### 6. Создать задачу

Перейдите в раздел **Задачи** и создайте задачу через форму создания. Эквивалент в CLI —
команда `uv run raytsystem task create` (точные аргументы и статусы см. в
[«Задачи: интерфейс и CLI»](/tasks/ui-and-cli)). Убедиться, что задача записана:

```bash
uv run raytsystem task list --json
```

Ожидаемо: новая задача появляется в операционном журнале. История задач неизменяема —
статусы меняются переходами, а не перезаписью.

## Ожидаемый результат

Интерфейс запущен локально, граф кода построен, первый graph-запрос вернул результат, а
в журнале задач есть ваша первая задача.

## Ограничения и безопасность

Выполнение моделей по умолчанию отключено (`runtime_execution_enabled` выключен): интерфейс
не запускает модель и не обращается к провайдеру. Реальная публикация знаний работает по
принципу default-deny. Не редактируйте вручную `_raw/`, `ledger/` и сгенерированные
страницы `knowledge/`.

## Связанные страницы

- [Установка](/getting-started/installation)
- [Первый запуск](/getting-started/first-run)
- [Экскурсия по интерфейсу](/getting-started/interface-tour)
- [Обзор графа кода](/code-graph/overview)

## Источники истины

- `README.md`
- `web/src/app/App.tsx`
- `web/src/presentation.ts`

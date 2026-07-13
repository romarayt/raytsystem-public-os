---
title: "Graph-first запросы к архитектуре"
description: "Как агрегированный query --scope auto направляет архитектурные вопросы в граф кода, а факты — в знания, с явным fallback и воспроизводимым benchmark."
audience: [developer]
status: stable
feature_flags: [code_graph_enabled, graph_first_query_enabled]
related_commands:
  - "uv run raytsystem query \"...\" --scope auto --json"
  - "uv run raytsystem graph benchmark --json"
related_pages:
  - /code-graph/overview
  - /code-graph/query-explain-neighbors
  - /knowledge/overview
  - /code-graph/freshness-update-rebuild
  - /reference/cli
source_of_truth:
  - path: src/raytsystem/querying.py
  - path: src/raytsystem/codegraph/querying.py
  - path: src/raytsystem/cli.py
  - path: docs/11-code-graph-and-execution-plane.md
  - path: benchmarks/codegraph/questions.jsonl
last_verified_against: "schema v1.4.0"
---

# Graph-first запросы к архитектуре

## Что это

Команда `query` — единая точка входа для вопросов. Она сама решает, куда направить запрос: архитектурные вопросы уходят в **граф кода** (graph-first), а фактические — в **проверенные знания** (FTS5 по канонической генерации). Логика маршрутизации живёт в `QueryService.route` (`src/raytsystem/querying.py`).

Правило маршрутизации:

- `--scope code` — всегда пытаться через граф кода; при недоступности графа запрос **отклоняется** (fail-closed), а не подменяется поиском.
- `--scope auto` (по умолчанию) — через граф, только если включён флаг `graph_first_query_enabled` **и** запрос распознан как «кодовый» по маркерам намерения (например `архитектур`, `зависимост`, `импорт`, `модул`, `влияни`, `где определ`, `код`, `функци`, `.py`, `.tsx`). Иначе — обычный поиск по знаниям.
- `--scope knowledge` — всегда обычный поиск.

## Когда использовать

- Ориентация в архитектуре, зависимостях, владельцах функциональности и импактах — начинайте с `query --scope auto`.
- Смешанные вопросы, где заранее неясно, факт это или структура кода.
- Сравнение «обычное чтение файлов против graph-first» — через `graph benchmark`.

## Предварительные условия

- Для graph-ветки нужны флаги `code_graph_enabled` и `graph_first_query_enabled` (по умолчанию включены) и свежий граф.
- Для knowledge-ветки нужна собранная генерация и индекс FTS5.

## Пошагово

1. Задайте вопрос: `uv run raytsystem query "..." --scope auto --json`.
2. Управляйте глубиной обхода графа флагом `--depth` (1..3, по умолчанию 2) и числом результатов знаний `--limit` (1..20).
3. Прочитайте в ответе `resolved_scope` (`code` или `knowledge`) и `fallback_reason`, если он есть.

## Fallback и бюджет контекста

Если при `--scope auto` граф отсутствует или устарел, `route` не падает, а переключается на обычный поиск и записывает причину `code_graph_unavailable_or_stale`. Если graph-first выключен флагом, а вопрос кодовый, причина будет `graph_first_disabled` (`src/raytsystem/querying.py`).

Ответ графа ограничен бюджетом узлов, рёбер и байт: результат «seed-first», но при превышении жёсткого лимита байт операция завершается ошибкой, а не выдаёт неполный небезопасный ответ (`src/raytsystem/codegraph/querying.py`). Это и есть механизм экономии контекста — вернуть релевантный минимум вместо чтения множества файлов целиком.

## Benchmark

`graph benchmark` сравнивает ограниченный обычный лексический/файловый базовый поиск с graph-first при **одинаковом** байтовом лимите. Он использует объявленные вопросы и ожидаемые пути из `benchmarks/codegraph/questions.jsonl` и сообщает: хешированные вопросы, байты контекста, число прочитанных файлов, число поисковых операций, покрытие, точность ссылок на источники, задержку, число fallback и провалы из-за устаревшего графа. На устаревшем графе команда отказывается работать.

```bash
uv run raytsystem graph benchmark --json
```

Важно: результаты — это **наблюдения**, а не маркетинговые обещания. Целевое сокращение контекста (в исходниках упоминается ориентир 40%) сообщается как pass/fail без подгонки. Не выдавайте конкретные числа за гарантированный факт — benchmark воспроизводим, запустите его в своём репозитории.

## Ожидаемый результат

- При кодовом намерении и свежем графе — срез графа (`resolved_scope: code`).
- При факте, отключённом флаге или устаревшем графе (в режиме auto) — ответ из знаний с указанным `fallback_reason`.

## Ограничения и безопасность

- Граф — производное состояние и не заменяет `ledger/CURRENT`; ответы знаний строятся только на поддержанных claim с проверенными цитатами.
- Запросы проходят проверку на длину и политику чувствительности; секреты в тексте запроса отклоняются.
- В режиме `--scope code` недоступность графа приводит к отказу, а не к тихому fallback.

## Частые ошибки

- Ждёте графовый ответ, а получаете знания — проверьте `fallback_reason` и свежесть графа (`graph status`).
- Кодовый вопрос уходит в знания — включён ли `graph_first_query_enabled` и распознаётся ли намерение (добавьте явные маркеры вроде «архитектура», «зависимости», «impact»).

## Связанные страницы

- [Обзор графа кода](/code-graph/overview)
- [query, explain, neighbors](/code-graph/query-explain-neighbors)
- [Обзор знаний](/knowledge/overview)
- [Свежесть: update и rebuild](/code-graph/freshness-update-rebuild)
- [Справочник CLI](/reference/cli)

## Источники истины

- `src/raytsystem/querying.py`
- `src/raytsystem/codegraph/querying.py`
- `src/raytsystem/cli.py`
- `docs/11-code-graph-and-execution-plane.md`
- `benchmarks/codegraph/questions.jsonl`

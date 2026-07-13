---
title: "Сценарии анализа через граф"
description: "Практические сценарии работы с графом кода: понять архитектуру, найти владельца функциональности, оценить влияние изменения и отладить проблему, плюс восстановление устаревшего графа."
audience: [developer]
status: stable
feature_flags: [code_graph_enabled, graph_first_query_enabled]
related_commands:
  - "uv run raytsystem graph query \"...\" --depth 2 --json"
  - "uv run raytsystem graph impact <file-or-node> --depth 3 --json"
  - "uv run raytsystem graph rebuild --json"
related_pages:
  - /code-graph/overview
  - /code-graph/query-explain-neighbors
  - /code-graph/path-and-impact
  - /code-graph/graph-first
  - /code-graph/freshness-update-rebuild
  - /troubleshooting/code-graph-missing-or-stale
source_of_truth:
  - path: src/raytsystem/codegraph/querying.py
  - path: src/raytsystem/querying.py
  - path: src/raytsystem/cli.py
  - path: docs/11-code-graph-and-execution-plane.md
  - path: AGENTS.md
last_verified_against: "commit 3f2a123 / schema v1.4.0"
---

# Сценарии анализа через граф

## Что это

Подборка рабочих сценариев: какую команду взять под задачу и как читать результат. Все команды ниже — только чтение свежего снимка графа. Если исходники менялись, сначала обновите граф (см. раздел о восстановлении).

## Когда использовать

- Вы впервые разбираетесь в незнакомой части кодовой базы.
- Нужно быстро найти, где реализована функциональность и кто от неё зависит.
- Готовите изменение и оцениваете риск.
- Отлаживаете проблему и хотите увидеть цепочку связей между компонентами.

## Предварительные условия

- Флаги `code_graph_enabled` и `graph_first_query_enabled` включены (по умолчанию).
- Граф в состоянии `current`; проверка — `uv run raytsystem graph status --json`.

## Сценарий 1. Понять архитектуру

Задайте архитектурный вопрос и получите ограниченный релевантный срез вместо чтения десятков файлов:

```bash
uv run raytsystem graph query "How does task checkout work?" --depth 2 --json
```

Как читать: узлы отсортированы по релевантности (seed-first), god- и bridge-узлы подсвечивают центральные модули и связующие точки. Через агрегированный `query --scope auto` тот же вопрос маршрутизируется автоматически — см. [graph-first](/code-graph/graph-first).

## Сценарий 2. Найти владельца функциональности

Начните с `explain`, чтобы увидеть узел с ближайшим окружением, затем сузьте направлением через `neighbors`:

```bash
uv run raytsystem graph explain TaskService --json
uv run raytsystem graph neighbors TaskService --direction in --json
```

Как читать: входящие соседи (`--direction in`) показывают, кто использует компонент, — это и есть кандидаты во «владельцы» и потребители. Детали операций — на странице [query, explain, neighbors](/code-graph/query-explain-neighbors).

## Сценарий 3. Оценить влияние изменения

Перед правкой файла посмотрите обратные зависимости:

```bash
uv run raytsystem graph impact src/raytsystem/tasking.py --depth 3 --json
```

Как читать: возвращённые узлы — это потребители, которых затронет изменение; поле глубины показывает, насколько далеко они отстоят. Учитываются рёбра `calls`, `depends_on`, `imports`, `implements`, `inherits`, `references`, `tests`, `verifies`, `configured_by` (`src/raytsystem/codegraph/querying.py`). Подробнее — [путь и влияние](/code-graph/path-and-impact).

## Сценарий 4. Отладка через граф

Когда неясно, как связаны два компонента в проблемной цепочке, постройте кратчайший путь:

```bash
uv run raytsystem graph path TaskService ControlDB --json
```

Как читать: упорядоченная цепочка узлов и рёбра между ними показывают маршрут связи; если пути нет, вы получите явную ошибку — значит, прямой связи в графе нет. Согласно `AGENTS.md`, для ориентации в архитектуре, зависимостях, владельцах и импактах граф запрашивают первым; при устаревшем графе сообщают fallback и используют точечный обычный поиск, не считая производный граф канонической истиной.

## Восстановление при устаревшем графе

Изменение исходников, конфигурации или экстрактора делает граф устаревшим, и операции чтения завершаются с ошибкой (fail-closed). Порядок восстановления:

1. `uv run raytsystem graph status --json` — увидеть состояние и изменённые пути.
2. `uv run raytsystem graph update --json` — инкрементально обновить по валидному кэшу.
3. Если update не подходит — `uv run raytsystem graph rebuild --json` полностью пересобирает граф, игнорируя кэш.

Снимки лежат под `.raytsystem/graph/` и одноразовы; не создавайте и не коммитьте `graphify-out/`. Никогда не удаляйте и не переписывайте канонические объекты реестра ради сброса графа. Диагностика — [устаревший или отсутствующий граф](/troubleshooting/code-graph-missing-or-stale).

## Ожидаемый результат

- Быстрая ориентация без полного чтения файлов, с ограниченным и воспроизводимым контекстом.
- Явные ошибки вместо неполных ответов, когда граф устарел или результат не помещается в бюджет.

## Ограничения и безопасность

- Все операции принимают только типизированные ID, символы или относительные пути — не абсолютный путь, cwd, argv или команду.
- Результат ограничен бюджетом узлов, рёбер и байт.
- Граф — производное состояние и не заменяет `ledger/CURRENT`.

## Частые ошибки

- Операция падает с «не current» — сначала `graph update`/`graph rebuild`.
- «Узел не найден» — задайте точный ID узла или относительный путь.
- Ожидали графовый ответ от `query`, получили знания — проверьте `fallback_reason` (см. [graph-first](/code-graph/graph-first)).

## Связанные страницы

- [Обзор графа кода](/code-graph/overview)
- [query, explain, neighbors](/code-graph/query-explain-neighbors)
- [Путь и влияние](/code-graph/path-and-impact)
- [Graph-first запросы](/code-graph/graph-first)
- [Свежесть: update и rebuild](/code-graph/freshness-update-rebuild)

## Источники истины

- `src/raytsystem/codegraph/querying.py`
- `src/raytsystem/querying.py`
- `src/raytsystem/cli.py`
- `docs/11-code-graph-and-execution-plane.md`
- `AGENTS.md`

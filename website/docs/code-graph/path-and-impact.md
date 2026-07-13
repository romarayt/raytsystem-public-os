---
title: "Путь и влияние: path, impact"
description: "Как найти кратчайший путь между двумя узлами графа кода и оценить обратные зависимости изменения через graph path и graph impact."
audience: [developer]
status: stable
feature_flags: [code_graph_enabled, graph_first_query_enabled]
related_commands:
  - "uv run raytsystem graph path <a> <b> --json"
  - "uv run raytsystem graph impact <file-or-node> --depth 3 --json"
related_pages:
  - /code-graph/overview
  - /code-graph/query-explain-neighbors
  - /code-graph/freshness-update-rebuild
  - /code-graph/scenarios
  - /troubleshooting/code-graph-missing-or-stale
source_of_truth:
  - path: src/raytsystem/codegraph/querying.py
  - path: src/raytsystem/cli.py
  - path: docs/11-code-graph-and-execution-plane.md
last_verified_against: "schema v1.4.0"
---

# Путь и влияние: path, impact

## Что это

Две операции графа кода, которые отвечают на структурные вопросы «как связаны две сущности» и «что сломается, если я это трону»:

- `graph path` — детерминированный **кратчайший путь** между двумя узлами. Обход идёт в ширину (BFS) по рёбрам в обоих направлениях, а среди нескольких рёбер между соседями предпочитаются достоверные, а не выведенные или неоднозначные (`src/raytsystem/codegraph/querying.py`).
- `graph impact` — **обратные зависимости**: кто ссылается на файл или узел. Операция идёт по рёбрам в обратную сторону и показывает, что затрагивает предлагаемое изменение.

Обе операции только читают текущий снимок графа и никогда не пишут в него.

## Когда использовать

- Нужно понять, через какие модули связаны два компонента, — `graph path`.
- Планируется правка файла или функции, и нужно заранее увидеть затронутых потребителей — `graph impact`.
- Готовится ревью или оценка риска изменения.

## Предварительные условия

- Флаги `code_graph_enabled` и `graph_first_query_enabled` включены (значения по умолчанию).
- Граф в состоянии `current`. Если исходники, конфигурация или экстрактор менялись, граф считается устаревшим и операции завершаются с ошибкой (fail-closed), пока не выполнена `graph update` или `graph rebuild`. См. [обновление и пересборку](/code-graph/freshness-update-rebuild).

## Пошагово

1. Убедитесь, что граф свежий: `uv run raytsystem graph status --json`.
2. Для пути укажите два узла (типизированный ID, символ или относительный путь):
   `uv run raytsystem graph path <a> <b> --json`.
3. Для оценки влияния укажите файл или узел:
   `uv run raytsystem graph impact <file-or-node> --json`.
   Глубина обхода задаётся флагом `--depth` (1..3, по умолчанию 3).

## Пример

```bash
uv run raytsystem graph path TaskService ControlDB --json
uv run raytsystem graph impact src/raytsystem/tasking.py --depth 3 --json
```

## Ожидаемый результат

- `path` возвращает упорядоченную цепочку узлов от источника к цели и рёбра между ними. Если пути нет, операция сообщает об этом явной ошибкой.
- `impact` возвращает множество узлов-потребителей с их глубиной (на каком расстоянии от изменения они находятся). Учитываются рёбра типов `calls`, `configured_by`, `depends_on`, `implements`, `imports`, `inherits`, `references`, `tests`, `verifies` (`src/raytsystem/codegraph/querying.py`).
- Оба результата снабжены идентификатором и отпечатком снимка, а также оценкой размера контекста.

## Ограничения и безопасность

- Результат ограничен настроенным бюджетом узлов и байт. Если кратчайший путь не помещается в бюджет узлов, операция завершается ошибкой, а не выдаёт усечённый путь. Для `impact` обход останавливается при достижении лимита узлов.
- API и CLI принимают только типизированные ссылки на узлы, символы или относительные пути — никогда не абсолютный путь, cwd, argv или команду.
- Неоднозначная ссылка (одинаковая метка у разных файлов) отклоняется с ошибкой — уточните узел точным ID.
- Граф — производное состояние. Он не заменяет канонический реестр `ledger/CURRENT` и не является источником истины о фактах.

## Частые ошибки

- «Граф не current» — сначала `graph update`/`graph rebuild`, затем повторите запрос. См. [устаревший граф](/troubleshooting/code-graph-missing-or-stale).
- «Узел не найден» или «ссылка неоднозначна» — задайте точный ID узла или относительный путь файла.
- «Путь не помещается в бюджет» — сузьте задачу: используйте `impact` от конкретного узла вместо длинного сквозного пути.

## Связанные страницы

- [Обзор графа кода](/code-graph/overview)
- [query, explain, neighbors](/code-graph/query-explain-neighbors)
- [Свежесть: update и rebuild](/code-graph/freshness-update-rebuild)
- [Сценарии анализа через граф](/code-graph/scenarios)

## Источники истины

- `src/raytsystem/codegraph/querying.py`
- `src/raytsystem/cli.py`
- `docs/11-code-graph-and-execution-plane.md`

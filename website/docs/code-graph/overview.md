---
title: "Граф проекта: обзор"
description: "Что такое нативный граф кода raytsystem, чем он является и чем не является, и как он соотносится с каноническим реестром."
audience: [user, developer]
status: stable
feature_flags: [code_graph_enabled, graph_first_query_enabled]
related_commands:
  - "uv run raytsystem graph status"
related_pages:
  - /code-graph/freshness-update-rebuild
  - /code-graph/query-explain-neighbors
  - /code-graph/path-and-impact
  - /code-graph/graph-first
  - /interface/universe
  - /reference/feature-flags
source_of_truth:
  - path: docs/11-code-graph-and-execution-plane.md
  - path: ops/decisions/ADR-016-native-derived-code-graph.md
  - path: src/raytsystem/codegraph/projection.py
  - path: config/raytsystem.toml
last_verified_against: "commit 3f2a123 / schema v1.4.0"
---

# Граф проекта: обзор

## Что это

Граф кода raytsystem — это нативный производный (derived) слой, который проецирует репозиторий в набор нод и связей: файлы, директории, модули, функции/классы, зависимости и репозиторий как узел. Он строится прямо из рабочей копии кода с помощью Tree-sitter для поддерживаемого синтаксиса JavaScript/TypeScript, локального AST Python и детерминированных читателей метаданных, SQL, конфигурации и ADR (`ops/decisions/ADR-016-native-derived-code-graph.md`).

Граф решает класс задач, похожий на инструменты вроде Graphify (архитектурная навигация, анализ зависимостей и влияния), но это собственный компонент raytsystem: никакой сторонний рантайм, отдельная графовая база или каталог `graphify-out/` не подключаются и не становятся источником истины (`ADR-016`).

Работа графа управляется двумя флагами в `config/raytsystem.toml`: `code_graph_enabled = true` (сам граф) и `graph_first_query_enabled = true` (маршрутизация архитектурных запросов в граф). Оба включены по умолчанию.

## Чем граф НЕ является: canonical против derived

Ключевое различие — граф производный и одноразовый, он не заменяет канонический реестр знаний:

- Снимки графа лежат под `.raytsystem/graph/` и полностью пересобираемы. Удаление этой директории не теряет никаких исходных данных — `rebuild` восстанавливает проекцию (`ADR-016`, «Consequences»).
- `CURRENT` графа — это точка фиксации производного состояния. Ни снимок, ни манифест, ни WAL графа не могут писать в `ledger/CURRENT`. Канонический реестр остаётся единственным источником знаний, а `ops/task-ledger/` — единственным журналом задач (`docs/11-code-graph-and-execution-plane.md`).
- При изменении исходников граф становится видимо устаревшим (STALE), а не молча возвращает смешанное состояние.

## Типы нод, связей и происхождение (provenance)

Каждый снимок привязан к хешам файлов, отпечаткам экстрактора/конфигурации и метаданным Git — стабильные логические входы дают одинаковые идентификаторы нод, рёбер и снимка (`ADR-016`, `src/raytsystem/codegraph/projection.py`). У нод сохраняется происхождение: узлы без пути допускаются только для видов `DEPENDENCY` и `REPOSITORY`, иначе узел обязан иметь исходный файл (`projection.py`, `_validate_projection_semantics`).

Связи маркируются уровнем доверия (confidence): `EXTRACTED`, `INFERRED` или `AMBIGUOUS`. У рёбер сохраняются исходные локации и провенанс экстрактора (`ADR-016`, «Decision»). Метрика `ambiguous_edges` в снимке отражает число неоднозначных рёбер.

## Когда использовать

- Нужно понять архитектуру, зависимости, владение или влияние изменения, не читая десятки файлов вручную.
- Нужна воспроизводимая навигация по коду с указанием источников и уровня доверия.

Для точных фактических знаний по-прежнему используется путь QUERY по подтверждённым свидетельствам; граф отвечает на структурные вопросы о коде (`ADR-016`, «Decision»).

## Ограничения и безопасность

- Граф — проекция, а не истина. Не редактируйте и не коммитьте `.raytsystem/graph/`; не создавайте `graphify-out/`.
- Ноды/метаданные ограничены по размеру, а значения, похожие на секреты, редактируются до попадания в кэш или API (`projection.py`).
- API обхода принимает только типизированные идентификаторы нод — никогда путь файловой системы, cwd, argv или команду (`docs/11-...`, «Security boundary»).

## Быстрый пример

```bash
uv run raytsystem graph status
```

Команда `graph status` только читает и показывает свежесть текущего графа.

## Связанные страницы

- [Актуальность графа: status, update, rebuild](/code-graph/freshness-update-rebuild)
- [Запросы к графу: query, explain, neighbors](/code-graph/query-explain-neighbors)
- [Путь и влияние: path, impact](/code-graph/path-and-impact)
- [Graph-first маршрутизация](/code-graph/graph-first)
- [Вселенная и линза «Код»](/interface/universe)

## Источники истины

- `docs/11-code-graph-and-execution-plane.md`
- `ops/decisions/ADR-016-native-derived-code-graph.md`
- `src/raytsystem/codegraph/projection.py`
- `config/raytsystem.toml`

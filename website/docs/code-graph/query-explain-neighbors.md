---
title: "Запросы к графу: query, explain, neighbors"
description: "Как задавать вопросы графу кода: поиск по подграфу, объяснение узла и обход соседей в пределах бюджета контекста."
audience: [user, developer]
status: stable
feature_flags: [code_graph_enabled, graph_first_query_enabled]
related_commands:
  - "uv run raytsystem graph query \"...\" --depth 2 --json"
  - "uv run raytsystem graph explain <node> --json"
  - "uv run raytsystem graph neighbors <node> --json"
related_pages:
  - /code-graph/overview
  - /code-graph/freshness-update-rebuild
  - /code-graph/path-and-impact
  - /code-graph/graph-first
  - /interface/universe
source_of_truth:
  - path: src/raytsystem/codegraph/querying.py
  - path: config/raytsystem.toml
  - path: docs/11-code-graph-and-execution-plane.md
last_verified_against: "commit 3f2a123 / schema v1.4.0"
---

# Запросы к графу: query, explain, neighbors

## Что это

Три операции чтения позволяют опрашивать граф кода:

- **`graph query`** — задаёт вопрос о коде: находит наиболее релевантные seed-ноды и расширяет вокруг них подграф в пределах бюджета.
- **`graph explain`** — берёт конкретный узел и показывает его непосредственное окружение.
- **`graph neighbors`** — обходит соседей узла с заданной глубиной и направлением.

Все три реализованы в `src/raytsystem/codegraph/querying.py` и возвращают ноды с указанием источников, уровня доверия (confidence) и идентификатора снимка.

## Когда использовать

- Нужно понять, как устроена часть системы, — начните с `graph query "..."`.
- Известен конкретный узел (например `TaskService`) и нужно объяснить его роль — `graph explain`.
- Нужно точечно посмотреть, с кем связан узел, и в какую сторону — `graph neighbors` с `--direction`.

## Предварительные условия

Граф должен быть текущим (CURRENT). Запросы читают только актуальный снимок: если статус не CURRENT (граф устарел, строится или отсутствует), операция безопасно отказывает (fail closed) с ошибкой недоступности, а не отвечает на смешанном состоянии (`querying.py`, `_current_snapshot`). Проверьте свежесть через `graph status` и при необходимости выполните `graph update` (см. [Актуальность графа](/code-graph/freshness-update-rebuild)).

## Как это работает

### `graph query`

Запрос ранжирует ноды по релевантности к тексту вопроса, берёт до трёх лучших seed-нод и расширяет подграф обходом соседей. Результат ограничен бюджетом из `config/raytsystem.toml`: `query_max_nodes = 24`, `query_max_edges = 36`, `query_max_bytes = 48000`. Если даже минимальный результат не помещается в жёсткий байтовый бюджет, запрос отказывает, а не отдаёт усечённый мусор (`querying.py`, `_result`). Ответ помечается `truncated`, когда бюджет заставил отбросить ноды или рёбра.

Каждый ответ содержит: идентификатор и логический отпечаток снимка, хеш текста запроса, список нод с их глубиной, рёбра с уровнем доверия, `seed_node_ids`, `ordered_node_ids` и `estimated_context_bytes`. Так ответ привязан к конкретному снимку и его источникам.

Глубину задаёт `--depth`; по умолчанию для `query` это 2. Допустимый диапазон глубины — 1..3 (`querying.py`, `_snapshot`). На практике для запросов достаточно глубины 1–2: это удерживает контекст компактным.

### `graph explain`

`explain` разрешает переданное значение в один узел (по идентификатору, по пути файла или по релевантности) и показывает его окружение с глубиной по умолчанию 1 в обоих направлениях. Если ссылка на узел неоднозначна (совпадает метка, но разные пути), операция сообщает об ошибке, а не угадывает (`querying.py`, `_resolve_node`).

### `graph neighbors`

`neighbors` обходит соседей узла. Глубина по умолчанию 1, направление — `both`, `out` или `in` (`querying.py`, метод `neighbors`). Это самый точечный способ посмотреть входящие или исходящие связи конкретного узла.

## Пошагово

```bash
# 1. Убедиться, что граф актуален
uv run raytsystem graph status --json

# 2. Задать архитектурный вопрос
uv run raytsystem graph query "How does task checkout work?" --depth 2 --json

# 3. Объяснить конкретный узел
uv run raytsystem graph explain TaskService --json

# 4. Посмотреть исходящих соседей на один шаг
uv run raytsystem graph neighbors TaskService --depth 1 --json
```

## Пример

`graph query "How does task checkout work?" --depth 2` вернёт seed-ноды, наиболее релевантные вопросу, плюс их ближайшее окружение в пределах 24 нод и 36 рёбер, с указанием исходных локаций и уровня доверия у связей.

## Ожидаемый результат

JSON с полями снимка, списком нод (с глубиной) и рёбер (с confidence), seed- и упорядоченными идентификаторами и оценкой размера контекста. Те же данные показывает линза **Код** во «Вселенной» с фильтрами по типам нод, связям и уровню доверия и с доступной табличной альтернативой (`docs/11-code-graph-and-execution-plane.md`).

## Ограничения и безопасность

- Текст запроса ограничен: от 1 до 512 символов, без нулевых байтов, и проходит проверку политики чувствительности — запрос, похожий на секрет, отклоняется (`querying.py`, `_validate_text`).
- Контекст жёстко ограничен бюджетом нод/рёбер/байтов; расширяйте глубину осознанно.
- API обхода принимает типизированные идентификаторы нод, а не пути файловой системы, cwd, argv или команды (`docs/11-...`, «Security boundary»).

## Частые ошибки

- **«Code graph is not current»** — граф устарел или не построен. Запустите `graph status`, затем `graph update` или `rebuild`.
- **«node reference is ambiguous»** — метка узла совпадает у нескольких файлов. Уточните узел его точным идентификатором или путём.
- **Пустой или усечённый ответ** — вопрос слишком широкий для бюджета. Сузьте формулировку или уменьшите глубину.

## Связанные страницы

- [Граф проекта: обзор](/code-graph/overview)
- [Актуальность графа: status, update, rebuild](/code-graph/freshness-update-rebuild)
- [Путь и влияние: path, impact](/code-graph/path-and-impact)
- [Вселенная и линза «Код»](/interface/universe)

## Источники истины

- `src/raytsystem/codegraph/querying.py`
- `config/raytsystem.toml`
- `docs/11-code-graph-and-execution-plane.md`

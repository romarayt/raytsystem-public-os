---
title: "Источники, claims, evidence и цитаты"
description: "Как устроены знания внутри raytsystem: источники и ревизии, claims и сущности, evidence с привязкой к точным байтам, цитаты в ответах и связь с Knowledge Universe."
audience: [user, developer]
status: stable
feature_flags: [code_graph_enabled]
related_commands:
  - "uv run raytsystem query \"...\" --json"
  - "uv run raytsystem ingest <src>"
related_pages:
  - /knowledge/overview
  - /knowledge/operations
  - /interface/universe
  - /observability/runs
source_of_truth:
  - path: ops/decisions/ADR-002-immutable-raw-and-evidence.md
  - path: ops/decisions/ADR-011-generation-bound-retrieval-and-draft-save.md
  - path: src/raytsystem/ingestion.py
  - path: src/raytsystem/querying.py
last_verified_against: "commit 3f2a123 / schema v1.4.0"
---

# Источники, claims, evidence и цитаты

## Что это

Внутри raytsystem знание — это не свободный текст, а набор связанных типизированных записей. Понимание этих сущностей помогает читать ответы QUERY и разбираться в разделе Вселенная.

## Источники и ревизии

**Источник (Source)** — это логическая единица материала (например, конкретный файл в рабочей области). У источника есть **ревизии (SourceRevision)**: каждая ревизия неизменяема и привязана к точному хешу содержимого. Точные байты хранятся в `_raw/` с адресацией по содержимому, поэтому одинаковые байты всегда дают одинаковый адрес (ADR-002, `src/raytsystem/ingestion.py`).

Поверх ревизии строится **нормализация (Normalization)** — извлечённый текст, разбитый на **сегменты (Segment)**. Снимок нормализации ключуется ревизией источника, версией адаптера и конфигурацией; старые снимки не переписываются.

## Claims, сущности, связи, ревью

- **Claim** — отдельное утверждение (факт), у которого есть статус (например, поддержан/подтверждён) и список ссылок на evidence. В ответах QUERY используются только активные подтверждённые claim'ы (`src/raytsystem/querying.py`).
- **Сущности (entities)** и **связи (relations)** образуют граф знаний поверх claim'ов.
- **Ревью (reviews)** фиксируют результаты семантической проверки. Важно: ревью сообщают о находках, но сами по себе не меняют каноническое знание (ADR-011).

## Evidence и цитаты

**Evidence** связывает claim с конкретным местом источника. Полная цепочка: сырьё (raw) → ревизия (revision) → нормализация (normalization) → сегмент (segment). Сегмент указывает на точный фрагмент, а его извлечение (excerpt) закреплено хешем.

Когда QUERY отвечает, каждый факт снабжается **цитатой (QueryCitation)**. Цитата хранит диапазон символов в ответе, ID claim'а и всю цепочку evidence вплоть до `source_revision_id`, `normalization_id`, `segment_id` и `cited_excerpt_sha256`. Ответ и все цитаты привязаны к одному поколению и его хешу; несоответствие приводит к отказу «fail closed» (`src/raytsystem/querying.py`). Так цитата остаётся проверяемой привязкой к точным байтам источника, а не пересказом.

## Ручные заметки

Человеку доступна для правки только зона `knowledge/manual/`. Заметки из неё не становятся знанием «в обход» конвейера — они возвращаются в базу через операцию INGEST на общих основаниях (см. [Что можно редактировать](/knowledge/editable-vs-immutable)).

## Поиск

Поиск по знаниям выполняется через полнотекстовый индекс FTS5. Он принимает ограниченную грамматику литеральных токенов, работает через параметризованный SQL и доступен всегда, без модели или API-ключа (ADR-011). Каждый найденный элемент несёт ID поколения и хеш канонического объекта — так поиск не может подсунуть факт из другого поколения. Запускается поиск командой QUERY (`uv run raytsystem query "..." --json`).

## Связь с Knowledge Universe

Всё перечисленное визуализируется в разделе **Вселенная** (Knowledge Universe). Линзы Orbit, Knowledge, Work, Agent и Evidence показывают знания и их происхождение с разных сторон, а линза «Код» — отдельный code-graph. Рендер использует Sigma/WebGL с доступным списочным запасным вариантом. Подробнее — [Раздел Вселенная](/interface/universe).

## Ограничения и безопасность

- Индексируемый текст источника всегда трактуется как инертные данные и экранируется перед отображением — он не является инструкцией (ADR-011).
- Evidence и raw неизменяемы; их нельзя редактировать вручную.
- Если подтверждённого claim нет, QUERY возвращает явный пробел, а не догадку.

## Связанные страницы

- [Модель данных базы знаний](/knowledge/overview)
- [Операции: INGEST, QUERY, LINT, SAVE](/knowledge/operations)
- [Раздел Вселенная](/interface/universe)
- [Запуски и наблюдаемость](/observability/runs)

## Источники истины

- `ops/decisions/ADR-002-immutable-raw-and-evidence.md`
- `ops/decisions/ADR-011-generation-bound-retrieval-and-draft-save.md`
- `src/raytsystem/ingestion.py`
- `src/raytsystem/querying.py`

---
title: "База знаний: модель данных"
description: "Как raytsystem превращает исходные материалы в проверенные знания: от неизменяемого сырья до канонического ledger и материализованных Markdown, FTS5 и графов."
audience: [user, developer]
status: stable
feature_flags: [code_graph_enabled, graph_first_query_enabled]
related_commands: ["uv run raytsystem query \"...\" --json", "uv run raytsystem lint --json", "uv run raytsystem rebuild-index --json"]
related_pages:
  - /knowledge/operations
  - /knowledge/sources-and-evidence
  - /knowledge/editable-vs-immutable
  - /interface/universe
  - /code-graph/overview
source_of_truth:
  - path: ops/decisions/ADR-001-local-first-canonical-state.md
  - path: ops/decisions/ADR-002-immutable-raw-and-evidence.md
  - path: ops/decisions/ADR-003-ledger-and-markdown-views.md
  - path: ops/decisions/ADR-011-generation-bound-retrieval-and-draft-save.md
  - path: src/raytsystem/ingestion.py
last_verified_against: "schema v1.4.0"
---

# База знаний: модель данных

## Что это

База знаний raytsystem — это способ хранить факты так, чтобы у каждого утверждения была прослеживаемая, неизменяемая опора на исходный материал. Знания не «пишутся» напрямую в готовые страницы. Они проходят конвейер, где каждый шаг фиксируется и его можно перепроверить.

Полный путь данных выглядит так:

1. **Raw evidence (сырьё)** — точные байты источника попадают в неизменяемый `_raw/` с адресацией по содержимому (content-addressed). Одни и те же байты дают один и тот же адрес.
2. **Normalization (нормализация)** — из сырья извлекается текст и разбивается на сегменты. Снимки нормализации привязаны к ревизии источника, версии адаптера и конфигурации; старые снимки никогда не переписываются (ADR-002).
3. **Proposal (предложение)** — из нормализованного текста формируется типизированное предложение с claim'ами и ссылками на evidence. Вывод модели — это всегда предложение, а не факт.
4. **Validation (валидация)** — предложение проверяется детерминированно: замыкание evidence, хеши, целостность.
5. **Promotion (промоушен)** — только после проверки предложение становится каноническим знанием. Для реального корпуса это действие по умолчанию запрещено и требует одобрения.
6. **Canonical ledger** — типизированные записи в `ledger/` плюс указатель `ledger/CURRENT` и неизменяемые поколения (generations) — это и есть каноническое состояние (ADR-001, ADR-003).
7. **Materialized views** — из ledger собираются производные представления: Markdown-страницы `knowledge/`, полнотекстовый индекс FTS5 и графы. Это удобные проекции, а не источник истины (ADR-011).

## Каноническое против производного

Ключевая идея: **каноническими** являются точное сырьё, типизированные записи ledger, манифесты поколений и указатель `ledger/CURRENT`. Всё остальное — SQLite, Markdown-страницы, графы JSON — **производное** и пересобирается из ledger одной командой (ADR-001). Удаление индекса или сгенерированной страницы не удаляет знания: их можно восстановить (см. [Восстановление индексов](/knowledge/recovery)).

## Неизменяемое происхождение

У каждого факта есть цепочка: сырой байт → ревизия источника → нормализация → сегмент. Каждое поколение получает свой ID, а каждый ответ QUERY привязан к конкретному поколению и хешам канонических объектов (ADR-011). Поэтому ответ можно воспроизвести и проверить, а не принять на веру.

## Knowledge graph и code graph — это разное

- **Knowledge graph** строится из знаний (claims, сущности, связи) и является одной из производных проекций ledger.
- **Code graph** описывает структуру самого исходного кода (модули, зависимости, влияние). Он живёт отдельно, включается флагом `code_graph_enabled` и доступен через отдельные команды `uv run raytsystem graph ...` и линзу «Код» в разделе Вселенная. Подробнее — [Обзор code graph](/code-graph/overview).

Не путайте их: knowledge graph отвечает на вопрос «что мы знаем», code graph — «как устроен код».

## Ограничения и безопасность

- Никогда не редактируйте вручную `_raw/`, объекты и поколения ledger, а также сгенерированные страницы `knowledge/` — прямые правки отклоняются (ADR-003).
- Хранилище растёт только добавлением (append-only); удаление и компакция требуют отдельной проверенной политики (ADR-002).
- Промоушен реального корпуса запрещён по умолчанию и открывается только через одобрение.

## Связанные страницы

- [Операции: INGEST, QUERY, LINT, SAVE](/knowledge/operations)
- [Источники, claims, evidence и цитаты](/knowledge/sources-and-evidence)
- [Что можно и что нельзя редактировать](/knowledge/editable-vs-immutable)
- [Knowledge Universe (интерфейс)](/interface/universe)

## Источники истины

- `ops/decisions/ADR-001-local-first-canonical-state.md`
- `ops/decisions/ADR-002-immutable-raw-and-evidence.md`
- `ops/decisions/ADR-003-ledger-and-markdown-views.md`
- `ops/decisions/ADR-011-generation-bound-retrieval-and-draft-save.md`
- `src/raytsystem/ingestion.py`

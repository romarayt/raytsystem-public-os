---
title: "Операции: INGEST, QUERY, LINT, SAVE"
description: "Четыре операции над знаниями raytsystem и вспомогательный rebuild-index: что делает каждая, меняет ли она каноническое состояние и как её вызвать."
audience: [user, operator]
status: stable
feature_flags: [graph_first_query_enabled]
related_commands:
  - "uv run raytsystem ingest <src>"
  - "uv run raytsystem query \"...\" --json"
  - "uv run raytsystem lint --json"
  - "uv run raytsystem save ..."
  - "uv run raytsystem rebuild-index --json"
related_pages:
  - /knowledge/overview
  - /knowledge/editable-vs-immutable
  - /knowledge/recovery
  - /security/approvals
  - /reference/cli
source_of_truth:
  - path: src/raytsystem/ingestion.py
  - path: src/raytsystem/querying.py
  - path: ops/decisions/ADR-011-generation-bound-retrieval-and-draft-save.md
  - path: skills/raytsystem-ingest/SKILL.md
  - path: skills/raytsystem-query/SKILL.md
last_verified_against: "schema v1.4.0"
---

# Операции: INGEST, QUERY, LINT, SAVE

## Что это

Над базой знаний определены четыре операции. Из них только **INGEST** может изменить каноническое состояние (и то лишь на этапе промоушена). **QUERY** и **LINT** доступны только на чтение. **SAVE** пишет во временную зону и никогда не публикует.

## INGEST — добавить знание

INGEST превращает исходный файл в проверенное знание через этапы `prepare` → `validate` → `promote`.

- `uv run raytsystem prepare SOURCE --json` — захватывает сырьё в `_raw/`, нормализует, формирует типизированное предложение и складывает его в staging. Каноническое состояние ещё не меняется.
- `uv run raytsystem validate RUN_ID --json` — детерминированно проверяет подготовленный run (хеши, замыкание evidence, целостность). Только чтение.
- `uv run raytsystem promote RUN_ID --json` — единственный шаг, который меняет канон. Для реального корпуса он по умолчанию запрещён: система переводит run в `awaiting_approval` и требует `ApprovalRecord`, привязанный к точному хешу кандидата (`src/raytsystem/ingestion.py`).

Одноимённая команда `uv run raytsystem ingest SOURCE --json` проходит весь путь сразу, но для реального материала так же упирается в одобрение. Повтор той же операции — это no-op: новое поколение и событие не создаются.

## QUERY — спросить у знаний

`uv run raytsystem query "ВОПРОС" --limit 10 --json` отвечает **только фактами из активного поколения** с проверенными цитатами. Ответ привязан к конкретному поколению; каждый факт ссылается на цепочку evidence (сырьё → ревизия → нормализация → сегмент). Если подходящего подтверждённого claim нет, QUERY возвращает явный пробел (gap), а не догадку (`src/raytsystem/querying.py`, `skills/raytsystem-query/SKILL.md`).

QUERY работает **только на чтение** канона. Ему разрешено пересобрать устаревший производный индекс из `ledger/CURRENT`, но он никогда не вызывает SAVE, промоушен или внешние инструменты. При спорной ситуации (гонка поколений) выполняется один повтор, затем отказ «fail closed».

## LINT — проверить целостность

`uv run raytsystem lint --json` — детерминированная проверка целостности только на чтение. Она проверяет каноническое состояние, evidence и raw, свежесть проекций, сгенерированные страницы, локальные ссылки, алиасы/ID, секреты и расхождение операций. Семантический режим только сообщает о находках и **никогда** не меняет каноническое знание (ADR-011).

## SAVE — типизированный черновик без публикации

`uv run raytsystem save ...` записывает хеш-замкнутый типизированный набор (EvidencePack / ProposalRequest / ProposalResponse / Artifact) в `ops/staging/` и экранированный DRAFT-предпросмотр в `artifacts/drafts/`. SAVE **не пишет** ledger, события, сгенерированные страницы, Git-ссылки или outbox и не производит внешних побочных эффектов (ADR-011). Это удобная граница для ревью и передачи, но SAVE не может ничего промоутить или опубликовать.

## rebuild-index — пересобрать производные представления

`uv run raytsystem rebuild-index --json` пересобирает производный пакет проекций (FTS5, граф, `knowledge/index.md`, `knowledge/hot.md` и сгенерированные страницы) из `ledger/CURRENT`. Это восстановление кэша, а не изменение знаний. Подробнее — [Восстановление индексов](/knowledge/recovery).

## Что меняет состояние

| Операция | Меняет канон? |
|---|---|
| INGEST (`promote`) | Да — только после валидации и, для реального корпуса, одобрения |
| QUERY | Нет (только чтение; может пересобрать индекс) |
| LINT | Нет (только чтение) |
| SAVE | Нет (пишет в staging/drafts) |
| rebuild-index | Нет (пересобирает производное) |

## Ограничения и безопасность

- Промоушен реального корпуса запрещён по умолчанию и требует одобрения — см. [Одобрения](/security/approvals).
- Никогда не редактируйте напрямую `_raw/`, `ledger/`, `ops/events/` или сгенерированные `knowledge/`.
- В примерах предпочитайте команды только на чтение (`query`, `lint`).

## Связанные страницы

- [Модель данных базы знаний](/knowledge/overview)
- [Что можно и что нельзя редактировать](/knowledge/editable-vs-immutable)
- [Восстановление индексов](/knowledge/recovery)
- [Справочник CLI](/reference/cli)

## Источники истины

- `src/raytsystem/ingestion.py`
- `src/raytsystem/querying.py`
- `ops/decisions/ADR-011-generation-bound-retrieval-and-draft-save.md`
- `skills/raytsystem-ingest/SKILL.md`
- `skills/raytsystem-query/SKILL.md`

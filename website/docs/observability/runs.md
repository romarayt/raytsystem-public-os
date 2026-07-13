---
title: "Запуски и execution records"
description: "Экран «Запуски» и записи наблюдаемости: санитизированные, редактированные записи о зафиксированных операциях и (за флагами) реальных прогонах."
audience: [operator]
status: stable
feature_flags: [telemetry_enabled, replay_enabled, runtime_execution_enabled]
related_commands:
  - "uv run raytsystem trace list --json"
  - "uv run raytsystem replay list --json"
  - "uv run raytsystem eval list --json"
related_pages:
  - /interface/runs
  - /observability/tracing
  - /observability/replay
  - /observability/evaluation
  - /security/defaults
source_of_truth:
  - path: docs/12-platform-capabilities.md
  - path: src/raytsystem/telemetry/service.py
  - path: src/raytsystem/replay/service.py
  - path: config/platform.yaml
  - path: ops/decisions/ADR-021-otel-trace-model.md
  - path: ops/decisions/ADR-022-replay-fork-compare.md
last_verified_against: "schema v1.4.0"
---

# Запуски и execution records

## Что это

Раздел «Запуски» в интерфейсе показывает записи наблюдаемости о том, что происходило внутри raytsystem: трейсы прогонов (`TraceRecord`/`TraceSpan`) и неизменяемые execution records (`ExecutionRecord`). Это фиксированные, доказательные записи — не живой поток команд. Все они хранятся в изолированном append-only хранилище `ops/platform.sqlite`; канонические данные (`_raw/`, `ledger/`, сгенерированный `knowledge/`) при этом никогда не пишутся (`docs/12-platform-capabilities.md`).

Важно понимать границу этой сборки: реальное выполнение прогонов (запуск цифрового сотрудника через рантайм-адаптер) выключено по умолчанию — `runtime_execution_enabled=false`, `codex_local_enabled=false`, `claude_local_enabled=false` в `config/raytsystem.toml`. Поэтому «Запуски» в первую очередь показывают зафиксированные записи и их наблюдаемость, а не результат живого вызова модели.

## Когда использовать

- Нужно посмотреть, какие операции уже зафиксированы, с какими токенами, стоимостью и статусом.
- Идёт разбор поведения: от записи прогона вы переходите к трейсам, replay и eval.
- Требуется подтвердить, что чувствительные данные не попали в наблюдаемость.

## Санитизация и redaction

Записи очищаются до попадания в хранилище, а не после. Для спанов действуют политики из `config/platform.yaml`: не более `max_span_attributes` (32) атрибутов и не более `max_span_attribute_bytes` (16384) байт на атрибут. Чувствительные ключи (prompt, input, output, arguments, secret, credential и т. п.) и любые срабатывания сканера секретов заменяются на хэш-дайджест, а поля provider/model/tool/error редактируются; статус записи помечается как `REDACTED` (`src/raytsystem/telemetry/service.py`). Списковые представления никогда не отдают сырые payload'ы. Execution records неизменяемы и привязаны к хэшу: повторная запись с другими байтами — это ошибка целостности, а не обновление (`src/raytsystem/replay/service.py`, `ops/decisions/ADR-022-replay-fork-compare.md`).

## Пошагово

1. Откройте раздел «Запуски» в интерфейсе (`/interface/runs`).
2. Для деталей трейса используйте `uv run raytsystem trace list --json`, затем `uv run raytsystem trace detail <trace_id>`.
3. Для разбора зафиксированных прогонов — `uv run raytsystem replay list --json`.
4. Для сопоставления с оценками качества — `uv run raytsystem eval list --json`.

## Ожидаемый результат

Список записей с идентификаторами, статусом, токенами и стоимостью; из него можно уйти в трейсинг, replay и eval. Если хранилище платформы недоступно, снимок честно вернёт состояние `unavailable`, а не выдумает данные.

## Ограничения и безопасность

- Живого выполнения прогонов в этой сборке нет: рантайм выключен и требует явного включения флага и одобрения.
- GET/HEAD никогда не пишет; интерфейс работает только по петле 127.0.0.1.
- Наблюдаемость — это read-модель; она не позволяет запускать команды или менять канонические данные.

## Связанные страницы

- [Интерфейс: Запуски](/interface/runs)
- [Трейсинг и spans](/observability/tracing)
- [Replay, fork, compare](/observability/replay)
- [Лаборатория оценки](/observability/evaluation)
- [Безопасные значения по умолчанию](/security/defaults)

## Источники истины

- `docs/12-platform-capabilities.md`
- `src/raytsystem/telemetry/service.py`
- `src/raytsystem/replay/service.py`
- `config/platform.yaml`
- `ops/decisions/ADR-021-otel-trace-model.md`
- `ops/decisions/ADR-022-replay-fork-compare.md`

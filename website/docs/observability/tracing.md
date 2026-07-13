---
title: "Трейсинг и spans"
description: "Локальная модель трейсов task→run→spans с редакцией до записи и ограничением атрибутов. OTLP-экспорт выключен по умолчанию и требует одобрения."
audience: [operator]
status: stable
feature_flags: [telemetry_enabled, otel_export_enabled]
related_commands:
  - "uv run raytsystem trace list --json"
  - "uv run raytsystem trace detail ..."
  - "uv run raytsystem trace export-fingerprint ..."
related_pages:
  - /observability/runs
  - /observability/replay
  - /security/approvals
  - /security/defaults
  - /reference/feature-flags
source_of_truth:
  - path: docs/12-platform-capabilities.md
  - path: src/raytsystem/telemetry/service.py
  - path: config/platform.yaml
  - path: ops/decisions/ADR-021-otel-trace-model.md
last_verified_against: "commit 3f2a123 / schema v1.4.0"
---

# Трейсинг и spans

## Что это

Трейсинг — это локальная, выровненная под OpenTelemetry модель наблюдаемости. Она включена по умолчанию (`telemetry_enabled=true` в `config/platform.yaml`). Трейс задачи разворачивается в дерево спанов: task-трейс → run-спан → спаны `model`, `graph-query`, `retrieval`, `tool`, `filesystem`, `approval`, `test`, `artifact` — с проверкой родителя и иерархии на каждой записи (`docs/12-platform-capabilities.md`, `src/raytsystem/telemetry/service.py`). Всё хранится в изолированном `ops/platform.sqlite`; канонические данные не пишутся.

## Когда использовать

- Нужно увидеть, из каких шагов состоял прогон, и раскрутить дерево спанов.
- Требуется учёт токенов и стоимости, агрегированный из самих спанов, а не со слов вызывающего.
- Идёт проверка, что промпты и выводы не утекли в хранилище наблюдаемости.

## Редакция и ограничения

Данные санитизируются до записи. Действуют политики из `config/platform.yaml`: не более `max_span_attributes` (32) атрибутов и не более `max_span_attribute_bytes` (16384) байт на атрибут — превышение отклоняется. Чувствительные ключи (prompt, input, output, arguments, environment, secret, credential, cookie, token) и любые срабатывания сканера секретов заменяются на SHA-256-дайджест, а поля provider/model/tool/error редактируются; статус спана помечается `REDACTED`. Идентификаторы спанов неизменяемы, а терминальные спаны нельзя переоткрыть или переписать. При закрытии трейса счётчики спанов, токены и стоимость агрегируются из хранимых спанов (`ops/decisions/ADR-021-otel-trace-model.md`).

## Пошагово

1. Список трейсов: `uv run raytsystem trace list --json`.
2. Детали конкретного трейса и его спанов: `uv run raytsystem trace detail <trace_id>`.
3. Перед экспортом получите идентичность, которую должно покрывать одобрение: `uv run raytsystem trace export-fingerprint` — она печатает `action`, `target_id`, `artifact_sha256` и требуемый scope `otel_export`.

## OTLP-экспорт: выключен по умолчанию

Экспорт в формате OTLP пишет один канонический JSON-документ только в локальный файл — сетевого экспортёра не существует. Он гейтится дважды: нужен `telemetry_enabled` плюс `otel_export_enabled` (по умолчанию **off**), и сверх того точное разрешённое одобрение (`export_traces`, scope `otel_export`), привязанное к хэшу всех экспортируемых трейсов/спанов и к пути назначения. Путь должен быть несуществующим и не symlink; отрендеренный документ ещё раз проверяется сканером секретов. То есть непрерывной отправки в коллектор в этой сборке нет: каждый экспорт — это осознанное включение флага и свежее хэш-привязанное одобрение.

## Ожидаемый результат

Список трейсов и деталь с отсортированными спанами; суммы токенов/стоимости выведены из спанов и не могут разойтись с ними. Снимки списков в выключенном состоянии честно возвращают `disabled`/`unavailable`.

## Ограничения и безопасность

- Сырые промпты и выводы в атрибутах спанов не хранятся — только дайджесты.
- `export-otlp` без включённого флага и без одобрения падает с ошибкой подсистемы.
- Наблюдаемость — read-модель по петле 127.0.0.1; GET/HEAD не пишет.

## Связанные страницы

- [Запуски и execution records](/observability/runs)
- [Replay, fork, compare](/observability/replay)
- [Одобрения](/security/approvals)
- [Безопасные значения по умолчанию](/security/defaults)
- [Справочник: фиче-флаги](/reference/feature-flags)

## Источники истины

- `docs/12-platform-capabilities.md`
- `src/raytsystem/telemetry/service.py`
- `config/platform.yaml`
- `ops/decisions/ADR-021-otel-trace-model.md`

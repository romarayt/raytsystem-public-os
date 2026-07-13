---
title: "Replay, fork, compare"
description: "Планирование поверх зафиксированных execution records: без повторного выполнения побочных эффектов, без переноса одобрений, со структурированными diff'ами и честным сравнением прогонов."
audience: [developer]
status: stable
feature_flags: [replay_enabled, runtime_execution_enabled]
related_commands:
  - "uv run raytsystem replay plan ..."
  - "uv run raytsystem replay fork ..."
  - "uv run raytsystem replay compare ..."
  - "uv run raytsystem replay list --json"
related_pages:
  - /observability/runs
  - /observability/tracing
  - /observability/evaluation
  - /security/approvals
  - /reference/feature-flags
source_of_truth:
  - path: docs/12-platform-capabilities.md
  - path: src/raytsystem/replay/service.py
  - path: config/platform.yaml
  - path: ops/decisions/ADR-022-replay-fork-compare.md
last_verified_against: "commit 3f2a123 / schema v1.4.0"
---

# Replay, fork, compare

## Что это

Replay позволяет переиграть прошлый прогон с закреплёнными входами для отладки, не повторяя побочные эффекты (send/publish/pay) и не наследуя устаревшие одобрения. Подсистема включена по умолчанию (`replay_enabled=true` в `config/platform.yaml`). Она планирует, стейджит и аудирует, но не исполняет: запуск материализованной записи отложен на плоскость рантайм-адаптера, а она выключена по умолчанию (`runtime_execution_enabled=false`). Всё хранится в изолированном `ops/platform.sqlite`; канонические данные не пишутся (`docs/12-platform-capabilities.md`, `ops/decisions/ADR-022-replay-fork-compare.md`).

## Когда использовать

- Нужно переиграть зафиксированный прогон с теми же входами для разбора поведения.
- Требуется fork: изменить ограниченный набор полей и увидеть, что поменялось.
- Нужно честно сравнить два прогона по токенам, стоимости, задержке, вызовам инструментов и eval-результатам.

## Как это безопасно

`ExecutionRecord` неизменяемы и привязаны к хэшу; повторная запись с другими байтами — ошибка целостности, а не обновление. Результаты побочных эффектов хранятся отдельными неизменяемыми записями; план replay может подставить только проверенные записанные результаты, а каждый исходный побочный эффект без записи попадает в список `blocked`. Одобрения никогда не переносятся: план несёт производные плейсхолдеры по позициям, поэтому исходные `approval_id` не могут попасть в план, а стейджинг помечает план `approval_required`, если исходный прогон держал одобрения (`src/raytsystem/replay/service.py`).

Fork ограничен разрешённым набором полей — `runtime_id`, `model`, хэши инструкций и навыков, `toolset_sha256`, бюджеты (`token_budget`/`cost_budget`), `graph_snapshot_id` — пересечённым с контрактом; изменённая запись перевалидируется. Различия фиксируются по каждому полю с исходным и новым значением. Целостность плана проверяется при каждом использовании: хэш плана, производный ID, ожидаемый blocked-набор, плейсхолдеры одобрений и привязки записанных результатов должны совпасть, иначе план отклоняется как попытка обойти побочные эффекты или свежие одобрения.

## Пошагово

1. Постройте план replay поверх зафиксированного прогона: `uv run raytsystem replay plan ...`.
2. Или форкните с ограниченными изменениями: `uv run raytsystem replay fork ...`.
3. Сравните два прогона: `uv run raytsystem replay compare ...`.
4. Посмотрите существующие планы: `uv run raytsystem replay list --json`.

## Ожидаемый результат

`RunComparison` заполняется по каждому измерению с явным происхождением: токены/стоимость/задержка — из трейсов (с откатом на бюджеты, где записано), изменения вызовов инструментов и сбоев — из спанов трейса, изменения файлов — из расширений записи, изменения eval-счётов/assertions/артефактов — из связанных eval-результатов. Недоступные измерения перечисляются в `unavailable_dimensions`; `test_changes` всегда явно недоступно, так как observation-уровневые результаты тестов не персистятся.

## Ограничения и безопасность

- Replay никогда не пере-отправляет, не пере-публикует и не пере-оплачивает: эффекты берутся из записанных доказательств или блокируются.
- Материализация возможна только из неизменяемого `ready`-плана без blocked-эффектов и без ожидающих одобрений.
- Любая точка входа fail-closed: при `replay_enabled=false` вызовы падают с ошибкой подсистемы.

## Связанные страницы

- [Запуски и execution records](/observability/runs)
- [Трейсинг и spans](/observability/tracing)
- [Лаборатория оценки](/observability/evaluation)
- [Одобрения](/security/approvals)
- [Справочник: фиче-флаги](/reference/feature-flags)

## Источники истины

- `docs/12-platform-capabilities.md`
- `src/raytsystem/replay/service.py`
- `config/platform.yaml`
- `ops/decisions/ADR-022-replay-fork-compare.md`

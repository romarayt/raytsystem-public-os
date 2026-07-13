---
title: "Лаборатория оценки (eval)"
description: "Детерминированная оценка поведения агентов: 17 типов assertions, неизменяемые baseline, сравнение и работа с регрессиями. LLM-судьи строго отделены и выключены."
audience: [operator, developer]
status: stable
feature_flags: [evals_enabled, promptfoo_adapter_enabled, promptfoo_remote_generation_enabled]
related_commands:
  - "uv run raytsystem eval self-test --json"
  - "uv run raytsystem eval list --json"
  - "uv run raytsystem eval baseline ..."
  - "uv run raytsystem eval compare ..."
  - "uv run raytsystem eval reject ..."
related_pages:
  - /observability/runs
  - /observability/replay
  - /security/approvals
  - /interface/safety
  - /reference/feature-flags
source_of_truth:
  - path: docs/12-platform-capabilities.md
  - path: src/raytsystem/evals/service.py
  - path: config/platform.yaml
  - path: ops/decisions/ADR-020-evaluation-laboratory.md
last_verified_against: "schema v1.4.0"
---

# Лаборатория оценки (eval)

## Что это

Лаборатория оценки — это детерминированный движок регрессионного тестирования поведения агентов. Он включён по умолчанию (`evals_enabled=true` в `config/platform.yaml`) и никогда не исполняет код цели: он лишь судит переданные ему доказательства (`EvalObservation`). Определение eval не может стать каналом исполнения кода или сетевого доступа — конфигурации, называющие код или провайдеров (`javascript`, `python`, `script`, `command`, `exec`, `shell`, `provider`, `remote`), отклоняются (`src/raytsystem/evals/service.py`, `ops/decisions/ADR-020-evaluation-laboratory.md`).

## Когда использовать

- Нужно зафиксировать эталон поведения и ловить регрессии между прогонами.
- Требуется доказуемая, воспроизводимая проверка без запуска модели «на живую».
- Идёт ревью безопасности: проверка на утечку секретов и на неизменность защищённых путей.

## 17 детерминированных assertions

Закрытый набор проверок: точное совпадение, вхождение подстроки, ограниченный (без катастрофического бэктрекинга) regex, подмножество JSON Schema, существование файла и хэш файла в пределах воркспейса, тип артефакта, результат теста, код выхода команды, наличие цитаты, наличие source-location, переход задачи, соответствие одобрению (approval compliance), отсутствие запрещённого действия, соблюдение бюджета, скан на утечку секрета и отсутствие модификации защищённых путей (`_raw/`, `ledger/`, `knowledge/`). Недетерминированный `EvalAssertion` сконструировать нельзя.

## LLM-судьи отделены и выключены

Оценки от LLM — это отдельный контракт `EvalJudge`, опциональный и выключенный по умолчанию; гейтит только детерминированный счёт. Адаптер Promptfoo работает исключительно в режиме валидации (`promptfoo_adapter_enabled=false`): он отклоняет исполняемые assertions и exec-подобных провайдеров безусловно. Удалённая генерация/шеринг/телеметрия требуют отдельного флага `promptfoo_remote_generation_enabled` (тоже off).

## Пошагово

1. Проверьте готовность движка: `uv run raytsystem eval self-test --json`.
2. Посмотрите существующие прогоны, baseline и сравнения: `uv run raytsystem eval list --json`.
3. Зафиксируйте baseline из прошедшего прогона: `uv run raytsystem eval baseline ...` — требуется явное точное одобрение (`accept_eval_baseline`, scope `eval_baseline`, привязанное к агрегатному хэшу результатов).
4. Сравните кандидата с baseline: `uv run raytsystem eval compare ...`.
5. При осознанной регрессии зафиксируйте её отклонение: `uv run raytsystem eval reject ...` — записывается высокоприоритетный `EvalFinding` с автором и причиной.

## Ожидаемый результат

Baseline неизменяемы и привязаны к хэшу; при сравнении заново проверяются идентичность baseline, агрегатный хэш и каждый хэш результата — подделанный или изменённый baseline не проходит верификацию. Регрессия порождает уведомление `eval_regression` во входящих.

## Ограничения и безопасность

- Все записи идут в изолированный `ops/platform.sqlite`; канонические данные не пишутся.
- Реальный запуск employee для получения observation отложен на плоскость исполнения (выключена по умолчанию).
- Любая точка входа fail-closed: при `evals_enabled=false` вызовы падают с ошибкой подсистемы.

## Частые ошибки

- Ожидать, что eval «сам прогонит» модель — нет, он судит только переданные доказательства.
- Пытаться создать baseline без одобрения — операция требует точного, привязанного к хэшу approval.

## Связанные страницы

- [Запуски и execution records](/observability/runs)
- [Replay, fork, compare](/observability/replay)
- [Одобрения](/security/approvals)
- [Интерфейс: Безопасность](/interface/safety)
- [Справочник: фиче-флаги](/reference/feature-flags)

## Источники истины

- `docs/12-platform-capabilities.md`
- `src/raytsystem/evals/service.py`
- `config/platform.yaml`
- `ops/decisions/ADR-020-evaluation-laboratory.md`

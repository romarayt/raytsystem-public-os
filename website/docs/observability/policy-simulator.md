---
title: "Симулятор политик"
description: "Dry-run проверка ExecutionPlan через тот же движок политики, что и рантайм-preflight: ничего не пишет, ничего не выдаёт, fail-closed. В интерфейсе — действие «Проверить запуск»."
audience: [operator]
status: stable
feature_flags: [policy_simulator_enabled, runtime_execution_enabled]
related_commands:
  - "uv run raytsystem policy simulate ..."
related_pages:
  - /security/approvals
  - /security/emergency-controls
  - /observability/runs
  - /interface/safety
  - /reference/feature-flags
source_of_truth:
  - path: docs/12-platform-capabilities.md
  - path: src/raytsystem/policy_simulator/service.py
  - path: config/platform.yaml
  - path: ops/decisions/ADR-023-policy-simulation.md
last_verified_against: "schema v1.4.0"
---

# Симулятор политик

## Что это

Симулятор политик отвечает на вопрос «что этому запуску будет разрешено сделать?» — до того как что-либо одобрено. Он включён по умолчанию (`policy_simulator_enabled=true` в `config/platform.yaml`). Ключевое свойство: у симулятора нет своей копии правил. `raytsystem policy simulate` прогоняет `ExecutionPlan` через тот же самый движок `evaluate_execution_policy`, что использует рантайм-preflight, — поэтому симуляция и реальное принуждение не могут разойтись (`src/raytsystem/policy_simulator/service.py`, `ops/decisions/ADR-023-policy-simulation.md`).

## Когда использовать

- Перед выдачей одобрения нужно понять, что план сможет и чего не сможет.
- Требуется проверить, что защищённые корни, воркспейс-режимы и side-effect'ы гейтятся правильно.
- Нужна безопасная для операторов и агентов проверка: только чтение, без записи и без выдачи прав.

## Как это работает

Каждый `ExecutionPlan` привязан к `policy_sha256` — хэшу `config/policies.yaml` плюс `config/platform.yaml`. План, собранный против устаревшей политики, отклоняется до оценки. Неразрешимые в dry-run факты fail-closed: чувствительность фиксируется в `RESTRICTED`, поэтому симуляция всегда предполагает самый широкий scope одобрения на provider-egress. Активные emergency-действия читаются из изолированного `ops/platform.sqlite`; если хранилище есть, но не читается, симуляция несёт `emergency_state_unavailable` и блокирует — оператор не увидит `allowed`, пока рантайм остановлен.

Движок оценивает защищённые корни (`_raw`, `ledger/*`, `knowledge/*`), режимы записи воркспейса и staging-only-границы, виды одобрений для сети/провайдера/секретов и для побочных эффектов (send/publish/upload/delete/pay/git_push/pull_request), allowlist read-only инструментов и гейты MCP. Итог эхом возвращает полные факты плана: сотрудник, задача, runtime, provider, model, режим и корни воркспейса, сеть, scope'ы, бюджеты, одобрения, хэш политики — плюс исход `allowed`, `approval_required` или `blocked`. Каждая симуляция — это `dry_run` и не выдаёт ничего.

## В интерфейсе: «Проверить запуск»

Веб-действие «Проверить запуск» вызывает тот же движок, что и CLI и рантайм-preflight. Это self-monitoring: оператор видит вердикт заранее, но никакого выполнения при этом не происходит — тем более что рантайм по умолчанию выключен (`runtime_execution_enabled=false`).

## Пошагово

1. Подготовьте файл плана и запустите: `uv run raytsystem policy simulate PLAN.json`.
2. Прочитайте исход (`allowed`/`approval_required`/`blocked`), недостающие одобрения и отсортированные reason codes.
3. При необходимости оформите нужное одобрение и повторите проверку.

## Ожидаемый результат

Симулированный `allowed` означает, что рантайм-preflight под теми же байтами политики тоже разрешил бы запуск — второго набора правил, который мог бы разойтись, не существует.

## Ограничения и безопасность

- Симулятор ничего не пишет, не создаёт воркспейс, не вызывает модель и не выдаёт секреты.
- Присутствующее, но нечитаемое хранилище платформы блокирует симуляцию.
- Рантайм-гейт `authorize_execution` использует тот же чистый движок и отказывается принимать «заявленные» вызывающим виды одобрений — только разрешённые записи одобрений удовлетворяют гейт.
- Fail-closed: при `policy_simulator_enabled=false` или недоступной конфигурации симуляция падает с ошибкой подсистемы.

## Связанные страницы

- [Одобрения](/security/approvals)
- [Аварийные средства управления](/security/emergency-controls)
- [Запуски и execution records](/observability/runs)
- [Интерфейс: Безопасность](/interface/safety)
- [Справочник: фиче-флаги](/reference/feature-flags)

## Источники истины

- `docs/12-platform-capabilities.md`
- `src/raytsystem/policy_simulator/service.py`
- `config/platform.yaml`
- `ops/decisions/ADR-023-policy-simulation.md`

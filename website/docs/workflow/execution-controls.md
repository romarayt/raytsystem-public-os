---
title: "Управление выполнением workflow"
description: "Как оператор управляет запусками workflow: pause/resume/cancel, артефакты, уведомления, бюджеты, аудит-трейл, ограничения выполнения и флаги. Без approval нет внешних эффектов."
audience: [operator]
status: experimental
feature_flags: [workflow_engine_enabled, runtime_execution_enabled, heartbeats_enabled, notifications_enabled, external_notifications_enabled, emergency_controls_enabled]
related_commands:
  - "uv run raytsystem workflow list --json"
  - "uv run raytsystem workflow approve ..."
  - "uv run raytsystem workflow cancel ..."
related_pages:
  - /workflow/overview
  - /workflow/node-types
  - /workflow/example
  - /security/approvals
  - /security/emergency-controls
  - /observability/runs
  - /reference/feature-flags
  - /troubleshooting/workflow-blocked
source_of_truth:
  - path: ops/decisions/ADR-029-workflow-dag.md
  - path: ops/decisions/ADR-017-digital-employee-execution-plane.md
  - path: src/raytsystem/contracts/workflows.py
  - path: docs/10-execution-security.md
  - path: docs/11-code-graph-and-execution-plane.md
  - path: config/raytsystem.toml
  - path: config/platform.yaml
last_verified_against: "commit 3f2a123 / schema v1.4.0"
---

# Управление выполнением workflow

## Что это

Раздел для оператора: как наблюдать за запусками workflow и управлять ими, не открывая поверхность произвольного выполнения. Каждый запуск (`WorkflowRun`) и каждый шаг (`WorkflowStepRun`) хранятся как версионированные записи с хеш-цепочкой событий в изолированном хранилище `ops/platform.sqlite` (`ops/decisions/ADR-029-workflow-dag.md`).

## Когда использовать

Когда нужно проверить статус зарегистрированных workflow, согласовать гейт, поставить запуск на паузу или отменить его. Помните: движок доступен (`workflow_engine_enabled = true`), но реальное выполнение рантайма выключено по умолчанию (`runtime_execution_enabled = false`, `config/raytsystem.toml`), поэтому в базовой поставке контур преимущественно описателен и ждёт человека.

## CLI

Группа `workflow` содержит три листовые команды. Начинайте с чтения:

```bash
uv run raytsystem workflow list --json
```

Согласование гейта и отмена запуска:

```bash
uv run raytsystem workflow approve ...
uv run raytsystem workflow cancel ...
```

Уведомления читаются и переводятся отдельной группой:

```bash
uv run raytsystem notifications list --json
```

Полный список листовых команд смотрите в [Справочнике CLI](/reference/cli). Веб-контур запусков доступен в маршруте «Запуски» ([Наблюдаемость: запуски](/observability/runs)).

## Pause / resume / cancel

Состояния запуска (`WorkflowRun.state`): `planned`, `running`, `paused`, `cancelled`, `succeeded`, `failed`. Состояния шага (`WorkflowStepRun.state`): `pending`, `running`, `waiting`, `paused`, `skipped`, `succeeded`, `failed`, `cancelled` (`src/raytsystem/contracts/workflows.py`).

Тот же ключ идемпотентности не может создать второй запуск. При падении `run_ready_steps` пересчитывает готовность из durable-записей и возобновляет ровно с последнего зафиксированного шага — это воспроизведение записей, а не эвристика согласования (`ADR-029`). Узлы `wait` продвигаются ручным сигналом `wake` с той же дисциплиной timeout.

## Артефакты и уведомления

Артефакты привязываются через `WorkflowArtifactBinding` (узел, имя выхода, тип, обязательность). Уведомления по гейтам публикуются через notification outbox: продюсеры публикуют смены состояния workflow с политикой-allowlist назначений. При этом `external_notifications_enabled` остаётся выключенным по умолчанию, поэтому наружу с машины ничего не уходит (`ADR-029`, `config/platform.yaml`). Записи outbox всегда помечены `redacted = True` (`src/raytsystem/contracts/workflows.py`).

## Бюджеты

Бюджеты могут привязываться к сотруднику, задаче, проекту, запуску или workspace и учитывать входные/выходные/кешированные токены, оценочную/фактическую стоимость, число запусков и heartbeats. Жёсткий лимит блокирует новый запуск; лимиты по токенам не зависят от приблизительной конвертации валюты (`docs/11-code-graph-and-execution-plane.md`).

## Аудит-трейл

Каждая запись и шаг хешируются с хеш-цепочкой событий; входы и выходы ограничены каноническим JSON и сканируются на секреты (`ADR-029`). Координация идёт через комментарии, события прогресса и дочерние задачи — скрытых межагентских сообщений в дизайне нет (`ADR-017`).

## Ограничения и безопасность

- **Без approval нет внешних эффектов.** Недетерминированные узлы не выполняются сами — они ждут оператора; единственные исполняемые тела — чистые функции движка (`ADR-029`).
- Перед стартом, шагом и approve движок проверяет `EmergencyService.assert_runtime_allowed()`, поэтому глобальный стоп-переключатель останавливает оркестрацию (`ADR-029`). См. [Аварийные средства](/security/emergency-controls).
- При `workflow_engine_enabled = false` любая операция fail-closed поднимает ошибку подсистемы workflow.
- Ни push, publish, deploy, send, delete, payment, доступ к внешнему корню, ни продвижение реального корпуса не выполняются без отдельного action-специфичного approval (`docs/10-execution-security.md`).

## Частые ошибки

- Ожидать, что `workflow list` покажет живое выполнение агентов — при выключенных runtime-флагах агентские узлы ждут. См. [Workflow заблокирован](/troubleshooting/workflow-blocked).
- Пытаться отправить уведомление наружу — `external_notifications_enabled = false`, allowlist назначений пуст.

## Связанные страницы

- [Обзор workflow](/workflow/overview)
- [Типы узлов](/workflow/node-types)
- [Согласования](/security/approvals)
- [Наблюдаемость: запуски](/observability/runs)

## Источники истины

- `ops/decisions/ADR-029-workflow-dag.md`
- `src/raytsystem/contracts/workflows.py`
- `docs/10-execution-security.md`
- `config/raytsystem.toml`, `config/platform.yaml`

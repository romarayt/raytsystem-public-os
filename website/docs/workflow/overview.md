---
title: "Workflow и координация работ: обзор"
description: "Контур координации raytsystem: цифровые сотрудники, назначения, задачи, managed workspaces, сессии, запуски и типизированный workflow DAG. Что включено и что за флагами."
audience: [operator, developer]
status: experimental
feature_flags: [workflow_engine_enabled, digital_employees_enabled, task_workspaces_enabled, heartbeats_enabled, runtime_execution_enabled]
related_commands:
  - "uv run raytsystem workflow list --json"
  - "uv run raytsystem task list --json"
  - "uv run raytsystem platform-status"
related_pages:
  - /workflow/node-types
  - /workflow/execution-controls
  - /workflow/example
  - /tasks/overview
  - /agents/overview
  - /security/approvals
  - /reference/feature-flags
source_of_truth:
  - path: ops/decisions/ADR-029-workflow-dag.md
  - path: ops/decisions/ADR-017-digital-employee-execution-plane.md
  - path: src/raytsystem/contracts/workflows.py
  - path: docs/11-code-graph-and-execution-plane.md
  - path: docs/10-execution-security.md
  - path: config/raytsystem.toml
  - path: config/platform.yaml
last_verified_against: "commit 3f2a123 / schema v1.4.0"
---

# Workflow и координация работ: обзор

## Что это

Контур координации raytsystem — это набор связанных механизмов, которые превращают инертные определения агентов в управляемую работу: цифровые сотрудники, назначения, задачи и managed workspaces, сессии, запуски и типизированный workflow DAG. Он решает класс задач, похожий на инструменты оркестрации вроде Paperclip (который рассматривался только как поведенческий ориентир), но описан в собственных терминах raytsystem и не импортирует чужую модель задач, наследование инструкций или телеметрию по умолчанию (`ops/decisions/ADR-017-digital-employee-execution-plane.md`).

Ключевой принцип: raytsystem остаётся единственным control plane, а `ops/task-ledger/` — единственным журналом задач. Назначения, workspaces, лизы, сессии, запуски, бюджеты, approvals, комментарии и транскрипты хранятся как связанные операционные записи в локальной control-базе и не встраиваются в неизменяемые объекты задач (`ADR-017`, `docs/11-code-graph-and-execution-plane.md`).

## Из чего состоит контур

- **Цифровые сотрудники** — типизированные записи `DigitalEmployee`, спроецированные из версионированных `AgentDefinition`. Активация в каталоге сама по себе не даёт прав на выполнение (`ADR-017`).
- **Задачи и managed workspaces** — задача остаётся в append-only Task Ledger; под неё создаётся отдельный worktree ниже `.raytsystem/workspaces/` с неизменяемым манифестом и ограниченным контекстом (`docs/11-code-graph-and-execution-plane.md`). Подробнее: [Задачи](/tasks/overview).
- **Сессии и запуски** — каждый запуск привязан к точной генерации/ревизии задачи, ревизии конфигурации сотрудника, адаптеру, манифесту workspace, снимку графа, хешу контекста, решению политики и ключу идемпотентности (`ADR-017`).
- **Workflow DAG** — движок регистрирует workflow как неизменяемые, привязанные к хешу манифеста ревизии типизированных узлов и рёбер. Валидация DAG выполняет топологическую сортировку (алгоритм Кана) и отклоняет дубли рёбер и циклы до сохранения (`ops/decisions/ADR-029-workflow-dag.md`). Десять типов узлов описаны в разделе [Типы узлов](/workflow/node-types).

## Статус: движок включён, выполнение — нет

Флаг `workflow_engine_enabled = true` (`config/platform.yaml`) означает, что движок доступен и может строить, валидировать и хранить workflow, вести запуски по записям. Но **реальное выполнение рантайма выключено по умолчанию**: `runtime_execution_enabled = false`, `codex_local_enabled = false`, `claude_local_enabled = false` (`config/raytsystem.toml`).

Из этого следует главное свойство базовой поставки: единственные исполняемые тела узлов — это чистые функции, поставляемые самим движком; всё остальное (агенты, внешние эффекты) ставится на паузу и ждёт человека или будущий управляемый рантайм (`ADR-029`, «Consequences»). Первый включённый триггер запусков — ручной heartbeat; запланированные heartbeats остаются выключены (`scheduled_heartbeats_enabled = false`).

## Ограничения и безопасность

- Движок fail-closed: при `workflow_engine_enabled = false` каждая операция поднимает ошибку подсистемы workflow (`ADR-029`).
- Перед стартом, шагом и approve проверяется `EmergencyService.assert_runtime_allowed()`, поэтому глобальный стоп-переключатель останавливает и оркестрацию (`ADR-029`). См. [Аварийные средства](/security/emergency-controls).
- Вывод рантайма считается недоверенной уликой: он не может писать в `_raw/`, `ledger/`, сгенерированные знания или продвигать контент (`ADR-017`, `docs/10-execution-security.md`).

## Связанные страницы

- [Типы узлов workflow](/workflow/node-types)
- [Управление выполнением workflow](/workflow/execution-controls)
- [Безопасный пример workflow](/workflow/example)
- [Задачи: обзор](/tasks/overview)
- [Флаги функций](/reference/feature-flags)

## Источники истины

- `ops/decisions/ADR-029-workflow-dag.md`
- `ops/decisions/ADR-017-digital-employee-execution-plane.md`
- `src/raytsystem/contracts/workflows.py`
- `docs/11-code-graph-and-execution-plane.md`
- `docs/10-execution-security.md`
- `config/raytsystem.toml`, `config/platform.yaml`

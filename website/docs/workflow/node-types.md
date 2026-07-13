---
title: "Типы узлов workflow"
description: "Десять типизированных узлов workflow DAG, их зависимости и conditions, узлы approval и review, retry и timeout, и почему deterministic_command запрещает raw shell."
audience: [developer]
status: experimental
feature_flags: [workflow_engine_enabled]
related_commands:
  - "uv run raytsystem workflow list --json"
related_pages:
  - /workflow/overview
  - /workflow/execution-controls
  - /workflow/example
  - /reference/workflow-nodes
  - /security/approvals
  - /reference/feature-flags
source_of_truth:
  - path: src/raytsystem/contracts/workflows.py
  - path: ops/decisions/ADR-029-workflow-dag.md
  - path: docs/10-execution-security.md
last_verified_against: "commit 3f2a123 / schema v1.4.0"
---

# Типы узлов workflow

## Что это

Workflow в raytsystem — это направленный ацикличный граф (DAG) из типизированных узлов и рёбер. Тип узла задаётся перечислением `WorkflowNodeType` в `src/raytsystem/contracts/workflows.py` и определяет, какая типизированная ссылка обязательна и как узел ведёт себя при запуске. Полный автогенерируемый список полей смотрите в справочнике [Типы узлов workflow](/reference/workflow-nodes) — он строится прямо из контрактов.

## Десять типов узлов

1. **task** — шаг, привязанный к задаче Task Ledger.
2. **agent** — вызов цифрового сотрудника; требует `agent_id`.
3. **deterministic_command** — детерминированная операция; требует `operation_id` (см. ниже).
4. **review** — точка человеческого ревью результата.
5. **approval** — гейт согласования; требует `approval_gate_id`.
6. **condition** — ветвление; требует `condition_id`.
7. **wait** — ожидание; продвигается ручным сигналом `wake` с той же дисциплиной timeout (`ADR-029`).
8. **artifact** — привязка выходного артефакта узла.
9. **notification** — уведомление через outbox.
10. **subworkflow** — вложенный workflow; требует `subworkflow_id`.

Инвариант узла (`WorkflowNode._node_invariants`) жёстко требует типизированную ссылку для `deterministic_command`, `agent`, `subworkflow`, `condition` и `approval`; без неё запись не создаётся (`src/raytsystem/contracts/workflows.py`).

## Зависимости и conditions

Рёбра (`WorkflowEdge`) связывают выход одного узла (`source_output`, по умолчанию `result`) со входом другого (`target_input`, по умолчанию `input`). Само-циклы запрещены инвариантом ребра. На уровне ревизии (`WorkflowRevision`) проверяется, что идентификаторы узлов непусты и уникальны, идентификаторы рёбер уникальны, а каждое ребро ссылается на известный узел. Перед сохранением ревизии движок дополнительно прогоняет топологическую сортировку (алгоритм Кана) и отклоняет дубли рёбер и циклы (`ops/decisions/ADR-029-workflow-dag.md`).

Узлы `condition` используют `WorkflowCondition` с операциями `equals`, `not_equals`, `contains`, `exists`, `all_passed`, `any_failed`. Ребро тоже может нести собственный `condition_id` для условного перехода (`src/raytsystem/contracts/workflows.py`).

## Узлы approval и review

Узлы `approval` привязаны к неизменяемым `WorkflowApprovalGate`. Выдача согласования требует разрешённого approval, связанного с производной целью «запуск/узел», хешем входа запуска и требуемой ролью гейта (`required_role`); гейт истекает через `expires_after_seconds`, а отказ проваливает весь запуск (`ADR-029`). Узлы `review` — это точки человеческой проверки; вместе с approval они образуют барьер, за которым не происходит внешних эффектов без явного одобрения. Подробнее: [Согласования](/security/approvals).

## Retry и timeout

- **Timeout.** У каждого узла есть `timeout_seconds` (по умолчанию 300, диапазон 1..86400). Ожидающие шаги истекают детерминированно (`ADR-029`).
- **Retry.** Политика `WorkflowRetryPolicy` задаёт `max_attempts` (1..20), задержки и `backoff` из `none`, `linear`, `exponential`, а также список `retryable_error_codes`. Записанная задержка и номер попытки фиксируются в durable-записях шага (`WorkflowStepRun.attempt`).

## Почему raw shell запрещён

Узел `deterministic_command` может ссылаться только на встроенные операции, зарегистрированные в движке. Callables, переданные в конструктор, запрещены, а идентификаторы операций с shell-метасимволами отклоняются (`ADR-029`). Это осознанно закрывает поверхность произвольного выполнения кода/shell, которую открыл бы движок n8n-типа с кодом в узлах (`ADR-029`, «Context»; `docs/10-execution-security.md`). Недетерминированные типы узлов не выполняются сами — они ставятся на паузу и ждут оператора.

## Ограничения и безопасность

- Единственные исполняемые тела узлов — чистые функции, поставляемые движком; всё остальное ждёт человека или будущий управляемый рантайм (`ADR-029`).
- Входы и выходы ограничены каноническим JSON и сканируются на секреты (`ADR-029`).
- Реальное выполнение агентских узлов через runtime-адаптеры намеренно отложено на отдельный проверяемый этап (`ADR-029`, «Consequences»).

## Связанные страницы

- [Обзор workflow](/workflow/overview)
- [Управление выполнением](/workflow/execution-controls)
- [Справочник: типы узлов](/reference/workflow-nodes)
- [Согласования](/security/approvals)

## Источники истины

- `src/raytsystem/contracts/workflows.py`
- `ops/decisions/ADR-029-workflow-dag.md`
- `docs/10-execution-security.md`

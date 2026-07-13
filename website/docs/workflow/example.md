---
title: "Безопасный пример workflow"
description: "Пример последовательности узлов от создания задачи до ревью: task → agent/deterministic_command → review → approval. Реальное выполнение требует включённых флагов и approvals."
audience: [operator, developer]
status: experimental
feature_flags: [workflow_engine_enabled, digital_employees_enabled, task_workspaces_enabled, runtime_execution_enabled]
related_commands:
  - "uv run raytsystem workflow list --json"
  - "uv run raytsystem task list --json"
related_pages:
  - /workflow/overview
  - /workflow/node-types
  - /workflow/execution-controls
  - /tasks/overview
  - /security/approvals
  - /reference/workflow-nodes
source_of_truth:
  - path: src/raytsystem/contracts/workflows.py
  - path: ops/decisions/ADR-029-workflow-dag.md
  - path: ops/decisions/ADR-017-digital-employee-execution-plane.md
  - path: docs/10-execution-security.md
  - path: config/raytsystem.toml
last_verified_against: "schema v1.4.0"
---

# Безопасный пример workflow

## Что это

Иллюстративная последовательность узлов workflow DAG, которая показывает безопасный по умолчанию контур: от задачи до человеческого ревью и согласования. Все имена типов узлов взяты из `src/raytsystem/contracts/workflows.py`; это не готовый исполняемый рецепт, а модель того, как узлы связываются и где контур ждёт человека.

## Когда использовать

Как ориентир при проектировании собственного workflow: где ставить `review`/`approval`, чем отличается детерминированный шаг от агентского и почему без включённых флагов и approvals реальных внешних эффектов не будет.

## Предварительные условия

- Движок доступен: `workflow_engine_enabled = true` (`config/platform.yaml`).
- Задача существует в Task Ledger. См. [Задачи: обзор](/tasks/overview).
- Помните про статус выполнения: `runtime_execution_enabled = false`, `codex_local_enabled = false`, `claude_local_enabled = false` (`config/raytsystem.toml`) — агентские узлы будут ждать.

## Пошагово: последовательность узлов

Рассмотрим линейный DAG из четырёх узлов, соединённых рёбрами `WorkflowEdge`:

1. **task** — стартовый узел, привязанный к задаче Task Ledger. Задаёт вход для остальных шагов.
2. **deterministic_command** или **agent** — рабочий шаг:
   - `deterministic_command` ссылается на `operation_id` встроенной операции движка. Raw shell запрещён, идентификаторы с shell-метасимволами отклоняются (`ADR-029`).
   - `agent` ссылается на `agent_id` цифрового сотрудника. Он не выполняется, пока не открыты runtime- и провайдерские флаги и нет approval на egress — иначе шаг ставится на паузу и ждёт (`ADR-017`, `docs/10-execution-security.md`).
3. **review** — точка человеческого ревью результата предыдущего шага.
4. **approval** — гейт `WorkflowApprovalGate`. Согласование требует разрешённого approval, связанного с целью «запуск/узел», хешем входа запуска и требуемой ролью (`required_role`); гейт истекает по `expires_after_seconds`, а отказ проваливает запуск (`ADR-029`).

Схематически:

```text
task → (agent | deterministic_command) → review → approval
```

Перед сохранением ревизии движок проверяет уникальность узлов и рёбер, ссылки рёбер на известные узлы и прогоняет топологическую сортировку (алгоритм Кана), отклоняя циклы (`src/raytsystem/contracts/workflows.py`, `ADR-029`).

## Пример: чтение состояния

Реальных команд для «запуска» произвольного workflow из UI нет; UI никогда не собирает и не выполняет shell. Оператор наблюдает и согласовывает через CLI:

```bash
uv run raytsystem workflow list --json
```

Управление согласованием и отменой описано в разделе [Управление выполнением](/workflow/execution-controls).

## Ожидаемый результат

- В базовой поставке узлы `task`, `review`, `approval` и `deterministic_command` (при наличии зарегистрированной операции) продвигаются как durable-записи; агентские и внешне-эффектные узлы ждут.
- Каждый шаг фиксируется как `WorkflowStepRun` с хеш-цепочкой; повторный ключ идемпотентности не создаёт дубль запуска (`ADR-029`).

## Ограничения и безопасность

- **Реальное выполнение требует включения флагов и approvals.** Без `runtime_execution_enabled`, соответствующего провайдерского флага и ALLOW-решения политики агентский шаг не запустится (`docs/10-execution-security.md`).
- Единственные исполняемые тела узлов — чистые функции движка; всё остальное ждёт человека или будущий управляемый рантайм (`ADR-029`).
- Вывод рантайма — недоверенная улика: он не пишет `_raw/`, `ledger/`, сгенерированные знания и не продвигает контент (`ADR-017`).

## Частые ошибки

- Ожидать, что `agent`-узел «просто выполнится» — при выключенных runtime-флагах он на паузе.
- Забыть узел `approval` перед шагом с внешним эффектом — без него внешних эффектов не будет вовсе, что и является безопасным поведением по умолчанию.

## Связанные страницы

- [Обзор workflow](/workflow/overview)
- [Типы узлов](/workflow/node-types)
- [Управление выполнением](/workflow/execution-controls)
- [Согласования](/security/approvals)

## Источники истины

- `src/raytsystem/contracts/workflows.py`
- `ops/decisions/ADR-029-workflow-dag.md`
- `ops/decisions/ADR-017-digital-employee-execution-plane.md`
- `docs/10-execution-security.md`
- `config/raytsystem.toml`

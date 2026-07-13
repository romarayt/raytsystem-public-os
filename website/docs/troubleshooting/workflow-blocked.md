---
title: "Workflow заблокирован"
description: "Почему процесс (workflow) не продвигается и как это безопасно диагностировать."
audience: [operator]
status: experimental
feature_flags: [workflow_engine_enabled, runtime_execution_enabled]
related_commands:
  - "uv run raytsystem workflow list"
  - "uv run raytsystem workflow approve"
  - "uv run raytsystem workflow cancel"
related_pages:
  - /workflow/execution-controls
  - /workflow/overview
  - /workflow/node-types
  - /security/approvals
source_of_truth:
  - path: src/raytsystem/contracts/workflows.py
  - path: config/platform.yaml
  - path: config/raytsystem.toml
last_verified_against: "commit 3f2a123 / schema v1.4.0"
---

# Workflow заблокирован

## Что это

Движок процессов (`workflow_engine_enabled: true` в `config/platform.yaml`) исполняет
типизированный DAG из узлов. Иногда процесс «застревает»: активный узел не переходит
дальше, а следующие узлы не запускаются. Почти всегда это не сбой, а сработавшая
граница безопасности — узел ждёт человека или ресурс, который сознательно выключен.

## Когда использовать

Когда `workflow list` показывает процесс в состоянии «в работе», но прогресса нет
дольше ожидаемого.

## Пошагово: симптом → причина → диагностика → решение

### 1. Узел ждёт `approval` или `review`

Среди десяти типов узлов есть `review` и `approval`
(`src/raytsystem/contracts/workflows.py`). Такой узел не проходит сам — ему нужно явное
человеческое решение.

- Диагностика: `uv run raytsystem workflow list` — найдите процесс и текущий узел.
- Решение: выдайте разрешение через `uv run raytsystem workflow approve` (approval
  должен быть точным, не истёкшим и связанным с payload — см.
  [/security/approvals](/security/approvals)). Если решение отрицательное —
  `uv run raytsystem workflow cancel`.

### 2. Узел требует runtime-исполнения, которое выключено

Узлы `agent` и `task` могут требовать запуска провайдера. В базовой поставке
`runtime_execution_enabled = false` (`config/raytsystem.toml`), поэтому такой узел не
стартует и процесс останавливается. Это ожидаемое состояние, а не поломка. Подробнее —
[/troubleshooting/provider-unavailable](/troubleshooting/provider-unavailable).

### 3. Узел `deterministic_command` ссылается на незарегистрированную операцию

`deterministic_command` исполняет только зарегистрированные operation ID; произвольный
shell запрещён (`src/raytsystem/contracts/workflows.py`). Если операция не зарегистрирована,
узел не выполнится. Проверьте определение процесса.

### 4. Исчерпан retry, тайм-аут или сработал автомат защиты

Платформенные автоматы защиты (`config/platform.yaml`) латчат при накоплении ошибок:
`retry_loop: 20`, `failed_approvals: 5`, а также предел длительности
`max_run_duration_seconds: 7200`. После срабатывания продвижение останавливается до
разбора причины.

- Диагностика: изучите [/workflow/execution-controls](/workflow/execution-controls) и
  историю узла в `workflow list`.

## Ожидаемый результат

После выдачи корректного approval, включения нужного ресурса под одобрением или отмены
процесс либо продолжается, либо завершается контролируемо.

## Ограничения и безопасность

- Движок процессов помечен как экспериментальный: полагайтесь на явные состояния
  из `workflow list`, а не на догадки.
- `GET`/`HEAD` ничего не пишет; продвижение процесса всегда идёт через типизированные,
  защищённые CSRF/сессией мутации.
- `deterministic_command` никогда не выполняет сырой shell.

## Когда открыть issue

Если узел находится в допустимом состоянии, все approval выданы и корректны, нужный
флаг включён под одобрением, но узел всё равно не продвигается — это баг. Приложите
идентификатор процесса и узла из `workflow list` (без секретов).

## Связанные страницы

- [/workflow/overview](/workflow/overview)
- [/workflow/node-types](/workflow/node-types)
- [/workflow/execution-controls](/workflow/execution-controls)
- [/security/approvals](/security/approvals)

## Источники истины

- `src/raytsystem/contracts/workflows.py`
- `config/platform.yaml`
- `config/raytsystem.toml`

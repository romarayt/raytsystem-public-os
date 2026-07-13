---
title: "Жизненный цикл задачи"
description: "Состояния задачи, допустимые переходы, зависимости, блокировки и конфликт поколений в операционном журнале raytsystem."
audience: [user, operator]
status: stable
feature_flags: []
related_commands:
  - "uv run raytsystem task transition <task_id> <target> --idempotency-key <key> --expected-generation <id>"
  - "uv run raytsystem task list --json"
related_pages:
  - /tasks/overview
  - /tasks/ui-and-cli
  - /tasks/examples
  - /troubleshooting/task-not-transitioning
  - /troubleshooting/generation-conflict
source_of_truth:
  - path: src/raytsystem/tasking.py
  - path: ops/decisions/ADR-015-local-web-control-plane-and-task-ledger.md
  - path: web/src/features/Tasks.tsx
last_verified_against: "commit 3f2a123 / schema v1.4.0"
---

# Жизненный цикл задачи

## Что это

У каждой задачи есть ровно одно состояние. Переходы описаны детерминированным конечным автоматом в `src/raytsystem/tasking.py` — незаявленные переходы отклоняются. Так история задачи остаётся предсказуемой и проверяемой.

## Состояния

- **inbox** — «Входящие»: только что созданная задача.
- **planned** — «Запланировано».
- **ready** — «Готово» (готова к работе).
- **running** — «В работе».
- **review** — «На проверке».
- **blocked** — «Заблокировано».
- **done** — «Завершено» (финальное состояние).
- **cancelled** — «Отменено» (финальное состояние).

Новая задача всегда создаётся в состоянии `inbox`.

## Допустимые переходы

Таблица переходов из `_TRANSITIONS` в `src/raytsystem/tasking.py`:

- **inbox** → planned, cancelled
- **planned** → ready, blocked, cancelled
- **ready** → running, blocked, cancelled
- **running** → review, blocked, cancelled
- **review** → done, running, blocked, cancelled
- **blocked** → planned, ready, running, cancelled
- **done** → (нет переходов, финальное)
- **cancelled** → (нет переходов, финальное)

Обратите внимание: из `review` можно вернуть задачу в `running` (доработка), а из `blocked` — снять блокировку в planned/ready/running. `done` и `cancelled` — тупиковые состояния; их нельзя переоткрыть, историю можно только читать.

## Зависимости

При создании задачи все её `dependency_ids` должны уже существовать на доске, иначе команда отклоняется. При переходе в `ready` или `running` все зависимости обязаны быть в состоянии `done` — иначе raytsystem отклоняет переход как «зависимости не завершены». Граф зависимостей проверяется на отсутствие циклов.

## Блокировки

Переход в `blocked` требует явную причину (`blocked_reason`). Причина сохраняется в задаче; при выходе из `blocked` она очищается. История блокировки остаётся неизменяемой.

## Правила времени и ревизий

Каждый переход увеличивает `revision` задачи на единицу и обновляет `updated_at`. Метка времени не может «идти назад»: переход с временем раньше текущего `updated_at` отклоняется. Переход меняет только контролируемые поля (состояние, причину блокировки, ревизию, время) — остальные поля задачи неизменяемы.

## Конфликт поколений

Каждый переход требует `expected_generation_id`. Если доска изменилась между чтением и записью (другой переход, другая сессия), команда падает с конфликтом поколений (`TaskConflict`). Это защита от гонок: перечитайте доску командой `task list` и повторите переход с актуальным поколением. Идемпотентный повтор той же команды с тем же ключом безопасен — он вернёт прежний результат.

## Пример

Перевести задачу в работу (сначала прочитайте актуальное поколение через `task list`):

```bash
uv run raytsystem task transition task_XXXX running \
  --idempotency-key move-running-01 \
  --expected-generation tgen_YYYY
```

## Частые ошибки

- **«Illegal task transition»** — целевое состояние не разрешено из текущего. Сверьтесь с таблицей выше.
- **«Task dependencies are not complete»** — вы переводите в ready/running, но не все зависимости `done`.
- **Конфликт поколений** — доска изменилась; перечитайте её и повторите. См. [конфликт поколений](/troubleshooting/generation-conflict).
- **Задача «застряла»** — см. [задача не переходит](/troubleshooting/task-not-transitioning).

## Связанные страницы

- [Задачи: обзор](/tasks/overview)
- [Задачи через UI и CLI](/tasks/ui-and-cli)
- [Типовые рабочие процессы](/tasks/examples)

## Источники истины

- `src/raytsystem/tasking.py`
- `ops/decisions/ADR-015-local-web-control-plane-and-task-ledger.md`
- `web/src/features/Tasks.tsx`

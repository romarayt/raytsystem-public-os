---
title: "Задачи через UI и CLI"
description: "Как выполнять одни и те же операции с задачами в интерфейсе (раздел «Задачи») и в командной строке raytsystem."
audience: [user, operator]
status: stable
feature_flags: []
related_commands:
  - "uv run raytsystem task list --json"
  - "uv run raytsystem task create <title> --idempotency-key <key>"
  - "uv run raytsystem task transition <task_id> <target> --idempotency-key <key> --expected-generation <id>"
related_pages:
  - /tasks/overview
  - /tasks/lifecycle
  - /interface/tasks
  - /reference/cli
  - /troubleshooting/generation-conflict
source_of_truth:
  - path: web/src/features/Tasks.tsx
  - path: src/raytsystem/cli.py
  - path: src/raytsystem/tasking.py
last_verified_against: "commit 3f2a123 / schema v1.4.0"
---

# Задачи через UI и CLI

## Что это

Задачи можно вести двумя способами: в разделе **«Задачи»** веб-интерфейса и через группу команд `task` в CLI. Оба пути работают с одним и тем же операционным журналом (`ops/task-ledger/`) и подчиняются одному конечному автомату состояний. Разница только в подаче: доска в браузере или команды в терминале.

## Что меняет состояние

- **Чтение** ничего не меняет: `task list` и открытие доски в UI — это операции только для чтения (GET/HEAD никогда не пишут).
- **Запись** — это создание задачи и смена состояния. Обе операции добавляют событие и новое поколение доски.

## Раздел «Задачи» в интерфейсе

Раздел открывается по маршруту `tasks` («Задачи»). Что доступно (`web/src/features/Tasks.tsx`):

- **Доска (Kanban)** с колонками по состояниям: Входящие, Запланировано, Готово, В работе, На проверке, Заблокировано, Завершено, Отменено.
- **Фильтр** по названию, описанию и тегам.
- **Бейдж поколения** — показывает текущее поколение доски.
- **«Новая задача»** — модальное окно с полями: название, описание (необязательно), приоритет (низкий/обычный/высокий/срочный). В окне явно указано: изменится только состояние задач, канонические знания останутся нетронутыми.
- **Кнопка перехода** на карточке двигает задачу по «прямому» маршруту: inbox → planned → ready → running → review → done, а из blocked → planned.
- **Меню действий** на карточке: «Заблокировать…» (с обязательной причиной), «Вернуть в работу» (из review), «Вернуть в готовые/в работу» (из blocked) и «Отменить…».

Интерфейс сам подставляет ожидаемое поколение и генерирует ключ идемпотентности для каждой команды — вручную их вводить не нужно.

## Те же операции в CLI

Прочитать доску:

```bash
uv run raytsystem task list --json
```

Создать задачу (ключ идемпотентности обязателен):

```bash
uv run raytsystem task create "Заголовок задачи" \
  --idempotency-key create-01 \
  --description "Контекст и критерии готовности" \
  --priority normal
```

Доступные опции `task create`: `--idempotency-key` (обязательна), `--description`, `--priority` (low|normal|high|urgent), `--expected-generation`, `--json`.

Сменить состояние (нужны и ключ идемпотентности, и ожидаемое поколение):

```bash
uv run raytsystem task transition task_XXXX blocked \
  --idempotency-key block-01 \
  --expected-generation tgen_YYYY \
  --blocked-reason "Ждём внешнего ревью"
```

Опции `task transition`: `target` (аргумент — целевое состояние), `--idempotency-key`, `--expected-generation` (обязательны), `--blocked-reason`, `--json`.

## Соответствие действий

| Действие | UI | CLI |
|---|---|---|
| Посмотреть доску | Открыть раздел «Задачи» | `task list` |
| Создать задачу | Кнопка «Новая задача» | `task create` |
| Двинуть по этапам | Кнопка перехода на карточке | `task transition` |
| Заблокировать | «Заблокировать…» + причина | `task transition ... blocked --blocked-reason` |
| Отменить | «Отменить…» | `task transition ... cancelled` |

## Ограничения и безопасность

- CLI-команды пишут от актора `user:local-cli`; в UI действия идут от локальной сессии.
- В CLI вы сами отвечаете за уникальность `--idempotency-key` и за актуальность `--expected-generation`; при устаревшем поколении команда падает с конфликтом ([что делать](/troubleshooting/generation-conflict)).
- UI никогда не передаёт путь, `cwd` или команду; всё выражено типизированными идентификаторами.

## Связанные страницы

- [Задачи: обзор](/tasks/overview)
- [Жизненный цикл задачи](/tasks/lifecycle)
- [Справочник CLI](/reference/cli)

## Источники истины

- `web/src/features/Tasks.tsx`
- `src/raytsystem/cli.py`
- `src/raytsystem/tasking.py`

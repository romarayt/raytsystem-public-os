---
title: "Типовые рабочие процессы задач"
description: "Практические сценарии работы с задачами raytsystem: простой поток inbox→done, поток с зависимостями и поток с проверкой."
audience: [user, operator]
status: stable
feature_flags: []
related_commands:
  - "uv run raytsystem task create <title> --idempotency-key <key>"
  - "uv run raytsystem task transition <task_id> <target> --idempotency-key <key> --expected-generation <id>"
  - "uv run raytsystem task list --json"
related_pages:
  - /tasks/overview
  - /tasks/lifecycle
  - /tasks/ui-and-cli
  - /interface/tasks
  - /troubleshooting/task-not-transitioning
source_of_truth:
  - path: web/src/features/Tasks.tsx
  - path: src/raytsystem/tasking.py
  - path: src/raytsystem/cli.py
last_verified_against: "schema v1.4.0"
---

# Типовые рабочие процессы задач

Ниже — три сценария. Каждый шаг описан как действие и его результат. Состояния и переходы соответствуют конечному автомату из `src/raytsystem/tasking.py`.

## Пример 1. Простой поток inbox → done через UI

Подходит для отдельной задачи без зависимостей.

1. **Действие.** В разделе «Задачи» нажмите «Новая задача», введите название и приоритет, подтвердите.
   **Результат.** Задача появляется в колонке «Входящие» (`inbox`), доска получает новое поколение.
2. **Действие.** На карточке нажимайте кнопку перехода — она ведёт по прямому маршруту.
   **Результат.** Задача последовательно проходит «Запланировано» → «Готово» → «В работе» → «На проверке».
3. **Действие.** Из «На проверке» нажмите переход ещё раз.
   **Результат.** Задача переходит в «Завершено» (`done`) — финальное состояние. Её неизменяемая история остаётся доступной.

Интерфейс сам подставляет ожидаемое поколение и ключ идемпотентности на каждом шаге.

## Пример 2. Поток с зависимостями

Задача B должна начаться только после завершения задачи A.

1. **Действие.** Создайте задачу A и доведите её до конца.
   **Результат.** A в состоянии `done`.
2. **Действие.** Создайте задачу B, указав A в зависимостях. Важно: зависимость должна уже существовать на доске, иначе создание отклоняется.
   **Результат.** B создана в `inbox`.
3. **Действие.** Попробуйте перевести B в `ready` или `running`, пока A ещё не `done`.
   **Результат.** Переход отклоняется: «Task dependencies are not complete». Дождитесь завершения A.
4. **Действие.** Когда A в `done`, переведите B в `ready`, затем в `running`.
   **Результат.** Переходы проходят, потому что все зависимости завершены.

Правило проверяется и при создании, и при переходе в ready/running (см. [жизненный цикл](/tasks/lifecycle)).

## Пример 3. Поток с проверкой (review) через CLI

Показывает возврат на доработку и завершение.

1. **Действие.** Создайте задачу и доведите до состояния «в работе».

   ```bash
   uv run raytsystem task create "Обновить раздел документации" --idempotency-key doc-01
   uv run raytsystem task list --json   # прочитать task_id и tgen поколения
   uv run raytsystem task transition task_XXXX planned --idempotency-key doc-02 --expected-generation tgen_A
   uv run raytsystem task transition task_XXXX ready   --idempotency-key doc-03 --expected-generation tgen_B
   uv run raytsystem task transition task_XXXX running --idempotency-key doc-04 --expected-generation tgen_C
   ```

   **Результат.** После каждого перехода печатается новое поколение (`tgen_...`) — используйте его в следующей команде.
2. **Действие.** Отправьте задачу на проверку.

   ```bash
   uv run raytsystem task transition task_XXXX review --idempotency-key doc-05 --expected-generation tgen_D
   ```

   **Результат.** Задача в «На проверке».
3. **Действие (доработка).** Если нужны правки, верните из review в running, затем снова в review.
   **Результат.** Разрешённый переход `review → running` даёт цикл доработки; история каждого шага неизменяема.
4. **Действие.** Когда всё готово, завершите.

   ```bash
   uv run raytsystem task transition task_XXXX done --idempotency-key doc-06 --expected-generation tgen_E
   ```

   **Результат.** Задача в `done`.

## Частые ошибки

- **Конфликт поколений.** Забыли обновить `--expected-generation` после предыдущего перехода — перечитайте `task list` и повторите ([подробнее](/troubleshooting/generation-conflict)).
- **Переход отклонён.** Целевое состояние не разрешено из текущего или зависимости не завершены ([диагностика](/troubleshooting/task-not-transitioning)).
- **Повтор команды.** Тот же `--idempotency-key` для того же действия безопасен (вернётся прежний результат как `no_op`); тот же ключ для другого действия будет отклонён.

## Связанные страницы

- [Задачи: обзор](/tasks/overview)
- [Жизненный цикл задачи](/tasks/lifecycle)
- [Задачи через UI и CLI](/tasks/ui-and-cli)

## Источники истины

- `web/src/features/Tasks.tsx`
- `src/raytsystem/tasking.py`
- `src/raytsystem/cli.py`

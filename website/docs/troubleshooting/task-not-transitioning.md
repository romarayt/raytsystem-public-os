---
title: "Задача не меняет статус"
description: "Почему переход задачи отклонён и как это безопасно диагностировать через task list."
audience: [user, operator]
status: stable
feature_flags: []
related_commands:
  - "uv run raytsystem task list --json"
  - "uv run raytsystem task transition"
related_pages:
  - /troubleshooting/overview
  - /troubleshooting/generation-conflict
  - /tasks/lifecycle
  - /tasks/ui-and-cli
source_of_truth:
  - path: src/raytsystem/tasking.py
  - path: src/raytsystem/cli.py
last_verified_against: "schema v1.4.0"
---

# Задача не меняет статус

## Что это

Переходы задач в raytsystem проходят через детерминированную машину состояний в отдельном неизменяемом реестре задач. Разрешён только заранее описанный набор переходов, а часть переходов дополнительно требует, чтобы были выполнены зависимости. Если правило нарушено, команда отклоняет переход и ничего не пишет.

## Симптом

`uv run raytsystem task transition ...` завершается ошибкой, статус задачи не меняется.

## Вероятная причина

Наиболее частые причины отказа:

1. **Недопустимый переход.** Разрешены только эти маршруты:
   - `INBOX → PLANNED, CANCELLED`
   - `PLANNED → READY, BLOCKED, CANCELLED`
   - `READY → RUNNING, BLOCKED, CANCELLED`
   - `RUNNING → REVIEW, BLOCKED, CANCELLED`
   - `REVIEW → DONE, RUNNING, BLOCKED, CANCELLED`
   - `BLOCKED → PLANNED, READY, RUNNING, CANCELLED`
   - `DONE` и `CANCELLED` — терминальные, из них переходов нет.

   Любой другой переход отклоняется как `Illegal task transition`.

2. **Незакрытые зависимости.** Перевод в `READY` или `RUNNING` требует, чтобы все зависимости задачи были в статусе `DONE`. Иначе — `Task dependencies are not complete`.

3. **Конфликт поколения.** Переход требует точного `--expected-generation`. Если доска задач изменилась и указанное поколение больше не текущее — `Task board generation changed`. См. [Конфликт поколения](/troubleshooting/generation-conflict).

4. **Повторно использованный ключ идемпотентности.** Если тот же `--idempotency-key` уже применялся для другой команды, операция отклоняется как конфликт.

5. **Метка времени назад.** Переход не может иметь метку времени раньше текущего `updated_at` задачи.

## Безопасная диагностика

Прочитайте текущую доску задач без изменения состояния:

```bash
uv run raytsystem task list --json
```

В ответе есть `generation_id` (актуальное поколение для `--expected-generation`) и список `tasks` со статусами, зависимостями (`dependency_ids`) и `revision`. Сверьте:

- допустим ли переход из текущего `status` в целевой по таблице выше;
- все ли зависимости задачи в статусе `DONE`;
- совпадает ли ваш `--expected-generation` с текущим `generation_id`.

## Решение

- Выберите допустимый целевой статус согласно таблице переходов.
- Если мешают зависимости — сначала доведите их до `DONE`, затем повторите.
- Если поколение устарело — перечитайте `task list`, возьмите свежий `generation_id` и повторите с ним.
- Для перевода в `BLOCKED` можно указать причину через `--blocked-reason`.

## Пример

```bash
# Посмотреть текущее поколение и статусы
uv run raytsystem task list --json

# Легальный переход READY -> RUNNING с актуальным поколением
uv run raytsystem task transition task_XXXX running \
  --idempotency-key move-1 \
  --expected-generation tgen_XXXX
```

## Ожидаемый результат

Команда возвращает результат перехода с новым `generation_id` и `revision`, увеличенным на единицу.

## Ограничения и безопасность

- Реестр задач отдельный и неизменяемый; он не касается канонических знаний.
- Идемпотентность привязана к актору и ключу: повтор той же команды возвращает прежний результат как no-op, а не создаёт дубликат.
- Полезная нагрузка задачи проходит гейт чувствительности; секреты/PII в задачах не допускаются.

## Частые ошибки

- Попытка «перепрыгнуть» статус (например, сразу `PLANNED → DONE`) — такой маршрут не разрешён.
- Перевод в `READY`/`RUNNING` при незавершённых зависимостях.
- Устаревший `--expected-generation`.

## Когда открыть issue

Если переход допустим по таблице, зависимости закрыты и поколение актуально, но команда всё равно отклоняет переход с сообщением о нарушении целостности реестра, приложите к issue вывод `task list --json` и точный текст ошибки.

## Связанные страницы

- [Жизненный цикл задач](/tasks/lifecycle)
- [Задачи в UI и CLI](/tasks/ui-and-cli)
- [Конфликт поколения](/troubleshooting/generation-conflict)

## Источники истины

- `src/raytsystem/tasking.py`
- `src/raytsystem/cli.py`

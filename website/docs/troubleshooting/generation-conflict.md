---
title: "Конфликт поколения"
description: "Ошибка о несовпадении generation/snapshot — что это, почему безопасно и как повторить операцию."
audience: [operator, developer]
status: stable
feature_flags: []
related_commands:
  - "uv run raytsystem task list --json"
  - "uv run raytsystem task transition"
  - "uv run raytsystem graph status --json"
related_pages:
  - /troubleshooting/overview
  - /troubleshooting/task-not-transitioning
  - /tasks/lifecycle
  - /code-graph/freshness-update-rebuild
  - /troubleshooting/skill-edit-conflict
source_of_truth:
  - path: src/raytsystem/tasking.py
  - path: src/raytsystem/codegraph/projection.py
last_verified_against: "schema v1.4.0"
---

# Конфликт поколения

## Что это

raytsystem использует оптимистичную конкурентность (optimistic concurrency): при записи вы указываете ожидаемое состояние — идентификатор поколения (`generation`) или снимок (snapshot), — а система перед фиксацией проверяет, что оно всё ещё текущее. Если состояние успело измениться между чтением и записью, операция отклоняется целиком и ничего не пишет. Это штатная защита, а не повреждение данных.

Редактор Skills использует ту же идею, но привязывается сразу к catalog и source SHA-256.
Для его конфликта есть отдельная инструкция:
[Конфликт редактирования Skill](/troubleshooting/skill-edit-conflict).

## Симптом

Команда или API-мутация завершается ошибкой о несовпадении поколения или снимка, например:

- `Task board generation changed` — при переходе задачи с устаревшим `--expected-generation`;
- `Task board changed before pointer commit` — состояние доски изменилось прямо во время фиксации;
- в графе кода — `Code graph inputs changed during extraction`, когда исходники поменялись во время сборки.

## Вероятная причина

Между тем, как вы прочитали состояние (`task list`, `graph status`) и попытались записать, доску задач или входы графа кто-то изменил: параллельная сессия, интерфейс, хук или ваш собственный второй запуск. Ожидаемое поколение/снимок в вашей команде больше не совпадает с текущим.

## Безопасная диагностика

Перечитайте актуальное состояние — только чтение, без изменений:

```bash
# Для задач: актуальный generation_id
uv run raytsystem task list --json

# Для графа кода: актуальное состояние снимка
uv run raytsystem graph status --json
```

Возьмите свежее значение (`generation_id` для задач) и сравните с тем, что вы передавали.

## Решение

1. Перечитайте текущее состояние (`task list` / `graph status`).
2. Убедитесь, что ваше изменение всё ещё имеет смысл поверх нового состояния (например, целевой переход задачи по-прежнему легален).
3. Повторите команду с актуальным ожидаемым поколением/снимком.

Данные при этом не повреждаются: отклонённая операция не оставляет частичной записи, поэтому повтор безопасен.

## Пример

```bash
# Получили конфликт — перечитываем поколение
uv run raytsystem task list --json
# Повторяем переход уже со свежим --expected-generation
uv run raytsystem task transition task_XXXX review \
  --idempotency-key review-1 \
  --expected-generation tgen_NEW
```

## Ожидаемый результат

После повтора со свежим поколением операция проходит и возвращает новый `generation_id`.

## Ограничения и безопасность

- Отклонение по конфликту поколения — это защита целостности, а не сбой; частичных записей не остаётся.
- Идемпотентность: если вы повторяете буквально ту же команду с тем же ключом и она уже применилась, вернётся прежний результат как no-op, а не дубликат. Но переиспользование того же ключа идемпотентности для другой команды само по себе вызывает конфликт.
- Мутации графа кода дополнительно подтверждают, что `ledger/CURRENT` остаётся байт-в-байт неизменным.

## Частые ошибки

- Слепо повторять команду со старым `--expected-generation` — конфликт повторится. Нужно сначала перечитать состояние.
- Менять ключ идемпотентности при повторе той же логической операции — теряется защита от дублей.

## Когда открыть issue

Если конфликт возникает снова и снова даже при последовательном (не параллельном) запуске и вы гарантированно передаёте только что прочитанное поколение, приложите к issue вывод `task list --json` (или `graph status --json`) до и после попытки записи и точный текст ошибки.

## Связанные страницы

- [Задача не меняет статус](/troubleshooting/task-not-transitioning)
- [Жизненный цикл задач](/tasks/lifecycle)
- [Свежесть: update и rebuild](/code-graph/freshness-update-rebuild)
- [Конфликт редактирования Skill](/troubleshooting/skill-edit-conflict)

## Источники истины

- `src/raytsystem/tasking.py`
- `src/raytsystem/codegraph/projection.py`

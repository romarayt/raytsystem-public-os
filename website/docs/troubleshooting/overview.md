---
title: "Решение проблем: обзор"
description: "Symptom-first индекс типовых проблем raytsystem со ссылками на конкретные страницы диагностики."
audience: [user, operator]
status: stable
feature_flags: []
related_commands:
  - "uv run raytsystem doctor"
  - "uv run raytsystem status"
related_pages:
  - /troubleshooting/ui-wont-start
  - /troubleshooting/doctor-errors
  - /troubleshooting/code-graph-missing-or-stale
  - /troubleshooting/task-not-transitioning
  - /troubleshooting/generation-conflict
  - /troubleshooting/skill-edit-conflict
  - /troubleshooting/feature-flag-off
  - /agents/tool-hub
source_of_truth:
  - path: src/raytsystem/cli.py
  - path: docs/STATUS.md
last_verified_against: "commit 3f2a123 / schema v1.4.0"
---

# Решение проблем: обзор

## Что это

Это точка входа в раздел диагностики. Здесь собран список типовых симптомов и ссылки на страницы, где каждый случай разобран по схеме: симптом → вероятная причина → безопасная диагностика → решение → когда открыть issue.

raytsystem работает локально и только на петле `127.0.0.1`, поэтому почти любую проблему можно исследовать безопасными командами только для чтения. Начинайте с общей проверки, а к частной странице переходите уже с конкретной ошибкой в руках.

## Когда использовать

Когда что-то повело себя не так, как ожидалось: интерфейс не открылся, команда завершилась с ошибкой, линза «Код» пустая или задача не сменила статус.

## Пошагово

1. Сначала запустите общую проверку окружения и указателей проекта:

   ```bash
   uv run raytsystem doctor
   ```

   `doctor` не меняет состояние проекта. Он возвращает набор проверок (`checks`) и итоговый признак `healthy`. Если какая-то проверка равна `false`, начните со страницы [doctor сообщает ошибку](/troubleshooting/doctor-errors).

2. Дополнительно посмотрите общее состояние проекта без создания баз и индексов:

   ```bash
   uv run raytsystem status
   ```

3. Затем перейдите на страницу под ваш симптом.

## Индекс проблем

- Интерфейс `raytsystem ui` не стартует или страница пустая → [Интерфейс не запускается](/troubleshooting/ui-wont-start).
- `doctor` возвращает `healthy: false` или отдельные проверки `false` → [doctor сообщает ошибку](/troubleshooting/doctor-errors).
- Линза «Код» показывает «Не построено» или «Устарело», а `graph query` завершается ошибкой → [Граф кода отсутствует или устарел](/troubleshooting/code-graph-missing-or-stale).
- Задача не меняет статус, переход отклонён → [Задача не меняет статус](/troubleshooting/task-not-transitioning).
- Ошибка о несовпадении поколения (`generation`/snapshot) при записи → [Конфликт поколения](/troubleshooting/generation-conflict).
- Skill editor показывает `skill_edit_conflict` и не сохраняет →
  [Конфликт редактирования Skill](/troubleshooting/skill-edit-conflict).
- Возможность описана, но не работает, потому что выключена по умолчанию → [Флаг функции выключен](/troubleshooting/feature-flag-off).
- Tool Hub отклоняет URL, не находит `ffmpeg`/`tesseract`, возвращает `partial` или MCP не готов →
  [Troubleshooting Tool Hub](/agents/tool-hub#troubleshooting).

## Ограничения и безопасность

- `doctor` и `status` работают только на чтение и ничего не меняют.
- Команды с суффиксами `list`, `status`, `query`, `explain` безопасны для диагностики: они не пишут в каноническое хранилище.
- Никогда не редактируйте вручную `_raw/`, объекты и поколения в `ledger/`, сгенерированные страницы `knowledge/`. Восстановление таких артефактов делается пересборкой, а не правкой файлов.

## Частые ошибки

- Прыгать сразу к правке файлов, не запустив `doctor`. Сначала диагностика, потом действие.
- Считать выключенную по умолчанию функцию сломанной. Проверьте раздел [Флаг функции выключен](/troubleshooting/feature-flag-off) и [справочник флагов](/reference/feature-flags).

## Связанные страницы

- [Флаг функции выключен](/troubleshooting/feature-flag-off)
- [Справочник CLI](/reference/cli)
- [Справочник флагов функций](/reference/feature-flags)

## Источники истины

- `src/raytsystem/cli.py`
- `docs/STATUS.md`

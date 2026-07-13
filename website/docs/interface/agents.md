---
title: "Агенты (интерфейс)"
description: "Единый раздел «Агенты»: определение и текущее execution-состояние одного Agent без дублей."
audience:
  - user
  - operator
status: stable
feature_flags:
  - digital_employees_enabled
  - runtime_execution_enabled
  - task_workspaces_enabled
related_commands:
  - raytsystem ui
  - raytsystem agent preflight
  - raytsystem agent subagent-check
related_pages:
  - /agents/overview
  - /workflow/overview
  - /interface/skills
  - /interface/overview
  - /security/overview
  - /reference/api
source_of_truth:
  - path: src/raytsystem/webapp/execution_views.py
  - path: web/src/features/AgentsSurface.tsx
  - path: web/src/features/AgentDetailView.tsx
  - path: web/src/presentation.ts
  - path: config/raytsystem.toml
last_verified_against: "2026-07-12 / schema v1.4.0"
---

# Агенты (интерфейс)

## Что это

Маршрут `/agents` показывает **один список Agent**. У Agent есть две ветви
данных: инертное `AgentDefinition` из каталога и текущее execution-состояние.
Бэкенд по-прежнему хранит их в разных плоскостях, а интерфейс соединяет их только
по стабильному `agent_id` / `employee_id`.

Вкладок «Цифровые сотрудники» и «Определения каталога» больше нет: они
показывали две проекции одной сущности и создавали дубли.

![Единый список Agent с definition- и runtime-состоянием](/img/interface/agents/agents-list-dark.png)

*Документационный снимок на детерминированном синтетическом fixture; реальные данные не использованы.*

## Имя и язык

Заголовок карточки — каноническое английское имя, например `Builder` или
`Researcher`. Оно не переводится. Роль, описание, статус, названия полей, кнопки,
ошибки и подсказки остаются русскими:

```text
Builder
Роль: реализация
```

Имя, роль и описание — разные поля. Перевод не участвует в соединении данных.

## Что видно в списке

Каждый `agent_id` показан один раз. Карточка разделяет definition- и runtime-состояние и
показывает:

- каноническое имя, ID, русскую роль и отдельное описание;
- runtime adapter и execution status;
- количество skills;
- filesystem mode и concurrency;
- текущую задачу и readiness;
- типизированную причину недоступности.

Если execution store ещё не создан, карточка остаётся видимой со статусом
«Runtime не настроен». Если выполнение выключено флагом — «Выполнение отключено».
`AgentDefinition` без execution record показывается как «Только каталог». Execution record без
определения не скрывается, а показывается как degraded-состояние.

## Поиск и фильтры

Поиск учитывает английское имя, ID, русскую роль, описание, adapter и ID назначенного
skill. Доступны фильтры «Все», «Готовы», «Отключены», «Только каталог»,
«Выполняют задачу» и «Требуют настройки».

## Подробности Agent

Выбор Agent открывает специализированную поверхность, а не общий metadata Inspector:

- **Обзор** — имя, ID, роль, описание, pack, версия, статус и назначение;
- **Инструкция** — безопасное представление definition, context paths, capabilities и ограничения;
- **Skills** — назначенные skills, их статус и permissions с переходом к skill detail;
- **Runtime** — adapter, session/workspace state, задача, concurrency, budgets, leases и причина блокировки;
- **Доступ** — filesystem, data classes, tools, network/egress, approvals и effective permissions;
- **История** — только безопасные task/run/revision/audit summaries.

![Подробная страница Agent с общими вкладками поверхности](/img/interface/agents/agent-detail-dark.png)

*Каноническое имя остаётся английским, а роль, статусы и управление — русскими.*

Если typed metadata для budgets, leases или history отсутствует, интерфейс честно показывает
пустое состояние и не выводит связи из текста инструкции.

## Ограничения и безопасность

- Просмотр Agent не запускает модель, процесс или shell-команду.
- Профиль каталога не означает, что execution включен.
- API не возвращает credentials, абсолютные пути, restricted content или неочищенные runtime payloads.
- Отключённые флаги и неинициализированное хранилище показываются как состояние, а не скрываются.

## Связанные страницы

- [Агенты и расширение](/agents/overview)
- [Workflow и execution plane](/workflow/overview)
- [Skills (интерфейс)](/interface/skills)
- [Обзор интерфейса](/interface/overview)
- [Безопасность](/security/overview)
- [HTTP API: Agents и Skills](/reference/api)

## Источники истины

- `src/raytsystem/webapp/execution_views.py`
- `web/src/features/AgentsSurface.tsx`
- `web/src/features/AgentDetailView.tsx`
- `web/src/presentation.ts`
- `config/raytsystem.toml`

---
title: "Для кого предназначен raytsystem"
description: "Аудитории raytsystem — пользователь знаний, оператор, разработчик и контрибьютор — и типичные сценарии для каждой."
audience: [user, operator, developer]
status: stable
feature_flags: []
related_commands: ["uv run raytsystem ui", "uv run raytsystem query", "uv run raytsystem status", "uv run raytsystem doctor"]
related_pages: [/getting-started/what-is-raytsystem, /getting-started/capabilities-and-limits, /getting-started/requirements, /interface/overview]
source_of_truth:
  - path: README.md
  - path: AGENTS.md
  - path: docs/STATUS.md
last_verified_against: "commit 3f2a123 / schema v1.4.0"
---

# Для кого предназначен raytsystem

## Что это

raytsystem рассчитан на команды и отдельных людей, которым нужна проверяемая работа
со знаниями на локальной машине, без обязательного облака. Ниже — четыре типичные
роли и их сценарии. Роли не взаимоисключающие: один человек может совмещать
несколько. Источник: `README.md`.

## Когда использовать

Выбирайте raytsystem, если вам важно, чтобы каждый вывод восходил к неизменяемому
свидетельству, а любое изменение проходило через явную операцию с журналом, а не
через ручную правку файлов. Источник: `AGENTS.md`, `README.md`.

## Аудитории и сценарии

### Пользователь знаний (user)

Работает через веб-панель на `127.0.0.1`. Типичные задачи:

- смотреть проверенный статус рабочего пространства в «Центре управления»;
- вести задачи на доске «Задачи» с надёжными переходами состояний;
- исследовать связи в «Вселенной» знаний и переходить от утверждения к точному
  исходному свидетельству;
- задавать фактические вопросы к каноническим знаниям (`uv run raytsystem query`).

Источник: `README.md`. См. [Интерфейс: обзор](/interface/overview).

### Оператор (operator)

Отвечает за запуск и здоровье локальной установки:

- поднимает панель командой `uv run raytsystem ui` (loopback `127.0.0.1`);
- проверяет состояние через `uv run raytsystem doctor` и `uv run raytsystem status`;
- следит за разделом «Системы» и «Безопасность» — трассировка, политики,
  аварийные средства, резервные копии;
- контролирует, что внешние возможности остаются выключенными до явного одобрения.

Источник: `README.md`, `docs/STATUS.md`. См.
[Возможности и ограничения](/getting-started/capabilities-and-limits).

### Разработчик (developer)

Строит и расширяет систему поверх кода raytsystem:

- ориентируется в архитектуре через граф кода
  (`uv run raytsystem graph query "ВОПРОС" --depth 2 --json`);
- работает с CLI-операциями INGEST / QUERY / LINT / SAVE;
- проектирует типизированные рабочие процессы (workflow DAG) без сырых shell-команд;
- запускает тесты, линт и проверки типов перед контрольной точкой.

Источник: `AGENTS.md`, `README.md`.

### Контрибьютор (contributor)

Вносит изменения в открытый проект (лицензия Apache-2.0):

- проходит проверочные ворота проекта (pytest, ruff, mypy, сборка фронтенда);
- уважает инварианты: не редактировать `_raw/`, объекты ledger и сгенерированные
  страницы `knowledge/`; вывод модели — это предложение, а не канон;
- сначала запрашивает контекст архитектуры у проверенного графа кода.

Источник: `AGENTS.md`, `README.md`, `docs/STATUS.md`.

## Ограничения и безопасность

Независимо от роли действуют общие правила: локальная работа, loopback-only, без
внешней отправки/публикации/продвижения без явного ограниченного одобрения.
Подробности — [Возможности и ограничения](/getting-started/capabilities-and-limits).

## Связанные страницы

- [Что такое raytsystem](/getting-started/what-is-raytsystem)
- [Возможности и ограничения текущей версии](/getting-started/capabilities-and-limits)
- [Системные требования](/getting-started/requirements)
- [Интерфейс: обзор](/interface/overview)

## Источники истины

- `README.md`
- `AGENTS.md`
- `docs/STATUS.md`

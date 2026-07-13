---
title: "Установка"
description: "Как получить рабочую копию raytsystem из исходного чекаута: клонирование, uv sync --dev и проверка через doctor."
audience: [user, operator]
status: stable
feature_flags: []
related_commands: ["uv sync --dev", "uv run raytsystem doctor", "uv run raytsystem start"]
related_pages:
  - /getting-started/requirements
  - /getting-started/first-run
  - /troubleshooting/ui-wont-start
  - /troubleshooting/doctor-errors
source_of_truth:
  - path: README.md
  - path: docs/STATUS.md
  - path: pyproject.toml
  - path: src/raytsystem/cli.py
last_verified_against: "working tree 2026-07-13 / CLI schema v1.4.0"
---

# Установка

## Что это

raytsystem ставится из исходного чекаута репозитория. На сегодня это единственный
поддерживаемый способ установки: standalone-шаблон рабочего пространства и готовые
релизные артефакты пока отложены (`docs/STATUS.md`, раздел «Known gaps»). Проект
разворачивается локально и не требует ни API-ключа, ни облачного аккаунта, ни
браузерного расширения.

## Предварительные условия

- Python 3.12+ (репозиторий ограничивает диапазон `>=3.12,<3.15`, см. `pyproject.toml`).
- [uv](https://docs.astral.sh/uv/) — менеджер окружения и запуска.
- Node.js 22+ нужен **только** для пересборки фронтенда. В обычной установке готовый
  бандл интерфейса уже лежит в репозитории, поэтому Node не требуется.

Подробнее о требованиях — на странице [Требования](/getting-started/requirements).

## Пошагово

1. Склонируйте репозиторий и перейдите в каталог:

   ```bash
   git clone https://github.com/romarayt/raytsystem-public-os.git raytsystem
   cd raytsystem
   ```

2. Установите зависимости (включая dev-инструменты) через uv:

   ```bash
   uv sync --dev
   ```

3. Проверьте окружение и канонические указатели, не меняя проект:

   ```bash
   uv run raytsystem doctor
   ```

4. Запустите loopback-only интерфейс:

   ```bash
   uv run raytsystem start
   ```

Команды взяты из блока «Canonical development commands» в `docs/STATUS.md` и раздела
«Open the interface» в `README.md`.

## Ожидаемый результат

`uv run raytsystem doctor` печатает сводку окружения: версию Python, версию SQLite и набор
проверок (`checks`). Установка считается корректной, когда команда завершается со
статусом `healthy` (в JSON-режиме поле `"healthy": true`). Если проверка не проходит,
`doctor` возвращает ненулевой код выхода и указывает, какой пункт не прошёл.

## Ограничения и безопасность

- Поддерживается установка из исходного GitHub-чекаута; хостовые инсталляторы пока не
  распространяются.
- Интерфейс запускается исключительно на локальном loopback `127.0.0.1` — привязка к
  внешнему адресу отклоняется (`src/raytsystem/cli.py`).
- Установка ничего не выполняет во внешних системах: рантайм-исполнение и провайдерские
  адаптеры по умолчанию выключены.

## Частые ошибки

- **`Web bundle is missing`** при попытке открыть интерфейс — бандл фронтенда отсутствует.
  См. [Интерфейс не запускается](/troubleshooting/ui-wont-start).
- **`doctor` не `healthy`** — разберитесь по пунктам `checks`. См.
  [Ошибки doctor](/troubleshooting/doctor-errors).
- **Неподдерживаемая версия Python** — проверка `python_supported` требует Python 3.12+.

## Связанные страницы

- [Требования](/getting-started/requirements)
- [Первый запуск](/getting-started/first-run)
- [Интерфейс не запускается](/troubleshooting/ui-wont-start)
- [Ошибки doctor](/troubleshooting/doctor-errors)

## Источники истины

- `README.md`
- `docs/STATUS.md`
- `pyproject.toml`
- `src/raytsystem/cli.py`

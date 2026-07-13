---
title: "Системные требования"
description: "Что нужно для запуска raytsystem локально: Python 3.12 через uv, SQLite, при пересборке фронтенда — Node.js."
audience: [user, operator]
status: stable
feature_flags: []
related_commands: ["uv sync --dev", "uv run raytsystem doctor", "uv run raytsystem ui"]
related_pages: [/getting-started/installation, /getting-started/first-run, /getting-started/capabilities-and-limits]
source_of_truth:
  - path: pyproject.toml
  - path: README.md
last_verified_against: "schema v1.4.0"
---

# Системные требования

## Что это

Минимальный набор для локального запуска raytsystem. Система рассчитана на работу на
одной машине по локальной петле и **не требует обязательного интернета после
установки**. Источник: `README.md`.

## Предварительные условия

- **Python 3.12** (диапазон `>=3.12,<3.15`), устанавливается и управляется через
  [uv](https://docs.astral.sh/uv/). Источник: `pyproject.toml` (`requires-python`),
  `README.md`.
- **uv** — менеджер окружения и запуска команд (`uv sync`, `uv run raytsystem ...`).
  Источник: `README.md`.
- **SQLite** — локальные хранилища: `ops/control.sqlite`, `.raytsystem/index.sqlite` и
  изолированный `ops/platform.sqlite`. Источник: `config/raytsystem.toml`,
  `docs/STATUS.md`. (Проверенная `raytsystem doctor` версия среды — SQLite 3.50.4.)
- **Node.js 22+** — **только** при пересборке фронтенд-бандла. Готовый
  самостоятельно размещаемый бандл уже включён в репозиторий, поэтому для обычного
  запуска панели Node.js не нужен. Источник: `README.md`.
- **Локальная машина.** Внешний интернет для работы панели не требуется: не нужны
  API-ключ, облачный аккаунт, расширение браузера или плагин Obsidian. Источник:
  `README.md`.

## Пошагово

1. Установите зависимости проекта:
   ```bash
   uv sync --dev
   ```
2. Проверьте здоровье среды:
   ```bash
   uv run raytsystem doctor
   ```
3. Запустите панель управления (loopback):
   ```bash
   uv run raytsystem ui
   ```

Команда `uv run raytsystem ui` открывает `http://127.0.0.1:8765`. raytsystem в этом
релизе отказывается привязываться к не-loopback-адресу. Источник: `README.md`.

## Ожидаемый результат

`uv run raytsystem doctor` сообщает о здоровой среде (Python 3.12, доступный SQLite),
а `uv run raytsystem ui` поднимает веб-панель на `127.0.0.1:8765` без обращения к
внешним провайдерам. Источник: `README.md`, `docs/STATUS.md`.

## Ограничения и безопасность

- Поддерживаемый путь установки в этом релизе — текущая копия исходников из
  репозитория; отдельный шаблон рабочего пространства и готовые релизные артефакты
  отложены. Источник: `docs/STATUS.md`.
- Пересборка фронтенда — отдельный сценарий для мейнтейнеров и требует Node.js 22+
  и дополнительных шагов npm. Источник: `README.md`.
- HTTP остаётся loopback-only и same-origin; внешние возможности выключены по
  умолчанию. См. [Возможности и ограничения](/getting-started/capabilities-and-limits).

## Частые ошибки

- **Пытаться открыть панель по внешнему адресу.** Не выйдет: разрешён только
  `127.0.0.1`. Источник: `README.md`.
- **Ставить Node.js ради обычного запуска.** Не требуется — бандл уже собран.
  Источник: `README.md`.
- **Использовать несовместимую версию Python.** Нужен диапазон `>=3.12,<3.15`;
  проще всего дать uv управлять интерпретатором. Источник: `pyproject.toml`.

## Связанные страницы

- [Установка](/getting-started/installation)
- [Первый запуск](/getting-started/first-run)
- [Возможности и ограничения текущей версии](/getting-started/capabilities-and-limits)

## Источники истины

- `pyproject.toml`
- `README.md`

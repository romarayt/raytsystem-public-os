---
title: "Первый запуск"
description: "Как открыть локальный интерфейс raytsystem командой uv run raytsystem start и убедиться, что окружение здорово."
audience: [user, operator]
status: stable
feature_flags: [runtime_execution_enabled]
related_commands: ["uv run raytsystem start", "uv run raytsystem doctor", "uv run raytsystem status"]
related_pages:
  - /getting-started/installation
  - /getting-started/interface-tour
  - /interface/overview
  - /troubleshooting/ui-wont-start
source_of_truth:
  - path: src/raytsystem/cli.py
  - path: README.md
  - path: docs/STATUS.md
last_verified_against: "working tree 2026-07-13 / CLI schema v1.4.0"
---

# Первый запуск

## Что это

После установки raytsystem открывается как локальная веб-панель управления. Команда
`uv run raytsystem start` поднимает same-origin интерфейс на локальном
loopback и открывает его в браузере.

## Предварительные условия

- Выполнена [установка](/getting-started/installation) (`uv sync --dev`).
- Проверка `uv run raytsystem doctor` проходит успешно.

## Пошагово

1. Запустите интерфейс:

   ```bash
   uv run raytsystem start
   ```

2. Команда печатает адрес и по умолчанию открывает браузер:

   ```text
   raytsystem: http://127.0.0.1:8765
   ```

3. `start` — это короткий alias для `ui`. Если нужно запустить без автооткрытия браузера,
   добавьте `--no-open`. Порт по
   умолчанию — `8765`, его можно сменить опцией `--port`.

Параметры команды заданы в `src/raytsystem/cli.py` (`start` и `ui`).

## Ожидаемый результат

Открывается панель управления raytsystem. Навигация ведёт к разделам: «Центр управления»,
«Задачи», «Вселенная» (Knowledge Universe), «Запуски», «Агенты», «Навыки», «Контекст»,
«Безопасность» и «Системы». Данные показываются из проверенного снимка рабочего
пространства; чтения (GET/HEAD) ничего не записывают.

Обзор разделов — на странице [Обзор интерфейса](/interface/overview) и в
[туре по интерфейсу](/getting-started/interface-tour).

## Проверка окружения

Параллельно с интерфейсом полезно свериться из терминала:

```bash
uv run raytsystem doctor
uv run raytsystem status
```

`doctor` показывает версию Python, версию SQLite и набор проверок (`checks`), включая
свежесть графа кода; `status` — состояние проекта без создания баз и индексов.

## Ограничения и безопасность

- Интерфейс работает только на `127.0.0.1`. Привязка к нехост-loopback адресу
  отклоняется: «Refusing remote bind: v1 supports only 127.0.0.1» (`src/raytsystem/cli.py`).
- Все запросы проходят проверки same-origin, Host/Origin, сессии, CSRF и идемпотентности.
  В интерфейсе нет ни одного эндпоинта, который запускал бы модель, shell, ingest,
  промоушен или публикацию.
- Первый старт не выполняет модель и не обращается к провайдеру: рантайм-исполнение
  выключено по умолчанию (`runtime_execution_enabled=false`, `docs/STATUS.md`). Определения
  агентов не означают запущенных агентов.
- Если бандл фронтенда отсутствует, команда завершится сообщением «Web bundle is missing»
  и кодом выхода 2.

## Частые ошибки

- **`Web bundle is missing. Run the documented frontend build.`** — не собран фронтенд.
  См. [Интерфейс не запускается](/troubleshooting/ui-wont-start).
- **`Verified workspace snapshot is unavailable.`** — недоступен проверенный снимок
  рабочего пространства; проверьте установку и `uv run raytsystem doctor`.
- **Порт занят** — укажите другой порт через `--port`.

## Связанные страницы

- [Установка](/getting-started/installation)
- [Тур по интерфейсу](/getting-started/interface-tour)
- [Обзор интерфейса](/interface/overview)
- [Интерфейс не запускается](/troubleshooting/ui-wont-start)

## Источники истины

- `src/raytsystem/cli.py`
- `README.md`
- `docs/STATUS.md`

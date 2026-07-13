---
title: "doctor сообщает ошибку"
description: "Как читать вывод raytsystem doctor и что делать при каждой проверке, равной false."
audience: [operator]
status: stable
feature_flags:
  - code_graph_enabled
related_commands:
  - "uv run raytsystem doctor"
  - "uv run raytsystem status"
  - "uv run raytsystem graph status"
related_pages:
  - /troubleshooting/overview
  - /troubleshooting/code-graph-missing-or-stale
  - /getting-started/requirements
  - /getting-started/installation
source_of_truth:
  - path: src/raytsystem/cli.py
last_verified_against: "schema v1.4.0"
---

# doctor сообщает ошибку

## Что это

`uv run raytsystem doctor` проверяет окружение и канонические указатели проекта, ничего не меняя. Он собирает словарь проверок `checks`, вычисляет итог `healthy = all(checks)` и при `healthy: false` завершается с кодом `1`. Эта страница объясняет, что означает каждая проверка и что делать, если она `false`.

## Симптом

`doctor` печатает `healthy: false` (или отдельные строки `checks`, где значение `false`) и выходит с ненулевым кодом.

## Как читать вывод

Запустите в машиночитаемом виде, чтобы удобно смотреть проверки:

```bash
uv run raytsystem doctor --json
```

В ответе есть `project_root`, `active_generation`, `python`, `sqlite`, блок `code_graph`, блок `platform` и словарь `checks`.

## Проверки и что делать при false

- **`root_exists`** — каталог проекта существует и является директорией. Если `false`, вы запускаете команду не из корня проекта; перейдите в корень или передайте `--root` с корректным путём.

- **`config_exists`** — найден `config/raytsystem.toml`. Если `false`, конфигурация проекта отсутствует по ожидаемому пути. Убедитесь, что запуск идёт из корня проекта raytsystem. См. [Установку](/getting-started/installation).

- **`python_supported`** — Python версии `3.12` или новее. Если `false`, обновите интерпретатор; поддерживаемая версия описана в [Требованиях](/getting-started/requirements).

- **`ledger_pointer_exists`** — существует указатель `ledger/CURRENT`. Если `false`, активное поколение канонического реестра не выбрано. Это ожидаемо для совсем пустого проекта; при необходимости соберите проекции документированной командой `rebuild-index`. Не редактируйте `ledger/CURRENT` вручную.

- **`generation_exists`** — файл поколения `ledger/generations/<id>.json`, на который указывает `CURRENT`, есть на диске. Если `false`, указатель ссылается на отсутствующий объект поколения. Не правьте объекты реестра руками — восстанавливайте состояние пересборкой.

- **`code_graph_current`** — присутствует только если в конфигурации есть секция графа кода. Проверка равна `true`, когда состояние графа `current`. Если `false`, граф не построен или устарел; разбор на странице [Граф кода отсутствует или устарел](/troubleshooting/code-graph-missing-or-stale). Полное состояние графа продублировано в блоке `code_graph` того же вывода.

- **`platform_health`** — платформенная самодиагностика; `true`, если состояние платформы не `error` и не `degraded`. `doctor` не падает на повреждённом платформенном хранилище, а честно понижает состояние. Если `false`, смотрите блок `platform` в выводе за подробностями.

## Безопасная диагностика

Все проверки — только чтение. Дополнительно можно посмотреть состояние без создания баз и индексов:

```bash
uv run raytsystem status
uv run raytsystem graph status --json
```

## Ожидаемый результат

После устранения причин `doctor` возвращает `healthy: true` и код выхода `0`.

## Ограничения и безопасность

- `doctor` и `status` не мутируют состояние проекта.
- Указатели и объекты реестра неизменяемы; их нельзя редактировать напрямую.

## Частые ошибки

- Запуск не из корня проекта — тогда `root_exists`/`config_exists` будут `false`.
- Попытка «починить» `ledger/CURRENT` правкой файла вместо документированной пересборки.

## Когда открыть issue

Если все входные условия выполнены (правильный корень, поддерживаемый Python, конфигурация на месте), но какая-то проверка стабильно `false` без внятной причины в блоках `code_graph`/`platform`, приложите к issue полный вывод `raytsystem doctor --json`.

## Связанные страницы

- [Решение проблем: обзор](/troubleshooting/overview)
- [Граф кода отсутствует или устарел](/troubleshooting/code-graph-missing-or-stale)
- [Требования](/getting-started/requirements)

## Источники истины

- `src/raytsystem/cli.py`

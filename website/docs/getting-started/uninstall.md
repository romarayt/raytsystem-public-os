---
title: "Удаление и очистка локальных данных"
description: "Что можно безопасно удалить и пересобрать, что трогать нельзя, и как полностью снять установку из исходного чекаута."
audience: [user, operator]
status: stable
feature_flags: [code_graph_enabled]
related_commands: ["uv run raytsystem uninstall", "uv run raytsystem rebuild-index", "uv run raytsystem graph rebuild"]
related_pages:
  - /knowledge/editable-vs-immutable
  - /knowledge/recovery
  - /code-graph/freshness-update-rebuild
  - /getting-started/installation
source_of_truth:
  - path: src/raytsystem/cli.py
  - path: docs/STATUS.md
  - path: AGENTS.md
  - path: README.md
last_verified_against: "commit 3f2a123 / schema v1.4.0"
---

# Удаление и очистка локальных данных

## Что это

raytsystem чётко разделяет **производное** состояние (его можно удалять и пересобирать) и
**неизменяемые источники** (их нельзя редактировать или удалять вручную). Эта страница
объясняет, что безопасно вычистить, а что трогать нельзя, и как снять установку целиком.

## Что можно удалять и пересобирать

Производные артефакты удаляемы и восстанавливаются из канонического указателя
`ledger/CURRENT`:

- `.raytsystem/graph/` — одноразовый слой графа кода;
- `.raytsystem/workspaces/` — управляемые рабочие пространства задач;
- `.raytsystem/index.sqlite` — индекс поиска (путь по умолчанию из конфигурации);
- материализованные проекции: FTS5, графовый JSON и Markdown в `knowledge/`.

Пересобрать их после очистки:

```bash
uv run raytsystem rebuild-index --json
uv run raytsystem graph rebuild --json
```

`rebuild-index` восстанавливает FTS5, граф, `index.md` и `hot.md` из `ledger/CURRENT`
(`src/raytsystem/cli.py`), а `graph rebuild` заново собирает граф кода.

## Что удалять вручную нельзя

Не редактируйте и не удаляйте вручную источники истины (`AGENTS.md`, `README.md`):

- `_raw/` — неизменяемые точные байты входных данных;
- `ledger/` и `ledger/generations/` — канонический журнал и генерации;
- сгенерированные страницы в `knowledge/`.

Эти каталоги неизменяемы, потому что существующие генерации привязывают их хеши.
Ручное удаление здесь ломает целостность. Как устроено разделение и как восстанавливать —
на страницах [Редактируемое и неизменяемое](/knowledge/editable-vs-immutable) и
[Восстановление](/knowledge/recovery).

## Пошагово: безопасная очистка производных данных

1. Остановите интерфейс (Ctrl+C в терминале, где запущен `uv run raytsystem start`).
2. Проверьте состояние проекта:

   ```bash
   uv run raytsystem status
   ```

   Поля `index_db_exists` и `control_db_exists` показывают, какие производные базы
   существуют.
3. Удалите производные каталоги/индексы (`.raytsystem/graph/`, `.raytsystem/index.sqlite`
   и при необходимости `.raytsystem/workspaces/`).
4. Пересоберите нужное командами `rebuild-index` и `graph rebuild`.

## Пошагово: полное удаление установки

Если raytsystem был подключён к другому проекту через Skill `start`/`bootstrap`, сначала запустите:

```bash
uv run raytsystem uninstall --target /path/to/project --json
```

Команда удаляет только файлы, созданные установщиком, и не трогает исходные данные. Для
самого исходного чекаута удаление — это удаление клонированного каталога после backup. Управляемое
uv-окружение `.venv` лежит внутри клона.

1. Остановите интерфейс.
2. При необходимости сначала сделайте резервную копию (см. раздел «Ограничения»).
3. Удалите каталог репозитория целиком.

## Ограничения и безопасность

- Удаление производного состояния не затрагивает `_raw/` и `ledger/`, но безвозвратно.
  Перед удалением всего каталога сделайте резервную копию, если в нём есть ценные данные.
- Промоушен реального корпуса по умолчанию запрещён (default-deny), поэтому в стандартной
  установке каноническое знание обычно ограничено синтетическими фикстурами.

## Частые ошибки

- **Удалили `ledger/` или `_raw/` вручную** — это неизменяемые источники; восстановление
  описано в разделе [Восстановление](/knowledge/recovery).
- **После очистки индекс устарел** — запустите `rebuild-index`; для графа —
  `graph rebuild` (см. [Свежесть, update и rebuild](/code-graph/freshness-update-rebuild)).

## Связанные страницы

- [Редактируемое и неизменяемое](/knowledge/editable-vs-immutable)
- [Восстановление](/knowledge/recovery)
- [Граф кода: свежесть, update и rebuild](/code-graph/freshness-update-rebuild)
- [Установка](/getting-started/installation)

## Источники истины

- `src/raytsystem/cli.py`
- `docs/STATUS.md`
- `AGENTS.md`
- `README.md`

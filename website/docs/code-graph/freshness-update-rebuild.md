---
title: "Актуальность графа: status, update, rebuild"
description: "Как проверить свежесть графа кода и обновить его инкрементально или полностью пересобрать."
audience: [user, developer]
status: stable
feature_flags: [code_graph_enabled]
related_commands:
  - "uv run raytsystem graph status --json"
  - "uv run raytsystem graph update --json"
  - "uv run raytsystem graph rebuild --json"
related_pages:
  - /code-graph/overview
  - /code-graph/query-explain-neighbors
  - /interface/universe
  - /troubleshooting/code-graph-missing-or-stale
source_of_truth:
  - path: docs/11-code-graph-and-execution-plane.md
  - path: src/raytsystem/codegraph/projection.py
  - path: ops/decisions/ADR-016-native-derived-code-graph.md
last_verified_against: "commit 3f2a123 / schema v1.4.0"
---

# Актуальность графа: status, update, rebuild

## Что это

Три команды управляют жизненным циклом графа кода: `status` проверяет свежесть, `update` обновляет граф инкрементально, `rebuild` строит полную новую проекцию. `status` и запросы — операции только для чтения; `update` и `rebuild` — явные записи (`docs/11-code-graph-and-execution-plane.md`, «Code-graph lifecycle»).

## Когда использовать

- **`graph status`** — когда нужно понять, актуален ли граф, и что именно изменилось.
- **`graph update`** — после локальных правок: изменённых, новых, удалённых или переименованных файлов.
- **`graph rebuild`** — когда нужно построить проекцию с нуля, игнорируя кэш (например, при сбросе плоскости графа).

## Предварительные условия

Флаг `code_graph_enabled` должен быть включён (по умолчанию `true` в `config/raytsystem.toml`). При выключенном флаге `status` вернёт состояние `MISSING` с причиной `disabled`, а сборка завершится ошибкой «disabled by project policy» (`src/raytsystem/codegraph/projection.py`).

## Как это работает

### `graph status` (только чтение)

`status` выполняет проверку свежести по content hash: сравнивает хеши содержимого файлов текущего снимка с наблюдаемыми в рабочей копии и возвращает ограниченные списки изменённых и удалённых относительных путей (`projection.py`, метод `status`). Возможные состояния: `CURRENT`, `STALE`, `BUILDING`, `MISSING`, `ERROR`, `UNCHECKED`. Граф считается устаревшим (STALE), если изменились или удалились файлы, поменялся отпечаток конфигурации/экстрактора, сменился Git-коммит/ветка или предыдущее обновление было брошено (`projection.py`). Списки путей ограничены (до 500) с флагом `paths_truncated`.

### `graph update` (инкрементально)

`update` переиспользует только семантически проверенные записи кэша по каждому файлу и заново извлекает лишь изменившиеся. Он учитывает изменённые, новые и удалённые файлы (переименование — это удаление плюс добавление). Результат содержит счётчики `processed_files`, `skipped_files`, `deleted_files`, `cache_hits`, число нод и рёбер (`projection.py`, `_build`).

### `graph rebuild` (полная проекция)

`rebuild` игнорирует кэш и извлекает все файлы заново, строя полную новую проекцию (`projection.py`; `docs/11-...`). Используйте его для чистого пересоздания.

### Атомарная установка

И `update`, и `rebuild` записывают неизменяемые объекты снимка/манифеста, а затем атомарно переключают указатель `.raytsystem/graph/CURRENT` под обновлённой ограждённой (fenced) арендой. Брошенный или подготовленный WAL никогда не выдаёт себя за текущую сборку (`docs/11-...`, `projection.py`). Если логический отпечаток, коммит и ветка совпадают с прежним снимком, операция завершается как `no_op`.

## Пошагово

```bash
# 1. Проверить свежесть (только чтение)
uv run raytsystem graph status --json

# 2. Обновить инкрементально после правок
uv run raytsystem graph update --json

# 3. Или собрать полную проекцию заново
uv run raytsystem graph rebuild --json
```

## Ожидаемый результат

`status --json` вернёт состояние и, при устаревании, списки изменённых/удалённых путей. `update`/`rebuild --json` вернут идентификатор снимка, отпечаток, счётчики обработанных/пропущенных/удалённых файлов и длительность.

## Из интерфейса

Те же операции доступны во «Вселенной» в линзе **Код**: Inspector предоставляет ограниченные действия обновления/пересборки. Браузер запрашивает только типизированную операцию update/rebuild, привязанную к ожидаемому снимку, под защитой сессии/Origin/CSRF/идемпотентности; UI никогда не собирает и не выполняет команды оболочки (`docs/11-...`, `ADR-016`).

## Ограничения и безопасность

- Изменение исходников/конфигурации/экстрактора делает граф устаревшим; при graph-first подготовке рабочего пространства это приводит к безопасному отказу (fail closed) до успешного `update` или `rebuild` (`docs/11-...`).
- Снимки под `.raytsystem/graph/` одноразовы. Не редактируйте и не коммитьте их; не создавайте `graphify-out/`.
- Для сброса плоскости остановите локальный UI, удалите только `.raytsystem/graph/`, восстановите флаги и выполните `graph rebuild`. Никогда не удаляйте и не переписывайте канонические объекты реестра ради сброса графа (`docs/11-...`).

## Частые ошибки

- **Граф отстаёт от кода** — обычная причина в том, что после правок не запущен `update`. Запустите `graph status`, затем `graph update`.
- **Ожидание, что `update` подхватит смену ветки** — смена Git-коммита/ветки помечает граф устаревшим; выполните `update` или `rebuild`.

## Связанные страницы

- [Граф проекта: обзор](/code-graph/overview)
- [Запросы к графу: query, explain, neighbors](/code-graph/query-explain-neighbors)
- [Вселенная и линза «Код»](/interface/universe)
- [Граф отсутствует или устарел](/troubleshooting/code-graph-missing-or-stale)

## Источники истины

- `docs/11-code-graph-and-execution-plane.md`
- `src/raytsystem/codegraph/projection.py`
- `ops/decisions/ADR-016-native-derived-code-graph.md`

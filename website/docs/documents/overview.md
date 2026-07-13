---
title: "Документы: обзор"
description: "Чем раздел «Документы» отличается от публичной базы знаний и Вселенной, какие Markdown-сценарии входят в экспериментальную поставку и где проходит граница доступа."
audience: [user, operator, developer]
status: experimental
feature_flags: []
related_commands: []
related_pages:
  - /documents/search
  - /documents/markdown-view
  - /documents/source-mode
  - /documents/editable-and-protected
  - /interface/universe
source_of_truth:
  - path: ops/decisions/ADR-035-managed-document-workspace.md
  - path: docs/13-documents-workspace-architecture.md
last_verified_against: "ADR-035 / experimental Documents contract"
---

# Документы: обзор

## Что это

«Документы» — это управляемый workspace для файлов и заметок текущего
проекта. Сервер показывает только файлы из явно разрешённых roots и сам
решает, можно ли каждый файл читать или редактировать. Браузер не передаёт
произвольный абсолютный путь.

Три раздела не смешиваются:

| Раздел | Что показывает | Можно ли редактировать |
|---|---|---|
| База знаний | Публичную инструкцию по raytsystem | Нет, в локальном UI это read-only Handbook |
| Документы | Разрешённые файлы workspace | Только в roots с режимом `read_write` |
| Вселенная | Связи знаний, документов, работы и кода | Нет, это производная проекция |

Handbook остаётся read-only как отдельный интерфейс. В maintainer workspace исходники публичной
документации могут отдельно появиться в «Документах» только через явный writable root; это не
объединяет два раздела и не расширяет доступ обычного workspace.

## Что входит в experimental-поставку

- дерево файлов, поиск, фильтры, сортировки и постраничные результаты;
- режимы «Чтение», «Визуальный», «Исходный Markdown» и «Diff»;
- properties, wikilinks, backlinks, создание, папки, rename/move, избранное и вкладки;
- явное сохранение с expected hash/snapshot и безопасным conflict-ответом;
- Git/local history, diff и переход к тому же документу во Вселенной.

Визуальный режим доступен не для всех `.md`: серверный syntax gate и client-side
проверка реального Milkdown parse→serialize блокируют визуальное сохранение, если
даже исходный cycle меняет байты. Для tight/mixed/неканонических списков и unknown syntax
остаётся Source mode.

![Документы на desktop: дерево, вкладки, оформленный Markdown и inspector](/img/documents/desktop.png)

На узком экране дерево и inspector становятся drawers, а активный документ остаётся основной
областью.

![Документы на mobile: поиск, вкладка и открытый Markdown](/img/documents/mobile.png)

## Быстрый маршрут

1. Откройте «Документы».
2. Выберите «Все», «Недавние», «Добавленные» или «Изменённые».
3. Откройте файл во вкладке и проверьте marker доступа.
4. Для правки выберите Source или доступный Visual, затем явно сохраните.

## Чего нет в этой версии

Нет collaboration, Sync, Canvas, marketplace плагинов, deletion, произвольного доступа к
файловой системе и редактирования PDF/audio/video. Также пока нет watcher/polling,
корреляции external rename, attachment picker/upload/copy и отдельной command palette. raytsystem не
обещает совместимость со всеми
community-расширениями Obsidian. Markdown остаётся обычным переносимым файлом, а
protected raytsystem state — read-only или hidden.

## Связанные страницы

- [Поиск по документам](/documents/search)
- [Просмотр Markdown](/documents/markdown-view)
- [Что можно редактировать](/documents/editable-and-protected)
- [Документ во Вселенной](/documents/graph)

## Источники истины

- `ops/decisions/ADR-035-managed-document-workspace.md`
- `docs/13-documents-workspace-architecture.md`

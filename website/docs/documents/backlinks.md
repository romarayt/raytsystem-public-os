---
title: "Backlinks"
description: "Как raytsystem строит обратные ссылки из отдельного document index, показывает контекст и количество и почему backlinks не являются canonical knowledge."
audience: [user, developer]
status: experimental
feature_flags: []
related_commands: []
related_pages:
  - /documents/wikilinks
  - /documents/graph
  - /documents/search
source_of_truth:
  - path: ops/decisions/ADR-035-managed-document-workspace.md
  - path: docs/13-documents-workspace-architecture.md
last_verified_against: "ADR-035 / experimental Documents contract"
---

# Backlinks

## Что показывает панель

Backlinks перечисляют документы, которые ссылаются на текущий файл. Для каждой ссылки текущий
inspector показывает исходные title/path, ограниченный контекст и переход во вкладку.
Общие счётчики входящих/исходящих связей видны в сводке документа.

API постраничен и ограничен: тысячи входящих ссылок не загружаются одним browser payload.
Текущий inspector показывает первую страницу и предупреждает о продолжении. Hidden/restricted
источники не раскрывают путь или snippet пользователю без authority.

## Откуда берутся backlinks

Scanner извлекает исходящие wikilinks, а отдельная disposable projection вычисляет обратные.
После изменения одного файла обновляются его ссылки и затронутые backlink rows. Rebuild может
восстановить всю проекцию из разрешённых файлов.

Backlink не делает текст verified claim и не меняет canonical knowledge. Это навигационная связь
между документами; во Вселенной она визуально отличается от claim/evidence relationships.

## Связанные страницы

- [Wikilinks](/documents/wikilinks)
- [Документ во Вселенной](/documents/graph)
- [Поиск](/documents/search)

## Источники истины

- `ops/decisions/ADR-035-managed-document-workspace.md`
- `docs/13-documents-workspace-architecture.md`

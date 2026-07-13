---
title: "Недавние, добавленные и изменённые"
description: "Откуда raytsystem берёт дату и статус нового или изменённого файла, почему ctime не считается универсальной датой создания и как читать объяснение сигнала."
audience: [user, operator]
status: experimental
feature_flags: []
related_commands: []
related_pages:
  - /documents/overview
  - /documents/search
  - /documents/history
source_of_truth:
  - path: ops/decisions/ADR-035-managed-document-workspace.md
  - path: docs/13-documents-workspace-architecture.md
last_verified_against: "ADR-035 / experimental Documents contract"
---

# Недавние, добавленные и изменённые

## Недавние и изменённые

«Недавние» сортируют все разрешённые файлы по filesystem `mtime`. Если Git доступен,
«Изменённые» фильтруют non-clean working-tree status. Без Git индекс использует свои
`hash_modified` и `mtime_modified` statuses. `first_seen` сам по себе не считается modified.

Внешнее изменение становится видно проекции после explicit refresh/rebuild; watcher и
polling пока нет. Git не помечает файл как canonical knowledge и не создаёт commit.

## Недавно добавленные

Реализованные сигналы:

1. сохранённый `first_seen_at` из durable document metadata;
2. Git untracked/new status.

Первый Git commit и отдельная installation/import дата пока не используются как источник
`added_at`; они остаются возможным расширением проекции.

Файловый `ctime` не считается датой создания: его семантика отличается между ОС.
Подпись может выглядеть так: «Добавлен сегодня · впервые обнаружен raytsystem».
Представление «Добавленные» сортирует все файлы по `first_seen_at`; поисковый `is:new`
фильтрует Git added/untracked или `first_seen_at` не старше 30 дней.

## Почему rebuild не делает все файлы «новыми»

Search index сам по себе disposable. `first_seen_at` и переходы rename/move хранятся в минимальной
защищённой metadata-плоскости. Пересборка восстанавливает их, а не переписывает текущим
временем. Идентичность сохраняется для rename/move, выполненных через raytsystem.
Корреляция переименования, сделанного внешним инструментом, отложена.

## Связанные страницы

- [Поиск](/documents/search)
- [История изменений](/documents/history)

## Источники истины

- `ops/decisions/ADR-035-managed-document-workspace.md`
- `docs/13-documents-workspace-architecture.md`

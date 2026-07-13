---
title: "Поиск по документам"
description: "Как искать по имени, пути, содержимому, headings, tags, aliases, properties и wikilinks, применять фильтры и понимать статус document index."
audience: [user, operator]
status: experimental
feature_flags: []
related_commands: []
related_pages:
  - /documents/overview
  - /documents/recent-new-modified
  - /documents/editable-and-protected
  - /documents/troubleshooting
source_of_truth:
  - path: ops/decisions/ADR-035-managed-document-workspace.md
  - path: docs/13-documents-workspace-architecture.md
last_verified_against: "ADR-035 / experimental Documents contract"
---

# Поиск по документам

## Что индексируется

Поиск охватывает filename, относительный путь, title, Markdown-текст, headings, tags,
aliases, простые frontmatter properties и wikilinks. Он работает по отдельному
локальному FTS5-индексу и не смешивает непроверенные заметки с canonical claims.

Файлы из hidden roots, секретные filenames и restricted content в полнотекстовый индекс не
попадают. В зависимости от policy они скрыты или показаны только как очищенные metadata.

## Синтакс

| Пример | Смысл |
|---|---|
| `архитектура` | Обычные литеральные токены |
| `"exact phrase"` | Точная фраза |
| `path:notes/projects` | Префикс относительного пути |
| `type:md` | Расширение файла |
| `tag:research` | Тег из frontmatter или Markdown |
| `property:status=active` | Простое property/value |
| `is:modified` | Нечистый Git status; без Git — raytsystem hash/mtime drift status |
| `is:new` | Git added/untracked или `first_seen_at` за последние 30 дней |
| `is:readonly` | Только read-only/protected |
| `after:2026-07-01` | Изменён после даты |
| `before:2026-08-01` | Изменён до даты |

Фильтры можно сочетать. Поиск не исполняет SQL, regex или синтакс из документа.
Выбор root, folder, kind и policy mode также доступен отдельными UI/API-фильтрами. Не
путайте `is:new` с представлением «Добавленные»: фильтр имеет 30-дневное окно или Git
new status, а представление сортирует весь разрешённый набор по durable `first_seen_at`.

## Сортировка и пагинация

Доступны relevance, недавно изменённые, недавно добавленные, имя A–Z/Z–A, размер,
папка и количество входящих/исходящих ссылок. Результаты постраничные; весь corpus не
отправляется в browser.

## Статус индекса

Кнопка индекса показывает state, а для `current` — ещё и число файлов. API status также
возвращает время refresh и счётчик ошибок. GET-поиск не перестраивает индекс. Refresh/rebuild
идут отдельно через защищённые write routes; watcher и polling в текущей поставке нет.

## Связанные страницы

- [Недавние, добавленные и изменённые](/documents/recent-new-modified)
- [Решение проблем](/documents/troubleshooting)

## Источники истины

- `ops/decisions/ADR-035-managed-document-workspace.md`
- `docs/13-documents-workspace-architecture.md`

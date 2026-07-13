---
title: "Properties и frontmatter"
description: "Как правая панель показывает YAML frontmatter, какие типы можно менять формой и когда сложный YAML остаётся доступен только в Source mode."
audience: [user, developer]
status: experimental
feature_flags: []
related_commands: []
related_pages:
  - /documents/markdown-view
  - /documents/visual-editor
  - /documents/source-mode
source_of_truth:
  - path: ops/decisions/ADR-035-managed-document-workspace.md
  - path: docs/13-documents-workspace-architecture.md
  - path: docs/14-documents-security-review.md
last_verified_against: "ADR-035 / experimental Documents contract"
---

# Properties и frontmatter

## Поддерживаемые поля

Правая панель представляет простой YAML frontmatter как форму. Поддерживаются string, number,
boolean, date, list, tags и aliases. Неизвестные поля не удаляются.

Frontmatter остаётся частью Markdown. Форма меняет только одну простую строку и не
пересериализует весь YAML. Поле с comment, multiline continuation или complex value помечается
`Source only`, чтобы не удалить comments, неизвестные поля или их порядок молча.

## Когда форма read-only

Сложный YAML — anchors, aliases, merge keys, duplicate keys, неоднозначные scalars, глубокие или
слишком большие структуры — не сериализуется формой. Properties остаются доступными для безопасного
просмотра, а правка выполняется в Source mode.

Depth, node count и scalar size ограничены, чтобы frontmatter не мог вызвать YAML bomb. Ошибка
парсинга не удаляет исходный envelope и не мешает открыть остальной Markdown.

## Сохранение

Изменение формы сначала становится тем же session draft, что и Source mode. Явный Save
затем проходит expected hash/snapshot, policy и atomic write. В Visual форма свойств отключена,
потому что открытая Milkdown-модель владеет draft. Если поле нельзя изменить без потери
comments или structure, редактируйте его в Source mode.

## Связанные страницы

- [Просмотр Markdown](/documents/markdown-view)
- [Визуальный редактор](/documents/visual-editor)
- [Исходный Markdown](/documents/source-mode)

## Источники истины

- `ops/decisions/ADR-035-managed-document-workspace.md`
- `docs/13-documents-workspace-architecture.md`
- `docs/14-documents-security-review.md`

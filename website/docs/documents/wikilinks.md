---
title: "Wikilinks"
description: "Какие wikilink-формы понимает raytsystem, как выбирается точное совпадение, обрабатываются aliases и headings и показывается неоднозначность."
audience: [user, developer]
status: experimental
feature_flags: []
related_commands: []
related_pages:
  - /documents/markdown-view
  - /documents/backlinks
  - /documents/graph
source_of_truth:
  - path: ops/decisions/ADR-035-managed-document-workspace.md
  - path: docs/13-documents-workspace-architecture.md
last_verified_against: "ADR-035 / experimental Documents contract"
---

# Wikilinks

## Поддерживаемые формы

raytsystem распознаёт:

```text
[[Документ]]
[[Документ|Название]]
[[Документ#Раздел]]
![[Изображение.png]]
```

Поиск цели использует разрешённые document IDs, относительные пути, title и aliases. Точное
совпадение открывается во вкладке. Suffix `#Раздел` парсится; после открытия в Read mode raytsystem
прокручивает и переводит keyboard focus к совпавшему heading. Если раздел не найден,
появляется явное сообщение.

Если название неоднозначно, интерфейс показывает ограниченный список кандидатов с root и
относительным путём. Если цели нет, ссылка остаётся видимой как unresolved — raytsystem не создаёт и
не угадывает документ автоматически.

## Embeds и безопасность

Image embed разрешается только в безопасный локальный asset с подходящим MIME и read policy.
Произвольный путь, remote tracking image, data URL или executable SVG не загружается. Встраивание
Markdown-документов как исполняемого HTML не поддерживается.

Wikilink — это пользовательские данные, а не подтверждённое утверждение и не инструкция агенту.
Разрешённая ссылка создаёт только производное document relationship.

## Связанные страницы

- [Backlinks](/documents/backlinks)
- [Документ во Вселенной](/documents/graph)
- [Просмотр Markdown](/documents/markdown-view)

## Источники истины

- `ops/decisions/ADR-035-managed-document-workspace.md`
- `docs/13-documents-workspace-architecture.md`

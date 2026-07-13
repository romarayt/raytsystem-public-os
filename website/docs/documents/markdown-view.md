---
title: "Просмотр Markdown"
description: "Как raytsystem показывает Markdown как оформленный документ, какой CommonMark/GFM subset квалифицирован и как блокируются HTML, опасные URL, remote images и SVG."
audience: [user, developer]
status: experimental
feature_flags: []
related_commands: []
related_pages:
  - /documents/overview
  - /documents/visual-editor
  - /documents/source-mode
  - /documents/wikilinks
source_of_truth:
  - path: ops/decisions/ADR-035-managed-document-workspace.md
  - path: docs/14-documents-security-review.md
last_verified_against: "ADR-035 / experimental Documents contract"
---

# Просмотр Markdown

## Поддерживаемый вид

«Чтение» показывает headings, bold, italic, strikethrough, links, images, ordered/unordered
lists, task lists, tables, inline/fenced code, blockquotes, horizontal rules и footnotes.
raytsystem-расширения добавляют wikilinks и квалифицированные callouts.

Frontmatter показывается как properties, но остаётся частью исходного Markdown-файла.

## Как устроена безопасность

Текст разбирается ограниченным локальным parser-ом, а React создаёт только разрешённые узлы.
Произвольный HTML не вставляется в DOM и не исполняется. Scripts, styles, event handlers,
iframes, object/embed и опасные SVG блокируются.

Для links допускаются безопасные schemes и типизированные локальные ссылки. `javascript:`,
`data:` и credential-bearing URL не становятся активными. Внешние tracking images по умолчанию
не загружаются.

## Локальные изображения

Квалифицированная база — безопасный просмотр уже существующего локального PNG/JPEG/WebP/GIF
через typed asset ID, MIME-проверку и document policy. Исходный абсолютный путь browser не видит.
Загрузка/копирование нового attachment не считается доступным, пока не пройдены его отдельные
MIME/path/no-overwrite tests.

## Если синтаксис не знаком

Неизвестная конструкция не удаляется из файла. Read mode показывает её как inert/
unsupported block, а для правки предлагает Source mode. Это не обещание полной Obsidian-совместимости.

## Связанные страницы

- [Визуальный редактор](/documents/visual-editor)
- [Исходный Markdown](/documents/source-mode)
- [Wikilinks](/documents/wikilinks)

## Источники истины

- `ops/decisions/ADR-035-managed-document-workspace.md`
- `docs/14-documents-security-review.md`

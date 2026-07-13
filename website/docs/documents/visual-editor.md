---
title: "Визуальный редактор"
description: "Когда доступен Milkdown-редактор, какие Markdown-команды он поддерживает и почему неизвестный или потенциально lossy синтаксис переводит документ в Source mode."
audience: [user, developer]
status: experimental
feature_flags: []
related_commands: []
related_pages:
  - /documents/markdown-view
  - /documents/source-mode
  - /documents/properties
  - /documents/wikilinks
source_of_truth:
  - path: ops/decisions/ADR-035-managed-document-workspace.md
  - path: docs/13-documents-workspace-architecture.md
last_verified_against: "ADR-035 / 2026-07-12 editor spike"
---

# Визуальный редактор

## Когда он доступен

Визуальный режим основан на Milkdown, ProseMirror, Remark и GFM. Он включается для конкретного
документа только после round-trip qualification: преобразование `Markdown → editor model →
Markdown` не должно потерять или незаметно изменить значимое содержимое.

raytsystem сохраняет raw frontmatter envelope и восстанавливает квалифицированные wikilinks и
callouts. Неизвестные блоки, сложный YAML, raw HTML или неподдерживаемое community-расширение
делают визуальное сохранение недоступным. Файл при этом не повреждается — его можно править в
Source mode.

## Почему выбран Milkdown

В Chromium/Vite spike проверялся Milkdown `7.21.2` (MIT) через прямой `@milkdown/kit` с
CommonMark/GFM. Он выбран как Markdown-first visual candidate: использует ProseMirror/Remark,
расширяется plugins и не требует cloud backend.

Сравнение было таким:

| Кандидат | Решение |
|---|---|
| Milkdown `7.21.2` | Выбран для Visual после qualification |
| Tiptap Markdown `3.27.3` | Не выбран: Markdown feature всё ещё Beta |
| CodeMirror 6 | Выбран для точного Source mode, не для Visual |
| Handbook renderer | Остаётся безопасной основой Read mode, но не является editor |

Изолированный spike измерил Milkdown core+CommonMark+GFM как 435.70 kB raw /
131.22 kB gzip, а CodeMirror basicSetup+Markdown+lint (`6.0.2` / `6.5.0` / `6.9.7`) —
607.53 / 207.79 kB. Parse→serialize после warm-up занял 0.7–5.4 ms; первый запуск —
34.9 ms.

В production build entry points другие, поэтому цифры фиксируются отдельно: проверенные
2026-07-13 Vite lazy chunks — Visual 456.48 / 137.45 kB и Source 607.69 / 207.86 kB,
raw / gzip. Editor загружается только для активного документа.

## Что показал round-trip

CommonMark/GFM meaning сохранился. Per-document serializer profile прошёл exact fixtures для
канонических однородных markers/indentation списков, thematic breaks и простой table layout.
Без защитного envelope Milkdown разрушал frontmatter, экранировал
wikilinks/embeds/callouts, нормализовал CRLF и добавлял final newline; envelope и protected tokens
закрывают эти случаи.

Tight lists, смешанные markers и некоторые неканонические четырёхпробельные вложения Milkdown всё
ещё нормализует. Raw HTML и неизвестные конструкции также остаются Source-only. Любой такой случай
виден до записи и не проходит строгий equality guard.

Поэтому Visual открывается после серверного syntax gate, а client создаёт реальный
Milkdown editor и сравнивает восстановленные байты с исходником до правок. Любое
несовпадение блокирует Visual Save. Зависимости закреплены lockfile; полный и
production-only `npm audit` от 2026-07-12 вернули ноль известных уязвимостей в
443-package resolved graph. Production notices генерируются из lockfile и проверяются
`npm --prefix web run licenses:check`.

## Панель и клавиатура

Панель содержит undo/redo, heading, bold, italic, strikethrough, link, wikilink, unordered/
ordered/task list, quote, callout, inline code, code block, table и horizontal rule. Кнопка
изображения видна как недоступная: attachment picker ещё не входит в поставку.

Поддерживаются `Cmd/Ctrl+B`, `Cmd/Ctrl+I`, `Cmd/Ctrl+K`, `Cmd/Ctrl+S`, undo/redo и
`Cmd/Ctrl+Shift+M` для переключения Visual/Source. Отдельной command palette в текущей
поставке нет. У каждой активной кнопки есть доступное имя.

## Безопасное сохранение

Visual сохраняет Markdown, а не отдельный rich-text формат. Browser выполняет точный runtime
round-trip guard, а сервер независимо проверяет expected snapshot, SHA-256, policy и filesystem
identity. Если transform стал lossy или файл успел измениться, запись блокируется и показывается
предупреждение либо conflict.

raytsystem не обещает полную совместимость с Obsidian plugins и community syntax. Source mode —
основной совместимый путь для конструкций, которые визуальный pipeline не умеет сохранять точно.

## Связанные страницы

- [Просмотр Markdown](/documents/markdown-view)
- [Исходный Markdown](/documents/source-mode)
- [Properties](/documents/properties)
- [Wikilinks](/documents/wikilinks)

## Источники истины

- `ops/decisions/ADR-035-managed-document-workspace.md`
- `docs/13-documents-workspace-architecture.md`

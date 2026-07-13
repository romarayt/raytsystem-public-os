---
title: "Исходный Markdown"
description: "Как Source mode сохраняет точный Markdown, где живёт черновик, как работает явное сохранение и что происходит при конфликте snapshot или content hash."
audience: [user, developer]
status: experimental
feature_flags: []
related_commands: []
related_pages:
  - /documents/overview
  - /documents/visual-editor
  - /documents/history
  - /documents/troubleshooting
source_of_truth:
  - path: ops/decisions/ADR-035-managed-document-workspace.md
  - path: docs/13-documents-workspace-architecture.md
  - path: docs/14-documents-security-review.md
last_verified_against: "ADR-035 / experimental Documents contract"
---

# Исходный Markdown

## Редактор исходника

Source mode использует CodeMirror 6 и работает с точной строкой, загруженной с диска. Текущий
`basicSetup` даёт Markdown highlighting, видимые line numbers, find/replace, bracket matching и
keyboard navigation. raytsystem добавляет diagnostics для конструкций, не квалифицированных для
Visual. Отдельная custom-подсветка frontmatter/wikilinks и настройка line numbers пока не
заявляются.

Редактор сохраняет LF/CRLF и наличие финального newline, если пользователь сам их не изменил.
Malformed UTF-8 не перезаписывается: raytsystem показывает read-only ошибку, чтобы не повредить байты.

## Черновик и сохранение

Черновик живёт в памяти или session-scoped storage. Private document content не попадает в
`localStorage`. Индикатор показывает несохранённые изменения, а закрытие вкладки требует решения
пользователя.

Сохранение выполняется явно кнопкой или `Cmd/Ctrl+S`; автосохранения каждого символа нет.
Каждый update передаёт document ID, expected snapshot и SHA-256 открытой версии. Сервер повторно
проверяет policy, stable document identity и делает no-follow atomic replace. Если изменился только
глобальный index snapshot из-за другого файла, update может выполнить scoped rebase и вернуть
`snapshot_rebased`; SHA-256 текущего документа при этом всё равно должен совпасть.

## Conflict и Diff

Если байты или stable identity файла изменились после открытия, raytsystem не перезаписывает его.
Conflict показывает, когда версии безопасно доступны:

1. базовую версию на момент открытия;
2. текущую версию на диске;
3. локальный draft.

Diff отмечает добавленные и удалённые строки и итоговый Markdown. Merge выполняется вручную;
автоматический LLM merge не запускается. После merge сохранение использует новый expected hash.

## Связанные страницы

- [Визуальный редактор](/documents/visual-editor)
- [История и Diff](/documents/history)
- [Решение проблем](/documents/troubleshooting)

## Источники истины

- `ops/decisions/ADR-035-managed-document-workspace.md`
- `docs/13-documents-workspace-architecture.md`
- `docs/14-documents-security-review.md`

---
title: "Создание документа"
description: "Как создать Markdown-документ в разрешённом root, выбрать папку и шаблон, заранее проверить destination и безопасно выполнить rename или move."
audience: [user, operator]
status: experimental
feature_flags: []
related_commands: []
related_pages:
  - /documents/overview
  - /documents/editable-and-protected
  - /documents/properties
  - /documents/troubleshooting
source_of_truth:
  - path: ops/decisions/ADR-035-managed-document-workspace.md
  - path: docs/13-documents-workspace-architecture.md
  - path: docs/14-documents-security-review.md
last_verified_against: "ADR-035 / experimental Documents contract"
---

# Создание документа

## Новый документ

Кнопка «Новый документ» предлагает выбрать название, writable root, папку, template,
properties и tags. Preview показывает только workspace-relative destination. raytsystem:

- добавляет `.md`, если расширения нет;
- запрещает absolute path, `..`, управляющие символы и конфликт имени;
- не перезаписывает существующий файл;
- создаёт файл атомарно, обновляет индекс и открывает документ во вкладке.

Доступные templates — пустой документ, заметка, проект, встреча, исследование и ежедневная
заметка. Шаблон является данными: его текст не исполняется и не становится инструкцией агенту.

## Папки, rename и move

Создание папки, переименование и перемещение работают только через root/folder/document IDs.
Сервер сам вычисляет путь, проверяет destination и отказывает при symlink, защищённой области или
существующем объекте. Move не может ослабить policy.

Удаления в этой версии нет. raytsystem также не делает автоматический Git commit.

## Изображения

В этой поставке доступен безопасный read-only просмотр уже проиндексированного локального
изображения. Picker существующего изображения, вставка Markdown-ссылки и upload/copy в
attachments-папку ещё не реализованы. Они появятся только вместе с MIME, size, path,
no-overwrite и audit tests. Arbitrary filesystem path и executable SVG не поддерживаются.

## Связанные страницы

- [Что можно редактировать](/documents/editable-and-protected)
- [Properties](/documents/properties)
- [Решение проблем](/documents/troubleshooting)

## Источники истины

- `ops/decisions/ADR-035-managed-document-workspace.md`
- `docs/13-documents-workspace-architecture.md`
- `docs/14-documents-security-review.md`

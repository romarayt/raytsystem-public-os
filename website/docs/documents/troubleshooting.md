---
title: "Документы: решение проблем"
description: "Что проверить, если файл не виден, index устарел, Visual недоступен, сохранение вернуло conflict, wikilink неоднозначен или изображение заблокировано."
audience: [user, operator, developer]
status: experimental
feature_flags: []
related_commands: []
related_pages:
  - /documents/overview
  - /documents/search
  - /documents/source-mode
  - /documents/editable-and-protected
source_of_truth:
  - path: ops/decisions/ADR-035-managed-document-workspace.md
  - path: docs/13-documents-workspace-architecture.md
  - path: docs/14-documents-security-review.md
last_verified_against: "ADR-035 / experimental Documents contract"
---

# Документы: решение проблем

## Файл не виден

Проверьте, что файл находится в configured root и не попал в hidden/protected, secret, ignored или
oversized policy. Symlinked roots/components/files не индексируются. Browser не может открыть файл
по абсолютному пути в обход policy.

## Индекс устарел или повреждён

Кнопка индекса показывает state, а для current — число файлов; полный API status также содержит
время refresh и счётчик ошибок. GET/search не запускает скрытую запись.
Пока background initialization ещё идёт, UI повторяет listing раз в секунду до 60 попыток.
Используйте защищённый refresh для изменившегося файла или explicit rebuild. Индекс disposable:
rebuild восстанавливает его из разрешённых файлов и durable metadata, не меняя document bytes.

## Нет кнопки Edit или Visual

Edit отсутствует для read-only/protected root или sensitivity decision. Visual может быть выключен
отдельно, если round-trip qualification обнаружила неизвестный синтаксис, complex frontmatter, raw
HTML или несовпадение line-ending/final-newline envelope. Используйте Source mode; не удаляйте
unsupported block ради включения Visual без осознанного решения.

## Save вернул conflict

Байты или stable identity файла изменились после открытия, либо нужный snapshot больше недоступен.
Сравните safely available base, current disk и local draft в Diff, вручную перенесите нужные
изменения и повторите Save с новым hash. Несвязанный дрейф глобального index snapshot может
быть принят как scoped rebase при совпадающем SHA-256. raytsystem не выполняет автоматический LLM merge.

## Wikilink не открывается

При нескольких совпадениях выберите candidate по root и относительному пути. Unresolved link может
указывать на скрытый/недоступный документ или отсутствующую цель; raytsystem не угадывает её.

## Изображение заблокировано

Проверьте, что это существующий разрешённый локальный asset с поддерживаемым MIME. Remote tracking
images, data URLs, arbitrary paths и executable SVG блокируются. Attachment picker, Markdown insertion и
upload/copy в этой experimental-поставке не реализованы.

## Большой workspace или много backlinks

Tree раскрывается лениво, results и backlinks постраничные, а editor загружает только активный
документ. Watcher/polling пока нет: для внешних изменений запустите explicit refresh/rebuild.
Браузер не получает весь corpus.

## Ограничения версии

Нет deletion, Sync, collaboration, Canvas, marketplace plugins, произвольного filesystem access и
редактирования PDF/audio/video. Также отложены external rename correlation, command palette и
attachment flow. Закрытые вкладки можно восстановить по ID, но private draft
не сохраняется в `localStorage`.

## Связанные страницы

- [Поиск](/documents/search)
- [Исходный Markdown](/documents/source-mode)
- [Что можно редактировать](/documents/editable-and-protected)

## Источники истины

- `ops/decisions/ADR-035-managed-document-workspace.md`
- `docs/13-documents-workspace-architecture.md`
- `docs/14-documents-security-review.md`

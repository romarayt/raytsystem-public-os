---
title: "Что можно редактировать и что защищено"
description: "Как document roots и режимы read_write, read_only, protected_read_only и hidden ограничивают workspace и какие raytsystem state области нельзя открыть generic editor."
audience: [user, operator, developer]
status: experimental
feature_flags: []
related_commands: []
related_pages:
  - /documents/overview
  - /documents/create-document
  - /documents/troubleshooting
source_of_truth:
  - path: ops/decisions/ADR-035-managed-document-workspace.md
  - path: docs/13-documents-workspace-architecture.md
  - path: docs/14-documents-security-review.md
  - path: website/docs/knowledge/editable-vs-immutable.md
last_verified_against: "ADR-035 / experimental Documents contract"
---

# Что можно редактировать и что защищено

## Режимы roots

| Режим | Видимость | Редактирование |
|---|---|---|
| `read_write` | Разрешённые metadata и content | Разрешено после всех write checks |
| `read_only` | Разрешённое read-представление | Нет |
| `protected_read_only` | Только безопасное protected-представление | Никогда |
| `hidden` | Нет в tree/search/API | Никогда |

Текущие repo roots: `knowledge/manual` (`manual`, notes), `docs` (`maintainer-docs`,
documentation) и `website/docs` (`public-docs`, documentation). `knowledge/manual` — явное
редактируемое исключение внутри protected `knowledge`. А `docs` и `website/docs` могут быть
writable только в maintainer workspace с явным `allow_maintainer_docs_write = true`; одного
объявления этих roots недостаточно. Другие пользовательские Markdown roots должны быть
добавлены явно.

## Жёстко защищённые области

Configuration может сузить доступ, но не снять compiled protection. Generic Documents editor не
изменяет `_raw/`, `normalized/`, ledger и generations, generated `knowledge/` за пределами
`knowledge/manual/`, run manifests, task ledger, audit events, `.git/`, secrets, `.raytsystem/`
managed state, platform/revision stores, indexes, graph snapshots, build/cache/dependency state,
package locks и machine credentials.

Repo-local skills редактируются только специализированным skill editor. Protected и read-only
объекты не получают кнопку «Редактировать»; сервер всё равно повторяет policy check при write.

## Почему browser не получает path authority

API использует document/root/folder IDs и относительные display paths. Абсолютные пути не входят в
payload, URL, logs или history. Сервер отклоняет traversal, symlink/hardlink и destination вне
разрешённого root.

## Связанные страницы

- [Создание документа](/documents/create-document)
- [Решение проблем](/documents/troubleshooting)
- [Editable vs immutable knowledge](/knowledge/editable-vs-immutable)

## Источники истины

- `ops/decisions/ADR-035-managed-document-workspace.md`
- `docs/13-documents-workspace-architecture.md`
- `docs/14-documents-security-review.md`
- `website/docs/knowledge/editable-vs-immutable.md`

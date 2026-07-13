---
title: "Документ во Вселенной"
description: "Как действие «Показать в графе» фокусирует Knowledge Universe на документе, links и backlinks и сохраняет различие между заметкой и canonical claim."
audience: [user, developer]
status: experimental
feature_flags: []
related_commands: []
related_pages:
  - /documents/overview
  - /documents/wikilinks
  - /documents/backlinks
  - /interface/universe
source_of_truth:
  - path: ops/decisions/ADR-035-managed-document-workspace.md
  - path: docs/13-documents-workspace-architecture.md
last_verified_against: "ADR-035 / experimental Documents contract"
---

# Документ во Вселенной

## Показать в графе

Действие «Показать в графе» передаёт opaque document ID. Сервер возвращает связанный с
текущим document snapshot bounded slice: сам документ и его resolved outgoing/backlink document
neighbors.

Граф не читает весь vault и не принимает browser path. Проекция typed document→claim/entity
связей пока не входит в document focus; они не выводятся из простого сходства текста.

## Типы и доверие

Узлы визуально различают как минимум:

```text
manual_document
documentation_document
generated_document
document
```

В общей Вселенной канонические типы по-прежнему отличаются:

```text
source
claim
entity
evidence
```

Пользовательская заметка остаётся непроверенным документом. Наличие ссылки на claim или evidence не
превращает её текст в canonical knowledge; promotion по-прежнему проходит через существующий INGEST
и validation boundary.

## Связанные страницы

- [Wikilinks](/documents/wikilinks)
- [Backlinks](/documents/backlinks)
- [Вселенная](/interface/universe)

## Источники истины

- `ops/decisions/ADR-035-managed-document-workspace.md`
- `docs/13-documents-workspace-architecture.md`

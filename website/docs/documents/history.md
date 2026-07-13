---
title: "История изменений и Diff"
description: "Какие Git и локальные revisions видны в истории, как сравнить версии и почему restore всегда требует preview, expected hash и подтверждение."
audience: [user, operator, developer]
status: experimental
feature_flags: []
related_commands: []
related_pages:
  - /documents/source-mode
  - /documents/recent-new-modified
  - /documents/troubleshooting
source_of_truth:
  - path: ops/decisions/ADR-035-managed-document-workspace.md
  - path: docs/13-documents-workspace-architecture.md
  - path: docs/14-documents-security-review.md
last_verified_against: "ADR-035 / experimental Documents contract"
---

# История изменений и Diff

## Источники истории

История показывает два server-side источника и один session-only слой:

1. bounded Git history, если workspace использует Git;
2. локальные immutable revisions, созданные DocumentService;
3. текущий unsaved diff относительно версии при открытии — только в browser session,
   он не становится history record.

Для записи показываются дата, источник, hash, безопасное отображаемое имя автора, diff и действие
копирования старой версии. Git email и произвольный формат commit не раскрываются.
Диалог diff привязан к окну браузера и адаптируется к доступной ширине: на узком экране сводка,
счётчики и действие копирования перестраиваются без горизонтального смещения, а содержимое diff
остаётся во внутренней прокрутке.

Durable first-seen, identity, idempotency и hash-only audit metadata находятся в защищённом
`ops/platform.sqlite`. Private revision objects и manifests хранятся content-addressed в
`ops/document-revisions/` и входят только в private/workspace-transfer backup. Restricted content
имеет metadata-only history: его bytes не записываются в revision store, пока отдельно не включена
и не квалифицирована encrypted policy.

## Restore

Restore — отдельная write-операция, а не кнопка мгновенного отката. Сначала показывается preview и
diff, затем проверяется expected current hash и запрашивается подтверждение. Успешное восстановление
создаёт новый audit/revision record, но не делает Git commit.

Если байты или stable identity текущего файла изменились после preview, restore возвращает
conflict и ничего не перезаписывает. Несвязанный дрейф глобального index snapshot может быть
принят как scoped rebase только при совпадающем expected SHA-256.

## Связанные страницы

- [Исходный Markdown](/documents/source-mode)
- [Недавние, добавленные и изменённые](/documents/recent-new-modified)
- [Решение проблем](/documents/troubleshooting)

## Источники истины

- `ops/decisions/ADR-035-managed-document-workspace.md`
- `docs/13-documents-workspace-architecture.md`
- `docs/14-documents-security-review.md`
